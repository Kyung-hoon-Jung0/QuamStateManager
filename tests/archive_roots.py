"""Where a lab's run archive lives on the verification machine.

docs/202 §12: the knowledge benchmarks hard-coded ``D:\\work\\dataset\\<lab>``.
The archives moved to ``D:\\work\\Customer_Codes\\dataset\\<lab>`` and that folder
stopped existing, so five benchmark pins failed as "only 42 keys resolved"
(eight AS keys silently dropped) and a real-archive pin quietly SKIPPED as
"AS archive absent" -- all while every archive was on disk. A hard-coded
path is a claim about a machine; this makes it a search, newest location
first, and still answers with a path that does not exist when the archive
truly is absent, so every existing ``skipif(not X.exists())`` keeps working.
"""
from __future__ import annotations

import os
from pathlib import Path

_DRIVE = Path("D:" + os.sep)
ROOTS = (
    _DRIVE / "work" / "Customer_Codes" / "dataset",   # where they live now
    _DRIVE / "work" / "dataset",                      # where they used to
)


def lab_archive(name: str) -> Path:
    """The first root that holds ``name``; the current root when none does."""
    for root in ROOTS:
        candidate = root / name
        if candidate.exists():
            return candidate
    return ROOTS[0] / name
