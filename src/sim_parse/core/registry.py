"""Solver registry: per-solver Path A implementations are registered here.

Each solver provides a set of tier functions:
    - identify(case_root)        -> dict   (Tier 1)
    - inventory(case_root, identity) -> dict (Tier 2)
    - metadata(case_root, identity, inventory) -> dict (Tier 3)
    - field_stats(case_root, identity, ...)    -> dict (Tier 4)
    - qoi(case_root, ...)        -> list   (Tier 5)
    - export_vtu(...)            -> Path   (Tier 6)

Built-in solvers (openfoam, fluent, ...) register on import via
    sim_parse/solvers/<name>/__init__.py.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

# format-name → bundle of tier functions
_REGISTRY: dict[str, dict[str, Callable]] = {}


def register_solver(
    name: str,
    *,
    identify: Callable | None = None,
    inventory: Callable | None = None,
    metadata: Callable | None = None,
    field_stats: Callable | None = None,
    qoi: Callable | None = None,
    export_vtu: Callable | None = None,
    priority: int = 50,
) -> None:
    """Register a solver's tier functions. Call from solver __init__.py.

    Args:
        name: format-level name ("openfoam", "fluent", "cgns", ...)
        identify, inventory, ...: callables for each tier
        priority: higher = checked first when multiple solvers claim a case
    """
    bundle: dict[str, Any] = {"_priority": priority}
    if identify is not None:
        bundle["identify"] = identify
    if inventory is not None:
        bundle["inventory"] = inventory
    if metadata is not None:
        bundle["metadata"] = metadata
    if field_stats is not None:
        bundle["field_stats"] = field_stats
    if qoi is not None:
        bundle["qoi"] = qoi
    if export_vtu is not None:
        bundle["export_vtu"] = export_vtu
    _REGISTRY[name] = bundle


def register_parser(name: str, priority: int = 50):
    """Public decorator API for user plugins (Path D).

    Usage:
        @register_parser("my_solver", priority=80)
        class MyParser:
            def identify(self, path): ...
            def metadata(self, path): ...
    """
    def decorator(cls):
        instance = cls()
        register_solver(
            name,
            identify=getattr(instance, "identify", None),
            inventory=getattr(instance, "inventory", None),
            metadata=getattr(instance, "metadata", None),
            field_stats=getattr(instance, "field_stats", None),
            qoi=getattr(instance, "qoi", None),
            export_vtu=getattr(instance, "export_vtu", None),
            priority=priority,
        )
        return cls
    return decorator


def get_solver(name: str) -> dict | None:
    """Look up a registered solver by format name."""
    return _REGISTRY.get(name)


def list_solvers() -> list[str]:
    """List all registered solver names, ordered by priority (high → low)."""
    return sorted(_REGISTRY.keys(), key=lambda n: -_REGISTRY[n].get("_priority", 50))


def all_solvers_ordered() -> list[tuple[str, dict]]:
    """For dispatcher: iterate solvers in priority order."""
    return sorted(
        _REGISTRY.items(),
        key=lambda kv: -kv[1].get("_priority", 50),
    )
