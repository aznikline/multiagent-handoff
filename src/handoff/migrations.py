"""Schema version migration framework for ContextPackage.

Supports forward-compatible deserialization and explicit migrations
between known schema versions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

MIGRATION_REGISTRY: dict[tuple[str, str], Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_migration(
    from_version: str, to_version: str
) -> Callable[[Callable[[dict[str, Any]], dict[str, Any]]], Callable[[dict[str, Any]], dict[str, Any]]]:
    """Decorator to register a migration function between two schema versions.

    Example:
        @register_migration("1.0", "1.1")
        def migrate_v1_0_to_v1_1(data: dict) -> dict:
            data["meta"]["new_field"] = "default"
            return data
    """

    def decorator(
        fn: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        MIGRATION_REGISTRY[(from_version, to_version)] = fn
        return fn

    return decorator


def migrate(data: dict[str, Any], target_version: str = "1.0") -> dict[str, Any]:
    """Migrate a raw dictionary to the target schema version.

    Args:
        data: Raw deserialized dictionary.
        target_version: Desired schema version.

    Returns:
        Migrated dictionary.

    Raises:
        ValueError: If no migration path exists.
    """
    current = data.get("meta", {}).get("spec_version", "1.0")
    if current == target_version:
        return data

    # Simple direct migration lookup
    key = (current, target_version)
    if key in MIGRATION_REGISTRY:
        migrated = deepcopy(data)
        return MIGRATION_REGISTRY[key](migrated)

    raise ValueError(
        f"No migration path from schema {current} to {target_version}"
    )


@register_migration("1.0", "1.1")
def _migrate_1_0_to_1_1(data: dict[str, Any]) -> dict[str, Any]:
    """Example migration: add security.classification if missing."""
    if "security" not in data:
        data["security"] = {}
    if "classification" not in data["security"]:
        data["security"]["classification"] = "internal"
    data["meta"]["spec_version"] = "1.1"
    return data
