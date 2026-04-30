"""VTK-related shared utilities.

Lazy-imports vtk and numpy_interface; raises clear error if VTK missing.

Dual-backend support:
    Some company-internal Python environments include `vtkRomtekIODriver`
    as part of an extended VTK build (see PostDrive's underlying C++
    binding). When available it can be faster / more compatible on certain
    formats than the standard VTK readers. `load_via_romtek()` lets each
    solver attempt the Romtek path first and silently fall back to the
    standard reader on any failure, with no PostDrive Python dependency.

    External users (no Romtek) get the standard VTK behavior unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path

from sim_parse.core.errors import never_raise


def require_vtk():
    """Lazy-import vtk + dataset_adapter. Returns (vtk, dsa)."""
    try:
        import vtk
        from vtkmodules.numpy_interface import dataset_adapter as dsa
    except ImportError as e:
        raise RuntimeError(
            "VTK is required for Tier 4+; install with `pip install vtk` "
            "or `pip install sim-parse[fields]`"
        ) from e
    return vtk, dsa


def ensure_foam_marker(case_root: Path) -> Path:
    """vtkOpenFOAMReader needs a .foam marker file. Create if missing.

    Returns path to the marker file.
    """
    case_root = Path(case_root)
    marker = case_root / f"{case_root.name}.foam"
    if not marker.exists():
        marker.touch()
    return marker


def iterate_blocks(multi_block):
    """Yield non-None leaf datasets from a vtkMultiBlockDataSet (recursive)."""
    if multi_block is None:
        return
    if hasattr(multi_block, "GetNumberOfBlocks"):
        for i in range(multi_block.GetNumberOfBlocks()):
            child = multi_block.GetBlock(i)
            if child is None:
                continue
            if hasattr(child, "GetNumberOfBlocks"):
                yield from iterate_blocks(child)
            else:
                yield child
    else:
        yield multi_block


# ─── Dual-backend reader support (Romtek + standard VTK fallback) ─────────────


# Map sim-parse format name → the vtkRomtekIODriver reader-name string that
# can read it. None means "Romtek doesn't have a dedicated reader for this
# format; always use standard VTK". Tested against vtkRomtekIODriver's
# getSupporReaders() in the PostProcessTool conda env.
ROMTEK_READER_FOR = {
    "cgns":            "CGNSReader",
    "openfoam":        "OpenFoamReader",
    "fluent_legacy":   "VTKFluentCasDataReader",
    "tecplot":         "TecplotReader",        # ASCII Tecplot — binary versions
                                               # have known crashes; tecplot
                                               # solver guards binary separately
    "ensight_gold":    "EnsightReader",
    "vtu":             "VTKVTUReader",
    "vtm":             "VTKVTMReader",
    "vts":             "VTKVTSReader",
    "vtp":             "VTKVTPReader",
    "plot3d":          "Plot3DReader",
    "lsdyna":          "VTKD3PlotReader",
    # Not in Romtek (none): fluent_cff (.cas.h5), starccm_sim, abaqus_*, ...
}

# Environment-variable kill switch — set SIMPARSE_USE_ROMTEK=0 to force
# standard VTK fallback even when Romtek is available. Useful for A/B
# testing performance / output equivalence between backends.
_ROMTEK_DISABLED_ENV = "SIMPARSE_USE_ROMTEK"


def _romtek_enabled() -> bool:
    val = os.environ.get(_ROMTEK_DISABLED_ENV, "1").strip().lower()
    return val not in ("0", "false", "no", "off")


@never_raise(default=None)
def load_via_romtek(file_paths, romtek_reader_name: str):
    """Try loading via vtkRomtekIODriver. Returns vtkMultiBlockDataSet or None.

    None on any failure including:
      - vtkRomtekIODriver not in this VTK build (running outside PostProcessTool)
      - SIMPARSE_USE_ROMTEK=0 set (kill switch)
      - Reader name not supported by this Romtek build
      - File can't be parsed by Romtek
      - Result has 0 blocks (empty / corrupted)

    Caller should fall back to standard VTK reader on None return.

    Args:
        file_paths: list of file paths (most readers want 1; Plot3D wants
                    [.xyz, .q]; Fluent wants [.cas, .dat]).
        romtek_reader_name: e.g. "CGNSReader". See ROMTEK_READER_FOR for
                            sim-parse format name → Romtek name mapping.

    Direct binding to vtkRomtekIODriver C++ class — does NOT use PostDrive's
    Python wrapper. This insulates sim-parse from PostDrive's interface
    quirks (incomplete suffix dispatch, no error contract, etc.) while
    still benefiting from the underlying Romtek readers.
    """
    if not _romtek_enabled():
        return None
    try:
        import vtk
    except ImportError:
        return None
    if not hasattr(vtk, "vtkRomtekIODriver"):
        return None

    driver = vtk.vtkRomtekIODriver()
    if not driver.isSupportReader(romtek_reader_name):
        return None
    paths = [str(p) for p in file_paths]
    driver.ReadFiles(paths, romtek_reader_name, False)
    out = driver.getOutPut()
    if out is None:
        return None
    if hasattr(out, "GetNumberOfBlocks") and out.GetNumberOfBlocks() == 0:
        return None
    return out


def iterate_named_blocks(multi_block, _path: tuple[str, ...] = ()):
    """Yield (path_tuple, leaf_block) for every leaf, where path_tuple is the
    chain of block names from root to leaf.

    Block names come from the vtkInformation slot
    `vtkCompositeDataSet.NAME()`. Anonymous blocks (no name set) get an empty
    string for that slot.
    """
    try:
        from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet
        name_key = vtkCompositeDataSet.NAME()
    except Exception:
        name_key = None

    if multi_block is None:
        return
    if hasattr(multi_block, "GetNumberOfBlocks"):
        n = multi_block.GetNumberOfBlocks()
        for i in range(n):
            child = multi_block.GetBlock(i)
            if child is None:
                continue
            name = ""
            if name_key is not None:
                try:
                    info = multi_block.GetMetaData(i)
                    if info is not None and info.Has(name_key):
                        name = info.Get(name_key) or ""
                except Exception:
                    pass
            sub_path = _path + (name,)
            if hasattr(child, "GetNumberOfBlocks"):
                yield from iterate_named_blocks(child, sub_path)
            else:
                yield sub_path, child
    else:
        yield _path, multi_block
