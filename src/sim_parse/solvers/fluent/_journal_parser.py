"""Fluent TUI journal (.jou) parser — text-only extraction of case setup
that vtkFLUENTReader can't expose from the binary .cas.

Focused on 5 high-value command families (covers ~80% of TUI-driven cases):
    1. /file/read{,-case,-data}        → case lineage (which file produced this)
    2. /define/models/viscous/<model>  → turbulence model
    3. /define/models/energy?          → energy equation toggle
    4. /define/boundary-conditions/set/<type> <zone>() <param> no <value> q
                                       → BC values per zone
    5. /solve iterate <N>              → target iteration count
    6. /file/export <fmt> <out> <fields...> quit  → exported field selection

Anything we don't recognize is skipped silently with a counter so callers
can see "we parsed N commands, recognized M of them" if they want.

Output is a flat dict — caller (fluent/tier3_metadata.py) decides how to
fold each piece into its existing fields (physics_setup, bc, etc).

This file deliberately does NOT depend on VTK — it's pure text parsing
and runs even when vtkFLUENTReader fails on the binary .cas.
"""
from __future__ import annotations

import re
from pathlib import Path

from sim_parse.core.errors import never_raise


# ─── Command pattern regexes ──────────────────────────────────────────────────
#
# Fluent TUI grammar in a nutshell:
#   - `/<path>/<cmd> <args...>` (leading slash means absolute path)
#   - some args end with `q` (quit out of the parameter prompt)
#   - blank lines and `;` comments are ignored
#   - case-insensitive in practice but most users write lowercase

# Fluent TUI separates menu nodes with EITHER `/` OR whitespace (after
# navigating into a menu, sub-commands can use space). `[/\s]+` matches
# both styles. e.g. `/file/read foo.cas`, `/file read foo.cas`,
# `file/read foo.cas` all valid.
_SEP = r"[/\s]+"

# /file/read steadyhb2.cas  OR  /file read foo.cas
_FILE_READ_RE = re.compile(
    rf"^\s*/?file{_SEP}(?:read|read-case|rc)\s+(\S+)",
    re.IGNORECASE,
)
# /file/write-case foo.cas / /file/write-data foo.dat / /file/write-case-dat foo
_FILE_WRITE_RE = re.compile(
    rf"^\s*/?file{_SEP}(?:write-case-dat|write-case|write-data|wcd|wc|wd)\s+(\S+)",
    re.IGNORECASE,
)

# /define/models/viscous/<model>
# Common model paths: laminar, ke-standard, ke-realizable, ke-rng,
#                     kw-standard, kw-sst, kw-bsl, spalart-allmaras,
#                     reynolds-stress-model, les-smagorinsky, des, sas
_VISCOUS_MODEL_RE = re.compile(
    rf"^\s*/?define{_SEP}models{_SEP}viscous{_SEP}([\w-]+)",
    re.IGNORECASE,
)

# /define/models/energy? yes/no  (toggle). The `?` is part of the command name.
_ENERGY_RE = re.compile(
    rf"^\s*/?define{_SEP}models{_SEP}energy\??\s+(yes|no|y|n|#t|#f)",
    re.IGNORECASE,
)

# /define/boundary-conditions/set/<bc-type> <zone>() <param> no <value> q
# `no` here is the "is profile?" answer (no = constant), then comes the value.
# Example: /define/boundary-conditions/set/pressure-far-field tria-1-inlet() mach number no 3 q
_BC_SET_RE = re.compile(
    rf"""^\s*/?define{_SEP}boundary-conditions{_SEP}set{_SEP}(?P<bc_type>[\w-]+)\s+
        (?P<zone>[\w\-.]+)\s*\(\)\s*           # zone name + ()
        (?P<param>[\w-]+(?:\s+[\w-]+)*?)\s+    # parameter name (1+ tokens)
        (?:no|n)\s+                             # "constant value" answer
        (?P<value>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)  # numeric value
        (?:\s+q)?                               # optional q to exit prompt
    """,
    re.IGNORECASE | re.VERBOSE,
)

# /solve iterate <N>  /solve/iterate <N>  /solve/dual-time-iterate <N>
_SOLVE_ITER_RE = re.compile(
    rf"^\s*/?solve{_SEP}(?:iterate|dual-time-iterate|it)\s+(\d+)",
    re.IGNORECASE,
)

# /file/export <fmt> <output> <field1> <field2> ... quit
# Variable list ends with the literal token `quit`. Format is one of:
#   cgns, ensight, fieldview, tecplot, ascii, ...
_EXPORT_RE = re.compile(
    rf"^\s*/?file{_SEP}export\s+(?P<fmt>[\w-]+)\s+(?P<out>\S+)\s+(?P<rest>.+?)\s+quit\b",
    re.IGNORECASE,
)


# ─── Main entry ───────────────────────────────────────────────────────────────


@never_raise(default=None)
def parse_fluent_journals(case_dir: Path,
                           journal_filenames: list[str]) -> dict | None:
    """Parse all *.jou files in case_dir; merge findings into one dict.

    Args:
        case_dir: directory containing the .jou files (Path).
        journal_filenames: list of bare filenames from Tier 2 inventory
            (e.g. ['runfluent.jou', 'init.jou']). Order matters — files
            are processed in the order given so case_lineage is correct.

    Returns:
        Merged dict (see schema in module docstring) or None if no .jou
        could be parsed. Empty result (no recognized commands) returns None.
    """
    if not journal_filenames:
        return None

    merged: dict = {
        "boundary_conditions": [],
        "case_lineage_reads": [],
        "case_lineage_writes": [],
        "exports": [],
        "_files_parsed": [],
        "_total_commands_recognized": 0,
        "_total_lines": 0,
    }

    for fname in journal_filenames:
        jou_path = case_dir / fname
        if not jou_path.is_file():
            continue
        per_file = _parse_one_journal(jou_path)
        if per_file is None:
            continue
        _merge_into(merged, per_file, fname)

    # Drop the helper lists that ended up empty so the output is clean
    if not merged["boundary_conditions"]:
        del merged["boundary_conditions"]
    if not merged["exports"]:
        del merged["exports"]

    # Build the unified case_lineage from reads + writes
    lineage = merged.pop("case_lineage_reads") + merged.pop("case_lineage_writes")
    if lineage:
        # De-dupe preserving order
        seen = set()
        merged["case_lineage"] = [x for x in lineage if not (x in seen or seen.add(x))]

    # If nothing was extracted besides bookkeeping fields, return None
    payload_keys = set(merged) - {"_files_parsed",
                                   "_total_commands_recognized",
                                   "_total_lines"}
    if not payload_keys:
        return None

    return merged


@never_raise(default=None)
def _parse_one_journal(jou_path: Path) -> dict | None:
    """Parse a single .jou file. Returns dict of recognized commands."""
    try:
        text = jou_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    out: dict = {
        "boundary_conditions": [],
        "case_lineage_reads": [],
        "case_lineage_writes": [],
        "exports": [],
        "_lines_total": 0,
        "_lines_recognized": 0,
    }

    for line_no, raw in enumerate(text.splitlines(), start=1):
        out["_lines_total"] += 1
        line = raw.strip()
        # Skip blank + comments
        if not line or line.startswith(";"):
            continue

        if _try_match_viscous(line, line_no, out):
            continue
        if _try_match_energy(line, line_no, out):
            continue
        if _try_match_bc(line, line_no, out):
            continue
        if _try_match_solve(line, line_no, out):
            continue
        if _try_match_export(line, line_no, out):
            continue
        if _try_match_file_read(line, line_no, out):
            continue
        if _try_match_file_write(line, line_no, out):
            continue
        # Unrecognized — silent. Counter via _lines_total - _lines_recognized.

    return out


# ─── Per-pattern matchers ─────────────────────────────────────────────────────


def _try_match_viscous(line: str, line_no: int, out: dict) -> bool:
    m = _VISCOUS_MODEL_RE.match(line)
    if not m:
        return False
    model = m.group(1).lower()
    # Filter out commands that look like /viscous but are actually
    # parameter setters (e.g. /define/models/viscous/turb-coeffs-default).
    # We accept only the well-known model identifiers.
    if model not in _KNOWN_VISCOUS_MODELS:
        return False
    out["viscous_model"] = {
        "model": model,
        "_source_line": line_no,
    }
    out["_lines_recognized"] += 1
    return True


def _try_match_energy(line: str, line_no: int, out: dict) -> bool:
    m = _ENERGY_RE.match(line)
    if not m:
        return False
    answer = m.group(1).lower()
    enabled = answer in ("yes", "y", "#t")
    out["energy_enabled"] = enabled
    out["_lines_recognized"] += 1
    return True


def _try_match_bc(line: str, line_no: int, out: dict) -> bool:
    m = _BC_SET_RE.match(line)
    if not m:
        return False
    try:
        value = float(m.group("value"))
    except ValueError:
        return False
    out["boundary_conditions"].append({
        "zone": m.group("zone"),
        "type": m.group("bc_type").lower(),
        "param": m.group("param").strip().lower(),
        "value": value,
        "_source_line": line_no,
    })
    out["_lines_recognized"] += 1
    return True


def _try_match_solve(line: str, line_no: int, out: dict) -> bool:
    m = _SOLVE_ITER_RE.match(line)
    if not m:
        return False
    iters = int(m.group(1))
    # Accumulate — multiple /solve iterate commands sum up
    out["solve_settings"] = out.get("solve_settings", {"iterations_target": 0})
    out["solve_settings"]["iterations_target"] += iters
    out["solve_settings"]["_source_line"] = line_no
    out["_lines_recognized"] += 1
    return True


def _try_match_export(line: str, line_no: int, out: dict) -> bool:
    m = _EXPORT_RE.match(line)
    if not m:
        return False
    fields_text = m.group("rest")
    # Tokens are space-separated field names; some Fluent CGNS exports use
    # `no` as a "include solid zones?" answer that gets mixed in. Filter
    # tokens that look like answer keywords (yes/no/y/n) when standalone.
    tokens = [t for t in fields_text.split()
              if t.lower() not in ("yes", "no", "y", "n")]
    out["exports"].append({
        "format": m.group("fmt").lower(),
        "output_file": m.group("out"),
        "fields": tokens,
        "_source_line": line_no,
    })
    out["_lines_recognized"] += 1
    return True


def _try_match_file_read(line: str, line_no: int, out: dict) -> bool:
    m = _FILE_READ_RE.match(line)
    if not m:
        return False
    out["case_lineage_reads"].append(m.group(1))
    out["_lines_recognized"] += 1
    return True


def _try_match_file_write(line: str, line_no: int, out: dict) -> bool:
    m = _FILE_WRITE_RE.match(line)
    if not m:
        return False
    out["case_lineage_writes"].append(m.group(1))
    out["_lines_recognized"] += 1
    return True


# ─── Merge logic ──────────────────────────────────────────────────────────────


def _merge_into(merged: dict, per_file: dict, fname: str) -> None:
    """Merge a per-file parse result into the cumulative merged dict.

    Strategy:
      - Last-write-wins for scalar fields (viscous_model, energy_enabled,
        solve_settings) — assumes journals run in order, later state
        overrides earlier.
      - Append for list fields (boundary_conditions, exports, lineage).
    """
    merged["_files_parsed"].append(fname)
    merged["_total_lines"] += per_file.pop("_lines_total", 0)
    merged["_total_commands_recognized"] += per_file.pop("_lines_recognized", 0)

    for key in ("viscous_model", "energy_enabled", "solve_settings"):
        if key in per_file:
            merged[key] = per_file[key]
            # Stamp source file alongside source line
            if isinstance(merged[key], dict):
                merged[key]["_source_file"] = fname

    for key in ("boundary_conditions", "exports",
                "case_lineage_reads", "case_lineage_writes"):
        items = per_file.get(key) or []
        for item in items:
            if isinstance(item, dict):
                item["_source_file"] = fname
            merged[key].append(item)


# ─── Known viscous model identifiers (whitelist for matcher) ──────────────────

_KNOWN_VISCOUS_MODELS = {
    "laminar",
    # k-epsilon family
    "ke-standard", "ke-realizable", "ke-rng", "kepsilon",
    # k-omega family
    "kw-standard", "kw-sst", "kw-bsl", "komega",
    # Spalart-Allmaras
    "spalart-allmaras", "sa",
    # Reynolds Stress Model
    "reynolds-stress-model", "rsm",
    # LES family
    "les", "les-smagorinsky", "les-wale", "les-dynamic-smagorinsky",
    # Hybrid RANS-LES
    "des", "sas", "iddes", "ddes",
    # Transition models
    "transition-sst", "transition-k-kl-w",
    # Inviscid
    "inviscid",
}
