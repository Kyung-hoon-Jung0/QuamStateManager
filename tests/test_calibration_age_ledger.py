"""docs/301 F11 -- Calibration Age reads the change ledger, not only stamps.

A qubit's ``last_calibrated`` was the newest ``*_updated_at`` stamp in its
state subtree. A lab whose nodes stamp nothing (T1, Ramsey, ...) showed
"8 days ago" for a chip re-measured the day before, while the ledger knew
the run whose saved state moved those values. Now each entity takes the
newer of its stamp and its last run change, and says which one it is.

Fixtures: ``test_hub_drawer``'s chip (run #1 the starting state; #2 moves
qA1.T1 by its own patch; #3 moves qA1.f_01 with no patch; #4 retargets
qA1's x180) -- qA2 never changes after the starting state.
"""

from __future__ import annotations

import json
import re
import time

from quam_state_manager.core import hub_sync
from quam_state_manager.web import routes
from tests.test_hub_chip_status import load
from tests.test_hub_drawer import T0, _inline, chip_state, iso, run, sm  # noqa: F401 -- fixtures


def ms(rid: int) -> int:
    return (T0 + rid * 10_000_000) // 1000


def topo(env) -> dict:
    r = env["client"].get("/api/topology")
    assert r.status_code == 200, r.data[:300]
    return r.get_json()


def node(t, qid) -> dict:
    return next(n for n in t["nodes"] if n["id"] == qid)


class TestTheLedgerDatesAQubit:
    def test_the_newest_run_change_dates_the_qubit(self, sm):
        t = topo(sm)
        a1 = node(t, "qA1")
        assert a1["last_calibrated"] == ms(4) and a1["last_calibrated_from"] == "run"
        assert a1["last_run_change"] == ms(4)
        assert t["summary"]["newest_calibration"] == ms(4) == t["summary"]["oldest_calibration"]

    def test_the_starting_state_is_not_a_calibration(self, sm):
        a2 = node(topo(sm), "qA2")
        assert a2.get("last_calibrated") is None and "last_calibrated_from" not in a2

    def test_an_sm_write_is_not_a_calibration(self, sm):
        c = sm["client"]
        assert c.post("/field/edit", data={"dot_path": "qubits.qA2.T1", "value": "4.5e-5"},
                      headers={"X-SM-Actor": "operator"}).status_code == 200
        assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "operator"}).status_code == 200
        assert node(topo(sm), "qA2").get("last_calibrated") is None

    def test_a_state_sm_only_saw_is_not_a_calibration(self, sm):
        """An edit made outside SM, seen by a Param History snapshot between
        runs, is an ``observed`` event: a change nobody is known to have
        measured. The next run carries it on unchanged."""
        live = sm["live"]
        st = json.loads((live / "state.json").read_text(encoding="utf-8"))
        st["qubits"]["qA2"]["T1"] = 7.0e-5
        (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
        with sm["app"].app_context():
            assert routes._history().check_and_snapshot(str(live), "auto", force=True)
        later = chip_state(alias="#./x180_DragCosine", t1=3.0e-5, f01=5.1e9, amp=0.25)
        later["qubits"]["qA2"]["T1"] = 7.0e-5
        run(sm["data"], 5, later, t_us=int(time.time() * 1e6) + 60_000_000)
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        assert node(topo(sm), "qA2").get("last_calibrated") is None

    def test_a_run_of_another_chip_never_counts(self, tmp_path):
        moved = chip_state(name="another-device")
        moved["qubits"]["qA2"]["T1"] = 9e-5
        env = load(tmp_path, [(chip_state(), None), (moved, None)], chip_state())
        assert node(topo(env), "qA2").get("last_calibrated") is None


class TestStampAndRun:
    def test_a_newer_stamp_wins_and_says_so(self, tmp_path):
        live = chip_state(t1=3e-5)
        live["qubits"]["qA1"]["T1_updated_at"] = iso(T0 + 100_000_000)
        env = load(tmp_path, [(chip_state(), None), (chip_state(t1=3e-5), None)], live)
        a1 = node(topo(env), "qA1")
        assert a1["last_calibrated"] == (T0 + 100_000_000) // 1000
        assert a1["last_calibrated_from"] == "stamp" and a1["last_run_change"] == ms(2)

    def test_an_older_stamp_gives_way_to_the_run(self, tmp_path):
        live = chip_state(t1=3e-5)
        live["qubits"]["qA1"]["T1_updated_at"] = iso(T0)
        env = load(tmp_path, [(chip_state(), None), (chip_state(t1=3e-5), None)], live)
        a1 = node(topo(env), "qA1")
        assert a1["last_calibrated"] == ms(2) and a1["last_calibrated_from"] == "run"

    def test_no_ledger_keeps_the_stamps_untouched(self, tmp_path):
        live = chip_state(t1=3e-5)
        live["qubits"]["qA1"]["T1_updated_at"] = iso(T0)
        env = load(tmp_path, [(chip_state(), None), (chip_state(t1=3e-5), None)], live, sync=False)
        a1 = node(topo(env), "qA1")
        assert a1["last_calibrated"] == T0 // 1000 and "last_calibrated_from" not in a1


class TestEverySurfaceReadsIt:
    def test_the_chip_status_page(self, sm):
        html = sm["client"].get("/topology").data.decode()
        assert '"last_calibrated_from": "run"' in html or '"last_calibrated_from":"run"' in html

    def test_the_report_dates_the_qubit(self, sm):
        body = sm["client"].get("/chip-status/report/section/chip_status?redact=0").data.decode()
        row = re.search(r"<strong>qA1</strong>.*?</tr>", body, re.S)
        assert row and "2026-01-01" in row.group(0), (row.group(0) if row else body[:800])
