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


def run(root: Path, rid: int, state, *, patches=None, name="scan") -> Path:
    t_us = T0 + rid * 10_000_000
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
        assert not new["data"], "no Data link: run #3 is not shown to have written it"

    def test_strings_and_booleans_have_a_history_too(self, sm):
        html = drawer(sm, "qubits.qA1.flag")
        assert rows(html)[0]["value"] == "True"


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
        assert all("vh-before-via" in r["cls"] for r in got),             "the chip has not run with this pointer: no recorded value was read through it"


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

    def test_a_chip_whose_ledger_holds_no_runs_gets_the_old_path_labelled(self, tmp_path):
        live = tmp_path / "chips" / "live"
        write_chip(live, chip_state(), None)
        app = make_app(tmp_path)
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        env = {"app": app, "client": c}
        html = drawer(env, "qubits.qA1.T1")
        assert "this chip has no change ledger yet" in html
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4e-5"}).status_code == 200
        assert c.post("/state/apply-to-live").status_code == 200
        assert (chip_dir(env) / "ledger.sqlite").exists()
        html = drawer(env, "qubits.qA1.T1")
        assert "holds no runs (no data folder is linked" in html and not rows(html)
        assert "holds no runs" in column(env, {"qA1": "qubits.qA1.T1"})
        j = c.get("/api/agent/field-history?path=qubits.qA1.T1").get_json()
        assert j["source"] == "snapshots" and "holds no runs" in j["note"]


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
        d = rows(drawer(sm, p))
        assert [v for _ts, v in pts] == [0.1, 0.2, 0.25]
        assert [routes_mod._fh_display_string(v) for _ts, v in reversed(pts)] == [r["value"] for r in d]


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
    assert "all 11 checks passed" in r.stdout, r.stdout

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
