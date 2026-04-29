"""Unit tests for parse_case auto-descent (wrapper-directory handling).

When the user passes a wrapping directory that is NOT itself a case but
contains one (or more) case folder underneath, parse_case should descend
to find it. First match wins; descent must be bounded; numeric / VCS /
cache subdirs must be skipped; the result reports which path actually
got parsed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sim_parse import parse_case
from sim_parse.core.cascade import _descend_to_case


# ─── Synthetic OpenFOAM-shaped fixtures ───────────────────────────────────────


def _make_minimal_openfoam_case(root: Path) -> Path:
    """Create the bare-bones structure that openfoam.identify recognizes:
    system/controlDict + constant/polyMesh/owner."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "system").mkdir(exist_ok=True)
    (root / "system" / "controlDict").write_text(
        "FoamFile {} application icoFoam;\n", encoding="utf-8"
    )
    (root / "constant" / "polyMesh").mkdir(parents=True, exist_ok=True)
    (root / "constant" / "polyMesh" / "owner").write_text(
        "FoamFile {}\n", encoding="utf-8"
    )
    return root


# ─── auto-descend behavior ────────────────────────────────────────────────────


def test_descent_finds_case_one_level_down(tmp_path):
    """Wrapper dir contains a single child that IS a valid case."""
    wrapper = tmp_path / "wrapper"
    case = wrapper / "actual_case"
    _make_minimal_openfoam_case(case)

    r = parse_case(wrapper, target_tier=1)
    assert r["tier_1_identify"]["format"] == "openfoam"
    # case_root in the result is the descended-to path, not the wrapper
    assert Path(r["case_root"]).name == "actual_case"
    # warning records the descent
    assert any("auto-descended" in w for w in r["warnings"])


def test_descent_finds_case_two_levels_down(tmp_path):
    """Even nested two levels deep (within max_depth=3 default) gets found."""
    case = tmp_path / "wrapper" / "subwrapper" / "actual_case"
    _make_minimal_openfoam_case(case)

    r = parse_case(tmp_path, target_tier=1)
    assert r["tier_1_identify"]["format"] == "openfoam"
    assert Path(r["case_root"]).name == "actual_case"


def test_descent_can_be_disabled(tmp_path):
    """auto_descend=False preserves the old behavior — wrapper fails outright."""
    wrapper = tmp_path / "wrapper"
    case = wrapper / "actual_case"
    _make_minimal_openfoam_case(case)

    r = parse_case(wrapper, target_tier=1, auto_descend=False)
    assert r["tier_1_identify"]["format"] is None
    assert any("Tier 1 identification failed" in w for w in r["warnings"])
    # NO descent warning when disabled
    assert not any("auto-descended" in w for w in r["warnings"])


def test_descent_does_not_run_when_root_already_identifies(tmp_path):
    """If the user gave the exact case_root, we must NOT descend further —
    the result's case_root stays put, no descent warning."""
    case = _make_minimal_openfoam_case(tmp_path / "case")
    r = parse_case(case, target_tier=1)
    assert r["tier_1_identify"]["format"] == "openfoam"
    assert Path(r["case_root"]) == case.resolve()
    assert not any("auto-descended" in w for w in r["warnings"])


def test_descent_skips_numeric_time_dirs(tmp_path):
    """Walking shouldn't try to identify '0/' or '5000/' etc. as cases —
    those are OpenFOAM time directories belonging to the parent."""
    case = _make_minimal_openfoam_case(tmp_path / "case")
    (case / "0").mkdir()    # time directory
    (case / "5000").mkdir()
    # Directly invoke _descend on the case root; should NOT recurse INTO
    # numeric children even though they're directories.
    result = _descend_to_case(case, force_solver=None, max_depth=3)
    # Should return None — numeric children skipped, no other case in there
    assert result is None


def test_descent_respects_max_depth(tmp_path):
    """A case buried 5 levels deep is NOT found with max_depth=2."""
    case = tmp_path / "a" / "b" / "c" / "d" / "actual_case"
    _make_minimal_openfoam_case(case)

    r = parse_case(tmp_path, target_tier=1, auto_descend_max_depth=2)
    # Depth 2 means we walk a/, a/b/ — case is 4 levels below tmp_path
    assert r["tier_1_identify"]["format"] is None
    # Warning explicitly mentions the depth limit
    assert any("auto_descend was enabled but no recognizable case found" in w
               for w in r["warnings"])


def test_descent_skips_cache_dirs(tmp_path):
    """Build / VCS / cache directories must not be descended into even if
    they happen to contain case-shaped folders. Common scenario: a CI
    checkout that has __pycache__/ alongside real cases."""
    # Put a misleading "case-shaped" file tree in __pycache__ — we should
    # NOT find it. Real case is right next to it.
    pyc = tmp_path / "__pycache__"
    _make_minimal_openfoam_case(pyc / "fake_case")
    real_case = _make_minimal_openfoam_case(tmp_path / "real_case")

    r = parse_case(tmp_path, target_tier=1)
    assert r["tier_1_identify"]["format"] == "openfoam"
    # The real one — not the one inside __pycache__
    assert Path(r["case_root"]).name == "real_case"


def test_descent_records_scanned_subdirs(tmp_path):
    """The result has a `descent_path` listing what was scanned, so a
    reviewer can audit which candidates were tried."""
    wrapper = tmp_path / "wrapper"
    case = wrapper / "case"
    _make_minimal_openfoam_case(case)
    # An unrelated sibling that should be in the scan list but not match
    (wrapper / "unrelated").mkdir()
    (wrapper / "unrelated" / "random.txt").write_text("nope")

    r = parse_case(wrapper, target_tier=1)
    assert r["tier_1_identify"]["format"] == "openfoam"
    assert "descent_path" in r
    scanned = [Path(p).name for p in r["descent_path"]]
    # 'case' is the one that matched, must be in the list
    assert "case" in scanned


def test_descent_returns_first_match(tmp_path):
    """If multiple subdirs ARE valid cases, BFS first-match wins (we don't
    silently merge or warn about ambiguity beyond the descent log)."""
    wrapper = tmp_path / "wrapper"
    case_a = _make_minimal_openfoam_case(wrapper / "a_case")
    case_b = _make_minimal_openfoam_case(wrapper / "b_case")

    r = parse_case(wrapper, target_tier=1)
    assert r["tier_1_identify"]["format"] == "openfoam"
    # Either one is a legitimate match (BFS order is sorted, so 'a_case'
    # comes first in iterdir's deterministic sorted output)
    assert Path(r["case_root"]).name in ("a_case", "b_case")


def test_descent_surfaces_other_candidates(tmp_path):
    """When the wrapper contains multiple cases, all are exposed via
    descent_candidates so the caller knows they exist."""
    wrapper = tmp_path / "wrapper"
    _make_minimal_openfoam_case(wrapper / "a_case")
    _make_minimal_openfoam_case(wrapper / "b_case")
    _make_minimal_openfoam_case(wrapper / "c_case")

    r = parse_case(wrapper, target_tier=1)
    assert "descent_candidates" in r
    candidates = r["descent_candidates"]
    assert len(candidates) == 3
    names = [Path(c["path"]).name for c in candidates]
    assert sorted(names) == ["a_case", "b_case", "c_case"]
    for c in candidates:
        assert c["format"] == "openfoam"
    # Warning text must mention the count
    assert any("3 candidate cases" in w for w in r["warnings"])


def test_descent_no_extra_field_when_only_one_case(tmp_path):
    """Single case found → no descent_candidates field (avoid noise)."""
    wrapper = tmp_path / "wrapper"
    _make_minimal_openfoam_case(wrapper / "case")

    r = parse_case(wrapper, target_tier=1)
    assert "descent_candidates" not in r


def test_descent_failure_message_helpful(tmp_path):
    """When descent fails, warnings should suggest force_solver as escape hatch."""
    # Empty wrapper, no case anywhere
    wrapper = tmp_path / "empty_wrapper"
    wrapper.mkdir()
    (wrapper / "subdir").mkdir()
    (wrapper / "subdir" / "irrelevant.txt").write_text("hi")

    r = parse_case(wrapper, target_tier=1)
    assert r["tier_1_identify"]["format"] is None
    joined = " | ".join(r["warnings"])
    assert "force_solver" in joined
    assert "auto_descend was enabled" in joined
