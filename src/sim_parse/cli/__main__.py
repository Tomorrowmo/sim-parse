"""sim-parse CLI entry point.

Usage:
    python -m sim_parse <case_dir> [--tier N] [--json] [--strict] [--discovery=off]
    simparse <case_dir> ...     (after pip install)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sim_parse import parse_case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="simparse",
        description="Parse a CAE simulation case folder, output tiered structured data.",
    )
    parser.add_argument("case_root", type=Path, help="Path to the case directory")
    parser.add_argument("--tier", type=int, default=3, choices=[1, 2, 3, 4, 5, 6, 7],
                        help="Maximum tier to run (default: 3)")
    parser.add_argument("--json", action="store_true", help="Output as JSON (default: pretty)")
    parser.add_argument("--strict", action="store_true",
                        help="Only Path A; do not fall back to B/C")
    parser.add_argument("--discovery", choices=["auto", "on", "off"], default="auto",
                        help="Path C LLM Discovery mode (default: auto)")
    parser.add_argument("--force-path", choices=["A", "B"],
                        help="Force a specific path; bypass cascade")
    parser.add_argument("--force-solver", help="Skip identify; use this solver name")
    parser.add_argument("--qoi-rules", type=Path,
                        help="External Tier 5 YAML rules (Re-ingest closed loop)")

    args = parser.parse_args(argv)

    if args.strict:
        # strict implies no fallback
        force_path = "A"
        discovery = "off"
    else:
        force_path = args.force_path
        discovery = args.discovery

    result = parse_case(
        args.case_root,
        target_tier=args.tier,
        discovery=discovery,
        force_solver=args.force_solver,
        force_path=force_path,
        qoi_rules_path=args.qoi_rules,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    # Exit code: 0 if any tier_X_* output produced, else 2
    has_output = any(k.startswith("tier_") for k in result)
    return 0 if has_output else 2


if __name__ == "__main__":
    sys.exit(main())
