"""Dispatcher: route a case through the A→B→C cascade across Tier 1-N.

This is the implementation of design.md §10. Pure deterministic logic; no LLM.
调度器
Dispatcher 干两件事：
  ① 决定走哪条 Path（A/B/C）
  ② 管理 Tier 之间的依赖（Tier 3 用 Tier 2 输出，Tier 4 用 Tier 3 输出...）

工作方式：
  Tier 1 [Path A 试]  → 失败？ → [Path B 试]  → 失败？ → [Path C 试]
                                                              │
                                                              ▼
                                                          报告 unknown
  ↓
  Tier 2 [Path A]    → ...
  ↓
  Tier 3 [Path A]    → ...
Public entry: parse_case().
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sim_parse.core.errors import never_raise, safe_call
from sim_parse.core.provenance import init_tier_output, add_warning, add_error
from sim_parse.core.registry import all_solvers_ordered, get_solver
from sim_parse.core.tiers import META_KEYS

DiscoveryMode = Literal["auto", "on", "off"]


@never_raise(default={"format": None, "_errors": ["parse_case top-level exception"], "_path": "A"})
def parse_case(
    case_root: str | Path,
    target_tier: int = 4,
    discovery: DiscoveryMode = "auto",
    force_solver: str | None = None,
    force_path: Literal["A", "B"] | None = None,
    qoi_rules_path: str | Path | None = None,
    auto_descend: bool = True,
    auto_descend_max_depth: int = 3,
    iterate_times: bool = False,
    fields: list[str] | None = None,
) -> dict:
    """Parse a case folder, return tiered structured output.

    Args:
        case_root: path to the case directory.
        target_tier: 1-7. Will run Tier 1 through this number.
        discovery: "auto" (default cascade) / "on" (force-attempt C) / "off" (no LLM).
        force_solver: skip identification, use this solver directly.
        force_path: "A" / "B" / None — force a path; bypass cascade.
        qoi_rules_path: external YAML for Tier 5 (Re-ingest closed loop).
        auto_descend: if True (default), when Tier 1 fails on the given path,
            walk subdirectories looking for a child that DOES identify as a
            known case. First match wins. The result's `case_root` becomes
            the matched child, with a warning explaining the descent.
        auto_descend_max_depth: how many levels deep to walk when descending
            (default 3). Subdir count cap of 100 prevents O(N) blowup on
            unrelated directory trees.

    Returns:
        dict with keys:
            case_root, case_hash, ingested_at, descent_path (if auto-descended),
            tier_1_identify, tier_2_inventory, tier_3_metadata,
            tier_4_field_stats, tier_5_qoi, ...
            errors, warnings
        Never raises. On failure, populates errors[] and leaves fields None.
    """
    user_supplied_root = Path(case_root).resolve()
    case_root = user_supplied_root
    out: dict = {
        "case_root": str(case_root),
        "case_hash": _case_hash(case_root),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "errors": [],
        "warnings": [],
    }

    if not case_root.exists():
        out["errors"].append(f"case_root does not exist: {case_root}")
        out["tier_1_identify"] = {"format": None, "_path": "A", "_errors": ["path missing"]}
        return out

    # Step 1: Tier 1 identify on the user-supplied path.
    identity = _run_tier1_identify(case_root, force_solver=force_solver)

    # Step 1b: if identification failed AND auto_descend is on, walk subdirs.
    # Common scenario: user passes a wrapper folder that contains the actual
    # case folder one or two levels deep (e.g. 'fluent界面计算算例/fluent_nobl1').
    if identity.get("format") is None and auto_descend and case_root.is_dir():
        descended = _descend_to_case(
            case_root,
            force_solver=force_solver,
            max_depth=auto_descend_max_depth,
        )
        if descended is not None:
            child_path, child_identity, scanned_paths, all_candidates = descended
            out["warnings"].append(
                f"auto-descended from {case_root} → {child_path} "
                f"(scanned {len(scanned_paths)} subdir(s) up to depth {auto_descend_max_depth})"
            )
            out["descent_path"] = [str(p) for p in scanned_paths]
            # Surface OTHER candidates so the caller knows there are multiple
            # cases inside the wrapper (and which one we picked).
            if len(all_candidates) > 1:
                out["descent_candidates"] = [
                    {"path": str(p), "format": ident.get("format"),
                     "solver": ident.get("solver")}
                    for p, ident in all_candidates
                ]
                out["warnings"].append(
                    f"found {len(all_candidates)} candidate cases under "
                    f"{user_supplied_root}; using {child_path} (BFS first match). "
                    f"Other candidates listed in descent_candidates — pass them "
                    f"explicitly to parse_case if you wanted a different one."
                )
            case_root = child_path
            out["case_root"] = str(case_root)
            out["case_hash"] = _case_hash(case_root)
            identity = child_identity

    out["tier_1_identify"] = identity

    if identity.get("format") is None:
        out["warnings"].append("Tier 1 identification failed; case format unknown.")
        if auto_descend:
            out["warnings"].append(
                f"auto_descend was enabled but no recognizable case found "
                f"within depth {auto_descend_max_depth} of {user_supplied_root}. "
                f"Try pointing parse_case at a more specific subdirectory, "
                f"or pass force_solver=<name> to bypass identification."
            )
        if discovery in ("auto", "on"):
            out["warnings"].append(
                "Path C (Discovery Agent) not yet implemented; case left unidentified."
            )
        return out

    # Step 2: Determine default path for subsequent tiers
    fmt = identity["format"]
    solver_bundle = get_solver(fmt)
    has_path_a = solver_bundle is not None
    default_path = "A" if has_path_a else "B"
    if force_path:
        default_path = force_path

    # Step 3: Run subsequent tiers
    if target_tier >= 2:
        out["tier_2_inventory"] = _run_tier2_inventory(case_root, identity, default_path)
    if target_tier >= 3:
        out["tier_3_metadata"] = _run_tier3_metadata(
            case_root, identity, out.get("tier_2_inventory", {}), default_path
        )
    if target_tier >= 4:
        out["tier_4_field_stats"] = _run_tier4_field_stats(
            case_root, identity,
            out.get("tier_3_metadata", {}),
            default_path,
            inventory=out.get("tier_2_inventory", {}),
            iterate_times=iterate_times,
            fields=fields,
        )
    if target_tier >= 5:
        out["tier_5_qoi"] = _run_tier5_qoi(
            case_root, identity,
            out.get("tier_3_metadata", {}),
            out.get("tier_4_field_stats", {}),
            out,
            default_path,
            qoi_rules_path,
        )
    if target_tier >= 6:
        out["tier_6_full_data"] = _run_tier6_export(
            case_root, identity, default_path,
        )
    if target_tier >= 7:
        out["tier_7_semantic"] = _run_tier7_semantic(out, default_path)

    # Promote each tier's _warnings to the top-level warnings[] list, prefixed
    # with the tier name so consumers know where the message came from. We
    # only do this AFTER all tiers have run so the order in warnings[]
    # matches tier order.
    for tier_key in (
        "tier_1_identify", "tier_2_inventory", "tier_3_metadata",
        "tier_4_field_stats", "tier_6_full_data", "tier_7_semantic",
    ):
        tier_data = out.get(tier_key)
        if isinstance(tier_data, dict):
            for w in tier_data.get("_warnings") or []:
                out["warnings"].append(f"[{tier_key}] {w}")

    # Unit-range sanity check on Tier 4 variable_ranges. Fires when a known
    # bounded field (mass fractions, kappa, ...) has a value outside its
    # expected range — indicating likely unit confusion or non-normalized data.
    if "tier_4_field_stats" in out:
        from sim_parse.core.diagnostics import check_unit_ranges
        t4 = out["tier_4_field_stats"] or {}
        vr = t4.get("variable_ranges") or {}
        if vr:
            violations = safe_call(check_unit_ranges, vr, default=[])
            if violations:
                t4.setdefault("unit_range_violations", violations)
                # Promote to top-level warnings — one per violation, with details
                for v in violations:
                    out["warnings"].append(
                        f"[unit_range] {v['variable']}.{v['stat']}={v['value']:.4g} "
                        f"outside expected [{v['expected_min']}, {v['expected_max']}] "
                        f"({v['label']}). {v['reason']}"
                    )

    # Validate Tier 5 QOI against sim-knowledge/labeled_cases ground truth
    # (silent no-op if no matching label found or sim-knowledge missing).
    if target_tier >= 5 and "tier_5_qoi" in out:
        from sim_parse.core.canonical.validation import validate_against_labels
        validation = safe_call(validate_against_labels, out, default=None)
        if validation:
            out["validation"] = validation
            n_mismatch = validation.get("summary", {}).get("n_mismatch", 0)
            if n_mismatch > 0:
                out["warnings"].append(
                    f"[validation] {n_mismatch} of {validation['summary']['n_total']} "
                    f"labeled QOI(s) disagree with ground truth in "
                    f"{validation.get('matched_label_file')}; see parse_result.validation.results"
                )

    return out


def _case_hash(case_root: Path) -> str:
    """Stable identifier for a case (based on absolute path)."""
    h = hashlib.sha256(str(case_root).encode("utf-8")).hexdigest()
    return f"sha256:{h[:16]}"


# Directories we never descend into. These are tooling / temp / VCS artefacts
# that can't possibly contain a CAE case but can contain very large file trees.
_DESCENT_SKIP_DIRS = {
    ".git", ".svn", ".hg",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", ".env",
    ".idea", ".vscode",
    "$RECYCLE.BIN", "System Volume Information",
    ".simparse_cache", ".sim",
}

# Hard cap on subdirs scanned during auto-descent, to keep response time
# predictable on huge file trees.
_DESCENT_MAX_SUBDIRS = 100


@never_raise(default=None)
def _descend_to_case(
    root: Path,
    *,
    force_solver: str | None,
    max_depth: int,
) -> tuple[Path, dict, list[Path], list[tuple[Path, dict]]] | None:
    """BFS over subdirectories looking for children that identify as a case.

    Returns:
        (first_match_path, first_match_identity, scanned_paths, all_candidates)
        on at least one hit, else None.

        `all_candidates` is the FULL list of (path, identity) for every
        descendant that Tier 1 identified — useful when the wrapper
        contains multiple cases (we still pick the BFS first for
        backward compatibility, but surface the rest as candidates so
        callers can report them or re-invoke parse_case on a different one).

    Strategy: BFS rather than DFS so shallower (more obvious) candidates win
    over deeply-buried ones. We continue scanning siblings of the first match
    at the SAME depth so callers see all top-level alternatives, but stop
    descending once we've found enough candidates (cap at 10 to bound cost).
    """
    if not root.is_dir() or max_depth <= 0:
        return None

    scanned: list[Path] = []
    candidates: list[tuple[Path, dict]] = []
    queue: list[tuple[Path, int]] = [(root, 0)]
    # Cap candidates to keep response time bounded on large trees.
    candidate_cap = 10

    while queue and len(scanned) < _DESCENT_MAX_SUBDIRS:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue

        try:
            children = sorted(current.iterdir())
        except (OSError, PermissionError):
            continue

        for child in children:
            if not child.is_dir():
                continue
            if child.name in _DESCENT_SKIP_DIRS:
                continue
            # Skip OpenFOAM time directories
            try:
                float(child.name)
                continue
            except ValueError:
                pass

            scanned.append(child)
            identity = _run_tier1_identify(child, force_solver=force_solver)
            if identity and identity.get("format"):
                candidates.append((child, identity))
                # Don't descend into a matched candidate — its own subdirs
                # are part of THAT case, not separate cases.
                if len(candidates) >= candidate_cap:
                    break
                continue

            # No match at this child — descend further
            queue.append((child, depth + 1))
            if len(scanned) >= _DESCENT_MAX_SUBDIRS:
                break
        if len(candidates) >= candidate_cap:
            break

    if not candidates:
        return None

    first_path, first_identity = candidates[0]
    return first_path, first_identity, scanned, candidates


@never_raise(default={"format": None, "_path": "A", "_errors": ["tier1 exception"]})
def _run_tier1_identify(case_root: Path, force_solver: str | None = None) -> dict:
    """Try every registered solver's identify() in priority order."""
    if force_solver:
        bundle = get_solver(force_solver)
        if bundle and "identify" in bundle:
            result = safe_call(bundle["identify"], case_root, default=None)
            if result and result.get("format"):
                result.setdefault("_path", "A")
                return result
        return init_tier_output("A") | {"format": None}

    # Cascade through registered solvers
    for name, bundle in all_solvers_ordered():
        if "identify" not in bundle:
            continue
        result = safe_call(bundle["identify"], case_root, default=None)
        if result and result.get("format"):
            result.setdefault("_path", "A")
            return result

    # Path B fallback: generic format detection (HDF5 magic etc.)
    from sim_parse.generic import magic
    b_result = safe_call(magic.identify_by_magic, case_root, default=None)
    if b_result and b_result.get("format"):
        b_result["_path"] = "B"
        return b_result

    return init_tier_output("A") | {"format": None}


@never_raise(default={"_path": "A", "_errors": ["tier2 exception"]})
def _run_tier2_inventory(case_root: Path, identity: dict, default_path: str) -> dict:
    fmt = identity.get("format")
    bundle = get_solver(fmt) if fmt else None
    if bundle and "inventory" in bundle and default_path == "A":
        result = safe_call(bundle["inventory"], case_root, identity, default=None)
        if result is not None:
            result.setdefault("_path", "A")
            return result

    # Path B: format-aware fallback
    if fmt in ("hdf5", "cgns", "fluent_cff", "h5part"):
        # HDF5-family: use h5py dump
        from sim_parse.generic.hdf5_dump import hdf5_inventory
        path = Path(identity.get("file_path", case_root))
        result = safe_call(hdf5_inventory, path, default=None)
        if result is not None:
            return result

    # Generic fallback: just list files
    return _generic_inventory(case_root)


@never_raise(default={"_path": "A", "_errors": ["tier3 exception"]})
def _run_tier3_metadata(case_root: Path, identity: dict, inventory: dict, default_path: str) -> dict:
    fmt = identity.get("format")
    bundle = get_solver(fmt) if fmt else None
    if bundle and "metadata" in bundle and default_path == "A":
        result = safe_call(bundle["metadata"], case_root, identity, inventory, default=None)
        if result is not None:
            result.setdefault("_path", "A")
            return result
    return init_tier_output(default_path)


@never_raise(default={"_path": "A", "_errors": ["tier4 exception"]})
def _run_tier4_field_stats(
    case_root: Path,
    identity: dict,
    metadata: dict,
    default_path: str,
    *,
    inventory: dict | None = None,
    iterate_times: bool = False,
    fields: list[str] | None = None,
) -> dict:
    fmt = identity.get("format")
    bundle = get_solver(fmt) if fmt else None
    if bundle and "field_stats" in bundle and default_path == "A":
        # Pass optional kwargs only if the solver's field_stats supports them,
        # so externally-registered solvers with older signatures still work.
        kwargs: dict = {}
        try:
            import inspect
            sig = inspect.signature(bundle["field_stats"])
            if "inventory" in sig.parameters:
                kwargs["inventory"] = inventory
            if "iterate_times" in sig.parameters:
                kwargs["iterate_times"] = iterate_times
            if "fields" in sig.parameters and fields is not None:
                kwargs["fields"] = fields
        except (TypeError, ValueError):
            pass
        result = safe_call(
            bundle["field_stats"], case_root, identity, metadata,
            default=None, **kwargs,
        )
        if result is not None:
            result.setdefault("_path", "A")
            return result
    return init_tier_output(default_path)


@never_raise(default=[])
def _run_tier5_qoi(
    case_root: Path, identity: dict, metadata: dict, field_stats: dict,
    full_result: dict, default_path: str, qoi_rules_path,
) -> list:
    """Tier 5: extract QOI long-format records via the rule engine."""
    fmt = identity.get("format")
    bundle = get_solver(fmt) if fmt else None
    if bundle and "qoi" in bundle and default_path == "A":
        result = safe_call(
            bundle["qoi"], case_root, identity, metadata, field_stats,
            parse_result=full_result, qoi_rules_path=qoi_rules_path,
            default=None,
        )
        if result is not None:
            return result
    # Generic fallback: run the engine with built-in rules against whatever we have
    from sim_parse.core.canonical.qoi_engine import evaluate_qoi_rules, load_qoi_rules
    rules = load_qoi_rules(qoi_rules_path)
    return evaluate_qoi_rules(rules, full_result) if rules else []


@never_raise(default={"_path": "A", "_errors": ["tier6 exception"]})
def _run_tier6_export(case_root: Path, identity: dict, default_path: str) -> dict:
    """Tier 6: VTU export via solver-specific exporter."""
    fmt = identity.get("format")
    bundle = get_solver(fmt) if fmt else None
    if bundle and "export_vtu" in bundle and default_path == "A":
        result = safe_call(bundle["export_vtu"], case_root, identity, default=None)
        if result is not None:
            return result
    return {"_path": default_path, "supported": False,
            "reason": "Tier 6 export not implemented for this format"}


@never_raise(default={"_path": "A", "_errors": ["tier7 exception"]})
def _run_tier7_semantic(full_result: dict, default_path: str) -> dict:
    """Tier 7: heuristic-based semantic interpretation."""
    from sim_parse.discovery.semantic import interpret
    return interpret(full_result)


def _generic_inventory(case_root: Path) -> dict:
    """Path B fallback inventory: list files / dirs without semantic interpretation."""
    out = init_tier_output("B")
    out["files"] = sorted([str(p.relative_to(case_root)) for p in case_root.iterdir() if p.is_file()])
    out["directories"] = sorted([str(p.relative_to(case_root)) for p in case_root.iterdir() if p.is_dir()])
    return out
