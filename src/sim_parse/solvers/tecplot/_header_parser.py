"""Pure-Python Tecplot binary/ASCII header reader.

Extracts metadata WITHOUT loading the full mesh — enough to answer:
    - sub_format ("binary_v112" / "ascii" / etc.)
    - title
    - variable names
    - zone summary (count + names + dimension info)

Spec reference: Tecplot Data File Format Guide (Tecplot 360 EX 2017+).
We support the wire format from TDV75 → TDV112 (the major versions in
the wild). The layout common across versions:

    bytes 0-7    : '#!TDVnnn'  (nnn = 75/101/108/111/112)
    int  (i32)   : byte_order_marker = 1
    int  (i32)   : file_type      (only TDV101+; v75 lacks this)
    string       : title          (null-terminated 32-bit chars)
    int  (i32)   : num_variables
    string × N   : variable names
    [zones]      : marker float 299.0 + zone header

We read just enough to enumerate variables and start reading zone headers;
we do NOT decode field data here (that's vtkTecplotReader's job).
"""
from __future__ import annotations

import struct
from pathlib import Path


# Marker float that delimits zone headers (TDV101+).
_ZONE_MARKER = 299.0
# Marker float for end-of-header / data section start.
_EOHMARKER = 357.0


class TecplotHeaderError(Exception):
    pass


def parse_binary_header(path: Path, *, max_zones: int = 100) -> dict:
    """Read a Tecplot binary file's header and return structured info.

    Bails out early once it has enumerated variables; doesn't try to parse
    every zone if there are thousands. `max_zones` caps how many zone
    headers we attempt to read.

    Returns:
        {
          "sub_format": "binary_v<NNN>",
          "byte_order": "little" | "big",
          "file_type": <int 0|1|2> | None,
          "title": str | None,
          "n_variables": int,
          "variables": list[str],
          "n_zones_seen": int,
          "zones": [{name, n_nodes, n_elements, ...}, ...],
        }
    Raises TecplotHeaderError on malformed input.
    """
    with open(path, "rb") as f:
        magic = f.read(8)
        if not magic.startswith(b"#!TDV"):
            raise TecplotHeaderError(f"not a Tecplot binary file: magic={magic!r}")

        version_str = magic[5:8].decode("ascii", errors="replace").strip()
        sub_format = f"binary_v{version_str}"
        try:
            version = int(version_str.replace(" ", ""))
        except ValueError:
            version = 0

        # Byte-order int
        bom = f.read(4)
        if len(bom) < 4:
            raise TecplotHeaderError("file truncated at byte-order marker")
        if struct.unpack("<i", bom)[0] == 1:
            endian = "<"
            byte_order = "little"
        elif struct.unpack(">i", bom)[0] == 1:
            endian = ">"
            byte_order = "big"
        else:
            # Default to little-endian; TDV75 didn't always include the bom.
            endian = "<"
            byte_order = "little"
            f.seek(8)  # rewind past magic only

        # File type only present in v101+
        file_type: int | None = None
        if version >= 101:
            ft = f.read(4)
            if len(ft) >= 4:
                file_type = struct.unpack(f"{endian}i", ft)[0]

        title = _read_tecplot_string(f, endian)
        nv_bytes = f.read(4)
        if len(nv_bytes) < 4:
            raise TecplotHeaderError("file truncated at num_variables")
        n_variables = struct.unpack(f"{endian}i", nv_bytes)[0]
        if n_variables < 0 or n_variables > 10_000:
            raise TecplotHeaderError(f"implausible n_variables={n_variables}")
        variables = [_read_tecplot_string(f, endian) for _ in range(n_variables)]

        # Read zone headers until we hit EOHMARKER or run out of zones.
        zones: list[dict] = []
        zones_seen = 0
        while zones_seen < max_zones:
            marker_bytes = f.read(4)
            if len(marker_bytes) < 4:
                break
            marker = struct.unpack(f"{endian}f", marker_bytes)[0]
            if abs(marker - _EOHMARKER) < 1e-3:
                break
            if abs(marker - _ZONE_MARKER) > 1e-3:
                # Unknown marker — bail out rather than misinterpret bytes.
                break
            zone = _read_zone_header(f, endian, version)
            if zone is None:
                break
            zones.append(zone)
            zones_seen += 1

        return {
            "sub_format": sub_format,
            "byte_order": byte_order,
            "file_type": file_type,
            "title": title,
            "n_variables": n_variables,
            "variables": variables,
            "n_zones_seen": zones_seen,
            "zones": zones,
        }


def _read_tecplot_string(f, endian: str) -> str:
    """Read a null-terminated Tecplot string (sequence of i32 chars, 0-terminated)."""
    chars: list[int] = []
    while True:
        c = f.read(4)
        if len(c) < 4:
            break
        n = struct.unpack(f"{endian}i", c)[0]
        if n == 0:
            break
        chars.append(n)
        # Defense: cap absurd lengths
        if len(chars) > 1024:
            break
    try:
        return "".join(chr(n) for n in chars if 0 < n < 0x110000)
    except Exception:
        return ""


def _read_zone_header(f, endian: str, version: int) -> dict | None:
    """Read one zone header. Returns minimal info or None on truncation.

    Common fields across versions:
        zone name (string)
        zone type (i32)         — varies by version, semantic differs
        ... lots of optional fields ...
    We extract what we can and stop at the first sign of trouble.
    """
    try:
        name = _read_tecplot_string(f, endian)
        # Parent zone (v107+) — skip 4 bytes
        if version >= 107:
            f.read(4)
        # Strand id (v107+)
        if version >= 107:
            f.read(4)
        # Solution time (double, v107+)
        if version >= 107:
            f.read(8)
        # Default Zone color (v107+)
        if version >= 107:
            f.read(4)
        # Zone type: 0=ORDERED, 1=FELINESEG, 2=FETRIANGLE, 3=FEQUADRILATERAL,
        # 4=FETETRAHEDRON, 5=FEBRICK, 6=FEPOLYGON, 7=FEPOLYHEDRON
        zt_b = f.read(4)
        if len(zt_b) < 4:
            return None
        zone_type = struct.unpack(f"{endian}i", zt_b)[0]

        # Data packing (block / point)
        # Specify var location (1 if any vars are cell-centered)
        # Are raw local 1-to-1 face neighbors supplied?
        # Number of miscellaneous user-defined face neighbor connections
        # ... lots of vars; format depends precisely on version. We bail
        # here and just return zone name + type — that's enough to give
        # the user a sense of "ZONE 1: FETETRAHEDRON" even if we can't
        # decode the rest.

        return {
            "name": name,
            "zone_type_id": zone_type,
            "zone_type_name": _ZONE_TYPE_NAMES.get(zone_type, f"unknown_{zone_type}"),
        }
    except Exception:
        return None


_ZONE_TYPE_NAMES = {
    0: "ORDERED",
    1: "FELINESEG",
    2: "FETRIANGLE",
    3: "FEQUADRILATERAL",
    4: "FETETRAHEDRON",
    5: "FEBRICK",
    6: "FEPOLYGON",
    7: "FEPOLYHEDRON",
}


def parse_ascii_header(path: Path) -> dict:
    """Read the first ~16 KB of an ASCII Tecplot file and extract title +
    variable list + zone names. ASCII format is line-oriented and tolerant
    so we don't need a full parser.
    """
    out = {
        "sub_format": "ascii",
        "title": None,
        "n_variables": 0,
        "variables": [],
        "n_zones_seen": 0,
        "zones": [],
    }
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(16384)
    except OSError:
        return out

    # Find TITLE / VARIABLES / ZONE — these can be anywhere in the head.
    upper = head.upper()
    # Title
    for kw in ("TITLE", "TITLE ="):
        idx = upper.find(kw)
        if idx >= 0:
            line = head[idx:].split("\n", 1)[0]
            # Title value typically in quotes
            quoted = _extract_quoted(line)
            out["title"] = quoted or line.split("=", 1)[-1].strip()
            break

    # Variables: VARIABLES = "x" "y" "z" "p" ...
    idx = upper.find("VARIABLES")
    if idx >= 0:
        # Read until next ZONE / DATASETAUXDATA / etc., or 1 KB
        end = idx
        for stop_kw in ("ZONE", "DATASETAUXDATA", "TEXT", "GEOMETRY"):
            j = upper.find(stop_kw, idx + 1)
            if j > 0:
                end = max(end, j)
        if end <= idx:
            end = min(idx + 1024, len(head))
        section = head[idx:end]
        var_names = _extract_quoted_all(section)
        out["variables"] = var_names
        out["n_variables"] = len(var_names)

    # Zones — count ZONE keyword occurrences and grab their T= name when present
    zones: list[dict] = []
    j = 0
    while True:
        j = upper.find("ZONE", j)
        if j < 0:
            break
        line = head[j:].split("\n", 1)[0]
        zones.append({"name": _extract_quoted(line) or "(unnamed)"})
        j += 4
    out["zones"] = zones
    out["n_zones_seen"] = len(zones)
    return out


def _extract_quoted(line: str) -> str | None:
    """Pick out the FIRST double-quoted substring on a line."""
    if '"' in line:
        try:
            _, rest = line.split('"', 1)
            value, _ = rest.split('"', 1)
            return value
        except ValueError:
            return None
    return None


def _extract_quoted_all(text: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(text):
        i = text.find('"', i)
        if i < 0:
            break
        j = text.find('"', i + 1)
        if j < 0:
            break
        out.append(text[i + 1: j])
        i = j + 1
    return out
