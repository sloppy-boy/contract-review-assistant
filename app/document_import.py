"""Bounded extraction for uploaded TXT, DOCX, and PDF files.

``ocr`` accepts ``(pdf_bytes, one_based_page_number)`` and returns text.
"""
from __future__ import annotations

from io import BytesIO
import math
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_RAW_BYTES = 10 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_EXTRACTED_CHARS = 1_000_000


class LocalTesseractOCR:
    """OCR a PDF page with configured local pdftoppm and Tesseract binaries."""

    def __init__(self, *, tesseract_cmd="tesseract", rasterizer_cmd="pdftoppm", timeout=30.0, language=None):
        try:
            timeout = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("OCR timeout must be finite and positive") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("OCR timeout must be finite and positive")
        self.tesseract_cmd = tesseract_cmd
        self.rasterizer_cmd = rasterizer_cmd
        self.timeout = timeout
        self.language = language

    def __call__(self, pdf_data: bytes, page_number: int) -> str:
        with TemporaryDirectory(prefix="contract-ocr-") as directory:
            work = Path(directory)
            pdf_path, image_stem = work / "input.pdf", work / "page"
            pdf_path.write_bytes(pdf_data)
            try:
                subprocess.run(
                    [self.rasterizer_cmd, "-f", str(page_number), "-l", str(page_number),
                     "-singlefile", "-png", str(pdf_path), str(image_stem)],
                    check=True, capture_output=True, timeout=self.timeout,
                )
                command = [self.tesseract_cmd, str(image_stem.with_suffix(".png")), "stdout"]
                if self.language:
                    command.extend(["-l", self.language])
                completed = subprocess.run(command, check=True, capture_output=True, timeout=self.timeout)
            except FileNotFoundError as exc:
                raise RuntimeError(f"local OCR dependency is unavailable: {exc.filename}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("local OCR timed out") from exc
            except subprocess.CalledProcessError as exc:
                raise RuntimeError("local OCR failed") from exc
            return completed.stdout.decode("utf-8", errors="replace").strip()


def _metadata(filename):
    return {"filename": {"value": filename, "source": "upload.filename"}}


def _check_text_limit(text):
    if len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError("extracted text exceeds 1,000,000 characters")


_EXPLICIT_METADATA = {
    "contractNumber": re.compile(r"^\s*合同编号\s*[:：]\s*(\S.*?)\s*$"),
    "partyA": re.compile(r"^\s*甲方\s*[:：]\s*(\S.*?)\s*$"),
    "partyB": re.compile(r"^\s*乙方\s*[:：]\s*(\S.*?)\s*$"),
    "signedDate": re.compile(r"^\s*签订日期\s*[:：]\s*(\S.*?)\s*$"),
    "amount": re.compile(r"^\s*合同金额\s*[:：]\s*(\S.*?)\s*$"),
}


def _add_explicit_metadata(metadata, segments):
    """Add only labelled values, retaining the exact supporting line."""
    for segment in segments:
        for line in segment["text"].splitlines():
            for key, pattern in _EXPLICIT_METADATA.items():
                if key in metadata:
                    continue
                match = pattern.match(line)
                if match:
                    metadata[key] = {
                        "value": match.group(1),
                        "source": {
                            "type": "segment",
                            "segmentId": segment["id"],
                            "location": dict(segment["location"]),
                            "quote": line.strip(),
                        },
                    }


def _configured_ocr():
    tesseract_cmd = os.environ.get("CONTRACT_OCR_TESSERACT_CMD")
    if not tesseract_cmd:
        return None
    try:
        timeout = float(os.environ.get("CONTRACT_OCR_TIMEOUT_SECONDS", "30"))
    except ValueError as exc:
        raise RuntimeError("CONTRACT_OCR_TIMEOUT_SECONDS must be numeric") from exc
    return LocalTesseractOCR(
        tesseract_cmd=tesseract_cmd,
        rasterizer_cmd=os.environ.get("CONTRACT_OCR_RASTERIZER_CMD", "pdftoppm"),
        timeout=timeout,
        language=os.environ.get("CONTRACT_OCR_LANGUAGE") or None,
    )


def _extract_txt(filename, data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("TXT document must be UTF-8 encoded") from exc
    _check_text_limit(text)
    parts = [part.strip() for part in text.replace("\r\n", "\n").split("\n\n") if part.strip()]
    segments = [{"id": f"paragraph-{i}", "location": {"paragraph": i}, "text": part}
                for i, part in enumerate(parts, 1)]
    return text, segments, _metadata(filename)


def _extract_docx(filename, data):
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_DOCX_EXPANDED_BYTES:
                raise ValueError("DOCX expanded content exceeds 50 MiB")
        document = Document(BytesIO(data))
    except ValueError:
        raise
    except (BadZipFile, KeyError, OSError, XMLSyntaxError) as exc:
        raise ValueError("invalid or damaged DOCX document") from exc
    segments = []
    paragraph_index = 0
    table_index = 0
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            paragraph_index += 1
            value = block.text.strip()
            if value:
                segments.append({"id": f"paragraph-{paragraph_index}", "location": {"paragraph": paragraph_index}, "text": value})
        elif isinstance(block, Table):
            table_index += 1
            for row_index, row in enumerate(block.rows, 1):
                value = " | ".join(cell.text.strip() for cell in row.cells)
                if value.strip(" |"):
                    segments.append({"id": f"table-{table_index}-row-{row_index}", "location": {"table": table_index, "row": row_index}, "text": value})
    text = "\n".join(segment["text"] for segment in segments)
    _check_text_limit(text)
    metadata = _metadata(filename)
    for key in ("title", "author", "subject"):
        value = getattr(document.core_properties, key, "")
        if value:
            metadata[key] = {"value": value, "source": f"docx.core_properties.{key}"}
    return text, segments, metadata


def _extract_pdf(filename, data, ocr):
    try:
        reader = PdfReader(BytesIO(data))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError("PDF exceeds 100 pages")
        extracted_pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except ValueError:
        raise
    except (PdfReadError, OSError, EOFError) as exc:
        raise ValueError("invalid or damaged PDF document") from exc
    if not any(extracted_pages) and ocr is None:
        raise RuntimeError("OCR is required for a scanned PDF, but no OCR engine was provided")
    missing_pages = [i for i, value in enumerate(extracted_pages, 1) if not value]
    if missing_pages and ocr is None:
        raise RuntimeError(f"page {missing_pages[0]} requires OCR to confirm complete extraction")
    segments, warnings, used_ocr = [], [], False
    for i, value in enumerate(extracted_pages, 1):
        if not value and ocr is not None:
            value = ocr(data, i)
            if not isinstance(value, str):
                raise RuntimeError("OCR callback must return text")
            value = value.strip()
            if value:
                used_ocr = True
                warnings.append(f"page {i} text was extracted with OCR")
            else:
                raise RuntimeError(f"page {i} OCR produced no text")
        if value:
            segments.append({"id": f"page-{i}", "location": {"page": i}, "text": value})
    if not segments:
        raise RuntimeError("OCR produced no text for the scanned PDF")
    text = "\n".join(segment["text"] for segment in segments)
    _check_text_limit(text)
    metadata = _metadata(filename)
    info = reader.metadata
    for key in ("title", "author", "subject"):
        value = getattr(info, key, None) if info else None
        if value:
            metadata[key] = {"value": str(value), "source": f"pdf.document_info.{key}"}
    method = "pdf-text+ocr" if used_ocr and any(extracted_pages) else "pdf-ocr" if used_ocr else "pdf-text"
    return text, segments, metadata, warnings, method


def extract_document(filename: str, data: bytes, *, ocr=None) -> dict:
    """Return text, located segments, sourced metadata, warnings, and method."""
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("filename is required")
    if not isinstance(data, bytes):
        raise ValueError("document data must be bytes")
    if not data:
        raise ValueError("document is empty")
    if len(data) > MAX_RAW_BYTES:
        raise ValueError("document exceeds 10 MiB")
    suffix = Path(filename).suffix.lower()
    warnings = []
    if suffix in {".txt", ".md"}:
        text, segments, metadata = _extract_txt(filename, data)
        method = "text"
    elif suffix == ".docx":
        text, segments, metadata = _extract_docx(filename, data)
        method = "docx"
    elif suffix == ".pdf":
        if ocr is None:
            ocr = _configured_ocr()
        text, segments, metadata, warnings, method = _extract_pdf(filename, data, ocr)
    else:
        raise ValueError("unsupported document type; expected TXT, Markdown, DOCX, or PDF")
    if not text.strip():
        raise ValueError("document contains no extractable text")
    _add_explicit_metadata(metadata, segments)
    return {"text": text, "segments": segments, "metadata": metadata,
            "warnings": warnings, "method": method}
