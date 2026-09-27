"""Queue #8 (2026-09-26): the Datasets sidebar run list defaults to COMPACT.

The user and a second customer both asked for it. ``quam_exp_list_compact``
absent now means compact; only an explicit ``'0'`` (the user pressed "Full
names") brings the wrapped rows back, and the toggle keeps working.

Two halves:
  * the server renders ``<body class="... exp-list-compact">`` and the Compact
    button pressed, so the default paints with no full-name flash before any
    script runs (pinned here on a REAL rendered page);
  * app.js's restore/toggle semantics (pinned in
    ``tests/exp_list_compact_selfcheck.cjs`` against the real app.js).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parents[1]


def test_the_rendered_page_starts_compact(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    html = app.test_client().get("/").get_data(as_text=True)
    body = re.search(r"<body\b[^>]*>", html).group(0)
    assert "exp-list-compact" in body.split('class="', 1)[1].split('"', 1)[0], body
    # queue #8 follow-up: the FIRST thing inside the body is the inline read
    # of the user's choice, so a stored '0' never paints compact (the reverse
    # flash); anything rendered before it would flash for Full-names users.
    after_body = html[re.search(r"<body\b[^>]*>", html).end():]
    m = re.match(r"\s*<script>(.*?)</script>", after_body, re.S)
    assert m, "the compact-class read must be the first element after <body>"
    inline = m.group(1)
    assert "quam_exp_list_compact" in inline and "'0'" in inline and "exp-list-compact" in inline, inline
    assert re.search(r"try\s*\{.*\}\s*catch\s*\(", inline, re.S), "a private window throws on storage access"
    comp = re.search(r'<button[^>]*id="exp-density-compact"[^>]*>', html).group(0)
    full = re.search(r'<button[^>]*id="exp-density-full"[^>]*>', html).group(0)
    assert 'aria-pressed="true"' in comp and 'aria-pressed="false"' in full, (comp, full)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_exp_list_compact_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "exp_list_compact_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 10, proc.stdout
