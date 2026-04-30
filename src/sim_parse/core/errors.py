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

import copy
import functools
import logging
import traceback
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger("sim_parse")

T = TypeVar("T")


class ParseError(Exception):
    """Internal sim-parse error. Should be caught at API boundary, never re-raised to caller."""


# Sentinel: avoid deepcopy churn for the common immutable defaults.
_IMMUTABLE_DEFAULTS = (type(None), bool, int, float, complex, str, bytes,
                        frozenset, tuple)


def _fresh_default(default: Any) -> Any:
    """Return a fresh copy of a mutable default; pass immutables through.

    Why this matters: declaring `@never_raise(default={"errors": [...]})` and
    returning that dict on every failure causes ALL failed calls to share the
    SAME dict instance. If a caller mutates `out["errors"].append(...)`, the
    mutation persists into the next failure's "default" — silent cross-call
    contamination. Same for default=[] passed to _run_tier5_qoi etc.

    Tuples can be returned as-is even though they're technically iterable —
    they're immutable. We deep-copy lists/dicts/sets/custom objects.
    """
    if isinstance(default, _IMMUTABLE_DEFAULTS):
        return default
    return copy.deepcopy(default)


def never_raise(default: Any = None, log: bool = True) -> Callable:
    """Decorator: catch any exception inside the function, log it, return `default`.

    Use to enforce the never-raise contract at module boundaries.

    Each suppressed call gets a FRESH copy of `default` (immutables passed
    through, mutables deep-copied) — preventing the classic shared-default
    contamination bug.

    Example:
        @never_raise(default={})
        def parse_metadata(case_root):
            ...  # may raise; caught and a fresh {} returned each time
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
                return _fresh_default(default)  # type: ignore[return-value]
        return wrapper
    return decorator


def safe_call(fn: Callable[..., T], *args, default: Any = None, **kwargs) -> T:
    """Inline equivalent of @never_raise. Useful when wrapping ad-hoc calls.

    Like never_raise, returns a FRESH copy of `default` on suppression.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.warning("safe_call(%s) suppressed: %s", getattr(fn, "__name__", fn), e)
        return _fresh_default(default)  # type: ignore[return-value]
