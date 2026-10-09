"""QA chipstatus-r2-15: Chip Status's History drawer after Take Snapshot.

Measured on the customer chip copy: press Take Snapshot and the History (N)
button and the Trends "N snapshots" label stayed at the old count while the
hover popup already said N+1, and every zero-diff row -- including a second
press with nothing changed -- read "(baseline)".

The duplicate snapshot itself is BY DESIGN and is not pinned away here:
docs/20 -- "`force=True` on `check_and_snapshot` bypasses dedup so the explicit
`/api/history/snapshot` endpoint always creates a fresh snapshot."
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "f_01": 6.1e9}},
         "qubit_pairs": {}, "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return c


def _oob_count(html: str):
    m = re.search(r'<span id="history-count" hx-swap-oob="true">(\d+)</span>', html)
    return int(m.group(1)) if m else None


# S10 C6: zero-diff label parsers of the snapshot drawer/page -> removed, that markup is deleted
# (the pins below assert the labels are absent from the ledger rows instead).


def _capture_value(client, value):
    ctx = client.application.config["contexts"][client.application.config["active_context"]]
    live = Path(ctx["path"]) / "state.json"
    doc = json.loads(live.read_text(encoding="utf-8"))
    doc["qubits"]["q1"]["f_01"] = value
    live.write_text(json.dumps(doc), encoding="utf-8")
    return client.post("/api/history/snapshot").get_data(as_text=True)


def _ledger_refs(html):
    return re.findall(r'data-ts="([^"]+)"', html)


class TestTheCountFollowsTakeSnapshot:
    def test_every_press_carries_the_new_total_out_of_band(self, client):
        # S10 C3: duplicate snapshot counts -> recorded state counts, change the state for each press.
        for n in (1, 2, 3):
            html = _capture_value(client, 6e9 + n * 1e8)
            assert _oob_count(html) == n, html[-400:]
        # paging renders carry it too (the full total, not the page's)
        html = client.get("/api/history?page=2&per_page=1").get_data(as_text=True)
        assert _oob_count(html) == 3

    def test_the_press_announces_the_history_moved(self, client):
        r = client.post("/api/history/snapshot")
        assert "stateHistoryChanged" in (r.headers.get("HX-Trigger") or "")

    def test_the_oob_span_is_not_a_row(self, client):
        """test_state_history counts rows by `data-ts="`; the span must not
        carry one."""
        html = client.post("/api/history/snapshot").get_data(as_text=True)
        assert html.count('data-ts="') == 1

    # S10 C6: renamed from test_the_trends_fragment_carries_the_same_count -- it pins the opposite
    def test_the_trends_fragment_leaves_the_count_to_the_drawer(self, client):
        client.post("/api/history/snapshot")
        client.post("/api/history/snapshot")
        html = client.get("/topology/trends").get_data(as_text=True)
        # S10 C3: Trends wrote the History (N) count -> a ledger's Trends counts recorded EVENTS
        # and leaves that count to what the drawer lists (docs/301 F14); the drawer carries it.
        assert _oob_count(html) is None
        assert "1 recorded event" in html
        assert _oob_count(client.get("/api/history").get_data(as_text=True)) == 1


class TestZeroDiffRowsSayWhatTheyAre:
    def test_only_the_first_snapshot_is_the_baseline(self, client):
        client.post("/api/history/snapshot")
        html = client.post("/api/history/snapshot").get_data(as_text=True)
        # S10 C3: zero-diff snapshot labels -> observed writer unknown, no false baseline.
        assert len(_ledger_refs(html)) == 1 and "writer unknown" in html
        assert "(baseline)" not in html and "(no changes)" not in html

    def test_the_baseline_is_the_chips_first_even_on_another_page(self, client):
        # S10 C3: snapshot pages -> ledger pages, two distinct observed states retain their order.
        _capture_value(client, 6.1e9)
        _capture_value(client, 6.3e9)
        p1 = client.get("/api/history?page=1&per_page=1").get_data(as_text=True)
        p2 = client.get("/api/history?page=2&per_page=1").get_data(as_text=True)
        assert len(_ledger_refs(p1)) == len(_ledger_refs(p2)) == 1
        assert _ledger_refs(p1)[0] > _ledger_refs(p2)[0]
        assert "writer unknown" in p1 and "writer unknown" in p2
        assert "(baseline)" not in p1 + p2

    def test_a_run_rows_zeros_do_not_claim_no_changes(self, client, tmp_path):
        """Bulk-backfilled EXP rows keep a zeroed summary that means NOT
        COMPUTED (history.py; docs/132), so they may not say "(no changes)"."""
        client.post("/api/history/snapshot")
        client.post("/api/history/snapshot")
        hm = client.application.config["history_manager"]
        path = tmp_path / "quam_state"
        newest = hm.list_snapshots(path)[0]
        meta_p = hm._history_dir(path) / newest.timestamp / "meta.json"
        data = json.loads(meta_p.read_text(encoding="utf-8"))
        data["kind"] = "exp"
        data["trigger"] = "experiment"
        meta_p.write_text(json.dumps(data), encoding="utf-8")
        # an out-of-band meta.json edit; drop the sidecar the way
        # test_state_versions does for its hand-written legacy row
        (hm._history_dir(path) / hm._MANIFEST_NAME).unlink(missing_ok=True)
        hm.clear_cache()
        html = client.get("/api/history").get_data(as_text=True)
        # S10 C3: edited snapshot run hint -> ledger observation, metadata cannot invent a run writer.
        assert len(_ledger_refs(html)) == 1
        assert "writer unknown" in html and "seen by SM" in html
        assert "(no changes)" not in html and "(baseline)" not in html

    def test_a_real_change_still_shows_its_badges(self, client, tmp_path):
        client.post("/api/history/snapshot")
        live = tmp_path / "quam_state" / "state.json"
        doc = json.loads(live.read_text(encoding="utf-8"))
        doc["qubits"]["q1"]["f_01"] = 6.3e9
        live.write_text(json.dumps(doc), encoding="utf-8")
        html = client.post("/api/history/snapshot").get_data(as_text=True)
        # S10 C3: snapshot diff badges -> observed states, the shared comparison still records the change.
        refs = _ledger_refs(html)
        assert len(refs) == 2 and html.count("writer unknown") == 2
        from quam_state_manager.web import routes
        with client.application.app_context():
            entries = routes._version_quick_entries(tmp_path / "quam_state", refs[1], refs[0])
        assert any(e.dot_path == "qubits.q1.f_01" and e.new_value == 6.3e9 for e in entries)


class TestTheStateHistoryPageSaysTheSame:
    """Review follow-up: the drawer said "(no changes)" for a row the State
    History page still called "(baseline)". Both now read the same ledger
    rows (S10 C6: the shared snapshot zero-diff macro is deleted with both
    snapshot lists)."""

    def test_only_the_first_snapshot_is_the_baseline(self, client):
        client.post("/api/history/snapshot")
        client.post("/api/history/snapshot")
        html = client.get("/state-history?body=1").get_data(as_text=True)
        # S10 C3: snapshot baseline labels -> one observed state, the drawer reads the same ledger row.
        assert len(_ledger_refs(html)) == 1 and "writer unknown" in html
        drawer = client.get("/api/history").get_data(as_text=True)
        assert _ledger_refs(drawer) == _ledger_refs(html)
        assert "(baseline)" not in html + drawer

    def test_the_baseline_is_the_chips_first_even_on_another_page(self, client):
        # S10 C3: snapshot pages -> ledger pages, distinct observations stay distinct on every page.
        _capture_value(client, 6.1e9)
        _capture_value(client, 6.3e9)
        p1 = client.get("/state-history?body=1&page=1&per_page=1").get_data(as_text=True)
        p2 = client.get("/state-history?body=1&page=2&per_page=1").get_data(as_text=True)
        assert len(_ledger_refs(p1)) == len(_ledger_refs(p2)) == 1
        assert _ledger_refs(p1)[0] > _ledger_refs(p2)[0]
        assert "writer unknown" in p1 + p2 and "(baseline)" not in p1 + p2

    def test_the_full_page_says_it_too(self, client):
        client.post("/api/history/snapshot")
        client.post("/api/history/snapshot")
        html = client.get("/state-history").get_data(as_text=True)
        # S10 C3: snapshot labels -> one observed ledger state, full and partial pages agree.
        assert len(_ledger_refs(html)) == 1 and "writer unknown" in html
        assert "(baseline)" not in html and "(no changes)" not in html


def test_chip_status_refreshes_trends_on_a_capture():
    """The page side: stateHistoryChanged re-fetches a BUILT Trends section
    (once per burst, same selection), opens the sparkline gate, and is torn
    down on navigation. Drives the real shipped JS under jsdom."""
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "chip_status_history_refresh_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, (r.stdout + r.stderr)
