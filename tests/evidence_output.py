"""External output paths for standalone mutation runners.

Set SM_EVIDENCE_DIR to choose an output directory; otherwise use system temp.
"""
import os
from pathlib import Path
import tempfile


def evidence_path(name):
    target = (Path(os.environ.get("SM_EVIDENCE_DIR") or tempfile.gettempdir()) / name).resolve()
    root = Path(__file__).resolve().parents[1]
    if target.is_relative_to(root):
        raise ValueError("Evidence output must be outside the repository")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target
