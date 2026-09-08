import math
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.asset_semantic import semantic_search


def citation(asset_id, text):
    return {
        "assetId": asset_id,
        "title": f"{asset_id}.txt",
        "segmentId": f"{asset_id}-1",
        "location": {"page": 1},
        "text": text,
    }


def test_ranks_by_real_cosine_similarity_and_truncates():
    vectors = {
        "付款": [1.0, 0.0],
        "正交": [0.0, 2.0],
        "接近": [3.0, 1.0],
        "相反": [-1.0, 0.0],
    }

    def encoder(texts):
        return [vectors[text] for text in texts]

    hits = semantic_search(
        "付款",
        [citation("orthogonal", "正交"), citation("near", "接近"), citation("opposite", "相反")],
        encoder=encoder,
        limit=2,
    )

    assert [hit["assetId"] for hit in hits] == ["near", "orthogonal"]
    assert hits[0]["score"] == pytest.approx(3 / math.sqrt(10))
    assert hits[1]["score"] == pytest.approx(0.0)
    assert isinstance(hits[0]["score"], float)


def test_empty_candidates_do_not_call_encoder():
    def encoder(_texts):
        raise AssertionError("empty candidates should return before encoding")

    assert semantic_search("付款", [], encoder=encoder) == []


@pytest.mark.parametrize("query", ["", "   ", None, 123, "x" * 2001])
def test_rejects_invalid_query(query):
    with pytest.raises((TypeError, ValueError)):
        semantic_search(query, [], encoder=lambda texts: [[1.0] for _ in texts])


def test_rejects_more_than_500_candidates_before_encoding():
    rows = [citation(str(index), "正文") for index in range(501)]
    with pytest.raises(ValueError, match="500"):
        semantic_search("付款", rows, encoder=lambda _: pytest.fail("must validate first"))


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_rejects_invalid_limit(limit):
    with pytest.raises((TypeError, ValueError)):
        semantic_search("付款", [citation("a", "正文")], encoder=lambda texts: [[1.0] for _ in texts], limit=limit)


@pytest.mark.parametrize(
    "encoded",
    [
        [[0.0, 0.0], [1.0, 0.0]],
        [[1.0, 0.0], [0.0, 0.0]],
        [[float("nan"), 0.0], [1.0, 0.0]],
        [[1.0, 0.0], [float("inf"), 0.0]],
        [[1.0, 0.0]],
        [[1.0, 0.0], [1.0]],
    ],
)
def test_rejects_malformed_encoder_output(encoded):
    with pytest.raises(ValueError, match="向量"):
        semantic_search("付款", [citation("a", "正文")], encoder=lambda _: encoded)


def test_rejects_malformed_candidates_before_encoding():
    bad_rows = [
        None,
        {"assetId": "a", "title": "a", "segmentId": "s", "location": 1},
        {"assetId": "a", "title": "a", "segmentId": "s", "location": 1, "text": ""},
    ]
    for row in bad_rows:
        with pytest.raises((TypeError, ValueError)):
            semantic_search("付款", [row], encoder=lambda _: pytest.fail("must validate first"))


def test_missing_default_model_configuration_has_service_friendly_error(monkeypatch):
    monkeypatch.delenv("CRA_EMBEDDING_MODEL_PATH", raising=False)
    with pytest.raises(RuntimeError, match="503") as error:
        semantic_search("付款", [citation("a", "正文")])
    assert "CRA_EMBEDDING_MODEL_PATH" in str(error.value)


def test_default_models_are_local_only_cached_and_isolated_by_path(monkeypatch, tmp_path):
    import app.asset_semantic as module

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    created = []
    created_lock = threading.Lock()

    class FakeModel:
        def __init__(self, path, *, local_files_only):
            with created_lock:
                created.append((path, local_files_only))

        def encode(self, texts):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=FakeModel),
    )
    monkeypatch.setattr(module, "_MODEL_CACHE", {})
    monkeypatch.setenv("CRA_EMBEDDING_MODEL_PATH", str(first))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: semantic_search("付款", [citation("a", "正文")]),
                range(16),
            )
        )
    assert all(result[0]["score"] == pytest.approx(1.0) for result in results)
    assert created == [(str(first.resolve()), True)]

    monkeypatch.setenv("CRA_EMBEDDING_MODEL_PATH", str(second))
    semantic_search("付款", [citation("b", "正文")])
    assert created == [(str(first.resolve()), True), (str(second.resolve()), True)]
