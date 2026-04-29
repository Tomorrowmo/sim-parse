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
