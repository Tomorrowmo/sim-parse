"""Shared pytest fixtures."""
import os
from pathlib import Path

import pytest


# Absolute path to the DLR_A_cavity test case (Phase 1 verification).
# Tests using this fixture skip if the path is missing (e.g. CI).
DLR_A_CAVITY_PATH = Path(r"D:\Git\SimGraph2\test_data\DLR_A_combustion\DLR_A_cavity")


@pytest.fixture
def dlr_a_cavity():
    """The DLR-A standard CH4-H2 jet flame OpenFOAM case (reactingFoam, v2212)."""
    if not DLR_A_CAVITY_PATH.exists():
        pytest.skip(f"DLR_A_cavity not present at {DLR_A_CAVITY_PATH}")
    return DLR_A_CAVITY_PATH


AIRFOIL2D_PATH = Path(r"D:\Git\SimGraph2\test_data\airFoil2D\airFoil2D")


@pytest.fixture
def airfoil2d_coldflow():
    """OpenFOAM cold-flow simpleFoam case with .gz-compressed polyMesh and a
    note-less owner header. Regression fixture for the Tier 3 extraction
    fixes (boundary.gz fallback + owner array-length fallback).
    """
    if not AIRFOIL2D_PATH.exists():
        pytest.skip(f"airFoil2D not present at {AIRFOIL2D_PATH}")
    return AIRFOIL2D_PATH


# Optional CFF (Fluent V19+ HDF5 .cas.h5) fixture. Drop a real Fluent-
# generated .cas.h5 here to enable end-to-end CFF coverage. Default path
# is intentionally explicit so the team can share it; override via the
# SIM_PARSE_CFF_FIXTURE env var if your case lives elsewhere.
FLUENT_CFF_PATH = Path(
    os.environ.get("SIM_PARSE_CFF_FIXTURE")
    or r"D:\Git\SimGraph2\test_data\fluent_cff\case.cas.h5"
)


@pytest.fixture
def fluent_cff_case():
    """Real Fluent CFF (.cas.h5) case for end-to-end Tier 3/4 testing.

    Auto-skips when no real fixture is present. To enable:
      - drop a real .cas.h5 (+ optional .dat.h5) at the default path, OR
      - export SIM_PARSE_CFF_FIXTURE=/abs/path/to/case.cas.h5

    Tier 1 detection is independently tested with synthetic HDF5-magic
    bytes in tests/unit/test_fluent_tier1.py — this fixture is for the
    full vtkFLUENTCFFReader pipeline which needs a structurally valid file.
    """
    if not FLUENT_CFF_PATH.exists():
        pytest.skip(
            f"No real CFF fixture at {FLUENT_CFF_PATH}; set SIM_PARSE_CFF_FIXTURE "
            f"or place a .cas.h5 there to enable CFF end-to-end tests."
        )
    return FLUENT_CFF_PATH


@pytest.fixture
def empty_dir(tmp_path):
    """An empty directory (for negative-case tests: no controlDict, no nothing)."""
    d = tmp_path / "empty_case"
    d.mkdir()
    return d


@pytest.fixture
def fake_hdf5_file(tmp_path):
    """Create a tiny fake HDF5 file (requires h5py)."""
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "fake.h5"
    with h5py.File(path, "w") as f:
        grp = f.create_group("mesh")
        grp.create_dataset("coordinates", data=[[0, 0, 0], [1, 0, 0], [0, 1, 0]])
        grp.create_dataset("connectivity", data=[[0, 1, 2]])
        sol = f.create_group("Solution")
        sol.create_dataset("T", data=[300.0, 305.0, 310.0])
        f.attrs["creator"] = "FakeSolver v1.0"
    return path
