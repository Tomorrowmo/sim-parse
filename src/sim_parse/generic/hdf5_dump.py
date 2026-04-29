"""Generic HDF5 inventory: list datasets, attrs, shapes — no semantic interpretation.

Used as Path B fallback when Path A solver-specific identification misses but
the file is an HDF5 of unknown structure.
"""
from __future__ import annotations

from pathlib import Path

from sim_parse.core.errors import never_raise
from sim_parse.core.provenance import (
    FieldProvenance,
    init_tier_output,
    set_field,
)


@never_raise(default=None)
def hdf5_inventory(path: Path, max_depth: int = 4) -> dict | None:
    """Walk an HDF5 tree, return inventory dict.

    Returns:
        {
            "datasets": [{"path": "/group/dataset", "shape": (N,), "dtype": "float64"}, ...],
            "groups": ["/group", ...],
            "root_attrs": {...},
            "_path": "B"
        }
    """
    try:
        import h5py
    except ImportError:
        return None

    path = Path(path)
    out = init_tier_output("B")

    datasets: list[dict] = []
    groups: list[str] = []

    try:
        with h5py.File(path, "r") as f:
            # Root attributes
            root_attrs = _decode_attrs(dict(f.attrs))
            set_field(out, "root_attrs", root_attrs,
                      FieldProvenance("B", "h5py f.attrs"))

            # Walk tree
            def visitor(name: str, obj):
                if name.count("/") > max_depth:
                    return
                if isinstance(obj, h5py.Group):
                    groups.append("/" + name)
                elif isinstance(obj, h5py.Dataset):
                    datasets.append({
                        "path": "/" + name,
                        "shape": list(obj.shape),
                        "dtype": str(obj.dtype),
                        "n_elements": int(obj.size),
                    })

            f.visititems(visitor)

    except OSError as e:
        out["_errors"].append(f"failed to open HDF5 file: {e}")
        return out

    set_field(out, "datasets", datasets,
              FieldProvenance("B", "h5py visititems()"))
    set_field(out, "groups", groups,
              FieldProvenance("B", "h5py visititems()"))
    set_field(out, "n_datasets", len(datasets),
              FieldProvenance("B", "len(datasets)"))

    # Heuristic: identify mesh-like vs field-like groups
    mesh_groups = _identify_mesh_groups(groups, datasets)
    if mesh_groups:
        set_field(out, "mesh_like_groups", mesh_groups,
                  FieldProvenance("B", "heuristic: groups containing coordinates/connectivity"))

    return out


def _decode_attrs(attrs: dict) -> dict:
    """Decode bytes attrs to str for JSON serialization."""
    out = {}
    for k, v in attrs.items():
        if isinstance(v, bytes):
            out[k] = v.decode("utf-8", errors="replace")
        elif hasattr(v, "tolist"):  # numpy array
            out[k] = v.tolist()
        else:
            out[k] = v
    return out


def _identify_mesh_groups(groups: list[str], datasets: list[dict]) -> list[str]:
    """Find groups that look like mesh definitions (have coords + connectivity)."""
    mesh_keywords = {"coordinates", "coords", "points", "vertices", "xyz"}
    conn_keywords = {"connectivity", "elements", "cells", "faces"}

    mesh_groups = []
    # Build path → child name map
    by_parent: dict[str, set] = {}
    for ds in datasets:
        path = ds["path"]
        parent = "/".join(path.split("/")[:-1])
        leaf = path.split("/")[-1].lower()
        by_parent.setdefault(parent or "/", set()).add(leaf)

    for parent, children in by_parent.items():
        has_mesh = any(any(kw in c for kw in mesh_keywords) for c in children)
        has_conn = any(any(kw in c for kw in conn_keywords) for c in children)
        if has_mesh and has_conn:
            mesh_groups.append(parent if parent else "/")

    return mesh_groups
