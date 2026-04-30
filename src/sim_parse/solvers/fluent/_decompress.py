"""Transparent .cas.gz / .dat.gz decompression for vtkFLUENTReader.

Why this module exists:
    vtkFLUENTReader (verified on VTK 9.4.1 and 9.6.1) does NOT handle gzipped
    Fluent files despite the legacy_gz sub-format being detected. Calling
    Update() on a .cas.gz triggers STATUS_STACK_BUFFER_OVERRUN on Windows
    (silent native crash, no Python exception, no stderr — the process just
    disappears). The fluent solver's old comment "vtk handles gz" was wrong.

    We work around it by gunzipping to a per-source fingerprinted cache dir
    under %TEMP%/sim_parse_decompressed/ BEFORE handing the file to
    vtkFLUENTReader. The cache reuses decompressed files across calls (keyed
    on absolute path + size + mtime) so subsequent parses of the same case
    skip the decompress step.

    vtkFLUENTReader auto-pairs <base>.cas with <base>.dat in the same
    directory, so we must decompress both into the same cache dir with
    matching basenames.

Failure modes:
    Any failure in this module is non-fatal — it returns the original .gz
    paths so vtkFLUENTReader sees what Tier 1 identified. The reader will
    then crash as before, but at least we don't add new failure modes.
"""
from __future__ import annotations

import gzip
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path


_CACHE_ROOT_NAME = "sim_parse_decompressed"


def ensure_decompressed_fluent_pair(
    cas_path: str | Path | None,
    dat_path: str | Path | None,
) -> tuple[Path | None, Path | None]:
    """Return (cas_path, dat_path) decompressed when needed; pass through otherwise.

    Behavior:
        - If cas_path is None → return (None, dat_path).
        - If cas_path is not .gz (e.g. .cas, .cas.h5) → return inputs unchanged.
        - If cas_path ends in .gz → decompress to cache dir; if dat_path also
          ends in .gz, decompress it to the SAME cache dir with a basename
          matching the cas (so vtkFLUENTReader's auto-pairing finds it).
        - On any error (disk full, permission, gzip corruption) → return the
          original paths so behavior degrades to current state, not worse.
    """
    if cas_path is None:
        return None, _to_path(dat_path)

    cas_p = Path(cas_path)
    if cas_p.suffix.lower() != ".gz":
        return cas_p, _to_path(dat_path)

    try:
        return _decompress_pair(cas_p, _to_path(dat_path))
    except Exception:
        # Never raise — let the reader try original paths.
        return cas_p, _to_path(dat_path)


def _to_path(p) -> Path | None:
    if p is None:
        return None
    return Path(p)


def _decompress_pair(cas_path: Path,
                     dat_path: Path | None) -> tuple[Path, Path | None]:
    if not cas_path.is_file():
        return cas_path, dat_path

    st = cas_path.stat()
    fingerprint = hashlib.sha256(
        f"{cas_path.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")
    ).hexdigest()[:16]

    cache_dir = Path(tempfile.gettempdir()) / _CACHE_ROOT_NAME / fingerprint
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Strip ".cas.gz" → "FLTG 1-0", then sanitize for filesystem safety.
    raw_base = _strip_cas_gz(cas_path.name)
    safe_base = _safe_basename(raw_base)

    cas_target = cache_dir / f"{safe_base}.cas"
    if not _is_complete_file(cas_target):
        _gunzip_to(cas_path, cas_target)

    dat_target: Path | None = None
    if dat_path is not None:
        if dat_path.suffix.lower() == ".gz" and dat_path.is_file():
            dat_target = cache_dir / f"{safe_base}.dat"
            if not _is_complete_file(dat_target):
                _gunzip_to(dat_path, dat_target)
        else:
            # .dat (uncompressed) or .dat.h5 — pass through. Note vtkFLUENTReader
            # won't find it auto-paired with our temp cas_target since they're
            # in different dirs, but that's a pre-existing limitation we
            # surface explicitly later if it bites.
            dat_target = dat_path

    return cas_target, dat_target


def _strip_cas_gz(name: str) -> str:
    """'FLTG 1-0.cas.gz' → 'FLTG 1-0'.   'foo.cas.gz' → 'foo'.   'foo.gz' → 'foo'."""
    lower = name.lower()
    if lower.endswith(".cas.gz"):
        return name[:-len(".cas.gz")]
    if lower.endswith(".gz"):
        return name[:-len(".gz")]
    return name


def _safe_basename(name: str) -> str:
    """Drop chars that have caused VTK reader path issues in the wild.
    Keeps [A-Za-z0-9._-]; replaces everything else (incl. spaces, CJK) with `_`."""
    out = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    # Avoid empty / dot-only names
    if not out or out.replace(".", "").replace("_", "") == "":
        out = "case"
    return out


def _is_complete_file(p: Path) -> bool:
    """Cache hit check: file exists with non-zero size."""
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _gunzip_to(src: Path, dst: Path) -> None:
    """Atomic gunzip: decompress to <dst>.tmp, then rename. Cleans up on failure."""
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        with gzip.open(src, "rb") as fsrc, open(tmp, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst, length=8 * 1024 * 1024)
        os.replace(tmp, dst)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
