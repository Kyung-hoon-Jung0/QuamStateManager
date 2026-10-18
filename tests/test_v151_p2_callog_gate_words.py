"""v1.5.1 U P2-25: Calibration log words while a gate is checked, and the
"SM writes" pill explained.

A run row read "gate ..." while its gate was still being computed in the
background; it now reads "checking gate". The day's "N SM writes" pill carries
its explanation (title), not a bare number. Pinned on the REAL day markup.
"""

from __future__ import annotations

import re

from quam_state_manager.core import story
from tests.test_calibration_log_hub import DAY, _run_event, world  # noqa: F401 -- fixture


def test_a_pending_gate_reads_checking_gate(world, monkeypatch):  # noqa: F811
    world["app"].config["JOURNAL_GATE_WAIT"] = False
    monkeypatch.setattr(story, "_compute_gate",
                        lambda run: {"verdict": "pass", "reason": "checked", "family": None, "anchor": "run"})
    _run_event(world, {"v": 1}, clock="11:00:00", run_id=1)
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    badge = re.search(r'<span class="jr-badge jr-gate-pending" title="([^"]+)">([^<]+)</span>', html)
    assert badge, html[:3000]
    assert badge.group(2) == "checking gate" and "being checked" in badge.group(1), badge.groups()
    assert "gate ...</span>" not in html
    pill = re.search(r'<span class="jr-stat jr-stat-sm" title="([^"]+)">', html)
    assert pill and "Writes SM made to the chip" in pill.group(1), "the SM writes pill says what it counts"
