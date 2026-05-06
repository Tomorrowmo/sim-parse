"""Parsers for OpenFOAM polyMesh/ files.

The polyMesh/ directory contains:
    - points        — node coordinates (binary or ASCII array)
    - faces         — face connectivity (binary)
    - owner         — face owner cell (binary; HEADER has nCells/nPoints/nFaces note)
    - neighbour     — internal-face neighbour cell (binary)
    - boundary      — TEXT dictionary listing boundary patches

For Tier 3 metadata extraction, we only need:
    - owner header `note` field (all mesh counts in one line)
    - boundary text dict (patch list with type/nFaces/startFace)
"""
from __future__ import annotations

import re
from pathlib import Path

from sim_parse.core.errors import never_raise


_NOTE_FIELDS = re.compile(
    r"(?P<key>nPoints|nCells|nFaces|nInternalFaces)\s*[:=]\s*(?P<value>\d+)"
)


@never_raise(default=None)
def read_owner_header_note(case_root: Path, region: str = "") -> dict | None:
    """Extract nPoints/nCells/nFaces/nInternalFaces from polyMesh/owner header.

    Two extraction strategies, in order:
      1. note "nPoints:... nCells:..." in FoamFile{} block (snappyHexMesh,
         blockMesh-with-decomposeParDict, foamyHexMesh — they fill it in)
      2. fallback: first integer after the FoamFile{} block — it's the
         array length, which for `owner` IS nFaces. nCells/nPoints stay
         unknown at Tier 3 and may be filled by Tier 4 via VTK.

    Args:
        case_root: case root directory.
        region: region name (empty for single-region).

    Returns:
        Dict with int values, e.g. {"nPoints": 7109, "nCells": 3466, ...}, or None.
        Always returns at least {"nFaces": ...} when owner is readable.
    """
    if region:
        owner = case_root / "constant" / region / "polyMesh" / "owner"
    else:
        owner = case_root / "constant" / "polyMesh" / "owner"
    head_text = _read_text_head(owner, n_bytes=4096)
    if head_text is None:
        return None
    return _parse_note_text(head_text) or _parse_array_length_fallback(
        head_text, count_key="nFaces")


def _read_text_head(path: Path, *, n_bytes: int) -> str | None:
    """Read first n_bytes from path, with transparent .gz fallback. latin-1 decode.

    Why latin-1: the header is ASCII but the file body may be binary; latin-1
    is byte-preserving so we never raise on stray binary bytes.
    """
    import gzip
    try:
        if path.is_file():
            with open(path, "rb") as f:
                head = f.read(n_bytes)
        else:
            gz = path.with_suffix(path.suffix + ".gz")
            if not gz.is_file():
                return None
            with gzip.open(gz, "rb") as f:
                head = f.read(n_bytes)
    except OSError:
        return None
    return head.decode("latin-1", errors="replace")


def _parse_note_text(head_text: str) -> dict | None:
    """Find note "..." in header and parse the key:value pairs."""
    m = re.search(r'note\s+"([^"]*)"', head_text)
    if not m:
        return None
    note = m.group(1)
    found = {}
    for match in _NOTE_FIELDS.finditer(note):
        try:
            found[match.group("key")] = int(match.group("value"))
        except ValueError:
            continue
    return found if found else None


def _parse_array_length_fallback(head_text: str, *, count_key: str) -> dict | None:
    """Find the first standalone integer after the FoamFile {...} block.

    OpenFOAM stores list-typed data as
        FoamFile {...}
        // ...
        <N>
        (
            v1 v2 v3 ...
        )
    so the first int after the closing brace is the list length. Caller
    tells us whether that length means nFaces / nPoints / nCells based
    on which file we're reading.
    """
    # Find end of FoamFile { ... } block (matched at top of file)
    foam_start = head_text.find("FoamFile")
    if foam_start == -1:
        return None
    brace_open = head_text.find("{", foam_start)
    if brace_open == -1:
        return None
    depth = 1
    i = brace_open + 1
    while i < len(head_text) and depth > 0:
        if head_text[i] == "{":
            depth += 1
        elif head_text[i] == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    # i is now just past the closing brace; find first integer
    m = re.search(r"^\s*(\d+)\s*$", head_text[i:], re.MULTILINE)
    if not m:
        return None
    try:
        return {count_key: int(m.group(1))}
    except ValueError:
        return None


@never_raise(default=None)
def read_boundary_patches(case_root: Path, region: str = "") -> list[dict] | None:
    """Parse polyMesh/boundary text dict; return list of patches.

    Each patch dict: {name, type, nFaces, startFace, [physicalType], [inGroups]}
    """
    if region:
        boundary = case_root / "constant" / region / "polyMesh" / "boundary"
    else:
        boundary = case_root / "constant" / "polyMesh" / "boundary"

    text: str | None = None
    if boundary.is_file():
        text = boundary.read_text(encoding="utf-8", errors="replace")
    else:
        # .gz fallback (mirror of read_owner_header_note)
        boundary_gz = boundary.with_suffix(boundary.suffix + ".gz")
        if boundary_gz.is_file():
            import gzip
            try:
                with gzip.open(boundary_gz, "rb") as f:
                    text = f.read().decode("utf-8", errors="replace")
            except OSError:
                return None
    if text is None:
        return None

    text = _strip_comments(text)

    # The list looks like:
    #   N
    #   (
    #       patchName1 { type ...; nFaces ...; startFace ...; }
    #       patchName2 { ... }
    #   )
    # Find the opening (
    paren_open = text.find("(")
    if paren_open == -1:
        return None
    paren_close = _find_matching_paren(text, paren_open)
    if paren_close is None:
        return None

    body = text[paren_open + 1 : paren_close]
    # Now extract: patchName { content }
    patches = []
    i = 0
    n = len(body)
    while i < n:
        # Skip whitespace
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            break
        # Read patch name (alphanumeric + _-)
        j = i
        while j < n and (body[j].isalnum() or body[j] in "_-."):
            j += 1
        if j == i:
            i += 1
            continue
        name = body[i:j]
        i = j
        # Skip whitespace
        while i < n and body[i].isspace():
            i += 1
        if i >= n or body[i] != "{":
            continue
        end = _find_matching_brace(body, i)
        if end is None:
            break
        block = body[i + 1 : end]
        patch = _parse_patch_block(block)
        patch["name"] = name
        patches.append(patch)
        i = end + 1

    return patches


def _parse_patch_block(block: str) -> dict:
    """Extract type/nFaces/startFace/etc from inside a patch's {} block."""
    out: dict = {}
    for key in ("type", "nFaces", "startFace", "physicalType", "inGroups", "transform"):
        m = re.search(rf"\b{key}\s+([^;]+);", block)
        if m:
            value = m.group(1).strip()
            if key in ("nFaces", "startFace"):
                try:
                    out[key] = int(value)
                except ValueError:
                    out[key] = value
            else:
                out[key] = value
    return out


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _find_matching_paren(text: str, open_pos: int) -> int | None:
    if text[open_pos] != "(":
        return None
    depth = 1
    i = open_pos + 1
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _find_matching_brace(text: str, open_pos: int) -> int | None:
    if text[open_pos] != "{":
        return None
    depth = 1
    i = open_pos + 1
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None
