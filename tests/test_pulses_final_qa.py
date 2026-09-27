"""Pulses final QA (2026-09-28): the UI pins (tests/pulses_final_qa_selfcheck.cjs
-- real htmx + real app.js under jsdom) and the worker's process-state warning.
"""
from __future__ import annotations

import dataclasses
import re
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def test_the_env_strip_syncs_against_itself():
    """P2: the strip's requests must not inherit #inspector-pane's
    hx-sync="this:replace" -- that made every 2 s poll tick abort an in-flight
    POST /api/pulse/create in the browser."""
    tpl = (_ROOT / "quam_state_manager" / "web" / "templates" / "_pulse_env_strip.html").read_text(encoding="utf-8")
    tag = re.search(r'<div id="pulse-env-strip"[^>]*>', tpl).group(0)
    m = re.search(r'hx-sync="([^"]*)"', tag)
    assert m and m.group(1).startswith("this:"), tag


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pulses_final_qa_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "pulses_final_qa_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 10, proc.stdout


class TestTheWarmWorkerDrawsLikeAColdOne:
    """P3a: quam's "This component is not part of any QuamRoot, using last
    instantiated QuamRoot ..." describes the worker PROCESS (a root left by an
    earlier draw), not the pulse; a warm worker showed it, a cold one did not."""

    def _fake_pulse_class(self, monkeypatch, messages):
        rpw = pytest.importorskip("quam_state_manager.generator.run_pulse_waveform")
        try:
            from quam.components.pulses import Pulse
        except Exception:  # noqa: BLE001
            pytest.skip("quam not importable here")

        @dataclasses.dataclass
        class FakeLabPulse(Pulse):
            amplitude: float = 0.1

            def calculate_waveform(self):
                for msg in messages:
                    warnings.warn(msg)
                return [self.amplitude] * 4

        FakeLabPulse.__module__ = "fakelab.pulses"
        monkeypatch.setattr(rpw, "_import_class", lambda q: FakeLabPulse)
        return rpw

    def test_the_root_warning_is_dropped_and_the_others_kept(self, monkeypatch):
        rpw = self._fake_pulse_class(monkeypatch, [
            "This component is not part of any QuamRoot, using last instantiated QuamRoot (Quam)",
            "not attached to a channel, cannot determine sampling rate",
        ])
        rec = rpw._draw_one("fakelab.pulses.FakeLabPulse", {"length": 16, "amplitude": 0.2}, 1000)
        assert rec["ok"] is True, rec
        assert rec["warnings"] == ["not attached to a channel, cannot determine sampling rate"], rec["warnings"]

    def test_a_clean_draw_has_no_warnings(self, monkeypatch):
        rpw = self._fake_pulse_class(monkeypatch, [])
        rec = rpw._draw_one("fakelab.pulses.FakeLabPulse", {"length": 16}, 1000)
        assert rec["ok"] is True and rec["warnings"] == []
