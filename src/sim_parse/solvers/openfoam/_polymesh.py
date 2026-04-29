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

    Args:
        case_root: case root directory.
        region: region name (empty for single-region).

    Returns:
        Dict with int values, e.g. {"nPoints": 7109, "nCells": 3466, ...}, or None.
    """
    if region:
        owner = case_root / "constant" / region / "polyMesh" / "owner"
    else:
        owner = case_root / "constant" / "polyMesh" / "owner"
    if not owner.is_file():
        # also try compressed
        owner_gz = owner.with_suffix(owner.suffix + ".gz")
        if owner_gz.is_file():
            return _read_owner_note_from_gz(owner_gz)
        return None

    # Read first ~512 bytes — the header is at the top
    with open(owner, "rb") as f:
        head = f.read(2048)
    # Decode as latin-1 (header is ASCII, body is binary)
    head_text = head.decode("latin-1", errors="replace")

    return _parse_note_text(head_text)


def _read_owner_note_from_gz(path: Path) -> dict | None:
    """For .gz compressed owner files."""
    import gzip
    try:
        with gzip.open(path, "rb") as f:
            head = f.read(2048)
    except OSError:
        return None
    return _parse_note_text(head.decode("latin-1", errors="replace"))


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


@never_raise(default=None)
def read_boundary_patches(case_root: Path, region: str = "") -> list[dict] | None:
    """Parse polyMesh/boundary text dict; return list of patches.

    Each patch dict: {name, type, nFaces, startFace, [physicalType], [inGroups]}
    """
    if region:
        boundary = case_root / "constant" / region / "polyMesh" / "boundary"
    else:
        boundary = case_root / "constant" / "polyMesh" / "boundary"
    if not boundary.is_file():
        return None

    text = boundary.read_text(encoding="utf-8", errors="replace")
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
