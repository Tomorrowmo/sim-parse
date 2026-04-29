"""OpenFOAM dictionary file parser (lightweight).

Not a full grammar parser; handles 95% of practical cases:
    - top-level key value;
    - top-level key (val1 val2 val3);
    - top-level named block: name { ... }
    - nested blocks
    - comments: //, /* */

For arbitrary deep parsing, prefer pyFoam's Foam.IO.ParsedParameterFile.
We avoid that dependency for Phase 1.
"""
from __future__ import annotations

import re
from pathlib import Path

from sim_parse.core.errors import safe_call


_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_FOAM_BANNER = re.compile(r"Version:\s+(v?\d{2,4}(?:\.\d+)?)", re.IGNORECASE)
_KEY_VALUE = lambda key: re.compile(  # noqa: E731
    rf"^\s*{re.escape(key)}\s+([^{{;]+?)\s*;", re.MULTILINE
)


def strip_comments(text: str) -> str:
    """Remove // line comments and /* block */ comments."""
    text = _BLOCK_COMMENT.sub("", text)
    text = _LINE_COMMENT.sub("", text)
    return text


def read_text(path: Path) -> str:
    """Read file with encoding fallback. Returns '' on failure."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_foam_value(path: Path, key: str) -> str | None:
    """Read top-level `key value;` from a FoamFile dictionary.

    Returns value as stripped string, or None if not found.
    """
    text = strip_comments(read_text(path))
    if not text:
        return None
    m = _KEY_VALUE(key).search(text)
    if not m:
        return None
    return m.group(1).strip()


def read_foam_block(path: Path, block_name: str) -> str | None:
    """Read the body content of a top-level `name { ... }` block.

    Returns the content between { and } (excluding braces), or None.
    """
    text = strip_comments(read_text(path))
    if not text:
        return None
    m = re.search(rf"^\s*{re.escape(block_name)}\s*\{{", text, re.MULTILINE)
    if not m:
        return None
    start = m.end() - 1  # position of {
    end = _find_matching_brace(text, start)
    if end is None:
        return None
    return text[start + 1 : end]


def parse_block_to_dict(block_text: str) -> dict:
    """Parse a block body into a flat key→value dict (top-level keys only).

    Nested blocks become sub-dicts (one level recursion).
    """
    if not block_text:
        return {}
    text = strip_comments(block_text)
    out: dict = {}

    # Walk the text token by token (simplified)
    i = 0
    n = len(text)
    while i < n:
        # Skip whitespace
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        # Read key (alphanumeric + _)
        j = i
        while j < n and (text[j].isalnum() or text[j] in "_"):
            j += 1
        if j == i:
            i += 1
            continue
        key = text[i:j]
        i = j
        # Skip whitespace after key
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        # Now look at next char
        if text[i] == "{":
            # Block value — always try to recurse; if it has key-value pairs
            # ('; ' separators), result is a dict. If just a single value,
            # parse_block_to_dict returns {} and we fall back to string.
            end = _find_matching_brace(text, i)
            if end is None:
                break
            inner = text[i + 1 : end]
            inner_dict = parse_block_to_dict(inner)
            if inner_dict:
                out[key] = inner_dict
            else:
                out[key] = _strip_value(inner)
            i = end + 1
        else:
            # Find ;
            semi = text.find(";", i)
            if semi == -1:
                break
            out[key] = text[i:semi].strip()
            i = semi + 1
    return out


def read_foam_file_version(path: Path) -> str | None:
    """Extract OpenFOAM version from FoamFile banner of any dict file."""
    text = read_text(path)[:1024]
    m = _FOAM_BANNER.search(text)
    return m.group(1) if m else None


def _find_matching_brace(text: str, open_pos: int) -> int | None:
    """Given index of '{', return index of the matching '}', or None."""
    if open_pos >= len(text) or text[open_pos] != "{":
        return None
    depth = 1
    i = open_pos + 1
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _strip_value(s: str) -> str:
    """Clean up a value string."""
    return s.strip().strip(";").strip()


def parse_uniform_value(value_str: str):
    """Parse OpenFOAM 'uniform N' or 'uniform (a b c)' value string.

    Returns:
        - float for scalar uniform
        - list[float] for vector uniform
        - None if not a uniform / unparsable

    Examples:
        "uniform 292"            -> 292.0
        "uniform (0 0 0.3)"      -> [0.0, 0.0, 0.3]
        "nonuniform List<...>"   -> None  (we don't decode big arrays here)
    """
    if not value_str:
        return None
    s = value_str.strip()
    if not s.startswith("uniform"):
        return None
    rest = s[len("uniform"):].strip()
    if not rest:
        return None
    # Vector form: (a b c)
    if rest.startswith("("):
        end = rest.find(")")
        if end == -1:
            return None
        try:
            return [float(x) for x in rest[1:end].split()]
        except ValueError:
            return None
    # Scalar form
    try:
        return float(rest.split()[0])
    except (ValueError, IndexError):
        return None
