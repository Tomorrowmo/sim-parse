"""Verify sim-parse output matches sim-knowledge/labeled_cases ground truth.

This is the contract between extraction (sim-parse) and judgment (sim-knowledge).
Demonstrates Agent accuracy mechanism #2 (calibration).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sim_parse import parse_case


# Path to sim-knowledge/labeled_cases (sibling of sim-parse)
SIM_KNOWLEDGE_LABELED = (
    Path(__file__).resolve().parents[2].parent / "sim-knowledge" / "labeled_cases"
)


@pytest.fixture
def dlr_a_normal_label():
    """Load the DLR_A_cavity_normal ground_truth.yaml."""
    yml = SIM_KNOWLEDGE_LABELED / "combustion_extinction" / "DLR_A_cavity_normal" / "ground_truth.yaml"
    if not yml.exists():
        pytest.skip(f"sim-knowledge labeled case not present: {yml}")
    with open(yml, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_dlr_a_label_case_path_resolves(dlr_a_normal_label):
    """The case_path in ground_truth.yaml must point to a real directory."""
    case_path = Path(dlr_a_normal_label["case_path"])
    if not case_path.exists():
        pytest.skip(f"DLR_A_cavity not at {case_path}")


def test_dlr_a_label_evidence_matches_extraction(dlr_a_normal_label):
    """The 'evidence' values in ground_truth.yaml should match sim-parse output."""
    case_path = Path(dlr_a_normal_label["case_path"])
    if not case_path.exists():
        pytest.skip(f"DLR_A_cavity not at {case_path}")

    result = parse_case(case_path, target_tier=4)
    evidence = dlr_a_normal_label["evidence"]

    # mesh_cells
    assert result["tier_3_metadata"]["mesh_cells"] == evidence["mesh_cells"]

    # T_max — within 5% (recompute may differ slightly)
    extracted_T_max = result["tier_4_field_stats"]["variable_ranges"]["T"]["max"]
    expected_T_max = evidence["T_max"]
    assert abs(extracted_T_max - expected_T_max) / expected_T_max < 0.05, (
        f"T_max mismatch: extracted={extracted_T_max} vs label={expected_T_max}"
    )

    # OH peak — within 10%
    extracted_OH_max = result["tier_4_field_stats"]["variable_ranges"]["OH"]["max"]
    expected_OH_max = evidence["OH_max"]
    assert abs(extracted_OH_max - expected_OH_max) / expected_OH_max < 0.10, (
        f"OH_max mismatch: extracted={extracted_OH_max} vs label={expected_OH_max}"
    )


def test_dlr_a_label_extinction_judgment(dlr_a_normal_label):
    """Apply the simplified extinction criterion to extracted signals,
    verify it agrees with the ground truth label.

    This is the core "Agent accuracy mechanism #2" loop:
        ground_truth label → sim-parse extracts → criterion → must match label.
    """
    case_path = Path(dlr_a_normal_label["case_path"])
    if not case_path.exists():
        pytest.skip(f"DLR_A_cavity not at {case_path}")

    result = parse_case(case_path, target_tier=4)
    ranges = result["tier_4_field_stats"]["variable_ranges"]

    # Apply criteria_reference.md §5.3.1 default thresholds (CH4-air @ 1 atm)
    qdot_max = ranges.get("Qdot", {}).get("max", 0)
    T_max = ranges["T"]["max"]
    T_inlet = 292  # from BC; could read from BC parser later
    OH_max = ranges.get("OH", {}).get("max", 0)

    # Voting (3 criteria, threshold 2)
    votes = []
    if qdot_max < 1.0e6:
        votes.append("low_heat_release")
    if (T_max - T_inlet) < 100:
        votes.append("low_temperature_excess")
    if OH_max < 1.0e-6:
        votes.append("no_oh_radical")

    judged_extinguished = len(votes) >= 2
    expected_extinguished = dlr_a_normal_label["labels"]["is_extinguished"]

    assert judged_extinguished == expected_extinguished, (
        f"Criterion verdict ({judged_extinguished}) disagrees with ground truth "
        f"({expected_extinguished}). Votes: {votes}, "
        f"signals: Qdot.max={qdot_max:.2e}, T.max={T_max:.1f}, OH.max={OH_max:.2e}"
    )
