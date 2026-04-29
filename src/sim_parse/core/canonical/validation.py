"""Validate Tier 5 QOI records against sim-knowledge labeled_cases ground truth.

When sim-knowledge is present and a labeled case matches the parse_result's
case_root, every Tier 5 QOI variable that has a corresponding entry in
ground_truth.yaml::labels gets cross-checked. The result is attached to
the parse_result as a `validation` block so consumers can confirm that
sim-parse's output agrees with hand-curated truth — and, when it doesn't,
exactly which value disagrees and by how much.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from sim_parse.core.errors import never_raise


# Same sibling-detection logic as qoi_engine. Kept independent so the
# validation module doesn't have a hard dependency on the engine.
_SIM_KNOWLEDGE = Path(__file__).resolve().parents[4].parent / "sim-knowledge"

# Default tolerance for numeric comparison (relative). Override per-label
# in ground_truth.yaml::tolerances.<label_name>.
_DEFAULT_REL_TOLERANCE = 0.05  # 5%


@never_raise(default=None)
def find_matching_label(case_root: str | Path) -> dict | None:
    """Walk sim-knowledge/labeled_cases/ looking for a ground_truth.yaml
    whose `case_path` matches `case_root` (exact match after path
    normalization).

    Returns the parsed ground_truth dict, with `_source_file` added.
    Returns None if sim-knowledge isn't installed, the directory is missing,
    or no match is found.
    """
    if not _SIM_KNOWLEDGE.is_dir():
        return None
    labeled_dir = _SIM_KNOWLEDGE / "labeled_cases"
    if not labeled_dir.is_dir():
        return None

    target = Path(case_root).resolve()
    target_norm = _normalize_path(str(target))

    for gt_yaml in labeled_dir.rglob("ground_truth.yaml"):
        try:
            with open(gt_yaml, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        case_path = doc.get("case_path")
        if not case_path:
            continue
        if _normalize_path(str(case_path)) == target_norm:
            doc["_source_file"] = str(gt_yaml)
            return doc

    return None


def _normalize_path(p: str) -> str:
    """Normalize Windows / Unix-style paths for comparison."""
    return str(Path(p)).replace("\\", "/").rstrip("/").lower()


@never_raise(default=None)
def validate_against_labels(parse_result: dict) -> dict | None:
    """Compare every Tier 5 QOI in parse_result against the matching
    labeled_cases ground truth.

    Returns:
      None — if no matching labeled case found.
      dict — otherwise, with structure:
        {
          "matched_label_file": str,
          "case_id": str,
          "results": [
            {
              "label": "<label_name>",
              "expected": <value from ground_truth.labels>,
              "actual": <value from tier_5_qoi>,
              "status": "match" | "mismatch" | "missing_in_qoi"
                        | "missing_in_labels" | "skipped",
              "tolerance_used": float | None,    # for numeric only
              "difference": float | None,        # for numeric only
            },
          ],
          "summary": {
            "n_match": int,
            "n_mismatch": int,
            "n_skipped": int,
            "n_missing_in_qoi": int,
          },
        }
    """
    case_root = parse_result.get("case_root")
    if not case_root:
        return None
    label_doc = find_matching_label(case_root)
    if label_doc is None:
        return None

    expected_labels = label_doc.get("labels") or {}
    if not isinstance(expected_labels, dict):
        return None

    tolerances = label_doc.get("tolerances") or {}
    qoi_records = parse_result.get("tier_5_qoi") or []
    qoi_by_name = {
        rec.get("variable"): rec for rec in qoi_records
        if isinstance(rec, dict) and rec.get("variable")
    }

    results: list[dict] = []
    n_match = n_mismatch = n_skipped = n_missing = 0

    for label_name, expected in expected_labels.items():
        rec = qoi_by_name.get(label_name)
        if rec is None:
            results.append({
                "label": label_name,
                "expected": expected,
                "actual": None,
                "status": "missing_in_qoi",
                "tolerance_used": None,
                "difference": None,
            })
            n_missing += 1
            continue

        # Skipped rules don't have a value to compare.
        if rec.get("value") is None and rec.get("status", "").startswith("skipped"):
            results.append({
                "label": label_name,
                "expected": expected,
                "actual": None,
                "status": "skipped",
                "tolerance_used": None,
                "difference": None,
                "skip_reason": rec.get("reason"),
            })
            n_skipped += 1
            continue

        actual = rec.get("value")
        tol = tolerances.get(label_name) if isinstance(tolerances, dict) else None
        match, diff, used_tol = _compare(expected, actual, tol)
        results.append({
            "label": label_name,
            "expected": expected,
            "actual": actual,
            "status": "match" if match else "mismatch",
            "tolerance_used": used_tol,
            "difference": diff,
        })
        if match:
            n_match += 1
        else:
            n_mismatch += 1

    return {
        "matched_label_file": label_doc.get("_source_file"),
        "case_id": label_doc.get("case_id"),
        "results": results,
        "summary": {
            "n_match": n_match,
            "n_mismatch": n_mismatch,
            "n_skipped": n_skipped,
            "n_missing_in_qoi": n_missing,
            "n_total": len(results),
        },
    }


def _compare(expected, actual, tolerance) -> tuple[bool, float | None, float | None]:
    """Return (match, difference, tolerance_used).

    Booleans / strings: equality check; difference is None.
    Numerics: relative-tolerance check; difference is the absolute diff.
    Lists: order-insensitive equality (treated as sets).
    Anything else: equality.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        # bool must come before int because bool is a subclass of int
        return (bool(expected) == bool(actual), None, None)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        rel_tol = tolerance if isinstance(tolerance, (int, float)) else _DEFAULT_REL_TOLERANCE
        if expected == 0:
            match = abs(actual - expected) < rel_tol
        else:
            match = abs(actual - expected) / max(abs(expected), 1e-30) <= rel_tol
        return (match, float(actual - expected), float(rel_tol))
    if isinstance(expected, list) and isinstance(actual, list):
        return (sorted(expected) == sorted(actual), None, None)
    return (expected == actual, None, None)
