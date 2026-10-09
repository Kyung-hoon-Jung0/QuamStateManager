"""S10 walk: the value drawer's footer names what the change ledger counted.

It said "from the change ledger (N events: runs and SM writes)" whatever the
events were -- also on a chip with no data folder, whose history holds only
the states SM saw. It now names the kinds the history holds, with their
counts; Column History's footer says the same.
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

from quam_state_manager.web import routes
from tests.ledger_fixture import no_runs  # noqa: F401
from tests.test_hub_drawer import _inline, chip_state, column, drawer, sm, write_chip  # noqa: F401


def _foot(html: str) -> str:
    m = re.search(r'<p class="(?:fh-foot vh-foot|ch-foot ch-chg-foot)">(.*?)</p>', html, re.S)
    assert m, html[-800:]
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()


def test_a_history_of_states_sm_saw_says_so(no_runs):
    app = no_runs["app"]
    with app.app_context():
        live = Path(routes._active_ctx()["path"])
        hm = routes._history()
        assert hm.check_and_snapshot(live, "manual", force=True)
        write_chip(live, chip_state(t1=4e-5), None)
        assert hm.check_and_snapshot(live, "auto", force=True)
    foot = _foot(drawer(no_runs, "qubits.qA1.T1"))
    assert "from the change ledger (2 events: 2 states SM saw)" in foot, foot
    assert "runs and SM writes" not in foot


def test_runs_and_an_sm_write_are_each_counted(sm):
    c = sm["client"]
    assert "from the change ledger (4 events: 4 runs)" in _foot(drawer(sm, "qubits.qA1.T1"))
    assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4.5e-5"}).status_code == 200
    assert c.post("/state/apply-to-live").status_code == 200
    foot = _foot(drawer(sm, "qubits.qA1.T1"))
    assert re.search(r"from the change ledger \(\d+ events: 4 runs, 1 SM write(, 1 state SM saw)?\)", foot), foot
    col = _foot(column(sm, {"qA1": "qubits.qA1.T1"}))
    assert re.search(r"from the change ledger \(\d+ events: 4 runs, 1 SM write(, 1 state SM saw)?\)", col), col
