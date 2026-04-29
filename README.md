# sim-parse

Universal CAE simulation result parser. Per-case static extraction with tiered output.

```python
from sim_parse import parse_case

result = parse_case("/data/case_run023", target_tier=4)
# {
#   "tier_1_identify": {"format": "openfoam", "solver": "reactingFoam", ...},
#   "tier_2_inventory": {"time_steps": [0, 500, ...], "variables": [...], ...},
#   "tier_3_metadata":  {"mesh_cells": 3466, "turbulence_model": "kEpsilon", ...},
#   "tier_4_field_stats": {"variable_ranges": {"T": {"min": 292, "max": 2350}}, ...},
# }
```

## CLI

```bash
simparse <case_dir>                          # default: tier 1+2+3 JSON
simparse <case_dir> --tier 4 --json
simparse <case_dir> --strict                 # only Path A, no fallback
simparse <case_dir> --discovery=off          # 涉密环境模式
```

## Architecture

See [docs/design.md](docs/design.md). Three-axis core architecture:

| Action | Knowledge base | Component |
|---|---|---|
| 怎么跑 (run) | sim-skills/ | sim-cli driver Agent |
| 怎么读 (extract) | sim-knowledge/formats/ | Discovery Agent (in sim-parse Path C) |
| 怎么判 (judge) | sim-knowledge/physics/ | Query Agent |

sim-parse is the **per-case extraction framework** — a code framework, not a knowledge base.

## Documentation

- [design.md](docs/design.md) — High-level architecture
- [extraction_reference.md](docs/extraction_reference.md) — Per-solver extraction reference (the "读" axis)
- [criteria_reference.md](docs/criteria_reference.md) — Physics judgment criteria reference (the "判" axis)

## Status

**Phase 1 MVP** — OpenFOAM Tier 1-4 + HDF5 generic Path B + dispatcher + CLI.

## Install

```bash
pip install -e .[fields]    # core + VTK
pip install -e .[all]       # everything (incl. discovery / LLM client)
pip install -e .[dev]       # dev tools (pytest, ruff)
```
