"""VTK-related shared utilities.

Lazy-imports vtk and numpy_interface; raises clear error if VTK missing.
"""
from __future__ import annotations

from pathlib import Path


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
