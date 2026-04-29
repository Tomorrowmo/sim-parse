"""Error contract: NEVER raise from public API.

This module provides:
    - ParseError: internal exception type for sim-parse logic errors
    - never_raise: decorator that catches any exception and returns a default

Public API contract (parse_case, to_vtu, etc.):
    - MUST NOT raise
    - On unrecoverable error: set fields to None, append to result["errors"]
    - Caller can always trust result is a dict with documented keys
"""
from __future__ import annotations

import functools
import logging
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger("sim_parse")

T = TypeVar("T")


class ParseError(Exception):
    """Internal sim-parse error. Should be caught at API boundary, never re-raised to caller."""


def never_raise(default: Any = None, log: bool = True) -> Callable:
    """Decorator: catch any exception inside the function, log it, return `default`.

    Use to enforce the never-raise contract at module boundaries.

    Example:
        @never_raise(default={})
        def parse_metadata(case_root):
            ...  # may raise; caught and {} returned
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if log:
                    logger.warning(
                        "[%s] suppressed: %s\n%s",
                        fn.__name__,
                        e,
                        traceback.format_exc(),
                    )
                return default  # type: ignore[return-value]
        return wrapper
    return decorator


def safe_call(fn: Callable[..., T], *args, default: Any = None, **kwargs) -> T:
    """Inline equivalent of @never_raise. Useful when wrapping ad-hoc calls."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.warning("safe_call(%s) suppressed: %s", getattr(fn, "__name__", fn), e)
        return default  # type: ignore[return-value]
