"""Mapping from VTK XML extension → reader class name.

Centralized so Tier 1/2/3/4 stay consistent on which reader handles
which extension.
"""
from __future__ import annotations


# (extension lowercase, vtk_class_name, vtk_xml_type_attribute)
EXTENSION_READERS: dict[str, tuple[str, str]] = {
    ".vtu":   ("vtkXMLUnstructuredGridReader", "UnstructuredGrid"),
    ".vtp":   ("vtkXMLPolyDataReader",         "PolyData"),
    ".vts":   ("vtkXMLStructuredGridReader",   "StructuredGrid"),
    ".vti":   ("vtkXMLImageDataReader",        "ImageData"),
    ".vtr":   ("vtkXMLRectilinearGridReader",  "RectilinearGrid"),
    ".vtm":   ("vtkXMLMultiBlockDataReader",   "vtkMultiBlockDataSet"),
    ".vtmb":  ("vtkXMLMultiBlockDataReader",   "vtkMultiBlockDataSet"),
    ".pvtu":  ("vtkXMLPUnstructuredGridReader", "PUnstructuredGrid"),
    ".pvtp":  ("vtkXMLPPolyDataReader",        "PPolyData"),
    ".pvts":  ("vtkXMLPStructuredGridReader",  "PStructuredGrid"),
    ".pvti":  ("vtkXMLPImageDataReader",       "PImageData"),
    ".pvtr":  ("vtkXMLPRectilinearGridReader", "PRectilinearGrid"),
}


def reader_for_extension(ext: str):
    """Return (reader_class_or_None, xml_type_string) for a given extension."""
    info = EXTENSION_READERS.get(ext.lower())
    if not info:
        return None, None
    return info[0], info[1]
