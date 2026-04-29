"""Unit tests for OpenFOAM Tier 2 variable-kind classification + Tier 4 verification."""
from __future__ import annotations

import pytest

from sim_parse.core.diagnostics import (
    array_rank as _array_rank,
    verify_variable_kinds as _verify_variable_kinds,
)
from sim_parse.solvers.openfoam.tier2_inventory import (
    _classify_variables,
    _is_face_flux_name,
)


# ─── Tier 2 classifier ────────────────────────────────────────────────────────


def test_classify_face_flux():
    """phi and phi_<species> are face fluxes, not cell scalars."""
    per_time = {"phi": [0.0, 5000.0], "phi_CO2": [5000.0]}
    out = _classify_variables(per_time, [0.0, 5000.0], [])
    assert out["phi"]["kind"] == "face_flux"
    assert out["phi_CO2"]["kind"] == "face_flux"


def test_classify_face_flux_does_not_overmatch():
    """A name that just starts with 'phi' but isn't phi/phi_*/phi.* must not
    be misclassified as face flux."""
    assert not _is_face_flux_name("phiphi")  # no _ or . separator
    assert not _is_face_flux_name("phisical_thing")
    assert _is_face_flux_name("phi")
    assert _is_face_flux_name("phi_CO2")


def test_classify_template():
    """Ydefault is a reactingMixture template, not a real cell field."""
    out = _classify_variables({"Ydefault": [0.0]}, [0.0], [])
    assert out["Ydefault"]["kind"] == "template"


def test_classify_diagnostic():
    """Solver-internal diagnostics are flagged."""
    out = _classify_variables(
        {"TabulationResults": [5000.0], "rDeltaT": [5000.0], "cellCpuTimes": [5000.0]},
        [5000.0], [],
    )
    assert out["TabulationResults"]["kind"] == "diagnostic"
    assert out["rDeltaT"]["kind"] == "diagnostic"
    assert out["cellCpuTimes"]["kind"] == "diagnostic"


def test_classify_ic_only_when_only_in_initial_dirs():
    """File present ONLY in 0.orig/initial dirs (no numeric times) → ic_only."""
    per_time = {"G": ["0.orig"]}
    out = _classify_variables(per_time, [], ["0.orig"])
    assert out["G"]["kind"] == "ic_only"


def test_classify_does_not_mark_ic_only_if_in_any_numeric():
    """OpenFOAM keeps unchanged fields available across timesteps without
    rewriting them. A file present in 0/ but not in 5000/ is NOT phantom —
    vtkOpenFOAMReader will still expose it. So we leave it cell_field for
    Tier 4 to verify."""
    per_time = {"G": [0.0]}  # in 0/ only, but 0 is a numeric time
    out = _classify_variables(per_time, [0.0, 5000.0], [])
    assert out["G"]["kind"] == "cell_field"  # NOT ic_only


def test_classify_default_cell_field():
    """Anything without a special pattern → cell_field (refine in Tier 4)."""
    out = _classify_variables({"T": [0.0, 5000.0], "U": [0.0, 5000.0]}, [0.0, 5000.0], [])
    assert out["T"]["kind"] == "cell_field"
    assert out["U"]["kind"] == "cell_field"


def test_classify_evidence_is_present():
    """Every classification must carry an evidence string for traceability."""
    per_time = {"phi": [5000.0], "T": [5000.0], "Ydefault": [0.0]}
    out = _classify_variables(per_time, [0.0, 5000.0], [])
    for name, meta in out.items():
        assert meta.get("evidence")
        assert isinstance(meta["evidence"], str)
        assert len(meta["evidence"]) > 0


# ─── Tier 4 verifier ──────────────────────────────────────────────────────────


def test_array_rank_scalar():
    import numpy as np
    assert _array_rank(np.zeros(10)) == "scalar"


def test_array_rank_vector():
    import numpy as np
    assert _array_rank(np.zeros((10, 3))) == "vector"


def test_array_rank_tensor():
    import numpy as np
    assert _array_rank(np.zeros((10, 6))) == "tensor"
    assert _array_rank(np.zeros((10, 9))) == "tensor"


def test_array_rank_unknown():
    import numpy as np
    assert _array_rank(np.zeros((10, 4))) == "unknown"


def test_verify_promotes_scalar_field():
    """Tier 2 said cell_field; VTK confirms it as scalar → cell_scalar."""
    tier2 = {"T": {"kind": "cell_field", "evidence": "..."}}
    out = _verify_variable_kinds(
        tier2_meta=tier2,
        vtk_cell_arrays=["T"],
        rank_by_name={"T": "scalar"},
    )
    assert out["T"]["physical_kind"] == "physical"
    assert out["T"]["data_shape"] == "cell_scalar"
    assert out["T"]["phantom"] is False


def test_verify_keeps_diagnostic_intent_with_cell_shape():
    """TabulationResults is diagnostically labeled but VTK does store it as a
    cell array. Both labels are kept on different axes — physical_kind stays
    'diagnostic', data_shape becomes 'cell_scalar'. This is NOT a phantom."""
    tier2 = {"TabulationResults": {"kind": "diagnostic", "evidence": "..."}}
    out = _verify_variable_kinds(
        tier2_meta=tier2,
        vtk_cell_arrays=["TabulationResults"],
        rank_by_name={"TabulationResults": "scalar"},
    )
    assert out["TabulationResults"]["physical_kind"] == "diagnostic"
    assert out["TabulationResults"]["data_shape"] == "cell_scalar"
    assert out["TabulationResults"]["phantom"] is False


def test_verify_face_flux_not_in_vtk_is_consistent():
    """phi is correctly absent from VTK cell-array list; not phantom."""
    tier2 = {"phi": {"kind": "face_flux", "evidence": "..."}}
    out = _verify_variable_kinds(
        tier2_meta=tier2, vtk_cell_arrays=[], rank_by_name={},
    )
    assert out["phi"]["physical_kind"] == "face_flux"
    assert out["phi"]["data_shape"] == "not_in_vtk"
    assert out["phi"]["phantom"] is False


def test_verify_phantom_when_tier2_thinks_physical_but_vtk_has_nothing():
    """The alarm case: Tier 2 listed 'foo' as a probable physical field but
    VTK does not expose any cell array. This must surface as phantom=True."""
    tier2 = {"foo": {"kind": "cell_field", "evidence": "..."}}
    out = _verify_variable_kinds(
        tier2_meta=tier2, vtk_cell_arrays=[], rank_by_name={},
    )
    assert out["foo"]["physical_kind"] == "physical"
    assert out["foo"]["data_shape"] == "not_in_vtk"
    assert out["foo"]["phantom"] is True


def test_verify_unknown_rank_does_not_mark_phantom():
    """A field VTK exposes but with rank we don't recognize → cell_unknown,
    not phantom (it's there, just unfamiliar shape)."""
    out = _verify_variable_kinds(
        tier2_meta={"weird": {"kind": "cell_field"}},
        vtk_cell_arrays=["weird"],
        rank_by_name={"weird": "unknown"},
    )
    assert out["weird"]["data_shape"] == "cell_unknown"
    assert out["weird"]["phantom"] is False


def test_verify_works_without_tier2_meta():
    """If Tier 2 forwarded no metadata, we still produce per-name records
    purely from VTK info."""
    out = _verify_variable_kinds(
        tier2_meta=None,
        vtk_cell_arrays=["T", "U"],
        rank_by_name={"T": "scalar", "U": "vector"},
    )
    assert out["T"]["data_shape"] == "cell_scalar"
    assert out["T"]["physical_kind"] == "physical"
    assert out["U"]["data_shape"] == "cell_vector"
    # tier2_kind should be None when no meta available
    assert out["T"]["tier2_kind"] is None
    assert out["U"]["tier2_kind"] is None


def test_verify_records_tier2_kind_for_traceability():
    """Even when Tier 4 confirms a different shape, the original Tier 2
    heuristic kind must be preserved in `tier2_kind` so reviewers can audit."""
    tier2 = {"Ydefault": {"kind": "template", "evidence": "..."}}
    out = _verify_variable_kinds(
        tier2_meta=tier2,
        vtk_cell_arrays=["Ydefault"],
        rank_by_name={"Ydefault": "scalar"},
    )
    assert out["Ydefault"]["tier2_kind"] == "template"
    assert out["Ydefault"]["physical_kind"] == "template"
    assert out["Ydefault"]["data_shape"] == "cell_scalar"
