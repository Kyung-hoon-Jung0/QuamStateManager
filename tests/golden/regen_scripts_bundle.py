"""Regenerate ``tests/golden/scripts_bundle_cz/`` after an INTENTIONAL
emitter change.

``test_script_emitter.test_golden_bundle_stable`` pins the emitted build
bundle byte-for-byte (same spec + stamp -> identical files), so any deliberate
change to ``script_emitter`` -- e.g. a new name in ``_RUNTIME_FUNCS`` that the
recipe must inline -- drifts the golden and must be re-recorded here, then
reviewed in ``git diff`` so the ONLY drift is the intended one.

Run from the repo root::

    python tests/golden/regen_scripts_bundle.py

Files are written with LF line endings so a regeneration diffs as content,
never as line-ending churn.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # tests/golden
sys.path.insert(0, str(HERE.parent))              # tests/ -> the test helpers
sys.path.insert(0, str(HERE.parent.parent))       # repo root -> quam_state_manager

import test_script_emitter as tse  # noqa: E402  (the bundle builder lives there)

out = HERE / "scripts_bundle_cz"
out.mkdir(exist_ok=True)
for name, src in tse._bundle().items():
    with open(out / name, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(src)
    print("wrote", out / name)
