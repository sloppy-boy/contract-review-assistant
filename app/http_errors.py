"""Shared exception-to-HTTP boundary for resource routers."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException


def call(operation: Callable[[], Any], *, not_found: str = "record not found", forbidden: str | None = None) -> Any:
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=not_found) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=forbidden or str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
