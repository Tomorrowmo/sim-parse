"""EnSight Gold Tier 1 — Identify."""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)


_ENSIGHT_HEADER_KEYWORDS = ("FORMAT", "GEOMETRY", "model:")


@never_raise(default=None)
def identify(case_root: Path) -> dict | None:
    case_root = Path(case_root)

    # File input: must end in .case and have FORMAT+GEOMETRY structure
    if case_root.is_file():
        if case_root.suffix.lower() != ".case":
            return None
        if not _looks_like_ensight_case_file(case_root):
            return None
        return _build_identity(case_path=case_root, case_root_kind="file",
                               case_dir=case_root.parent)

    # Dir input: find a single *.case file at top level
    if case_root.is_dir():
        case_files = sorted(case_root.glob("*.case"))
        # Filter to those that actually look like EnSight (avoid e.g. some
        # *.case file that happens to end in .case but is unrelated).
        case_files = [c for c in case_files if _looks_like_ensight_case_file(c)]
        if not case_files:
            return None
        if len(case_files) > 1:
            # Multiple .case files in one dir is unusual; pick the first
            # alphabetically and warn via inventory layer rather than fail
            # here. (Tier 1's job is just to confirm the format.)
            pass
        return _build_identity(case_path=case_files[0], case_root_kind="directory",
                               case_dir=case_root)

    return None


def _looks_like_ensight_case_file(path: Path) -> bool:
    """Quick content check: read first ~512 bytes, look for the EnSight
    Gold ASCII signature. Cheap and conservative — false negatives mean
    the cascade falls through to Path B which has its own ensight_gold
    detection (so we never lose detection, just lose Path A privileges)."""
    try:
        head = path.read_bytes()[:1024]
    except OSError:
        return False
    text = head.decode("utf-8", errors="replace").upper()
    has_format = "FORMAT" in text
    has_ensight = "ENSIGHT" in text
    has_geom = "GEOMETRY" in text
    has_model = "MODEL:" in text
    # Need at least the FORMAT keyword + (ENSIGHT mention OR GEOMETRY+MODEL)
    return has_format and (has_ensight or (has_geom and has_model))


def _build_identity(*, case_path: Path, case_root_kind: str, case_dir: Path) -> dict:
    """Build the Tier 1 identity dict."""
    out = init_tier_output("A")
    set_field(out, "format", "ensight_gold",
              FieldProvenance("A", "*.case file with EnSight Gold ASCII signature"))
    set_field(out, "case_path", str(case_path),
              FieldProvenance("A", "found *.case file"))
    set_field(out, "case_dir", str(case_dir),
              FieldProvenance("A", "directory containing the *.case file"))
    set_field(out, "case_root_kind", case_root_kind,
              FieldProvenance("A", "input was directory or file"))
    # Solver / version are not stored in .case directly; defer to Tier 3.
    set_field(out, "solver", "ensight_gold",
              FieldProvenance("A", "no upstream solver named in .case file"))
    return out
