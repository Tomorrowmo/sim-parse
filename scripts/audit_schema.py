"""Schema-drift audit (one-shot diagnostic).

Runs parse_case() on a set of fixture cases (one per solver type) and
validates the Tier 1 / Tier 3 outputs against core.schema.

Goal: produce a violation list, not fix anything. Output is plain text;
redirect to docs/schema_drift_<date>.txt to keep as backlog.

Usage:
    python scripts/audit_schema.py
    python scripts/audit_schema.py > docs/schema_drift_2026-05-06.txt
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

from sim_parse import parse_case
from sim_parse.core.schema import Tier1Identify, Tier3Metadata


CASES: dict[str, str] = {
    "openfoam_DLR_combustion": "D:/Git/SimGraph2/test_data/DLR_A_combustion/DLR_A_cavity",
    "openfoam_airFoil_coldflow": "D:/Git/SimGraph2/test_data/airFoil2D/airFoil2D",
    "cgns_airfoil":               "D:/Git/SimGraph2/test_data/cgns_small/AOA10.5_mach1.2.cgns",
    "fluent_legacy_cas":          "D:/Git/SimGraph/sample_data/shared/aero/2024_08/fluent_Ma2_v2.cas",
}

TIERS = [
    ("tier_1_identify", Tier1Identify),
    ("tier_3_metadata", Tier3Metadata),
]


def audit_one(name: str, path: str) -> dict:
    print(f"\n{'='*70}")
    print(f"  {name}")
    print(f"  {path}")
    print(f"{'='*70}")

    if not Path(path).exists():
        print(f"  SKIP: path does not exist")
        return {"status": "skipped", "reason": "path_missing"}

    try:
        result = parse_case(path, target_tier=3)
    except Exception as e:
        print(f"  PARSE FAILED: {e}")
        return {"status": "parse_failed", "error": str(e)}

    # Quick top-level sanity
    fmt = (result.get("tier_1_identify") or {}).get("format")
    print(f"  detected format: {fmt!r}")
    if result.get("warnings"):
        print(f"  parse warnings: {len(result['warnings'])}")
        for w in result["warnings"][:3]:
            print(f"    - {w}")

    findings = {"violations_per_tier": {}}
    for tier_key, model in TIERS:
        tier_data = result.get(tier_key)
        if tier_data is None:
            print(f"  {tier_key}: MISSING (parse didn't produce this tier)")
            findings["violations_per_tier"][tier_key] = "missing_entirely"
            continue
        try:
            model.model_validate(tier_data)
            print(f"  {tier_key}: OK ({len(tier_data)} top-level fields)")
            findings["violations_per_tier"][tier_key] = "ok"
        except ValidationError as e:
            errs = e.errors()
            print(f"  {tier_key}: {len(errs)} schema violation(s)")
            for err in errs:
                loc = ".".join(str(x) for x in err["loc"])
                print(f"    - [{err['type']}] {loc}: {err['msg']}")
            findings["violations_per_tier"][tier_key] = [
                {"loc": ".".join(str(x) for x in e["loc"]),
                 "type": e["type"], "msg": e["msg"]}
                for e in errs
            ]

        # Surface the top-level keys actually present so we see what each
        # solver IS producing (the "extras" beyond schema).
        if isinstance(tier_data, dict):
            keys = sorted(k for k in tier_data.keys() if not k.startswith("_"))
            print(f"    actual top-level keys ({len(keys)}): {keys}")

    return findings


def main():
    print(f"sim-parse schema-drift audit")
    print(f"schema baseline: core.schema (Tier 1 + Tier 3 minimum)")

    summary: dict[str, dict] = {}
    for name, path in CASES.items():
        summary[name] = audit_one(name, path)

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    for name, findings in summary.items():
        status = findings.get("status") or "audited"
        per_tier = findings.get("violations_per_tier", {})
        line = f"  {name}: {status}"
        if per_tier:
            ok = sum(1 for v in per_tier.values() if v == "ok")
            bad = len(per_tier) - ok
            line += f"  ({ok} tier(s) OK, {bad} with violations)"
        print(line)

    # Machine-readable dump too
    out_json = Path(__file__).parent.parent / "docs" / "schema_drift.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nMachine-readable summary written to: {out_json}")


if __name__ == "__main__":
    main()
