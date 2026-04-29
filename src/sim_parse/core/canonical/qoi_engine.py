"""QOI rule evaluator: maps Tier 3+4 outputs → long-format QOI records.

Decoupled from any specific solver. Loads rules from YAML; evaluates each rule
against the tier outputs; emits a `tier_5_qoi` list of records.

Rule types supported:
    - pluck:      copy a value from a dotted path
    - expression: compute a numeric value via safe expression on multiple paths
    - voting:     multi-criterion boolean (each criterion may be path or expression)

Phase 2 added: expression criteria via safe_eval (restricted ast eval).
"""
from __future__ import annotations

import operator
from pathlib import Path
from typing import Any

import yaml

from sim_parse.core.canonical.safe_eval import safe_eval
from sim_parse.core.errors import never_raise


# Built-in default rules path (this file's neighbor)
_DEFAULT_RULES = Path(__file__).parent / "qoi_rules.yaml"

# Optional sim-knowledge sibling repo — if present, merge its physics/ rules
_SIM_KNOWLEDGE = Path(__file__).resolve().parents[4].parent / "sim-knowledge"

# Cache for variable alias map (lazy-loaded)
_ALIAS_CACHE: dict | None = None

# Map "tier_N" → actual key in the parse_case() output
_TIER_KEY_MAP = {
    "1": "tier_1_identify",
    "2": "tier_2_inventory",
    "3": "tier_3_metadata",
    "4": "tier_4_field_stats",
    "5": "tier_5_qoi",
}

_OPS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


def _coerce_numeric(v: Any) -> Any:
    """Try to coerce string-like value to float. PyYAML 1.1 parses '1.0e6' as
    string (only '1.0e+6' is recognized as float); silently fix.
    """
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return v
    return v


@never_raise(default=[])
def load_qoi_rules(rules_path: str | Path | None = None) -> list[dict]:
    """Load QOI rules from YAML.

    Resolution order:
      1. Explicit `rules_path` → use it alone
      2. sim-knowledge/physics/<domain>/criteria.yaml (if sibling repo present)
         → these take precedence by name
      3. Built-in `qoi_rules.yaml` (sim-parse self-contained fallback)
         → fills in any names not already covered by sim-knowledge

    Precedence rationale: sim-knowledge is the canonical, expert-curated source.
    sim-parse builtin is a self-contained fallback so the package works without
    sim-knowledge installed. When both have the same rule name, sim-knowledge
    wins so that updates land via sim-knowledge YAML edits without touching
    sim-parse code.

    Args:
        rules_path: external YAML path; if None, use defaults below.

    Returns:
        list of rule dicts.
    """
    if rules_path:
        path = Path(rules_path)
        if not path.is_file():
            return []
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return _annotate_source(_extract_rules(data), str(path))

    # 1. Load sim-knowledge first (highest precedence)
    rules: list[dict] = list(_load_sim_knowledge_physics_rules(seen_names=set()))
    seen = {r.get("name") for r in rules if isinstance(r, dict)}

    # 2. Fill in built-in for names not yet covered
    if _DEFAULT_RULES.is_file():
        with open(_DEFAULT_RULES, encoding="utf-8") as f:
            for rule in _annotate_source(_extract_rules(yaml.safe_load(f)), str(_DEFAULT_RULES)):
                if isinstance(rule, dict) and rule.get("name") not in seen:
                    rules.append(rule)
                    seen.add(rule.get("name"))

    return rules


def _annotate_source(rules: list[dict], source_file: str) -> list[dict]:
    """Stamp each rule with `_source_file` so Tier 5 records can cite where
    the rule definition lives. Mutates and returns the list.
    """
    for rule in rules:
        if isinstance(rule, dict) and "_source_file" not in rule:
            rule["_source_file"] = source_file
    return rules


def _extract_rules(data) -> list[dict]:
    """Extract rules from a yaml document with either 'qois' or 'intents' key.

    Both built-in qoi_rules.yaml and sim-knowledge/physics/<domain>/criteria.yaml
    use compatible schemas; we accept either top-level key.
    """
    if not isinstance(data, dict):
        return []
    rules = data.get("qois") or data.get("intents") or []
    return rules if isinstance(rules, list) else []


def _load_sim_knowledge_physics_rules(seen_names: set | None = None) -> list[dict]:
    """Scan sim-knowledge/physics/*/criteria.yaml and merge their rules.

    Silent-fail: if sim-knowledge isn't present, returns empty list.
    Caller controls dedup via `seen_names`.
    """
    if not _SIM_KNOWLEDGE.is_dir():
        return []
    physics_dir = _SIM_KNOWLEDGE / "physics"
    if not physics_dir.is_dir():
        return []

    if seen_names is None:
        seen_names = set()
    out: list[dict] = []

    for domain_dir in sorted(physics_dir.iterdir()):
        if not domain_dir.is_dir():
            continue
        criteria_yaml = domain_dir / "criteria.yaml"
        if not criteria_yaml.is_file():
            continue
        try:
            with open(criteria_yaml, encoding="utf-8") as f:
                doc = yaml.safe_load(f)
        except Exception:
            continue
        for rule in _extract_rules(doc):
            if not isinstance(rule, dict):
                continue
            name = rule.get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            # Stamp the rule with where it lives so Tier 5 records can
            # cite their source.
            rule.setdefault("_source_file", str(criteria_yaml))
            out.append(rule)

    return out


@never_raise(default=[])
def evaluate_qoi_rules(rules: list[dict], parse_result: dict) -> list[dict]:
    """Evaluate every rule against parse_case's full output dict.

    Returns:
        list of QOI records (long-format).

        Skipped rules are NOT silently dropped — they appear with
        `status: skipped_<reason>` and `value: None` so consumers can see
        which rules were applicable and why each one didn't fire.
    """
    capabilities = _detect_capabilities(parse_result)
    records: list[dict] = []
    for rule in rules:
        rec = _evaluate_one(rule, parse_result, capabilities)
        if rec is not None:
            records.append(rec)
    return records


def _detect_capabilities(parse_result: dict) -> dict:
    """Probe parse_result to decide which rule preconditions are satisfied.

    Currently sim-parse Tier 4 emits a single-snapshot view (variable stats
    at one selected time). True per-time series stats are not produced, so
    `time_series_field_stats` is always False today. When a future Tier 4
    revision starts emitting per-time stats, set the marker on Tier 4 itself
    (e.g. tier_4_field_stats.time_series_data) and we'll pick it up here.
    """
    t4 = parse_result.get("tier_4_field_stats") or {}
    has_ts_stats = bool(t4.get("time_series_data"))
    return {
        "time_series_field_stats": has_ts_stats,
    }


def _evaluate_one(rule: dict, parse_result: dict, capabilities: dict) -> dict | None:
    # 1. Temporal requirement gate. If a rule needs time-series data and we
    #    only have a snapshot, emit a skipped record rather than fabricating
    #    a value (e.g. last/peak ratios degenerate to 1.0 in single-snapshot).
    temporal = rule.get("temporal_requirement", "snapshot")
    if temporal == "time_series" and not capabilities.get("time_series_field_stats"):
        return _make_skipped_record(
            rule,
            status="skipped_static_snapshot",
            reason=(
                f"rule '{rule.get('name')}' declares temporal_requirement=time_series "
                f"but parse_result only contains a single-time snapshot; "
                f"evaluating would produce a degenerate value"
            ),
        )

    # 2. Auto-detect a degenerate input wiring: when two or more named
    #    inputs of an expression resolve to the same dotted path, the
    #    expression's value can never carry useful information about a
    #    relationship between them. Skip with explicit reason.
    if rule.get("type") == "expression":
        deg = _detect_degenerate_expression_inputs(rule)
        if deg is not None:
            shared_paths = sorted({
                spec.get("path", "")
                for spec in (rule.get("inputs") or {}).values()
                if isinstance(spec, dict) and spec.get("path")
            })
            return _make_skipped_record(
                rule,
                status="skipped_degenerate_inputs",
                reason=(
                    f"expression '{rule.get('expression')}' has inputs {deg} "
                    f"that all resolve to the same path(s) {shared_paths}; "
                    f"result would be a constant and not informative"
                ),
            )

    # 3. Check required_paths
    for p in rule.get("required_paths") or []:
        if _get_path(parse_result, p) is None:
            return None

    # 4. Dispatch on type
    rule_type = rule.get("type", "pluck")
    if rule_type == "pluck":
        return _eval_pluck(rule, parse_result)
    elif rule_type == "voting":
        return _eval_voting(rule, parse_result)
    elif rule_type == "expression":
        return _eval_expression(rule, parse_result)
    else:
        return None


def _detect_degenerate_expression_inputs(rule: dict) -> list[str] | None:
    """Return list of input names that all resolve to the same path, or None
    if every input has a distinct path. Inputs without a path (literal
    fallback only) are ignored."""
    inputs_spec = rule.get("inputs") or {}
    if not isinstance(inputs_spec, dict) or len(inputs_spec) < 2:
        return None
    by_path: dict[str, list[str]] = {}
    for name, spec in inputs_spec.items():
        if not isinstance(spec, dict):
            continue
        path = spec.get("path")
        if path:
            by_path.setdefault(path, []).append(name)
    # If any path has 2+ inputs sharing it, AND the rule has only inputs that
    # share paths (no truly distinct input), it's degenerate.
    duplicated = [names for names in by_path.values() if len(names) >= 2]
    if not duplicated:
        return None
    distinct_paths = len(by_path)
    if distinct_paths == 1 and len(inputs_spec) >= 2:
        # All inputs map to one path — fully degenerate.
        return sorted(inputs_spec.keys())
    return None


def _make_skipped_record(rule: dict, *, status: str, reason: str) -> dict:
    """Standard 'rule applicable but couldn't be evaluated' record."""
    return {
        "variable": rule.get("name"),
        "value": None,
        "unit": rule.get("unit", ""),
        "status": status,
        "reason": reason,
        "confidence": "N/A",
        "description": rule.get("description"),
    }


def _eval_expression(rule: dict, parse_result: dict) -> dict | None:
    """Evaluate a top-level expression rule (computes a single scalar value)."""
    expr_str = rule.get("expression")
    inputs_spec = rule.get("inputs") or {}
    if not expr_str:
        return None

    resolved = _resolve_inputs_traced(inputs_spec, parse_result)
    if resolved is None:
        return None  # required input missing

    names = {k: v["value"] for k, v in resolved.items()}
    value = safe_eval(expr_str, names)
    if value is None:
        return None

    return {
        "variable": rule["name"],
        "value": value,
        "unit": rule.get("unit", ""),
        "source": f"expression: {expr_str} (inputs: {list(names.keys())})",
        "confidence": rule.get("confidence", "HIGH"),
        "description": rule.get("description"),
        "rule_file": rule.get("_source_file"),
        "inputs_resolved": resolved,
    }


def _resolve_inputs(inputs_spec: dict, parse_result: dict) -> dict | None:
    """Back-compat shim: returns {name: numeric value} or None if missing."""
    traced = _resolve_inputs_traced(inputs_spec, parse_result)
    if traced is None:
        return None
    return {k: v["value"] for k, v in traced.items()}


def _resolve_inputs_traced(inputs_spec: dict, parse_result: dict) -> dict | None:
    """Resolve {name: {path, fallback}} → {name: {value, from_path, fallback_used}}.

    Returns None if any required input (no fallback) is missing. Each
    input's resolution carries enough trace info that a reviewer can
    re-resolve it manually from the parse_result.
    """
    out: dict = {}
    for name, spec in inputs_spec.items():
        if not isinstance(spec, dict):
            return None
        path = spec.get("path", "")
        v = _get_path(parse_result, path)
        fallback_used = False
        if v is None:
            if "fallback" in spec:
                v = spec["fallback"]
                fallback_used = True
            else:
                return None
        out[name] = {
            "value": v,
            "from_path": path,
            "fallback_used": fallback_used,
        }
    return out


def _eval_pluck(rule: dict, parse_result: dict) -> dict | None:
    src = rule.get("source", {})
    tier = str(src.get("tier", ""))
    path_within = src.get("path", "")
    full_path = f"tier_{tier}.{path_within}" if tier and path_within else ""
    value = _get_path(parse_result, full_path)
    if value is None:
        return None
    return {
        "variable": rule["name"],
        "value": value,
        "unit": rule.get("unit", ""),
        "source": full_path,
        "confidence": rule.get("confidence", "HIGH"),
        "description": rule.get("description"),
        "rule_file": rule.get("_source_file"),
        "inputs_resolved": {
            "_pluck": {"value": value, "from_path": full_path, "fallback_used": False},
        },
    }


def _eval_voting(rule: dict, parse_result: dict) -> dict | None:
    criteria = rule.get("criteria") or []
    voting_threshold = rule.get("voting_threshold", len(criteria))

    evidence: list[dict] = []
    matched_count = 0
    evaluable_count = 0  # criteria we could actually evaluate

    for c in criteria:
        threshold = _coerce_numeric(c.get("threshold"))
        op_str = c.get("op", "<")
        skip_if_missing = c.get("skip_if_missing", False)
        crit_name = c.get("name") or c.get("path") or c.get("expression", "<unnamed>")

        # Resolve value: either via path or via expression
        if "expression" in c:
            inputs_spec = c.get("inputs") or {}
            names = _resolve_inputs(inputs_spec, parse_result)
            if names is None:
                if skip_if_missing:
                    evidence.append({
                        "name": crit_name, "value": None,
                        "matched": None, "reason": "input signal missing"
                    })
                    continue
                else:
                    return None
            value = safe_eval(c["expression"], names)
            if value is None:
                evidence.append({
                    "name": crit_name, "value": None,
                    "matched": None, "reason": "expression eval failed"
                })
                continue
            value_source = f"expr: {c['expression']}"
        else:
            path = c.get("path")
            value = _get_path(parse_result, path) if path else None
            if value is None:
                if skip_if_missing:
                    evidence.append({
                        "name": crit_name, "value": None,
                        "matched": None, "reason": "signal missing"
                    })
                    continue
                else:
                    return None
            value_source = path

        evaluable_count += 1
        op_fn = _OPS.get(op_str)
        if op_fn is None:
            evidence.append({
                "name": crit_name, "value": value,
                "matched": None, "reason": f"unknown op {op_str}"
            })
            continue

        try:
            matched = bool(op_fn(value, threshold))
        except (TypeError, ValueError):
            evidence.append({
                "name": crit_name, "value": value,
                "matched": None, "reason": "incomparable"
            })
            continue

        evidence.append({
            "name": crit_name, "value": value, "value_source": value_source,
            "threshold": threshold, "op": op_str, "matched": matched,
        })
        if matched:
            matched_count += 1

    if evaluable_count == 0:
        # Every criterion was skip_if_missing-skipped because its path
        # didn't resolve. The rule IS applicable in principle (it knew
        # to look here) but no data was available. Emit an explicit
        # skipped record rather than dropping it silently — that way
        # downstream consumers (and labeled_cases validation) can see
        # the rule was attempted and why it couldn't fire.
        skipped_reasons = [e.get("reason") for e in evidence if e.get("reason")]
        return _make_skipped_record(
            rule,
            status="skipped_no_data",
            reason=(
                f"voting rule '{rule.get('name')}' has no evaluable criteria — "
                f"all required paths were missing from parse_result. "
                f"Per-criterion: {skipped_reasons}"
            ),
        )

    is_true = matched_count >= voting_threshold

    # Pick confidence
    if matched_count == evaluable_count:
        confidence = rule.get("confidence_when_all_match", "HIGH")
    elif matched_count > 0:
        confidence = rule.get("confidence_when_majority_match", "MED")
    else:
        confidence = rule.get("confidence_when_no_match", "HIGH")  # negative is also confident

    return {
        "variable": rule["name"],
        "value": is_true,
        "unit": rule.get("unit", "boolean"),
        "source": f"voting ({matched_count}/{evaluable_count} criteria matched, threshold={voting_threshold})",
        "confidence": confidence,
        "description": rule.get("description"),
        "evidence": evidence,
        "reference": rule.get("reference"),
        "rule_file": rule.get("_source_file"),
    }


def _get_path(obj: Any, dotted_path: str) -> Any:
    """Read a dotted path like 'tier_4.variable_ranges.T.max' from parse_result.

    Returns None if any step missing.

    Phase 2.1: if a step inside `variable_ranges` doesn't resolve, falls back
    to solver-specific aliases from sim-knowledge variables.yaml.
    Example: path 'tier_4.variable_ranges.T.max' on a Fluent case finds
    'TEMPERATURE' as alias and returns its 'max' value.

    For vector aliases that map to multiple components (U → X/Y/Z_VELOCITY),
    we synthesize magnitude_max/min/mean by combining components.
    """
    if not dotted_path:
        return None
    parts = dotted_path.split(".")

    # First part: tier_N → real key
    head = parts[0]
    if head.startswith("tier_"):
        num = head[len("tier_"):]
        full_key = _TIER_KEY_MAP.get(num)
        if full_key is None:
            return None
        cur = obj.get(full_key)
        rest = parts[1:]
    else:
        cur = obj
        rest = parts

    # Direct path resolution
    direct = _resolve_dotted(cur, rest)
    if direct is not None:
        return direct

    # Alias fallback: check if this is a variable_ranges access we can rescue
    if len(rest) >= 3 and rest[0] == "variable_ranges":
        canonical_var = rest[1]                # e.g. "T", "U"
        sub_field = ".".join(rest[2:])         # e.g. "max", "magnitude_max"
        solver = obj.get("tier_1_identify", {}).get("format")
        if solver:
            return _resolve_via_alias(cur, canonical_var, sub_field, solver)
    return None


def _resolve_dotted(start: Any, parts: list) -> Any:
    """Walk a dotted path through nested dicts. Return None if any step missing."""
    cur = start
    for p in parts:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur


def _resolve_via_alias(tier4: dict, canonical_var: str, sub_field: str, solver: str) -> Any:
    """Look up `canonical_var` via solver-specific aliases.

    Strategy:
        1. Treat aliases as ALTERNATIVES — try each as a scalar variable name.
           First one that resolves wins (covers both "two names for one scalar"
           and "one of three vector components is also exposed as magnitude").
        2. If sub_field requests magnitude_* and step 1 didn't return,
           try VECTOR SYNTHESIS by looking for X/Y/Z-prefixed component aliases.

    Looks up case-insensitively + handles dash↔underscore variants.

    Returns None if neither path resolves.
    """
    alias_map = _load_alias_map()
    aliases = alias_map.get((canonical_var, solver))
    if not aliases:
        return None

    var_ranges = tier4.get("variable_ranges") if isinstance(tier4, dict) else None
    if not var_ranges:
        return None

    ci_keys = {k.lower(): k for k in var_ranges if isinstance(k, str)}

    def _lookup_ci(name: str):
        """Case-insensitive + dash/underscore-tolerant lookup."""
        candidates = {name, name.upper(), name.lower(),
                      name.replace("-", "_"), name.replace("-", "_").upper(),
                      name.replace("_", "-"), name.replace("_", "-").lower()}
        for variant in candidates:
            if variant in var_ranges:
                return var_ranges[variant]
            actual = ci_keys.get(variant.lower())
            if actual:
                return var_ranges[actual]
        return None

    # ── Path 1: alternatives — try each alias as a scalar lookup ──────────────
    for alias in aliases:
        stats = _lookup_ci(alias)
        if stats:
            v = stats.get(sub_field)
            if v is not None:
                return v

    # ── Path 2: vector synthesis (when sub_field is magnitude_* / max / min / mean) ──
    vector_compatible_subs = {
        "magnitude_max", "magnitude_min", "magnitude_mean",
        "max", "min", "mean",
    }
    if sub_field not in vector_compatible_subs:
        return None

    # Look for X/Y/Z-prefixed components within aliases
    def _starts(s, prefix):
        s_up = s.upper()
        return s_up.startswith(prefix) or s_up.startswith(prefix.replace("_", "-"))

    x_aliases = [a for a in aliases if _starts(a, "X_") or _starts(a, "X-")]
    y_aliases = [a for a in aliases if _starts(a, "Y_") or _starts(a, "Y-")]
    z_aliases = [a for a in aliases if _starts(a, "Z_") or _starts(a, "Z-")]

    if not (x_aliases and y_aliases and z_aliases):
        return None

    x_stats = next((s for s in (_lookup_ci(a) for a in x_aliases) if s), None)
    y_stats = next((s for s in (_lookup_ci(a) for a in y_aliases) if s), None)
    z_stats = next((s for s in (_lookup_ci(a) for a in z_aliases) if s), None)
    if not (x_stats and y_stats and z_stats):
        return None

    return _synthesize_vector_stat(sub_field, [x_stats, y_stats, z_stats])


def _synthesize_vector_stat(sub_field: str, component_stats: list) -> Any:
    """Synthesize vector magnitude/component stats from per-component stats.

    Approximations (cell-by-cell magnitudes aren't stored at Tier 4):
        magnitude_max: max over components of max(|min|, |max|)
        magnitude_min: 0 (lower bound; exact requires cell access)
        magnitude_mean: ((mean_X)² + (mean_Y)² + (mean_Z)²)^0.5
    """
    if sub_field == "magnitude_max":
        return float(max(
            max(abs(s.get("max", 0) or 0), abs(s.get("min", 0) or 0))
            for s in component_stats
        ))
    if sub_field == "magnitude_min":
        return 0.0
    if sub_field == "magnitude_mean":
        means = [s.get("mean", 0) or 0 for s in component_stats]
        return float(sum(m * m for m in means) ** 0.5)
    if sub_field == "max":
        return max(s.get("max", 0) for s in component_stats)
    if sub_field == "min":
        return min(s.get("min", 0) for s in component_stats)
    if sub_field == "mean":
        return sum(s.get("mean", 0) for s in component_stats) / len(component_stats)
    return None


def _load_alias_map() -> dict:
    """Load and cache a flat (canonical_name, solver) → [solver_native_names] map.

    Reads sim-knowledge/physics/<domain>/variables.yaml. Silent-fail if
    sim-knowledge isn't present.
    """
    global _ALIAS_CACHE
    if _ALIAS_CACHE is not None:
        return _ALIAS_CACHE

    out: dict = {}
    if _SIM_KNOWLEDGE.is_dir():
        physics_dir = _SIM_KNOWLEDGE / "physics"
        if physics_dir.is_dir():
            for domain_dir in physics_dir.iterdir():
                vars_yaml = domain_dir / "variables.yaml"
                if not vars_yaml.is_file():
                    continue
                try:
                    with open(vars_yaml, encoding="utf-8") as f:
                        doc = yaml.safe_load(f)
                except Exception:
                    continue
                if not isinstance(doc, dict):
                    continue
                vars_section = doc.get("variables") or {}
                for var_def in vars_section.values():
                    if not isinstance(var_def, dict):
                        continue
                    canonical = var_def.get("canonical_name")
                    aliases = var_def.get("aliases") or {}
                    if not canonical or not isinstance(aliases, dict):
                        continue
                    for solver, names in aliases.items():
                        if isinstance(names, str):
                            names = [names]
                        if not isinstance(names, list):
                            continue
                        out[(canonical, solver)] = list(names)

    # Hand-add a few hardcoded aliases that variables.yaml might miss
    # (these handle the most common cross-solver mismatches)
    _DEFAULTS = {
        ("T", "fluent"): ["TEMPERATURE"],
        ("p", "fluent"): ["PRESSURE"],
        ("U", "fluent"): ["X_VELOCITY", "Y_VELOCITY", "Z_VELOCITY"],
        ("rho", "fluent"): ["DENSITY"],
    }
    for k, v in _DEFAULTS.items():
        out.setdefault(k, v)

    _ALIAS_CACHE = out
    return out
