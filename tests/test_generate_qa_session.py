"""Drives tests/generate_qa_session_selfcheck.cjs under node + jsdom.

Pins the Generate-Config wizard session findings from the 2026-09 real-browser
QA pass (package gen-session): the scripts folder keeps following the output
folder across a reload (F2), F5 keeps the current step's edits (F1), a refused
press brings its reason into view and a failed allocation names it at the
button (F3), a refused Generate replaces the stale success box (F3b), Reset
keeps the highlighted env as the real selection (F4), and QDAC instrument edits
reach the current spec after Reset / hydrate (F5). Chunk 2: leaving a dirty
Re-generate session asks first (F6), Re-generate never reads or clears the
Generate draft (F7), Reset on the Re-generate page re-fills from the source
chip through the page's own script (F8), a header Generate press answers in
view with both buttons busy (F10), and the scripts export means what the box
says (regenerate-r2-18). Chunk 4: "Load into app" retires the finished draft
(generate-r2-14), step 1 refuses an env that probed "missing"
(generate-r2-18), Review flags a relative folder (F21), the user's own
re-generate hands its report back (F20), and a source changed under the
wizard is named before the build (regenerate-r2-35). Review follow-ups: a
Re-generate build is never the next plain mount's own (the swap asks, the
answer is named, the plain draft records nothing), a failed request lands in
the slot on screen and closes the draft's pending record, and a plain build
stranded by a Re-generate hydrate hands its outcome to the plain draft.

Skips when node or jsdom is unavailable (the selfcheck exits 2 for a missing
jsdom). Install once with ``npm install``.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_qa_session_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_qa_session_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
