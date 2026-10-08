"""Resolve local verification resources from an external, optional lab map."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def map_path() -> Path:
    """SM_LAB_MAP overrides the workstation's external map location."""
    return Path(os.environ.get("SM_LAB_MAP", "D:/work/sm_lab_map.json"))


def load_map() -> dict:
    try:
        data = json.loads(map_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def missing_path(key: str, field: str) -> Path:
    """Return an absent path without creating anything on the machine."""
    path = Path(tempfile.gettempdir()) / "__sm_missing_lab_map__" / key / field
    while path.exists():
        path = path.with_name(path.name + ".missing")
    return path


def lab_value(key: str, field: str) -> str:
    entry = load_map().get(key)
    value = entry.get(field) if isinstance(entry, dict) else None
    return value if isinstance(value, str) and value else f"__sm_missing_{key}_{field}__"


def lab_path(key: str, field: str = "chip") -> Path:
    entry = load_map().get(key)
    paths = entry.get("paths") if isinstance(entry, dict) else None
    value = paths.get(field) if isinstance(paths, dict) else None
    return Path(value) if isinstance(value, str) and value else missing_path(key, field)


def archive_roots(key: str) -> list[Path]:
    entry = load_map().get(key)
    values = entry.get("archives") if isinstance(entry, dict) else None
    return [Path(value) for value in values if isinstance(value, str) and value] if isinstance(values, list) else []


def lab_archive(key: str) -> Path:
    """Use the first existing archive; absent resources keep path-gated skips."""
    for path in archive_roots(key):
        if path.exists():
            return path
    return missing_path(key, "archive")
