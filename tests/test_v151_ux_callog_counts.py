"""P1-4 (U08): the Calibration log's day count says whose writes it counts.

"Day totals ... 0 writes" on a day the lab changed the chip: the count was
SM's own write cards only. It now reads "SM writes", and beside it "values
changed by runs: N (writer not proven)" -- the sum of the day's run cards'
changed values.
"""

from __future__ import annotations

import re

from quam_state_manager.core import story
from tests.test_journal_page import world  # noqa: F401  (fixture)
from tests.test_story import DAY


def _run(rows, **kw):
    return {"kind": "run", "outcome": "successful", "writes": [{"path": p, "old": 0, "new": 1} for p in rows], **kw}


def test_the_count_adds_the_values_runs_changed():
    cards = [_run(["qubits.q1.f_01", "qubits.q1.T1"]), _run(["qubits.q2.f_01"]),
             _run(["qubits.q3.f_01"], other_chip=True),          # another chip's run: not counted
             {"kind": "write", "entries": [{"path": "qubits.q1.x", "old": 1, "new": 2}]}]
    c = story._counts(cards)
    assert c["run_values"] == 3
    assert c["write_cards"] == 1 and c["writes"] == 1          # SM's own, unchanged
    assert story._counts([])["run_values"] == 0


def test_the_day_pills_name_sm_writes_and_run_changes(world):  # noqa: F811
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    counts = body[body.index('class="jr-counts"'):]
    counts = counts[:counts.index("</div>")]
    assert re.search(r'class="jr-stat jr-stat-sm"[^>]*><b>\d+</b> SM writes?', counts), counts
    m = re.search(r'class="jr-stat jr-stat-runvals"[^>]*><b>(\d+)</b> values? changed by runs '
                  r'<span class="muted">\(writer not proven\)</span>', counts)
    assert m, counts
    assert not re.search(r"<b>\d+</b> writes?</span>", counts), "a bare 'writes' count is gone"
