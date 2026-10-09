"""docs/282 (S7) -- the per-value history surfaces read the change ledger.

Pins, on generic synthetic chips and run folders in ``tmp_path``:

* one implementation: the value drawer, Column History and the agent API all
  answer through ``routes._value_history`` and say the same words;
* an alias path resolves to its holder through the one resolver; the answer
  names the hop (``via``) and its retargets, and marks rows from before the
  alias named that holder;
* a run is named as the writer of a value only when the ledger proves it (its
  own patch); every other row says what is known;
* SM writes show actor and kind; an undone write is marked;
* while the ledger is building the drawer says so and shows no rows; a chip
  whose ledger holds no runs gets the OLD path, labelled;
* faults: a run missing from the ledger (in flight), an unreadable data
  folder, a deleted run folder, a pointer retargeted mid-history, an SM write
  later undone -- each checked by what the drawer SAYS.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_sync, value_history as vh
from quam_state_manager.web import routes as routes_mod

T0 = int(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1_000_000)
WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


@pytest.fixture(autouse=True)
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


def chip_state(*, t1=1.0e-5, f01=5.0e9, amp=0.1, gauss=0.3, alias="#./x180_DragCosine",
               wave=None, ro=0.05, name="device"):
    return {
        "qubits": {
            "qA1": {"id": "qA1", "f_01": f01, "T1": t1, "flag": True,
                    "wave": list(wave if wave is not None else [0.0] * 20),
                    "xy": {"operations": {"x180": alias,
                                          "x180_DragCosine": {"amplitude": amp, "length": 40},
                                          "x180_Gauss": {"amplitude": gauss, "length": 40}}},
                    "resonator": {"operations": {"readout": "#./readout_square",
                                                 "readout_square": {"amplitude": ro}}}},
            "qA2": {"id": "qA2", "f_01": 6.0e9, "T1": 2.0e-5},
        },
        "qubit_pairs": {},
        "extras": {"chip_name": name},
    }


def iso(t_us: int) -> str:
    return datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).isoformat()


def run(root: Path, rid: int, state, *, patches=None, name="scan", t_us=None) -> Path:
    t_us = T0 + rid * 10_000_000 if t_us is None else t_us
    hhmmss = datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).strftime("%H%M%S")
    folder = root / "2026-01-01" / f"#{rid}_{name}_{hhmmss}"
    (folder / "quam_state").mkdir(parents=True, exist_ok=True)
    body = {"created_at": iso(t_us), "metadata": {"status": "finished", "name": name},
            "id": rid, "data": {"parameters": {"model": {"qubits": ["qA1", "qA2"]}}}}
    if patches is not None:
        body["patches"] = patches
    (folder / "node.json").write_text(json.dumps(body), encoding="utf-8")
    if state is not None:
        (folder / "quam_state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    return folder


def patch(path: str, value, old=None) -> dict:
    return {"op": "replace", "path": "/quam/" + path.replace(".", "/"), "value": value, "old": old}


def write_chip(live: Path, state: dict, data: Path | None):
    live.mkdir(parents=True, exist_ok=True)
    state = json.loads(json.dumps(state))
    if data is not None:
        state["extras"]["data_folder"] = str(data)
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")


def make_app(tmp_path, *, sync=True):
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    app.config["HUB_SYNC_ON_OPEN"] = sync
    return app


@pytest.fixture
def sm(tmp_path):
    """A chip with a declared data folder of four runs:
    #1 the genesis state; #2 T1 changed BY ITS OWN PATCH; #3 f_01 changed with
    no patch; #4 x180 retargeted from x180_Gauss to x180_DragCosine (and
    DragCosine.amplitude changed, unproven)."""
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    run(data, 1, chip_state(alias="#./x180_Gauss"))
    run(data, 2, chip_state(alias="#./x180_Gauss", t1=3.0e-5),
        patches=[patch("qubits.qA1.T1", 3.0e-5, 1.0e-5)])
    run(data, 3, chip_state(alias="#./x180_Gauss", t1=3.0e-5, f01=5.1e9, amp=0.2))
    run(data, 4, chip_state(alias="#./x180_DragCosine", t1=3.0e-5, f01=5.1e9, amp=0.25))
    write_chip(live, chip_state(alias="#./x180_DragCosine", t1=3.0e-5, f01=5.1e9, amp=0.25), data)
    app = make_app(tmp_path)
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "data": data, "tmp": tmp_path}


def chip_dir(env) -> Path:
    with env["app"].app_context():
        return routes_mod._hub_chip_dir(routes_mod._active_ctx()["path"])


def drawer(env, path) -> str:
    r = env["client"].get("/field/history", query_string={"path": path})
    assert r.status_code == 200, r.data[:400]
    return r.data.decode()


def column(env, paths: dict) -> str:
    r = env["client"].post("/bulk/column-history", data={
        "grid": "qubit", "label": "col", "unit": "", "col_key": "c", "paths": json.dumps(paths)})
    assert r.status_code == 200, r.data[:400]
    return r.data.decode()


_ROW = re.compile(r'<tr class="vh-row (?P<cls>[^"]*)"\s+data-eid="(?P<eid>\d+)" '
                  r'data-provenance="(?P<prov>[^"]+)">(?P<body>.*?)</tr>', re.S)


def rows(html: str) -> list[dict]:
    out = []
    for m in _ROW.finditer(html):
        body = m.group("body")
        val = re.search(r'<td class="fh-val"[^>]*><code>([^<]*)</code>', body).group(1)
        by = re.search(r'<span class="fh-exp vh-by" title="([^"]*)">([^<]*)</span>', body)
        out.append({"cls": m.group("cls"), "prov": m.group("prov"), "value": val,
                    "label": by.group(2), "title": by.group(1),
                    "data": "fh-data" in body, "body": body})
    return out


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ======================================================================
# 1. the drawer reads the ledger, and names only what it proves
# ======================================================================

class TestDrawerOnTheLedger:
    def test_the_drawer_reads_the_ledger_and_names_a_run_only_on_its_own_patch(self, sm):
        html = drawer(sm, "qubits.qA1.T1")
        assert "from the change ledger" in html and "Older snapshot history" not in html
        got = rows(html)
        assert [(r["value"], r["prov"]) for r in got] == [
            ("3e-05", "run_proven"), ("1e-05", "first_record")], got
        proven, first = got
        assert "its own patch set it" in proven["body"]
        assert "writer unknown" in first["body"]
        assert proven["label"].startswith("#2") and proven["data"], \
            "the run whose patch set the value is named and opens"
        assert first["label"] == "first recorded in #1" and not first["data"], \
            "the ledger's first run did not set a value that was already there"

    def test_an_unproven_run_is_never_named_as_the_writer(self, sm):
        got = rows(drawer(sm, "qubits.qA1.f_01"))
        new = got[0]
        assert new["value"] == "5,100,000,000.0" and new["prov"] == "run_saved"
        assert new["label"].startswith("saved in #3") and "not proven" in new["title"]
        assert '<span class="vh-sub"' in new["body"] and "writer not proven" in new["body"], \
            "the row SAYS the writer is not proven, not only on hover"
        # docs/301 F9: still no WRITER link ("Data"); the run that saved it is
        # offered as "Run", and opens saying it is not proven to have measured it
        assert ">Data</button>" not in new["body"], "no Data link: run #3 is not shown to have written it"
        assert "?via=saved" in new["body"] and ">Run</button>" in new["body"]

    def test_strings_and_booleans_have_a_history_too(self, sm):
        # S10 C4: empty-row error -> explicit rows assertion, retain boolean and string history.
        for leaf, value in (("flag", "True"), ("id", "qA1")):
            html = drawer(sm, "qubits.qA1." + leaf)
            points = rows(html)
            assert points, html
            assert points[0]["value"] == value


# ======================================================================
# 2. aliases: one resolver, via + retargets, rows from before the alias
# ======================================================================

class TestAliases:
    def test_an_alias_reads_its_holder_and_says_via(self, sm):
        html = drawer(sm, "qubits.qA1.resonator.operations.readout.amplitude")
        assert "via <code>readout</code> &rarr; <code>readout_square</code>" in html
        assert [r["value"] for r in rows(html)] == ["0.05"]

    def test_a_pointer_retargeted_mid_history_is_named_and_older_rows_are_marked(self, sm):
        html = drawer(sm, "qubits.qA1.xy.operations.x180.amplitude")
        t = text(html)
        assert "via x180 → x180_DragCosine" in t.replace("&rarr;", "→")
        assert "pointed here since" in t and "#./x180_Gauss" in t, \
            "the retarget names when the alias moved and what it pointed to before"
        got = rows(html)
        assert [r["value"] for r in got] == ["0.25", "0.2", "0.1"]
        assert "vh-before-via" not in got[0]["cls"]
        assert all("vh-before-via" in r["cls"] for r in got[1:]), \
            "values from before #4 belonged to a holder the alias did not name then"
        assert "before x180 pointed here" in got[1]["body"]


    def test_a_pointer_to_a_whole_object_shows_the_pointers_own_history(self, sm):
        html = drawer(sm, "qubits.qA1.xy.operations.x180")
        assert [(r["value"], r["prov"]) for r in rows(html)] == [
            ("#./x180_DragCosine", "run_saved"), ("#./x180_Gauss", "first_record")]
        assert "vh-via" not in html, "the pointer holder IS the value here; nothing is via"

    def test_an_alias_pointer_edited_but_not_applied_marks_every_row(self, sm):
        r = sm["client"].post("/field/edit", data={"dot_path": "qubits.qA1.xy.operations.x180",
                                                   "value": "#./x180_Gauss"})
        assert r.status_code == 200, r.data[:300]
        html = drawer(sm, "qubits.qA1.xy.operations.x180.amplitude")
        assert "via <code>x180</code> &rarr; <code>x180_Gauss</code>" in html
        assert "this pointer is not recorded yet" in html
        got = rows(html)
        assert [r["value"] for r in got] == ["0.3"]
        # review P1-1: runs #1-#3 DID read x180_Gauss through x180, so its 0.3
        # (recorded at #1) is not "before x180 pointed here" -- one rule, the
        # holder the alias named at that row
        assert not any("vh-before-via" in r["cls"] for r in got)


# ======================================================================
# 3. SM writes: actor + kind; undone writes marked
# ======================================================================

class TestSmWrites:
    def test_an_apply_names_its_actor_and_an_undo_marks_it(self, sm):
        c = sm["client"]
        r = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4.5e-5"},
                   headers={"X-SM-Actor": "operator"})
        assert r.status_code == 200
        assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "operator"}).status_code == 200
        got = rows(drawer(sm, "qubits.qA1.T1"))
        assert (got[0]["value"], got[0]["prov"], got[0]["label"]) == (
            "4.5e-05", "sm", "applied by operator")
        assert c.post("/undo", headers={"X-SM-Actor": "operator"}).status_code == 200
        got = rows(drawer(sm, "qubits.qA1.T1"))
        assert got[0]["label"] == "undo by operator" and got[0]["value"] == "3e-05"
        assert got[1]["label"] == "applied by operator (undone)" and "vh-undone" in got[1]["cls"]
        assert "A later undo took this write back" in got[1]["title"]


    def test_an_agent_write_and_an_approved_plan_say_who(self, sm):
        c = sm["client"]
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "5.5e-5"},
                      headers={"X-SM-Agent": "helper"}).status_code == 200
        assert c.post("/state/apply-to-live", headers={"X-SM-Agent": "helper"}).status_code == 200
        got = rows(drawer(sm, "qubits.qA1.T1"))
        assert (got[0]["prov"], got[0]["label"]) == ("sm", "agent helper"), got[0]
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "6.5e-5"},
                      headers={"X-SM-Actor": "operator", "X-SM-Plan": "p-1"}).status_code == 200
        assert c.post("/state/apply-to-live",
                      headers={"X-SM-Actor": "operator", "X-SM-Plan": "p-1"}).status_code == 200
        got = rows(drawer(sm, "qubits.qA1.T1"))
        assert got[0]["label"] == "approved by operator (agent plan)" and "plan p-1" in got[0]["title"]


# ======================================================================
# 4. honesty: building, fallback, faults
# ======================================================================

class TestHonesty:
    def test_a_building_ledger_says_so_and_shows_no_rows(self, sm, monkeypatch):
        monkeypatch.setattr(hub_sync, "status", lambda d: {"state": "building", "done": 2, "total": 9})
        html = drawer(sm, "qubits.qA1.T1")
        assert "being built (2 of 9 runs)" in html and 'data-vh-retry="2000"' in html
        assert not rows(html) and "Older snapshot history" not in html
        col = column(sm, {"qA1": "qubits.qA1.T1"})
        assert "being built (2 of 9 runs)" in col and "ch-chip" not in col
        j = sm["client"].get("/api/agent/field-history?path=qubits.qA1.T1").get_json()
        assert j["source"] == "building" and j["history"] is None and "being built" in j["note"]

    def test_a_ledger_caught_building_mid_read_says_so(self, sm, monkeypatch):
        def boom(*a, **k):
            raise hub_sync.Building(chip_dir(sm), {"state": "building", "done": 1, "total": 4})
        monkeypatch.setattr(vh, "read", boom)
        assert "being built (1 of 4 runs)" in drawer(sm, "qubits.qA1.T1")

    def test_an_index_being_prepared_says_so(self, sm, monkeypatch):
        from quam_state_manager.core.ramcache import Warming

        def busy(*a, **k):
            raise Warming("hub_read_index", "x", 0.0)
        monkeypatch.setattr(vh, "read", busy)
        html = drawer(sm, "qubits.qA1.T1")
        assert "Preparing the change history" in html and 'data-vh-retry="800"' in html

    def test_a_run_still_in_flight_is_said_to_be_missing(self, sm):
        run(sm["data"], 5, None)                         # node.json written, no state yet
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        html = drawer(sm, "qubits.qA1.T1")
        assert "1 newest run is still being saved and not in this history yet" in html
        assert rows(html), "what IS recorded is still shown"

    def test_an_unreadable_data_folder_is_named(self, sm):
        shutil.move(str(sm["data"]), str(sm["tmp"] / "offline"))
        cs = hub_sync.sync_for(chip_dir(sm))
        cs.request(full=True)
        hub_sync._kick(cs)
        html = drawer(sm, "qubits.qA1.T1")
        assert "This history may be missing changes: a data folder cannot be read" in html
        assert "run folder deleted" not in html, "an offline folder is not every run deleted"

    def test_a_deleted_run_folder_keeps_its_row_but_loses_its_link(self, sm):
        folder = next((sm["data"] / "2026-01-01").glob("#2_*"))
        shutil.rmtree(folder)
        cs = hub_sync.sync_for(chip_dir(sm))
        cs.request(full=True)
        hub_sync._kick(cs)
        got = rows(drawer(sm, "qubits.qA1.T1"))
        assert got[0]["prov"] == "run_proven" and got[0]["value"] == "3e-05"
        assert "run folder deleted" in got[0]["body"]
        assert not got[0]["data"], "a Data link to a deleted folder would open nothing"

    def test_a_current_value_not_yet_recorded_is_said(self, sm):
        r = sm["client"].post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "9e-5"})
        assert r.status_code == 200
        assert "The value now is not the newest recorded one" in drawer(sm, "qubits.qA1.T1")

    # S10 C4: old path label -> ledger link offer, keep C3 behavior under its own name.
    def test_a_chip_whose_ledger_holds_no_runs_offers_the_link(self, tmp_path):
        live = tmp_path / "chips" / "live"
        write_chip(live, chip_state(), None)
        app = make_app(tmp_path)
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        env = {"app": app, "client": c}
        html = drawer(env, "qubits.qA1.T1")
        # S10 C1: opening a chip with no data folder runs its first sync slice, which
        # creates the ledger -- the first answer is "holds no runs", no longer "no ledger yet"
        assert (chip_dir(env) / "ledger.sqlite").exists()
        # S10 C3: old -> new, an empty readable ledger answers with the link offer.
        assert 'data-note="no_folder_linked"' in html and not rows(html)
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4e-5"}).status_code == 200
        assert c.post("/state/apply-to-live").status_code == 200
        assert (chip_dir(env) / "ledger.sqlite").exists()
        html = drawer(env, "qubits.qA1.T1")
        # S10 C3: old -> new, SM writes remain visible without a linked run folder.
        assert 'data-note="no_folder_linked"' in html and rows(html)
        assert 'data-note="no_folder_linked"' in column(env, {"qA1": "qubits.qA1.T1"})
        j = c.get("/api/agent/field-history?path=qubits.qA1.T1").get_json()
        # S10 C3: old -> new, the agent reads the same ledger and offers the same link.
        assert j["source"] == "ledger" and j["history"]["points"] and j["link"]["offer"]


# ======================================================================
# 5. one implementation for the drawer, Column History and the agent
# ======================================================================

class TestOneImplementation:
    def test_all_three_surfaces_answer_through_one_function(self, sm, monkeypatch):
        seen = []
        real = routes_mod._value_history

        def spy(ctx, path_map, **kw):
            seen.append(sorted(path_map.values()))
            return real(ctx, path_map, **kw)
        monkeypatch.setattr(routes_mod, "_value_history", spy)
        drawer(sm, "qubits.qA1.T1")
        column(sm, {"qA1": "qubits.qA1.T1"})
        sm["client"].get("/api/agent/field-history?path=qubits.qA1.T1")
        assert seen == [["qubits.qA1.T1"]] * 3

    def test_the_agent_reads_the_same_points_in_the_same_words(self, sm):
        j = sm["client"].get("/api/agent/field-history?path=qubits.qA1.xy.operations.x180.amplitude").get_json()
        assert j["ok"] and j["source"] == "ledger"
        h = j["history"]
        assert h["holder"] == "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
        assert h["via"] == [{"from_path": "qubits.qA1.xy.operations.x180", "pointer": "#./x180_DragCosine",
                             "to_path": "qubits.qA1.xy.operations.x180_DragCosine"}]
        assert h["retargets"][0]["changes"][0]["old"] == "#./x180_Gauss"
        # S10 C4: missing-key error -> explicit shape assertion, preserve the shared wording contract.
        assert h["points"] and all("label" in p for p in h["points"])
        html = drawer(sm, "qubits.qA1.xy.operations.x180.amplitude")
        assert [(routes_mod._fh_display_string(p["value"]), p["label"], p["before_via"])
                for p in h["points"]] == [
            (r["value"], r["label"], "vh-before-via" in r["cls"]) for r in rows(html)]
        assert [p["value"] for p in h["points"]] == [0.25, 0.2, 0.1]

    def test_column_history_shows_the_drawers_points(self, sm):
        html = column(sm, {"qA1": "qubits.qA1.T1", "qA2": "qubits.qA2.T1"})
        assert "from the change ledger" in html
        chips = re.findall(r'data-provenance="([^"]+)".*?<code>([^<]*)</code>.*?'
                           r'<span class="vh-chip-by">([^<]*)</span>', html.split("ch-view-byrun")[0], re.S)
        assert chips[:2] == [("run_proven", "3e-05", "#2 scan"), ("first_record", "1e-05", "first recorded in #1")]
        assert ('first recorded in #1</span><span class="vh-chip-sub"> · ledger start; '
                'writer unknown') in html, "a chip SAYS when its writer is unknown"
        assert html.count('class="ch-chip-data"') == 1, "only the proven chip opens a run"
        byrun = html.split("ch-view-byrun")[1]
        assert re.findall(r'>#(\d+)</a>', byrun) == ["4", "3", "2", "1"]
        assert 'data-fill="3e-05"' in byrun and 'data-fill="1e-05"' in byrun


# ======================================================================
# 6. long arrays, Trends' alias path
# ======================================================================

class TestElementsAndTrends:
    def test_an_element_of_a_long_array_changes_only_when_it_does(self, tmp_path):
        data, live = tmp_path / "data", tmp_path / "chips" / "live"
        w = [0.0] * 20
        run(data, 1, chip_state(wave=w))
        run(data, 2, chip_state(wave=w[:5] + [1.0] + w[6:]))     # element 5 only
        run(data, 3, chip_state(wave=w[:3] + [7.0] + w[4:5] + [1.0] + w[6:]))   # element 3 only
        write_chip(live, chip_state(wave=w[:3] + [7.0] + w[4:5] + [1.0] + w[6:]), data)
        app = make_app(tmp_path)
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        env = {"app": app, "client": c}
        # S2 stores a long array's integral floats as integers (1 == 1.0)
        assert [r["value"] for r in rows(drawer(env, "qubits.qA1.wave.3"))] == ["7", "0"]
        assert [r["value"] for r in rows(drawer(env, "qubits.qA1.wave.5"))] == ["1", "0"]
        assert [r["value"] for r in rows(drawer(env, "qubits.qA1.wave.7"))] == ["0"]

    def test_chip_trends_charts_an_alias_path_with_the_drawers_points(self, sm):
        p = "qubits.qA1.xy.operations.x180.amplitude"
        html = sm["client"].get("/topology/trends", query_string={"path": p}).data.decode()
        charts = json.loads(re.search(r'id="topo-trends-data">(.*?)</script>', html, re.S).group(1))
        series = [s for c in charts for s in c["series"] if s["entity"] == "qA1"]
        assert len(series) == 1, "the alias path drew no qA1 line"
        pts = [(ts, v) for ts, v in series[0]["points"] if ts not in (series[0].get("held") or {})]
        # review P0-1: the value IN FORCE through x180 (x180_Gauss's 0.3 at
        # #1-#3, x180_DragCosine's 0.25 from #4) -- the agent's in-force
        # series, from the same read, says exactly this
        j = sm["client"].get("/api/agent/field-history", query_string={"path": p}).get_json()
        in_force = [e["value"] for e in j["history"]["in_force"]]
        assert [v for _ts, v in pts] == in_force == [0.3, 0.25]


# ======================================================================
# 7. the drawer and Column History ask again while the ledger builds (jsdom)
# ======================================================================

def test_the_retry_selfcheck():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    r = subprocess.run([node, str(Path(__file__).with_name("vh_retry_selfcheck.cjs"))],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 2 and "jsdom not installed" in r.stdout:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    # S10 walk: old -> new, 13 -> 17 checks (the unreadable end state's manual Try again)
    assert "all 17 checks passed" in r.stdout, r.stdout

# ======================================================================
# 8. the index-build speedup keeps the decoder's answer (docs/282 perf)
# ======================================================================

def _reference_segments(path):
    """The S2 holder-path decoder, written out independently (char by char)."""
    if path == "":
        return []
    parts, buf, i = [], "", 0
    while i < len(path):
        ch = path[i]
        if ch == "\\" and i + 1 < len(path):
            buf += path[i:i + 2]
            i += 2
            continue
        if ch == ".":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
        i += 1
    parts.append(buf)
    out = []
    for part in parts:
        if part == "\\e":
            out.append("")
        else:
            out.append(re.sub(r"\\(.)", r"\1", part))
    return out


def test_holder_path_decoding_is_the_same_with_and_without_escapes():
    import random

    from quam_state_manager.core.hub_rules import _segment
    from quam_state_manager.core.hub_store import segments
    rng = random.Random(282)
    alphabet = ["q", "A", "1", ".", "\\", "e", "_", ""]
    for _ in range(3000):
        keys = ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 4)))
                for _ in range(rng.randint(1, 4))]
        spelled = ".".join(_segment(k) for k in keys)
        assert segments(spelled) == _reference_segments(spelled) == keys, spelled
    assert segments("qubits.qA1.T1") == ["qubits", "qA1", "T1"]
    assert segments("a\\.b.c") == ["a.b", "c"] and segments("\\e.x") == ["", "x"]


# ======================================================================
# 9. review round (docs/282 "Review round"): each pin was RED on 3a9a774f
# ======================================================================

def _truth_through_alias(folder: Path, dot_path: str):
    """The value an alias path had in ONE run's own saved state -- read from
    the run folder with the one resolver, independent of the ledger."""
    from quam_state_manager.core.hub_rules import merged
    from quam_state_manager.core.pointer_path import resolve_field_target
    state = json.loads((folder / "quam_state" / "state.json").read_text(encoding="utf-8"))
    wiring = json.loads((folder / "quam_state" / "wiring.json").read_text(encoding="utf-8"))
    return resolve_field_target(merged(state, wiring), dot_path)["resolved_value"]


def _runs_of(env) -> list[Path]:
    return sorted((env["data"] / "2026-01-01").glob("#*"), key=lambda f: int(f.name[1:].split("_")[0]))


def _column_cells(html: str, row: str) -> list[str]:
    byrun = html.split("ch-view-byrun")[1]
    tr = re.search(r'<tr data-row="%s">(.*?)</tr>' % re.escape(row), byrun, re.S).group(1)
    out = []
    for tag in re.findall(r'<td class="ch-val[^"]*"[^>]*>', tr):
        m = re.search(r'data-fill="([^"]*)"', tag)
        out.append(m.group(1) if m else "")
    return out


@pytest.fixture
def aba(tmp_path):
    """x180 names DragCosine (#1), then Gauss (#2, while DragCosine's amplitude
    moves), then DragCosine again (#3, amplitude moves again)."""
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    run(data, 1, chip_state(alias="#./x180_DragCosine", amp=0.1))
    run(data, 2, chip_state(alias="#./x180_Gauss", amp=0.12))
    run(data, 3, chip_state(alias="#./x180_DragCosine", amp=0.15))
    write_chip(live, chip_state(alias="#./x180_DragCosine", amp=0.15), data)
    app = make_app(tmp_path)
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "data": data, "tmp": tmp_path}


class TestReviewRound:
    ALIAS = "qubits.qA1.xy.operations.x180.amplitude"

    def test_p0_1_trends_draws_the_value_in_force_through_the_alias(self, sm):
        html = sm["client"].get("/topology/trends", query_string={"path": self.ALIAS}).data.decode()
        charts = json.loads(re.search(r'id="topo-trends-data">(.*?)</script>', html, re.S).group(1))
        series = [s for c in charts for s in c["series"] if s["entity"] == "qA1"]
        assert len(series) == 1
        held = series[0].get("held") or {}
        pts = [v for ts, v in series[0]["points"] if ts not in held]
        truth = [_truth_through_alias(f, self.ALIAS) for f in _runs_of(sm)]
        assert truth == [0.3, 0.3, 0.3, 0.25]
        assert pts == [0.3, 0.25], "every charted value is one the alias really had at that run"
        assert all(v == 0.25 for ts, v in series[0]["points"] if ts in held), \
            "the held tail is the current holder's own value"

    def test_p0_3_by_run_shows_the_then_holders_value_for_an_alias_row(self, sm):
        html = column(sm, {"qA1": self.ALIAS})
        cells = _column_cells(html, "qA1")
        truth = [_truth_through_alias(f, self.ALIAS) for f in reversed(_runs_of(sm))]
        assert [float(c) for c in cells] == truth == [0.25, 0.3, 0.3, 0.3]

    def test_p0_2_by_run_leaves_out_a_run_of_another_chip(self, sm):
        foreign = chip_state(alias="#./x180_DragCosine", amp=0.9, t1=9.0e-5, name="another-device")
        run(sm["data"], 5, foreign)
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        # S10 C3 review P1-4: the drawer listed it flagged "(chip uncertain)" -> it is left
        # out of the chip's timeline, and the drawer says so
        d = drawer(sm, "qubits.qA1.T1")
        assert "(chip uncertain)" not in d and "does not match this chip" in d
        html = column(sm, {"qA1": "qubits.qA1.T1"})
        byrun = html.split("ch-view-byrun")[1]
        assert ">#5</" not in byrun and 'data-fill="9e-05"' not in byrun, \
            "a run of another chip is never offered as this chip's saved value (nor Use all)"
        assert "1 run whose saved chip identity does not match this chip" in html

    def test_p1_1_before_via_marks_only_rows_the_alias_did_not_name(self, aba):
        html = drawer(aba, self.ALIAS)
        got = {r["value"]: "vh-before-via" in r["cls"] for r in rows(html)}
        assert got == {"0.15": False, "0.12": True, "0.1": False}, got
        j = aba["client"].get("/api/agent/field-history", query_string={"path": self.ALIAS}).get_json()
        assert {p["value"]: p["before_via"] for p in j["history"]["points"]} == {0.15: False, 0.12: True, 0.1: False}

    def test_p0_3_and_p1_1_share_one_rule_after_a_return(self, aba):
        html = column(aba, {"qA1": self.ALIAS})
        cells = [float(c) for c in _column_cells(html, "qA1")]
        truth = [_truth_through_alias(f, self.ALIAS) for f in reversed(_runs_of(aba))]
        assert cells == truth == [0.15, 0.3, 0.1]

    def test_p2_2_partly_undone_marks_only_the_path_taken_back(self, sm):
        c = sm["client"]
        for path, val in (("qubits.qA1.T1", "4.5e-5"), ("qubits.qA2.T1", "5.5e-5")):
            assert c.post("/field/edit", data={"dot_path": path, "value": val},
                          headers={"X-SM-Actor": "operator"}).status_code == 200
        assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "operator"}).status_code == 200
        assert c.post("/undo", headers={"X-SM-Actor": "operator"}).status_code == 200   # takes back qA2
        a1 = rows(drawer(sm, "qubits.qA1.T1"))
        a2 = rows(drawer(sm, "qubits.qA2.T1"))
        assert a1[0]["label"] == "applied by operator" and "vh-undone" not in a1[0]["cls"], \
            "qA1's edit is still live: never struck through"
        assert a2[0]["label"] == "undo by operator"
        assert a2[1]["label"] == "applied by operator (undone)" and "vh-undone" in a2[1]["cls"]

    def test_p2_4_an_array_that_shrank_keeps_its_element_history(self, tmp_path):
        data, live = tmp_path / "data", tmp_path / "chips" / "live"
        w = [0.0] * 20
        run(data, 1, chip_state(wave=w))
        run(data, 2, chip_state(wave=w[:3] + [5.0] + w[4:]))           # element 3, long list
        run(data, 3, chip_state(wave=[0.0, 0.0, 0.0, 5.0, 0.0]))        # shrinks, element 3 unchanged
        run(data, 4, chip_state(wave=[0.0, 0.0, 0.0, 6.0, 0.0]))        # element 3 moves, short list
        write_chip(live, chip_state(wave=[0.0, 0.0, 0.0, 6.0, 0.0]), data)
        app = make_app(tmp_path)
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        got = rows(drawer({"app": app, "client": c}, "qubits.qA1.wave.3"))
        assert [(r["value"], r["label"]) for r in got] == [
            ("6.0", "saved in #4 scan"), ("5", "saved in #2 scan"), ("0", "first recorded in #1")], got

    def test_p3_nan_in_by_run_is_not_a_change(self, tmp_path):
        data, live = tmp_path / "data", tmp_path / "chips" / "live"
        nan = float("nan")
        run(data, 1, chip_state(t1=nan))
        run(data, 2, chip_state(t1=nan, f01=5.2e9))
        write_chip(live, chip_state(t1=nan, f01=5.2e9), data)
        app = make_app(tmp_path)
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        html = column({"app": app, "client": c}, {"qA1": "qubits.qA1.T1"})
        byrun = html.split("ch-view-byrun")[1]
        tr = re.search(r'<tr data-row="qA1">(.*?)</tr>', byrun, re.S).group(1)
        assert "ch-changed" not in tr, "NaN then NaN is no change"

    def test_p3_column_history_says_before_via_in_text(self, aba):
        html = column(aba, {"qA1": self.ALIAS})
        changes = html.split("ch-view-byrun")[0]
        assert "before x180 pointed here" in changes, "a visible marker, not only opacity"


# ======================================================================
# 10. review round: the read index never blocks across chips, and is warm
# ======================================================================

def _small_ledger(tmp_path: Path, name: str) -> Path:
    from quam_state_manager.core import hub_build
    data, out = tmp_path / f"data_{name}", tmp_path / f"ledger_{name}"
    for rid in (1, 2, 3):
        run(data, rid, chip_state(t1=rid * 1e-5))
    hub_build.build(data, out)
    return out


class TestReviewIndexLocks:
    def test_p2_3_a_busy_chip_never_holds_a_read_of_another_chip(self, tmp_path):
        import threading
        import time
        from types import SimpleNamespace
        from quam_state_manager.core import hub_index
        from quam_state_manager.core.ramcache import Warming
        a, b = _small_ledger(tmp_path, "a"), _small_ledger(tmp_path, "b")
        A, B = SimpleNamespace(directory=a), SimpleNamespace(directory=b)
        try:
            for s in (A, B):
                with hub_index.snapshot(s):
                    pass
            holding, release = threading.Event(), threading.Event()

            def hold():                      # a slow read of chip A (an index build, say)
                with hub_index.snapshot(A):
                    holding.set()
                    release.wait(5)
            outcome = {}

            def second():                    # another reader of chip A
                t0 = time.perf_counter()
                try:
                    with hub_index.snapshot(A):
                        outcome["a"] = "read"
                except Warming:
                    outcome["a"] = "warming"
                outcome["a_s"] = time.perf_counter() - t0
            t1 = threading.Thread(target=hold)
            t1.start()
            assert holding.wait(5)
            t2 = threading.Thread(target=second)
            t2.start()
            time.sleep(0.05)                 # the second reader is now waiting on A
            t0 = time.perf_counter()
            with hub_index.snapshot(B):
                b_s = time.perf_counter() - t0
            t2.join(5)
            release.set()
            t1.join(5)
            assert b_s < 0.2, f"a read of chip B waited {b_s:.2f} s behind chip A"
            assert outcome.get("a") == "warming" and outcome.get("a_s", 9) < 1.0, \
                f"a second read of the busy chip answers 'preparing', not after the holder: {outcome}"
        finally:
            hub_index.close_readers()

    def test_p2_3_the_projector_builds_the_index_after_a_burst(self, tmp_path):
        import time
        from quam_state_manager.core import hub_index
        led = _small_ledger(tmp_path, "w")
        slot = str(led.resolve()).lower()
        old_inline, old_pre = hub._PROJECTOR.inline, hub.PREWARM
        hub.set_inline(False)
        hub.set_prewarm(True)
        try:
            hub_index.close_readers()
            assert hub_index.INDEX_CACHE.peek(slot) is None
            hub._PROJECTOR.kick_sync(hub.Hub.for_chip(led))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and hub_index.INDEX_CACHE.peek(slot) is None:
                time.sleep(0.05)
            assert hub_index.INDEX_CACHE.peek(slot) is not None, "no read index was built after the burst"
        finally:
            hub.set_inline(old_inline)
            hub.set_prewarm(old_pre)
            deadline = time.monotonic() + 10
            while hub._PREWARM_THREAD is not None and time.monotonic() < deadline:
                time.sleep(0.05)
            hub._PROJECTOR.flush(10)
            hub_index.close_readers()


class TestReviewObserved:
    def test_p1_2_a_state_sm_saw_between_runs_is_in_the_history(self, sm):
        import time
        live = sm["live"]
        st = json.loads((live / "state.json").read_text(encoding="utf-8"))
        st["qubits"]["qA1"]["T1"] = 7.0e-5                     # an edit made outside SM
        (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
        with sm["app"].app_context():
            assert routes_mod._history().check_and_snapshot(str(live), "auto", force=True)
        run(sm["data"], 5, chip_state(alias="#./x180_DragCosine", t1=8.0e-5, f01=5.1e9, amp=0.25),
            t_us=int(time.time() * 1e6) + 60_000_000)
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        got = rows(drawer(sm, "qubits.qA1.T1"))
        assert [(r["value"], r["prov"]) for r in got][:3] == [
            ("8e-05", "run_saved"), ("7e-05", "observed"), ("3e-05", "run_proven")], got
        assert got[1]["label"] == "seen by SM (auto snapshot)" and "writer unknown" in got[1]["body"]
        assert not got[1]["data"], "no run is named for a state SM only saw"

    def test_p1_2_an_sm_writes_own_snapshots_are_not_a_second_history(self, sm):
        """An Apply takes two Param History copies: a backup of the live chip
        before it and a save after it. The save IS the SM event's state: it
        is recorded as looked at and adds no event. (The backup is the live
        chip as SM saw it before the write; it is imported only for what no
        event explains -- here the live file's own data-folder entry, never T1.)"""
        import sqlite3
        c = sm["client"]
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4.5e-5"}).status_code == 200
        assert c.post("/state/apply-to-live").status_code == 200   # backup + save snapshots of the apply
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        got = rows(drawer(sm, "qubits.qA1.T1"))
        assert [r["prov"] for r in got].count("observed") == 0, \
            "the apply's own Param History copies are the SM event, never an extra row"
        assert got[0]["label"].startswith("applied by")
        con = sqlite3.connect(f"{(chip_dir(sm) / 'ledger.sqlite').resolve().as_uri()}?mode=ro", uri=True)
        try:
            kinds = [k for (k,) in con.execute("SELECT kind FROM events ORDER BY ord")]
            outcomes = sorted(o for (o,) in con.execute("SELECT outcome FROM observed_snapshots"))
        finally:
            con.close()
        assert kinds[-1] == "sm_apply", f"nothing after the SM write: its save copy is not an event ({kinds})"
        assert "same_before" in outcomes, outcomes


class TestReviewMore:
    ALIAS = "qubits.qA1.xy.operations.x180.amplitude"

    def test_p0_1_after_a_return_trends_draws_each_holders_value_in_force(self, aba):
        html = aba["client"].get("/topology/trends", query_string={"path": self.ALIAS}).data.decode()
        charts = json.loads(re.search(r'id="topo-trends-data">(.*?)</script>', html, re.S).group(1))
        series = [s for c in charts for s in c["series"] if s["entity"] == "qA1"][0]
        pts = [v for ts, v in series["points"] if ts not in (series.get("held") or {})]
        truth = [_truth_through_alias(f, self.ALIAS) for f in _runs_of(aba)]
        assert truth == [0.1, 0.3, 0.15] and pts == truth, (pts, truth)

    def test_p2_3_every_zone_shares_one_index(self, sm):
        from types import SimpleNamespace
        from quam_state_manager.core import hub_index
        led = chip_dir(sm)
        slot = str(led.resolve()).lower()
        hub_index.close_readers()
        calls = []
        real = hub_index.build_index
        real_extend = hub_index.extend_index

        def counting(conn, zone=None):
            calls.append(zone)
            return real(conn, zone)

        def preparing(conn, prev):
            # every time the cached index is (re)prepared for a token, even
            # when an extend finds nothing new: another zone is a cache HIT
            prepared.append(prev is not None)
            return real_extend(conn, prev)
        prepared = []
        try:
            hub_index.build_index = counting
            hub_index.extend_index = preparing
            for zone in ("UTC", "Asia/Seoul", "America/New_York"):
                with hub_index.snapshot(hub_index.context(SimpleNamespace(directory=led), zone=zone)) as (_c, idx):
                    assert idx.zone == zone
            assert len(calls) == 1, f"one ledger, one index: built {len(calls)} times for three zones"
            assert len(prepared) == 1, f"one ledger, one index: prepared {len(prepared)} times for three zones"
            assert hub_index.INDEX_CACHE.peek(slot) is not None
        finally:
            hub_index.build_index = real
            hub_index.extend_index = real_extend
            hub_index.close_readers()

    def test_p1_2_a_runs_save_seen_early_stays_the_runs(self, sm):
        """An auto capture of the state a run saved, stamped BEFORE that run
        (the experiment PC's clock is ahead), found after the run is already
        in the ledger: the change stays the run's (no new run lands with it,
        so only the import's own rule can decide)."""
        import time
        from quam_state_manager.core import history as history_mod
        from datetime import datetime, timezone
        live = sm["live"]
        nxt = chip_state(alias="#./x180_DragCosine", t1=8.0e-5, f01=5.1e9, amp=0.25)
        t_run = int(time.time() * 1e6) + 60_000_000
        run(sm["data"], 5, nxt, t_us=t_run)
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        assert rows(drawer(sm, "qubits.qA1.T1"))[0]["label"] == "saved in #5 scan"
        # the live chip holds exactly what the run saved (qualibrate writes both)
        (live / "state.json").write_text(json.dumps(nxt), encoding="utf-8")
        stamp = datetime.fromtimestamp((t_run - 5_000_000) / 1e6, tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:20]
        real = history_mod._ts_stamp
        try:
            history_mod._ts_stamp = lambda: stamp
            with sm["app"].app_context():
                assert routes_mod._history().check_and_snapshot(str(live), "auto", force=True)
        finally:
            history_mod._ts_stamp = real
        cs = hub_sync.sync_for(chip_dir(sm))
        cs.request(listing=True)
        hub_sync._kick(cs)
        got = rows(drawer(sm, "qubits.qA1.T1"))
        assert (got[0]["value"], got[0]["label"]) == ("8e-05", "saved in #5 scan"), got[:2]
        assert "observed" not in [r["prov"] for r in got]

    def test_p1_2_a_run_landing_after_its_own_early_observation_takes_it_back(self, sm):
        import time
        live = sm["live"]
        nxt = chip_state(alias="#./x180_DragCosine", t1=8.0e-5, f01=5.1e9, amp=0.25)
        body = json.dumps(nxt)
        (live / "state.json").write_text(body, encoding="utf-8")
        with sm["app"].app_context():
            assert routes_mod._history().check_and_snapshot(str(live), "auto", force=True)
            hub_sync.on_roots_moved([str(sm["data"])])
        first = rows(drawer(sm, "qubits.qA1.T1"))
        assert first[0]["prov"] == "observed", "the capture is in the history before any run explains it"
        folder = run(sm["data"], 5, nxt, t_us=int(time.time() * 1e6) + 60_000_000)
        (folder / "quam_state" / "state.json").write_text(body, encoding="utf-8")   # the very bytes SM saw
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        got = rows(drawer(sm, "qubits.qA1.T1"))
        assert (got[0]["value"], got[0]["label"]) == ("8e-05", "saved in #5 scan"), got[:2]
        assert "observed" not in [r["prov"] for r in got], "the run's own save, seen early, is the run's"


class TestReviewIncrementalIndex:
    def _base(self, tmp_path):
        from quam_state_manager.core import hub_build
        data, out = tmp_path / "data_i", tmp_path / "ledger_i"
        for rid in (1, 2, 3):
            run(data, rid, chip_state(t1=rid * 1e-5, alias="#./x180_Gauss" if rid == 2 else "#./x180_DragCosine"))
        hub_build.build(data, out)
        return data, out

    def test_p2_3_an_appended_run_extends_the_index_and_equals_a_full_build(self, tmp_path, monkeypatch):
        import sqlite3
        from types import SimpleNamespace
        from quam_state_manager.core import hub_index
        data, led = self._base(tmp_path)
        store = SimpleNamespace(directory=led)
        slot = str(led.resolve()).lower()
        try:
            with hub_index.snapshot(store):
                pass
            first = hub_index.INDEX_CACHE.peek(slot)[1]
            builds = []
            real = hub_index.build_index
            monkeypatch.setattr(hub_index, "build_index", lambda *a, **k: builds.append(1) or real(*a, **k))
            run(data, 4, chip_state(t1=4e-5, f01=5.3e9, alias="#./x180_Gauss"))   # a new run at the head
            hub_sync.catch_up(led, [str(data)])
            with hub_index.snapshot(hub_index.context(store, zone="UTC")) as (_c, view):
                newest = view.eids.tolist()[-1]
                assert newest == max(view.eids)
                assert any(newest in ids for ids in view.postings["day"].values()),                     "a zone's day postings follow the appended run"
            held = hub_index.INDEX_CACHE.peek(slot)[1]
            assert held is first and builds == [], "an append extends the index; it is not rebuilt"
            conn = sqlite3.connect(f"{(led / 'ledger.sqlite').resolve().as_uri()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cold = real(conn)
            conn.close()
            assert held == cold, "the extended index equals a full build field for field"
            # an indexed event changed IN PLACE (its folder deleted: SOURCE_GONE),
            # nothing appended: never served from the stale index
            shutil.rmtree(next(data.glob("*/#2_*")))
            hub_sync.catch_up(led, [str(data)])
            with hub_index.snapshot(store):
                pass
            held = hub_index.INDEX_CACHE.peek(slot)[1]
            conn = sqlite3.connect(f"{(led / 'ledger.sqlite').resolve().as_uri()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cold = real(conn)
            conn.close()
            assert held == cold, "an event re-flagged in place: the index equals a full build"
            assert builds, "an event changed in place is a full build, not an extend"
            builds.clear()
            # a run that lands in the MIDDLE (late) re-diffs its successor: full build
            run(data, 0, chip_state(t1=0.5e-5))
            hub_sync.catch_up(led, [str(data)])
            with hub_index.snapshot(store):
                pass
            assert builds, "a commit that changed an indexed event is a full build"
        finally:
            hub_index.close_readers()


# S10 C4: missing snapshot substitute -> terminal ledger mode, do not open a reader.
def test_a_missing_ledger_is_terminal_before_any_value_read(sm, tmp_path, monkeypatch):
    from quam_state_manager.core import value_history
    calls = []

    def read(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("unexpected ledger read")

    monkeypatch.setattr(value_history, "read", read)
    with sm["app"].test_request_context():
        ctx = dict(routes_mod._active_ctx(), hub_chip_dir=str(tmp_path / "absent"),
                   hub_no_folder=True)
        ans = routes_mod._value_history(ctx, {"v": "qubits.qA1.T1"})
    assert ans["mode"] == "unavailable" and ans["reason"] == "no_ledger"
    assert not calls


# S10 C4: old readers -> static absence pin, prevent snapshot paths from returning.
def test_removed_value_readers_are_absent():
    import ast
    package = Path(__file__).resolve().parents[1] / "quam_state_manager"
    names = ["_RUN_" + part for part in
             ("IDENT_CACHE", "CHIP_CACHE", "VALUE_CACHE", "CACHE_MAX", "CANDIDATES_MEMO")]
    names += ["_" + part for part in
              ("trim_run_caches", "store_run_candidates", "runs_candidates",
               "runs_field_series", "runs_column_series", "legacy_column_history",
               "legacy_field_history", "SCAN_SERIES", "SCAN_SERIES_LOCK", "SCAN_SERIES_MAX",
               "scan_one_snapshot", "scan_field_series", "snap_files_sig")]
    names += ["CH_SERIES_" + part for part in ("RUNS", "EXAMINE")]
    names += ["_" + part + "_history.html" for part in ("field", "column")]
    for path in package.rglob("*"):
        if not path.is_file() or "vendor" in path.parts or path.name.startswith("plotly"):
            continue
        if path.suffix in (".py", ".html", ".js", ".css"):
            source = path.read_text(encoding="utf-8")
            assert not [name for name in names if name in source], path
    tree = ast.parse((package / "core" / "history.py").read_text(encoding="utf-8"))
    manager = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HistoryManager")
    assert not any(isinstance(n, ast.FunctionDef) and n.name == "field_" + "history"
                   for n in manager.body)
