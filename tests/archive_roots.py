"""Resolve archive keys through SM_LAB_MAP, outside the repository.

The map holds local archive paths and environment metadata. Missing maps,
keys, or resources produce absent paths, preserving existing skip gates.
"""
from quam_state_manager.core.lab_map import lab_archive, lab_path, lab_value

__all__ = ["lab_archive", "lab_path", "lab_value"]
