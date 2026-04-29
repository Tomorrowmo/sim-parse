"""Pure-Python parser for the EnSight Gold .case ASCII control file.

Spec reference: EnSight User Manual §11.1 "Case File Format". We don't
re-implement the full grammar — only the bits sim-parse cares about
(model, variables, time set if present).
"""
from __future__ import annotations

from pathlib import Path


def parse_case_file(case_file: Path) -> dict:
    """Read an EnSight Gold .case ASCII file and extract structured info.

    Returns:
        {
          "format_type": str,                    # 'ensight gold' / 'ensight 6'
          "model_file": str | None,              # geometry file (relative to .case)
          "variables": [
              {"role": "scalar"|"vector"|"tensor",
               "scope": "per_node"|"per_element",
               "name": str,                      # field name (e.g. 'Pressure')
               "data_file": str},                # field data file
          ],
          "time_set": {                          # optional
              "n_steps": int,
              "filename_start_number": int,
              "filename_increment": int,
              "time_values": [float, ...],
          } | None,
          "raw_sections": {section_name: section_text},
        }
    """
    out = {
        "format_type": None,
        "model_file": None,
        "variables": [],
        "time_set": None,
        "raw_sections": {},
    }
    try:
        text = case_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out

    # Strip line-leading comments (# ...)
    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        cleaned.append(stripped)

    # Parse into top-level sections by all-caps keyword headers.
    # FORMAT / GEOMETRY / VARIABLE / TIME / FILE
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in cleaned:
        upper = line.split()[0].upper() if line.split() else ""
        if upper in ("FORMAT", "GEOMETRY", "VARIABLE", "TIME", "FILE"):
            current = upper
            sections.setdefault(current, [])
            # Sometimes the keyword line has no content beyond it
            rest = line[len(upper):].strip()
            if rest:
                sections[current].append(rest)
            continue
        if current is not None:
            sections[current].append(line)

    out["raw_sections"] = {k: "\n".join(v) for k, v in sections.items()}

    # FORMAT
    for line in sections.get("FORMAT", []):
        if line.lower().startswith("type:"):
            out["format_type"] = line.split(":", 1)[1].strip()

    # GEOMETRY
    for line in sections.get("GEOMETRY", []):
        if line.lower().startswith("model:"):
            tokens = line.split(":", 1)[1].split()
            # Last token is the filename; preceding tokens are time set ids etc.
            if tokens:
                out["model_file"] = tokens[-1]

    # VARIABLE
    # Lines look like:
    #   scalar per element:    Pressure  bin.Pressure
    #   vector per node:       Velocity  bin.Velocity
    for line in sections.get("VARIABLE", []):
        lower = line.lower()
        # Match the role + scope prefix
        for role in ("scalar", "vector", "tensor symm", "tensor asym",
                     "complex scalar", "complex vector"):
            for scope in ("per node", "per element", "per measured node",
                          "per measured element"):
                prefix = f"{role} {scope}"
                if lower.startswith(prefix):
                    rest = line[len(prefix):]
                    # Drop trailing colon if present
                    rest = rest.lstrip(":").strip()
                    tokens = rest.split()
                    # Last token is the data file; preceding tokens compose the name
                    # (which can include numbers if there's a timeset id)
                    if len(tokens) >= 2:
                        data_file = tokens[-1]
                        # Sometimes layout is `[timeset_id] <name> <file>`
                        # so take all middle tokens as the name
                        name_tokens = tokens[:-1]
                        # If first token is purely numeric, drop it (timeset id)
                        if name_tokens and name_tokens[0].isdigit():
                            name_tokens = name_tokens[1:]
                        name = " ".join(name_tokens) if name_tokens else "(unnamed)"
                        out["variables"].append({
                            "role": role.replace(" ", "_"),
                            "scope": scope.replace(" ", "_"),
                            "name": name,
                            "data_file": data_file,
                        })
                    break

    # TIME (optional)
    if "TIME" in sections:
        ts = {"n_steps": None, "filename_start_number": None,
              "filename_increment": None, "time_values": []}
        for line in sections["TIME"]:
            lower = line.lower()
            if lower.startswith("number of steps:"):
                try:
                    ts["n_steps"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif lower.startswith("filename start number:"):
                try:
                    ts["filename_start_number"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif lower.startswith("filename increment:"):
                try:
                    ts["filename_increment"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif lower.startswith("time values:") or lower.startswith("time set:"):
                continue  # the actual values are on subsequent lines
            else:
                # try to read floats
                for tok in line.split():
                    try:
                        ts["time_values"].append(float(tok))
                    except ValueError:
                        pass
        if any(v is not None for v in (ts["n_steps"], ts["filename_start_number"])) \
                or ts["time_values"]:
            out["time_set"] = ts

    return out
