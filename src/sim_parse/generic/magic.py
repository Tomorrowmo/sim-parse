"""Magic-byte / file-signature detection.

Used by Path B when Path A solver-specific identifiers all miss.
Detects HDF5 / Tecplot / VTK XML / EnSight / gzip / etc.

Two-tier strategy:
    1. Magic bytes (first 8-16 bytes) — high confidence when matched.
    2. Extension hint + CONTENT sniff — when magic doesn't match, peek
       at the first ~512 bytes of ASCII content to disambiguate look-alike
       extensions (e.g. `.dat` could be Tecplot ASCII, Fluent rfile CSV,
       CFD++ force log, or random text).

The previous version mapped `.dat → fluent_or_tecplot` blindly via ext alone,
which gave consumers a useless union-typed format string. Now the hint is
treated as a HYPOTHESIS that must be confirmed by content; otherwise we
return None and let the cascade declare the case unidentified.
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise

# Format signatures: (offset, magic bytes, format name).
# These are the high-confidence content matches — when present, they win
# over any extension hint (e.g. a `.bin` file with `#!TDV` magic IS Tecplot
# binary regardless of what the extension claims).
SIGNATURES = [
    (0, b"\x89HDF\r\n\x1a\n", "hdf5"),
    (0, b"\x1f\x8b", "gzip"),
    (0, b"PK\x03\x04", "zip"),
    (0, b"#!TDV", "tecplot_binary"),
    (0, b"<?xml", "xml_based"),
    (0, b"# vtk DataFile", "vtk_legacy"),
]

# Top-level extensions → high-confidence formats (no content check needed).
# These are extensions that, IF present, almost always mean the format
# claimed: HDF5/CGNS/VTU/.sim are unambiguous. `.plt` lives here too —
# Tecplot binary is so dominant in `.plt` that we accept it on extension
# alone (and the content sniff would catch any ASCII variants anyway).
EXT_HINTS_AUTHORITATIVE = {
    ".h5": "hdf5",
    ".hdf5": "hdf5",
    ".cgns": "cgns",
    ".vtu": "vtu",
    ".vtm": "vtm",
    ".pvtu": "pvtu",
    ".plt": "tecplot",        # binary or ASCII; the solver decides
    ".cas": "fluent",
    ".odb": "abaqus_odb",
    ".sim": "starccm_sim",
    ".inp": "abaqus_inp",
    ".rst": "ansys_rst",
    ".case": "ensight_gold",
    ".k": "lsdyna_keyword",
}

# Ambiguous extensions — must be content-sniffed to commit to a format.
# Returning None for these (instead of a junk union string) lets the
# cascade properly report "unidentified" rather than mislead consumers.
EXT_HINTS_AMBIGUOUS = {
    ".dat",   # could be Tecplot ASCII / Fluent rfile / CFD++ force log / random
    ".bin",   # could be Tecplot binary / CFD++ binary / random binary
    ".txt",
    ".out",
}


# Multi-file directory leading-file patterns: when a directory has a
# top-level file matching one of these globs, we treat that file as the
# "case entry point" — even if other files coexist. Order matters:
# more-specific first.
DIR_LEADING_FILE_PATTERNS: list[tuple[str, str]] = [
    # (glob, hint format) — the corresponding file's content is then sniffed
    # for confirmation.
    ("*.case",  "ensight_gold"),
    ("*.cas",   "fluent"),
    ("*.cas.h5", "fluent_cff"),
    ("*.cgns",  "cgns"),
    ("*.sim",   "starccm_sim"),
]


@never_raise(default=None)
def identify_by_magic(case_root: Path) -> dict | None:
    """Identify a case_root via content + extension heuristics.

    Three modes:
        1. case_root is a file → sniff magic bytes + extension + content.
        2. case_root is a single-file directory → forward to (1) on that file.
        3. case_root is a multi-file directory → look for a leading file
           matching DIR_LEADING_FILE_PATTERNS; if exactly one is found,
           identify by it.

    Returns:
        {"format": <name>, "file_path": <str>, "_path": "B", ...} or None.
    """
    if case_root.is_dir():
        files = [p for p in case_root.iterdir() if p.is_file()]
        if len(files) == 1:
            return _identify_file(files[0])
        if len(files) > 1:
            return _identify_dir_by_leading_file(case_root)
        return None

    if case_root.is_file():
        return _identify_file(case_root)

    return None


def _identify_dir_by_leading_file(case_root: Path) -> dict | None:
    """Scan top-level for a single 'leading file' that names the format.

    Common scenario: an EnSight Gold case folder has `bin.case` plus 12
    binary field files; the `.case` file is the entry point. We pick it
    out, identify by it, but return the DIRECTORY as case_root so
    downstream tiers can still see all sibling files.
    """
    matched: list[tuple[Path, str]] = []
    for glob, fmt_hint in DIR_LEADING_FILE_PATTERNS:
        for hit in case_root.glob(glob):
            if hit.is_file():
                matched.append((hit, fmt_hint))

    if not matched:
        return None
    if len(matched) > 1:
        # Multiple leading-file candidates — too ambiguous; let cascade fall
        # through to "unidentified" so the caller has to disambiguate.
        return None

    leading, fmt_hint = matched[0]
    # Confirm by content sniff (cheap defense against extension-only matches)
    confirmed = _content_check_format(leading, fmt_hint)
    if not confirmed:
        return None

    return {
        "format": confirmed,
        "file_path": str(leading),
        "case_dir": str(case_root),
        "file_size": leading.stat().st_size,
        "_path": "B",
        "_provenance": {"format": {
            "path": "B",
            "source": f"directory leading-file scan: matched {leading.name} → {confirmed}",
            "confidence": "HIGH",
        }},
        "_errors": [],
        "_warnings": [],
    }


def _identify_file(path: Path) -> dict | None:
    """Identify a single file by magic bytes + extension hint + content sniff."""
    ext = path.suffix.lower()

    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None

    # 1. Magic bytes — wins outright when matched (binary signatures are
    #    high-entropy patterns unlikely to occur by accident).
    magic_match: str | None = None
    for offset, sig, name in SIGNATURES:
        if head[offset:offset + len(sig)] == sig:
            magic_match = name
            break

    if magic_match:
        fmt = magic_match
        # HDF5 sub-disambiguation: could be CGNS / Fluent CFF / h5part / ...
        if fmt == "hdf5":
            sub = _disambiguate_hdf5(path)
            if sub:
                fmt = sub
        # Tecplot binary detected via magic — promote 'tecplot_binary' to
        # the unified 'tecplot' format name so the registered solver picks it up.
        if fmt == "tecplot_binary":
            fmt = "tecplot"
        return _build_identity(path, fmt, source="magic_byte", confidence="HIGH")

    # 2. Authoritative extension hints — confidence MED (no magic but the
    #    extension is reliable in practice).
    auth_hint = EXT_HINTS_AUTHORITATIVE.get(ext)
    if auth_hint:
        # Final content check for ASCII-only formats whose extension
        # might be misused (e.g. .case shadowed by a non-EnSight .case)
        confirmed = _content_check_format(path, auth_hint)
        if confirmed:
            return _build_identity(path, confirmed, source=f"ext={ext}+content", confidence="MED")
        # Authoritative hint failed content check — refuse rather than mislead.
        return None

    # 3. Ambiguous extension — content must commit.
    if ext in EXT_HINTS_AMBIGUOUS:
        sniffed = _sniff_ambiguous_text_file(path)
        if sniffed:
            return _build_identity(path, sniffed, source=f"ext={ext}+content_sniff", confidence="MED")
        return None

    return None


def _build_identity(path: Path, fmt: str, *, source: str, confidence: str) -> dict:
    return {
        "format": fmt,
        "file_path": str(path),
        "file_size": path.stat().st_size,
        "_path": "B",
        "_provenance": {"format": {"path": "B", "source": source, "confidence": confidence}},
        "_errors": [],
        "_warnings": [],
    }


def _content_check_format(path: Path, expected: str) -> str | None:
    """Read up to 512 bytes and verify the file content matches `expected`.

    Returns the (possibly refined) format name on confirmation, else None.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError:
        return None

    text = head.decode("utf-8", errors="replace")

    if expected == "ensight_gold":
        # EnSight .case files are ASCII with FORMAT/type/MODEL sections.
        if "FORMAT" in text and "ensight" in text.lower():
            return "ensight_gold"
        # Lenient: presence of 'GEOMETRY' + 'model:' is also a strong hint.
        if "GEOMETRY" in text and "model:" in text:
            return "ensight_gold"
        return None

    if expected == "tecplot":
        if head.startswith(b"#!TDV"):
            return "tecplot"
        # ASCII Tecplot: TITLE / VARIABLES / ZONE keywords near top
        upper = text.upper()
        if "TITLE" in upper and "VARIABLES" in upper:
            return "tecplot"
        if "ZONE" in upper and "VARIABLES" in upper:
            return "tecplot"
        return None

    if expected == "fluent":
        # Fluent legacy .cas — first non-comment line is `(0 "...")` header
        # or starts with `(1 "..."` (header section). Be lenient.
        if text.startswith("(") or "FLUENT" in text.upper():
            return "fluent"
        return None

    if expected == "cgns":
        # CGNS over ADF, not HDF5 — has 'CGNS' in early bytes
        if b"CGNS" in head:
            return "cgns"
        # If head reads HDF5 magic, this is hdf5-CGNS; let _disambiguate_hdf5 handle.
        return None

    # Default: trust the hint.
    return expected


def _sniff_ambiguous_text_file(path: Path) -> str | None:
    """Open `.dat` / `.bin` / `.txt` / `.out` and decide what it is by content.

    Returns:
        - 'tecplot' if Tecplot ASCII or binary detected
        - 'fluent_report' if a Fluent rfile-style CSV header is detected
        - 'cfd_force_log' if CFD++ force-log key-value layout is detected
        - None if we can't commit to anything
    """
    try:
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError:
        return None

    # Tecplot binary in disguise (.bin extension)
    if head.startswith(b"#!TDV"):
        return "tecplot"

    text = head.decode("utf-8", errors="replace")

    # Fluent rfile CSV: first line is a comma-separated header that includes
    # 'Iteration' / 'Time' / 'Cx' / 'Cl' / 'Cd' or similar coefficients.
    first_line = text.split("\n", 1)[0].strip()
    if "," in first_line and len(first_line.split(",")) >= 3:
        head_tokens = [t.strip() for t in first_line.split(",")]
        coef_words = {"iteration", "time", "cx", "cy", "cz",
                      "cl", "cd", "lift", "drag", "moment",
                      "mx", "my", "mz", "force"}
        if any(t.lower() in coef_words for t in head_tokens):
            return "fluent_report"

    # Tecplot ASCII: TITLE / VARIABLES / ZONE keywords
    upper = text.upper()
    if "TITLE" in upper and "VARIABLES" in upper:
        return "tecplot"
    if "ZONE" in upper and "VARIABLES" in upper:
        return "tecplot"

    # CFD++ force log: short ASCII lines like "CA 0.05" / "Cl -1.26e-7"
    # Count lines that look like "<NAME> <number>"
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    kv_lines = 0
    for ln in lines[:10]:
        parts = ln.split()
        if len(parts) >= 2:
            try:
                float(parts[1])
                # token 1 looks like a label (alphabetic-ish)
                if any(c.isalpha() for c in parts[0]):
                    kv_lines += 1
            except ValueError:
                continue
    if kv_lines >= 3:
        return "cfd_force_log"

    return None


def _disambiguate_hdf5(path: Path) -> str | None:
    """For HDF5 files, peek at top-level groups to identify CGNS / Fluent CFF / etc."""
    try:
        import h5py  # lazy import — h5py is optional
    except ImportError:
        return None

    try:
        with h5py.File(path, "r") as f:
            keys = list(f.keys())
            attrs = dict(f.attrs)
    except Exception:
        return None

    # CGNS: top-level group like "CGNSBase_*"
    if any(k.startswith("CGNSBase") for k in keys):
        return "cgns"

    # Fluent CFF: root attr "Solver" == "Fluent"
    if attrs.get("Solver") == b"Fluent" or attrs.get("Solver") == "Fluent":
        return "fluent_cff"

    # h5part: per-step groups
    if any(k.startswith("Step#") for k in keys):
        return "h5part"

    return "hdf5"  # generic
