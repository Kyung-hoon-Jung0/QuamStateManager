"""Drives the inline-edit commit behavioral check
(tests/pulses_commit_selfcheck.cjs) under node + jsdom, against the REAL
shipped app.js, plus the source/template pins that go with it.

Pins docs/75: a commit's own #inspector-pane re-render fires focusout on the
input it removes, and re-submitting there both double-commits and (because
htmx drops the duplicate WITHOUT preventing the event's default action) hands
the browser a native form submission — a full-page navigation carrying the
edit in the query string, which surfaced as the "Leave site?" prompt on every
Pulses parameter edit. Also pins the focus/caret/scroll restore that keeps
Enter working across the re-render, and the debounced refresh triggers.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "pulses_commit_selfcheck.cjs"
_APP_JS = _ROOT / "quam_state_manager" / "web" / "static" / "app.js"
_PULSES_HTML = _ROOT / "quam_state_manager" / "web" / "templates" / "_pulses.html"
_BASE_HTML = _ROOT / "quam_state_manager" / "web" / "templates" / "base.html"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_pulses_commit_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
        timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


class TestInlineCommitSource:
    """Source-level pins: the guards must stay wired even if app.js is
    refactored (they are invisible in a screenshot but load-bearing)."""

    def test_focusout_commit_is_guarded_by_the_in_flight_check(self):
        src = _APP_JS.read_text(encoding="utf-8")
        # the guard must sit BEFORE the requestSubmit inside the focusout handler
        m = re.search(r'document\.addEventListener\("focusout".*?\n\}\);', src, re.S)
        assert m, "the inline-edit focusout commit handler is gone"
        body = m.group(0)
        assert "InlineCommit.inFlight(form)" in body, body
        assert body.index("inFlight(form)") < body.index("requestSubmit()"), body

    def test_in_flight_reads_both_htmx_and_own_marker(self):
        src = _APP_JS.read_text(encoding="utf-8")
        assert 'contains("htmx-request")' in src
        assert 'dataset.committing === "1"' in src
        # the lifecycle listeners that maintain our own marker
        assert 'htmx:beforeRequest' in src and 'htmx:afterRequest' in src

    def test_htmx_owned_forms_never_submit_natively(self):
        src = _APP_JS.read_text(encoding="utf-8")
        m = re.search(r'document\.addEventListener\("submit", function \(evt\).*?\n\}\);',
                      src, re.S)
        assert m, "the native-submission armor is gone"
        body = m.group(0)
        assert 'getAttribute("hx-post")' in body and 'getAttribute("hx-get")' in body
        assert "evt.preventDefault()" in body
        # without htmx the native submission stays the fallback
        assert "if (!window.htmx) return;" in body

    def test_focus_restore_runs_on_inspector_pane_settle(self):
        src = _APP_JS.read_text(encoding="utf-8")
        assert 'evt.target.id === "inspector-pane"' in src
        assert "InlineCommit.afterSwap()" in src
        # multiple passes — one write is clamped by the not-yet-final height
        assert "RESTORE_PASSES_MS" in src


class TestRefreshDebouncePins:
    """The per-commit refresh fan-out is coalesced, not removed (docs/75)."""

    def test_pulses_table_refresh_is_debounced(self):
        html = _PULSES_HTML.read_text(encoding="utf-8")
        assert 'hx-trigger="pulses-changed[!(event.detail && event.detail.paths)] from:body delay:400ms"' in html

    def test_diagnostics_refresh_is_debounced(self):
        html = _BASE_HTML.read_text(encoding="utf-8")
        hits = re.findall(r'hx-trigger="load, diagnostics-changed from:body[^"]*"', html)
        assert hits, "the diagnostics slots lost their trigger"
        for spec in hits:
            assert "delay:" in spec, spec
