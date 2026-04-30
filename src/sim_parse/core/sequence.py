"""File-sequence detection helpers (cross-solver).

Many CFD post-processing pipelines export a time series as N files of the
same format in one directory:

    case_dir/
        flow_0001.cgns
        flow_0002.cgns
        ...
        flow_0050.cgns

OR (the user's E:/AJ/DLR case):

    E:/AJ/DLR/
        0.003.cgns
        0.004.cgns
        0.005.cgns

A solver's Tier 1 should not refuse to identify these directories just
because there's more than one file. This module gives a uniform way to:

  1. Detect the sequence pattern (numeric suffix / prefix)
  2. Pick a representative (typically the last / latest file)
  3. Surface the full list + parsed time values to Tier 2 inventory

Conventions recognized:
  - Pure numeric stem:           '0.003.cgns', '0.004.cgns', ...
  - Numeric suffix:              'case_0001.cgns', 'case_0002.cgns', ...
  - Numeric infix:               'flow_iter150.cgns', 'flow_iter160.cgns', ...
  - Numeric prefix:              '5000_T.cgns', '6000_T.cgns', ...
  - Anything sortable lexicographically still works (no parsed time)
"""
from __future__ import annotations

import re
from pathlib import Path


# Regex to extract the LAST numeric token from a filename stem.
# Captures decimals (5.5e-3 / 0.003 / 1234) so most time-stamp / iter-count
# conventions land in `time_value`.
_NUMERIC_TOKEN_RE = re.compile(r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")


def detect_file_sequence(files: list[Path]) -> dict | None:
    """Return a sequence-info dict if `files` looks like a numbered series.

    Args:
        files: list of Path objects, all assumed to share the same extension
               (caller has already filtered).

    Returns:
        None if fewer than 2 files (not a sequence by definition).
        Otherwise:
            {
              "representative": Path,           # last in sorted order
              "all_files": list[Path],          # sorted
              "n_files": int,
              "time_values": list[float] | None, # parsed numeric stems if all
                                                  # files share a parsable pattern
              "pattern_description": str,
            }

    "Last in sorted order" picks `0.005.cgns` over `0.003.cgns` in the
    user's example. For zero-padded suffixes (`flow_0050.cgns`) lexicographic
    sort matches numeric sort. For non-padded numerics with mixed widths
    (`case_2.cgns` vs `case_10.cgns`) we sort BY parsed numeric value
    when one is extractable, else fall back to lex.
    """
    if not files or len(files) < 2:
        return None

    # Try to extract a numeric value from each filename's stem
    parsed: list[tuple[float | None, Path]] = []
    for f in files:
        stem = f.stem  # 'flow_0050' from 'flow_0050.cgns'
        # Some users store CGNS with nested ext: 'flow.cgns' → stem='flow'
        # but for 'flow.cgns.h5' stem = 'flow.cgns' — that's fine.
        # Find the LAST numeric token (most likely the time/iter index)
        matches = _NUMERIC_TOKEN_RE.findall(stem)
        if matches:
            try:
                v = float(matches[-1])
                parsed.append((v, f))
                continue
            except ValueError:
                pass
        parsed.append((None, f))

    # If every file had a parsable numeric, sort by that value
    all_have_numeric = all(p[0] is not None for p in parsed)
    if all_have_numeric:
        sorted_pairs = sorted(parsed, key=lambda p: p[0])
        all_sorted = [p[1] for p in sorted_pairs]
        time_values = [p[0] for p in sorted_pairs]
        pattern = "numeric_token_in_stem"
    else:
        # Mixed pattern — fall back to lex sort. Time values not exposed.
        all_sorted = sorted(files)
        time_values = None
        pattern = "lexicographic_fallback"

    return {
        "representative": all_sorted[-1],   # last (typically latest time)
        "all_files": all_sorted,
        "n_files": len(all_sorted),
        "time_values": time_values,
        "pattern_description": pattern,
    }


def collect_sequence_files(case_dir: Path, glob_patterns: list[str]) -> list[Path]:
    """Glob the directory by all given patterns, deduplicate, return sorted list.

    Used by solver Tier 1 to gather candidate files before passing to
    detect_file_sequence().
    """
    seen: set[Path] = set()
    for pat in glob_patterns:
        for f in case_dir.glob(pat):
            if f.is_file():
                seen.add(f)
    return sorted(seen)
