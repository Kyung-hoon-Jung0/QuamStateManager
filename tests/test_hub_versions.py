"""docs/284 (S9) -- the Versions panel and State History read the change ledger.

Pins, on generic synthetic chips and run folders in ``tmp_path``:

* rows are the ledger's state-bearing events, named honestly (run #N + node,
  an SM write with its actor and kind, a state SM observed with no writer);
  an older Param History snapshot is listed only when no event holds its
  state (content hash; for a run snapshot, an event of THE SAME run);
* Diff / Compare read the ledger's merged document (``state_at``) under the
  one comparison rule (``compare_equal``);
* Stage / Restore-live take the exact pair (the event's own saved files, or
  the pair SM wrote) and write it through the EXISTING doors, every gate and
  the S4 record intact; a version the ledger cannot hand over exactly refuses
  with its reason before any confirm, backup or write;
* building / no ledger / no runs / unreadable draw the older snapshot path,
  saying so; a chip without a ledger keeps the old path, labelled.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub, hub_rules, hub_sync, hub_versions, safe_io
from quam_state_manager.core.hub_store import HubStore
from quam_state_manager.core.working_copy import content_hash
from quam_state_manager.web import routes as routes_mod

T0 = int(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1_000_000)
WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


@pytest.fixture(autouse=True)
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


def chip_state(*, t1=1.0e-5, f01=5.0e9, n_avg=1, ptr="#/qubits/qA1/f_01", name="device"):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "T1": t1, "n_avg": n_avg,
                               "freq_ref": ptr, "wave": [0.0] * 20},
                       "qA2": {"id": "qA2", "f_01": 6.0e9}},
            "qubit_pairs": {}, "extras": {"chip_name": name}}


def iso(t_us: int) -> str:
    return datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).isoformat()


def run(root: Path, rid: int, state, *, name="scan") -> Path:
    t_us = T0 + rid * 10_000_000
    hhmmss = datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc).strftime("%H%M%S")
    folder = root / "2026-01-01" / f"#{rid}_{name}_{hhmmss}"
    (folder / "quam_state").mkdir(parents=True, exist_ok=True)
    (folder / "node.json").write_text(json.dumps(
        {"created_at": iso(t_us), "metadata": {"status": "finished", "name": name}, "id": rid,
         "data": {"parameters": {"model": {"qubits": ["qA1"]}}}}), encoding="utf-8")
    (folder / "quam_state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "quam_state" / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    return folder


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


REF = re.compile(r"\d{8}_\d{6}_\d{6}_event-\d+")


@pytest.fixture
def env(tmp_path):
    """Three runs in a declared data folder: #1 genesis (n_avg the int 1);
    #2 T1 changed and n_avg saved as the float 1.0 (a type-only save the
    ledger's rows do not record); #3 f_01 changed. The live chip holds #3."""
    return build_env(tmp_path, [chip_state(), chip_state(t1=3.0e-5, n_avg=1.0),
                                chip_state(t1=3.0e-5, n_avg=1.0, f01=5.1e9)])


def build_env(tmp_path, states):
    """One run per state in a declared data folder; the live chip holds the last."""
    from quam_state_manager.web.app import create_app
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    folders = [run(data, i + 1, s) for i, s in enumerate(states)]
    live.mkdir(parents=True)
    current = json.loads(json.dumps(states[-1]))
    current["extras"]["data_folder"] = str(data)
    (live / "state.json").write_text(json.dumps(current), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    app.config["HUB_SYNC_ON_OPEN"] = True
    client = app.test_client()
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    with app.app_context():
        chip_dir = Path(routes_mod._hub_chip_dir(routes_mod._active_ctx()["path"]))
    assert hub_sync.status(chip_dir)["state"] == "ready"
    with HubStore(chip_dir) as store:
        events = [dict(r) for r in store.conn.execute("SELECT * FROM events ORDER BY ord")]
    return SimpleNamespace(app=app, client=client, live=live, data=data, folders=folders,
                           states=states, chip_dir=chip_dir, tmp=tmp_path,
                           refs=[hub_versions.ref_of(e) for e in events], events=events)


def ctx_of(env):
    return env.app.config["contexts"][env.app.config["active_context"]]


def apply_edit(env, path, value, actor="tester"):
    assert env.client.post("/field/edit", data={"dot_path": path, "value": str(value)}).status_code == 200
    r = env.client.post("/state/apply-to-live", headers={"X-SM-Actor": actor})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]


def sm_lines(env) -> list[dict]:
    lines = (env.chip_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines if '"kind"' in x]


def snapshot_count(env) -> int:
    return sum(1 for p in env.chip_dir.iterdir() if (p / "meta.json").is_file())


# ======================================================================
# 1. references and the two documents
# ======================================================================

def test_a_version_id_is_checked_against_its_event_instant(env):
    ref = env.refs[1]
    eid, stamp = hub_versions.parse_ref(ref)
    assert eid == env.events[1]["eid"] and ref.startswith(stamp)
    for bad in ("event-2", "20260101_120020_000000_event-0", "20260101_120020_000000_event-2/../x",
                "20260101_120020_event-2", "../20260101_120020_000000_event-2"):
        assert not hub_versions.is_ref(bad), bad
    # a reference from an earlier build of the ledger names another event now
    stale = "19990101_000000_000000_event-%d" % eid
    with pytest.raises(hub_versions.Unavailable, match="earlier build"):
        hub_versions.document(env.chip_dir, stale)
    with pytest.raises(hub_versions.Unavailable, match="earlier build"):
        hub_versions.exact_pair(env.chip_dir, stale)


def test_the_diff_document_is_the_ledgers_and_needs_no_archive_file(env):
    want = hub_rules.flatten(hub_rules.merged(env.states[1], WIRING))
    shutil.rmtree(env.folders[1])
    doc = hub_versions.document(env.chip_dir, env.refs[1])
    got = hub_rules.flatten(doc)
    assert got.keys() == want.keys() and all(hub_rules.same(got[k], want[k]) for k in want)
    assert doc["qubits"]["qA1"]["freq_ref"] == "#/qubits/qA1/f_01", "pointer strings stay as stored"


def test_the_exact_pair_is_the_saved_files_types_and_all(env):
    state, wiring = hub_versions.exact_pair(env.chip_dir, env.refs[1])
    assert json.dumps(state, sort_keys=True) == json.dumps(env.states[1], sort_keys=True)
    assert wiring == WIRING
    assert type(state["qubits"]["qA1"]["n_avg"]) is float
    # run #1 saved the int 1: its files say so, while the ledger's replay
    # hands every stored number back as a float (changes.num is REAL) --
    # equal for Diff, not for a write
    first, _ = hub_versions.exact_pair(env.chip_dir, env.refs[0])
    assert type(first["qubits"]["qA1"]["n_avg"]) is int
    assert type(hub_versions.document(env.chip_dir, env.refs[0])["qubits"]["qA1"]["n_avg"]) is float
    state["qubits"]["qA1"]["T1"] = 9.0
    again, _ = hub_versions.exact_pair(env.chip_dir, env.refs[1])
    assert again["qubits"]["qA1"]["T1"] == 3.0e-5, "the caller owns a fresh copy"


@pytest.mark.parametrize("how", ["deleted", "rewritten"])
def test_the_exact_pair_refuses_when_the_saved_files_are_not_the_state(env, how):
    assert hub_versions.exact_pair(env.chip_dir, env.refs[0])[0] == env.states[0], \
        "read once while the files are there: a later press must not answer from RAM"
    if how == "deleted":
        shutil.rmtree(env.folders[0])
    else:
        (env.folders[0] / "quam_state" / "state.json").write_text(
            json.dumps(chip_state(t1=7.0)), encoding="utf-8")
    with pytest.raises(hub_versions.Unavailable, match="SOURCE_GONE"):
        hub_versions.exact_pair(env.chip_dir, env.refs[0])
    assert hub_versions.document(env.chip_dir, env.refs[0])["qubits"]["qA1"]["T1"] == 1.0e-5


@pytest.mark.parametrize("fault,words", [
    ("derived", "DERIVED"), ("blob", "cannot rebuild"), ("uncertain", "chip identity is uncertain"),
    ("error", "No saved state")])
def test_a_state_the_ledger_cannot_hand_over_refuses(env, fault, words):
    eid = env.events[1]["eid"]
    with HubStore(env.chip_dir) as store:
        if fault == "derived":
            store.conn.execute("UPDATE events SET flags=flags|32 WHERE eid=?", (eid,))
        elif fault == "blob":
            store.conn.execute("DELETE FROM blobs WHERE hash=(SELECT shape_hash FROM events WHERE eid=?)", (eid,))
        elif fault == "uncertain":
            store.conn.execute("UPDATE events SET flags=flags|4 WHERE eid=?", (eid,))
        else:
            store.conn.execute("UPDATE events SET error='unreadable' WHERE eid=?", (eid,))
        store.conn.commit()
    with pytest.raises(hub_versions.Unavailable, match=words):
        hub_versions.document(env.chip_dir, env.refs[1])
    if fault != "blob":
        with pytest.raises(hub_versions.Unavailable, match=words):
            hub_versions.exact_pair(env.chip_dir, env.refs[1])


def test_an_sm_write_hands_over_the_pair_sm_wrote_and_verifies_a_replay(env):
    apply_edit(env, "qubits.qA1.T1", "4e-05")
    apply_edit(env, "qubits.qA1.T1", "5e-05")
    with HubStore(env.chip_dir) as store:
        sm = [dict(r) for r in store.conn.execute(
            "SELECT e.*, (SELECT 1 FROM sm_anchors a WHERE a.eid=e.eid) AS anchored FROM events e "
            "WHERE kind='sm_apply' ORDER BY ord")]
    assert len(sm) == 2 and sm[0]["anchored"] and not sm[1]["anchored"], \
        "the first write is an anchor, the second replays from it"
    first, _ = hub_versions.exact_pair(env.chip_dir, hub_versions.ref_of(sm[0]))
    assert first["qubits"]["qA1"]["T1"] == 4e-05
    first["qubits"]["qA1"]["T1"] = 9.0
    assert hub_versions.exact_pair(env.chip_dir, hub_versions.ref_of(sm[0]))[0]["qubits"]["qA1"]["T1"] == 4e-05, \
        "the caller owns a fresh copy of a remembered pair"
    # a replay that does not give the content hash recorded at the door refuses
    with HubStore(env.chip_dir) as store:
        real = store.conn.execute("SELECT post_chash FROM sm_events WHERE eid=?", (sm[1]["eid"],)).fetchone()[0]
        store.conn.execute("UPDATE sm_events SET post_chash='0' WHERE eid=?", (sm[1]["eid"],))
        store.conn.commit()
    with pytest.raises(hub_versions.Unavailable, match="cannot be rebuilt exactly"):
        hub_versions.exact_pair(env.chip_dir, hub_versions.ref_of(sm[1]))
    with HubStore(env.chip_dir) as store:
        store.conn.execute("UPDATE sm_events SET post_chash=? WHERE eid=?", (real, sm[1]["eid"]))
        store.conn.commit()
    live_pair = (safe_io.read_json(env.live / "state.json"), safe_io.read_json(env.live / "wiring.json"))
    state, wiring = hub_versions.exact_pair(env.chip_dir, hub_versions.ref_of(sm[1]))
    assert content_hash(state, wiring) == sm[1]["chash"]
    # a kept pair that is not the content recorded at the door is not handed over
    with HubStore(env.chip_dir) as store:
        store.conn.execute("UPDATE events SET chash='0' WHERE eid=?", (sm[0]["eid"],))
        store.conn.commit()
    hub_versions._PAIRS.clear()
    with pytest.raises(hub_versions.Unavailable, match="do not match the content hash"):
        hub_versions.exact_pair(env.chip_dir, hub_versions.ref_of(sm[0]))
    assert content_hash(state, wiring) == content_hash(*live_pair) == sm[1]["chash"]
    assert state["qubits"]["qA1"]["T1"] == 5e-05


# ======================================================================
# 2. rows: named events, older snapshots never listed twice
# ======================================================================

def snap(ts, *, trigger="manual", run_id=None, experiment=None, kind=None):
    return SimpleNamespace(timestamp=ts, trigger=trigger, run_id=run_id, experiment_name=experiment,
                           experiment_folder_path=None, kind=kind, state_hash=None, label=None,
                           note=None, pinned=False)


def write_snapshot(env, ts, state, wiring=WIRING) -> None:
    safe_io.write_state_wiring(env.chip_dir / ts, state, wiring)


def read_now(env, snaps, **kw):
    h = hub_versions.hashes_for(env.chip_dir)
    hub_versions.read(env.chip_dir, snaps, **kw)
    assert h.wait(30)
    return hub_versions.read(env.chip_dir, snaps, **kw)


def test_rows_are_events_newest_first(env):
    res = hub_versions.read(env.chip_dir, [])
    assert res["mode"] == "ledger"
    assert [r["ref"] for r in res["rows"]] == env.refs[::-1]
    assert res["total"] == 3 and res["events"] == 3


def test_an_older_snapshot_is_listed_only_when_no_event_holds_its_state(env):
    write_snapshot(env, "20260101_120020_000001", env.states[1])          # run #2's own state
    write_snapshot(env, "20260101_120019_000001", chip_state(t1=2.0e-5))  # run #2 before its save
    write_snapshot(env, "20251231_120000_000000", env.states[2])          # #3's content, no run
    write_snapshot(env, "20251230_120000_000000", chip_state(t1=8.0))     # before the ledger
    snaps = [snap("20260101_120020_000001", trigger="experiment", run_id=2, experiment="scan"),
             snap("20260101_120019_000001", trigger="experiment", run_id=2, experiment="scan"),
             snap("20251231_120000_000000", trigger="auto"),
             snap("20251230_120000_000000", trigger="auto")]
    res = read_now(env, snaps)
    listed = [r["ref"] for r in res["rows"] if r["legacy"]]
    assert listed == ["20260101_120019_000001", "20251230_120000_000000"], listed
    assert res["total"] == 5 and res["legacy_total"] == 2


def test_the_ledgers_observed_import_verdict_covers_a_snapshot(env):
    write_snapshot(env, "20251230_120000_000000", chip_state(t1=8.0))
    with HubStore(env.chip_dir) as store:
        hub_sync._observed_table(store.conn)
        store.conn.execute("INSERT INTO observed_snapshots VALUES(?,?,NULL)",
                           ("20251230_120000_000000", "same_after"))
        store.conn.commit()
    res = read_now(env, [snap("20251230_120000_000000", trigger="auto")])
    assert not any(r["legacy"] for r in res["rows"])


def test_matching_runs_in_the_background_and_the_row_says_so(env, monkeypatch):
    write_snapshot(env, "20260101_120020_000001", env.states[1])
    gate = threading.Event()
    real = hub_versions._Hashes._hash_one

    def slow(self, ts):
        gate.wait(10)
        return real(self, ts)
    monkeypatch.setattr(hub_versions._Hashes, "_hash_one", slow)
    snaps = [snap("20260101_120020_000001", trigger="experiment", run_id=2, experiment="scan")]
    res = hub_versions.read(env.chip_dir, snaps)
    pending = [r for r in res["rows"] if r["legacy"]]
    assert res["pending"] == 1 and pending and pending[0]["pending"]
    gate.set()
    assert hub_versions.hashes_for(env.chip_dir).wait(30)
    res = hub_versions.read(env.chip_dir, snaps)
    assert res["pending"] == 0 and not any(r["legacy"] for r in res["rows"])
    cached = json.loads((env.chip_dir / hub_versions.HASHES_FILE).read_text(encoding="utf-8"))
    assert "20260101_120020_000001" in cached["hashes"], "the hashes survive a restart"


def test_filtered_events_never_leave_a_short_page(env):
    with HubStore(env.chip_dir) as store:
        top = store.conn.execute("SELECT * FROM events ORDER BY ord DESC LIMIT 1").fetchone()
        for i in range(60):
            store.conn.execute(
                "INSERT INTO events(kind,t_utc_us,ord,run_id,experiment,state_hash,flags,shape_hash,n_changes) "
                "VALUES('run',?,?,?,?,?,4,?,0)",
                (top["t_utc_us"] + (i + 1) * 1000, top["ord"] + i + 1, 100 + i, "scan",
                 top["state_hash"], top["shape_hash"]))
        store.conn.commit()
    res = hub_versions.read(env.chip_dir, [], limit=2)
    assert [r["ref"] for r in res["rows"]] == env.refs[::-1][:2]
    assert res["total"] == 3 and res["uncertain"] == 60


@pytest.mark.parametrize("case", ["building", "preparing", "no_ledger", "no_runs", "unreadable"])
def test_modes_other_than_ledger_say_why(env, monkeypatch, case):
    from quam_state_manager.core import ramcache
    directory = env.chip_dir
    if case == "building":
        monkeypatch.setattr(hub_sync, "status", lambda _d: {"state": "building", "done": 1, "total": 3})
    elif case == "preparing":
        def warming(*_a, **_k):
            raise ramcache.Warming("hub_read_lock", str(directory), 0.25)
        monkeypatch.setattr(hub_query_mod(), "timeline", warming)
    elif case == "no_ledger":
        directory = env.tmp / "empty"
        directory.mkdir()
    elif case == "no_runs":
        monkeypatch.setattr(hub_sync, "status", lambda _d: {"state": "idle"})
        with HubStore(directory) as store:
            store.conn.execute("UPDATE events SET kind='sm_apply', status='failed', error='x'")
            store.conn.commit()
    else:
        monkeypatch.setattr(hub_query_mod(), "timeline", lambda *a, **k: 1 / 0)
    res = hub_versions.read(directory, [])
    # S10 C3: old -> new, no runs is ledger; permanent errors are unavailable.
    assert res["mode"] == (case if case in ("building", "preparing") else
                           "ledger" if case == "no_runs" else "unavailable")
    assert not res["rows"], "no ledger row is drawn from a list that is not complete"
    if case not in ("building", "preparing", "no_runs"):
        assert res["reason"] == case


def hub_query_mod():
    from quam_state_manager.core import hub_query
    return hub_query


def test_the_one_comparison_rule():
    a = ({"x": 1, "n": float("nan"), "s": "a", "t": 1.0, "__class__": "A"}, {})
    b = ({"x": 1.0, "n": float("nan"), "s": "a", "t": 1.0 + 1e-12, "__class__": "B"}, {})
    c = ({"x": 2.0, "n": float("nan"), "s": "b", "t": 1.0, "__class__": "B"}, {})
    assert [e.dot_path for e in hub_versions.compare(a, b)] == ["__class__"]
    rows = hub_versions.compare_n([a, b, c])
    assert {r["dot_path"]: r["changed"] for r in rows} == {
        "__class__": [False, True, False], "s": [False, False, True], "x": [False, False, True]}


# ======================================================================
# 3. the two surfaces
# ======================================================================

def test_both_surfaces_name_runs_sm_writes_and_observed_states(env):
    apply_edit(env, "qubits.qA1.T1", "4e-05")
    write_snapshot(env, "20260101_115900_000000", chip_state(t1=6.0e-6))
    with HubStore(env.chip_dir) as store:
        assert hub_sync.attach_observed(store, {
            "ts": "20260101_115900_000000", "t_us": T0 - 60_000_000, "trigger": "auto",
            "dir": str(env.chip_dir / "20260101_115900_000000")}) in ("added", "inserted")
    panel = env.client.get("/state/versions").get_data(as_text=True)
    assert 'data-source="ledger"' in panel
    rows = panel.split('<li class="state-version-row')[1:]
    now = [r for r in rows if "on this now" in r]
    assert len(now) == 1 and "applied by tester" in text(now[0]), "the live chip holds the SM write"
    words = text(panel)
    # docs/301 F16: the sub-line names the door in words, never its raw id
    assert "run #3 scan" in words and "applied by tester" in words and "SM write · Applied" in words
    assert "seen by SM (auto snapshot) writer unknown" in words
    page = text(env.client.get("/state-history").get_data(as_text=True))
    assert "run #1 scan" in page and "applied by tester" in page and "seen by SM (auto snapshot)" in page


def test_diff_and_compare_read_ledger_documents_in_time_order(env):
    c = env.client
    body = text(c.get(f"/state/versions/{env.refs[0]}/diff").get_data(as_text=True))
    assert "qubits.qA1. f_01" in body and "qubits.qA1. T1" in body
    assert "n_avg" not in body, "1 and 1.0 are one value under the one rule"
    r = c.get(f"/diff/snapshots?ts_a={env.refs[2]}&ts_b={env.refs[0]}")
    target = r.headers.get("HX-Redirect") or r.headers.get("Location") or ""
    assert target.index(env.refs[0]) < target.index(env.refs[2]), "oldest on the left"
    body = c.get(f"/diff/versions?ts={env.refs[2]}&ts={env.refs[0]}&ts={env.refs[1]}").get_data(as_text=True)
    assert body.index(env.refs[0]) < body.index(env.refs[1]) < body.index(env.refs[2])
    assert "run #2 scan" in body and "qubits.qA1.T1" in body and "n_avg" not in body
    chip = env.chip_dir.name
    state = c.get(f"/diff/data?a=hist:{chip}/{env.refs[0]}&b=hist:{chip}/{env.refs[2]}&tab=state").get_json()
    assert state["ok"] and state["counts"]["total"] == 2
    wiring = c.get(f"/diff/data?a=hist:{chip}/{env.refs[0]}&b=hist:{chip}/{env.refs[2]}&tab=wiring").get_json()
    assert not wiring["ok"] and "one merged document" in wiring["unavailable"]
    # against the working state: both sides merged, so only the data-folder
    # link the live chip carries differs from run #3
    now = c.get(f"/diff/data?a=hist:{chip}/{env.refs[2]}&b=working:{env.live}&tab=state").get_json()
    assert now["ok"] and now["counts"]["total"] == 1, now["counts"]


def test_stage_apply_revert_round_trip_through_the_existing_doors(env):
    c = env.client
    def typed():
        return tuple(json.dumps(safe_io.read_json(env.live / n), sort_keys=True)
                     for n in ("state.json", "wiring.json"))
    baseline = typed()
    before = safe_io.read_json(env.live / "state.json")
    r = c.post(f"/state-history/{env.refs[1]}/stage")
    assert r.status_code == 200 and "loaded as the working state" in r.get_data(as_text=True)
    assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "tester"}).status_code == 200
    state, wiring = safe_io.read_json(env.live / "state.json"), safe_io.read_json(env.live / "wiring.json")
    # docs/301 F6: the version's saved files exactly, except the chip's own
    # identity extras (name, data folder), which the stage keeps and names
    from quam_state_manager.core.identity_extras import keep_identity
    want, kept = keep_identity(env.states[1], before)
    assert [k["key"] for k in kept] == ["data_folder"]
    assert "Kept this chip&#39;s own data folder" in r.get_data(as_text=True)
    assert json.dumps(state, sort_keys=True) == json.dumps(want, sort_keys=True)
    assert wiring == WIRING
    line = sm_lines(env)[-1]
    assert (line["kind"], line["actor"], line["src"]) == ("sm_apply", "human:tester", "apply_staged")
    assert line["ref"]["snapshot"] == env.refs[1]
    pre = ctx_of(env)["last_apply"]["pre_ts"]
    assert c.post(f"/state-history/{pre}/stage?from=tray").status_code == 200
    assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "tester"}).status_code == 200
    assert typed() == baseline, "Revert last apply puts the chip back, types and all"
    assert sm_lines(env)[-1]["src"] == "revert_last_apply"


def test_restore_live_keeps_every_gate_and_the_record(env):
    c = env.client
    c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "9e-05"})
    r = c.post(f"/state-history/{env.refs[0]}/restore-live")
    assert r.status_code == 409 and "Restoring this version to live" in r.get_data(as_text=True),         "unsaved edits; a version is never called a snapshot"
    wiring = json.loads((env.live / "wiring.json").read_bytes())
    wiring["network"]["host"] = "127.0.0.2"
    (env.live / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    r = c.post(f"/state-history/{env.refs[0]}/restore-live?force_pending=1")
    assert r.status_code == 409 and "This version&#39;s wiring does not match" in r.get_data(as_text=True), "topology"
    before = snapshot_count(env)
    r = c.post(f"/state-history/{env.refs[0]}/restore-live?force_pending=1&force_align=1",
               headers={"X-SM-Actor": "tester"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert safe_io.read_json(env.live / "state.json")["qubits"]["qA1"]["T1"] == 1.0e-5
    assert snapshot_count(env) > before, "the current live state was snapshotted first"
    line = sm_lines(env)[-1]
    assert (line["kind"], line["actor"]) == ("restore", "human:tester")


def test_a_version_the_ledger_cannot_hand_over_refuses_before_any_write(env):
    shutil.rmtree(env.folders[0])
    c = env.client
    c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "9e-05"})
    live = tuple((env.live / n).read_bytes() for n in ("state.json", "wiring.json"))
    before = snapshot_count(env)
    for action in ("stage", "restore-live"):
        r = c.post(f"/state-history/{env.refs[0]}/{action}")
        assert r.status_code == 404 and "SOURCE_GONE" in r.get_data(as_text=True), action
    assert snapshot_count(env) == before, "no backup for a restore that cannot happen"
    assert tuple((env.live / n).read_bytes() for n in ("state.json", "wiring.json")) == live
    panel = env.client.get("/state/versions").get_data(as_text=True)
    row = panel.split(env.refs[0] + '"')[1].split("</li>")[0]
    assert "SOURCE_GONE" in row and "sv-restore" not in row and "sv-stage" not in row
    assert "StateVersions.diff" in row, "Diff still reads the ledger's document"
    page = env.client.get("/state-history").get_data(as_text=True)
    entry = page.split(f'data-ts="{env.refs[0]}"')[1].split('data-ts="')[0]
    assert "SOURCE_GONE" in entry and "/stage?" not in entry and "/restore-live?" not in entry
    assert "View changes vs current" in entry
    assert "Pull to Live" not in c.get(f"/state/versions/{env.refs[0]}/diff").get_data(as_text=True)
    assert "Pull to Live" in c.get(f"/state/versions/{env.refs[1]}/diff").get_data(as_text=True)


def test_another_chips_version_is_refused(env):
    for action in ("stage", "restore-live"):
        r = env.client.post(f"/state-history/{env.refs[0]}/{action}?chip_key=another")
        assert r.status_code == 409 and "open that chip first" in r.get_data(as_text=True)
    env.client.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "9e-05"})
    r = env.client.post(f"/state-history/{env.refs[0]}/stage?target=status&chip_key={env.chip_dir.name}")
    body = r.get_data(as_text=True)
    assert r.status_code == 409 and f"chip_key={env.chip_dir.name}" in body and "#status-bar" in body


def test_a_ledger_version_carries_no_label(env):
    r = env.client.post(f"/state-history/{env.refs[0]}/label", data={"label": "x"})
    assert r.status_code == 409


# S10 C6: renamed from test_a_chip_without_a_ledger_keeps_the_old_path_labelled -- the
# old path is deleted; a fresh chip gets a ledger and the link offer, which is what it pins
def test_a_fresh_chip_lists_through_its_new_ledger_with_the_link_offer(tmp_path):
    from quam_state_manager.web.app import create_app
    live = tmp_path / "live"
    live.mkdir()
    (live / "state.json").write_text(json.dumps(chip_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(live)})
    panel = c.get("/state/versions").get_data(as_text=True)
    # S10 C1: the open creates the ledger of a chip with no data folder (its first
    # sync slice); with nothing in it the panel says "holds no runs", not "no ledger yet"
    # S10 C3: old -> new, a fresh ledger uses the ledger renderer and link offer.
    assert 'data-source="ledger"' in panel and 'data-note="no_folder_linked"' in panel


def test_a_building_ledger_draws_the_older_snapshots_and_says_so(env, monkeypatch):
    monkeypatch.setattr(hub_sync, "status", lambda _d: {"state": "building", "done": 1, "total": 3})
    panel = env.client.get("/state/versions").get_data(as_text=True)
    # S10 C3: old -> new, building lists legacy snapshots through the ledger renderer.
    assert 'data-source="ledger"' in panel and "being built (1 of 3 runs)" in panel
    assert "run #" not in text(panel)
    assert "No recorded versions" not in panel, "the runs being read are not denied"
    # S10 C6: + the panel's own building line -- with the snapshot list deleted, a dropped
    # branch fell through to an empty Compare list (mutation building_says_no_versions survived)
    assert "No older snapshots to show meanwhile." in panel and 'id="sv-compare"' not in panel
    page = env.client.get("/state-history").get_data(as_text=True)
    assert "being built (1 of 3 runs)" in page and "run #1 scan" not in page


# ======================================================================
# 4. the fault table at the real doors (docs/284 section 6)
# ======================================================================

def release_ledger(env) -> None:
    """Close every handle on the chip's ledger (before deleting or damaging
    it, as a crash or a person would)."""
    from quam_state_manager.core import hub_index
    hub_index.close_readers()
    for h in list(hub.Hub._cache.values()):
        h.release()


def footprint(env) -> tuple:
    """What a refused door must not touch: the live bytes, the snapshot
    folders, the SM journal, the working state (a pending edit included)."""
    journal = env.chip_dir / "events.jsonl"
    return (tuple((env.live / n).read_bytes() for n in ("state.json", "wiring.json")),
            sorted(p.name for p in env.chip_dir.iterdir() if (p / "meta.json").is_file()),
            journal.read_bytes() if journal.is_file() else b"",
            json.dumps(ctx_of(env)["store"].merged, sort_keys=True, default=str))


def make_fault(env, fault, monkeypatch) -> str:
    """Inject *fault* by its real mechanism; the version id it is about."""
    if fault == "anchor_blob":
        apply_edit(env, "qubits.qA1.T1", "4e-05")
        apply_edit(env, "qubits.qA1.T1", "5e-05")
        with HubStore(env.chip_dir) as store:
            first = store.conn.execute("SELECT e.*, a.hash AS blob FROM events e JOIN sm_anchors a "
                                       "USING(eid) WHERE e.kind='sm_apply' ORDER BY e.ord LIMIT 1").fetchone()
            store.conn.execute("DELETE FROM blobs WHERE hash=?", (first["blob"],))
            store.conn.commit()
        return hub_versions.ref_of(first)
    if fault == "derived":
        # an SM write projected without its bytes (the restart path)
        rec = hub.Hub.for_chip(env.chip_dir).record(
            "sm_apply", "human:tester", [{"path": "qubits.qA1.T1", "old": 3.0e-5, "new": 7.0e-5}],
            None, None, "apply", post_hash="unknown")
        rec.landed()
        with HubStore(env.chip_dir) as store:
            ev = store.conn.execute("SELECT * FROM events WHERE kind='sm_apply' ORDER BY ord DESC LIMIT 1").fetchone()
        assert ev["flags"] & hub_versions.DERIVED
        return hub_versions.ref_of(ev)
    if fault == "uncertain":
        run(env.data, 4, chip_state(t1=9.0e-5, f01=5.1e9, name="another-device"))
        with env.app.app_context():
            hub_sync.on_roots_moved([str(env.data)])
        with HubStore(env.chip_dir) as store:
            ev = store.conn.execute("SELECT * FROM events WHERE run_id=4").fetchone()
        assert ev["flags"] & 4
        return hub_versions.ref_of(ev)
    if fault == "stale":
        # the ledger is rebuilt with an older run added: every id after it shifts
        old = env.refs[1]
        release_ledger(env)
        for name in ("ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm"):
            (env.chip_dir / name).unlink(missing_ok=True)
        run(env.data, 0, chip_state(t1=0.5e-5))
        cs = hub_sync.sync_for(env.chip_dir)
        cs.request(full=True)
        hub_sync._kick(cs)
        with HubStore(env.chip_dir) as store:
            moved = store.conn.execute("SELECT * FROM events WHERE run_id=2").fetchone()
        assert hub_versions.ref_of(moved) != old
        return old
    if fault == "unreadable":
        release_ledger(env)
        for name in ("ledger.sqlite-wal", "ledger.sqlite-shm"):
            (env.chip_dir / name).unlink(missing_ok=True)
        (env.chip_dir / "ledger.sqlite").write_bytes(b"not a database " * 512)
        return env.refs[1]
    assert fault == "building"
    monkeypatch.setattr(hub_sync, "status", lambda _d: {"state": "building", "done": 1, "total": 3})
    return env.refs[1]


@pytest.mark.parametrize("fault,words", [
    ("anchor_blob", "Missing ledger blob"), ("derived", "DERIVED"),
    ("uncertain", "chip identity is uncertain"), ("stale", "earlier build of the change history"),
    ("unreadable", "The change history could not be read"),
    ("building", "The change history is being built (1 of 3 runs); try again when it is complete.")])
def test_every_door_refuses_a_fault_with_its_reason_and_writes_nothing(env, monkeypatch, fault, words):
    c = env.client
    c.post("/field/edit", data={"dot_path": "qubits.qA2.f_01", "value": "6.5e9"})   # a pending edit
    ref = make_fault(env, fault, monkeypatch)
    before = footprint(env)
    try:
        panel = c.get("/state/versions").get_data(as_text=True)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"the Versions panel raised {exc!r}")
    if f'value="{ref}"' in panel:
        row = panel.split(f'value="{ref}"')[1].split("</li>")[0]
        assert words.split(" (")[0] in text(row) and "sv-stage" not in row and "sv-restore" not in row
    body = text(c.get(f"/state/versions/{ref}/diff").get_data(as_text=True))
    assert "Diff failed" in body and words in body, body[:300]
    for action in ("stage", "restore-live"):
        r = c.post(f"/state-history/{ref}/{action}?chip_key={env.chip_dir.name}",
                   headers={"X-SM-Actor": "tester"})
        said = text(r.get_data(as_text=True))
        assert r.status_code == 404 and words in said, (action, r.status_code, said[:300])
        assert "unsaved edits" not in said, "the refusal comes before the confirm"
        if fault == "building":
            assert "cannot be staged or restored exactly" not in said, "not now is not never"
    assert footprint(env) == before, "a refused door writes nothing: no backup, no record, no stage"


def test_a_runs_missing_blob_stops_its_diff_not_its_exact_files(env):
    with HubStore(env.chip_dir) as store:
        shape = store.conn.execute("SELECT shape_hash FROM events WHERE eid=?", (env.events[0]["eid"],)).fetchone()[0]
        store.conn.execute("DELETE FROM blobs WHERE hash=?", (shape,))
        store.conn.commit()
    panel = env.client.get("/state/versions").get_data(as_text=True)
    row = panel.split(f'value="{env.refs[0]}"')[1].split("</li>")[0]
    assert "Missing ledger blob" in row and "sv-stage" in row and "sv-restore" in row
    assert re.search(r'class="btn-xs outline sv-diff" disabled', row), "Diff cannot be rebuilt"
    page = text(env.client.get("/state-history").get_data(as_text=True))
    assert "Missing ledger blob" in page
    body = text(env.client.get(f"/state/versions/{env.refs[0]}/diff").get_data(as_text=True))
    assert "Diff failed" in body and "missing ledger blob" in body
    # the run's own saved files are still exact: Stage + Apply writes them
    c = env.client
    before = safe_io.read_json(env.live / "state.json")
    assert c.post(f"/state-history/{env.refs[0]}/stage").status_code == 200
    assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "tester"}).status_code == 200
    # docs/301 F6: exact except the chip's own identity extras, which it keeps
    from quam_state_manager.core.identity_extras import keep_identity
    assert (json.dumps(safe_io.read_json(env.live / "state.json"), sort_keys=True)
            == json.dumps(keep_identity(env.states[0], before)[0], sort_keys=True))


def test_the_list_follows_the_ledger_and_the_snapshot_list(env):
    write_snapshot(env, "20251230_120000_000000", chip_state(t1=8.0))
    snaps = [snap("20251230_120000_000000", trigger="auto")]
    first = read_now(env, snaps)
    assert [r["ref"] for r in first["rows"]] == env.refs[::-1] + ["20251230_120000_000000"]
    # a new run lands: the next read lists it (the cached part is keyed on the ledger)
    run(env.data, 4, chip_state(t1=4.0e-5, f01=5.1e9))
    with env.app.app_context():
        hub_sync.on_roots_moved([str(env.data)])
    again = hub_versions.read(env.chip_dir, snaps)
    assert again["events"] == 4 and again["rows"][0]["event"]["run_id"] == 4
    # a new snapshot no event holds: listed (the cached part is keyed on the list too)
    write_snapshot(env, "20251231_120000_000000", chip_state(t1=9.0))
    more = read_now(env, snaps + [snap("20251231_120000_000000", trigger="auto")])
    assert [r["ref"] for r in more["rows"] if r["legacy"]] == ["20251231_120000_000000", "20251230_120000_000000"]
    # a label given since is drawn as it is now (the cached part holds stamps, not rows)
    # (fresh objects, as a new listing after a label edit hands out)
    relabelled = [snap("20251230_120000_000000", trigger="auto"), snap("20251231_120000_000000", trigger="auto")]
    relabelled[0].label = "kept"
    rows = hub_versions.read(env.chip_dir, relabelled)["rows"]
    assert [r["snapshot"].label for r in rows if r["legacy"]] == [None, "kept"]


def test_state_history_pages_through_the_ledger_rows(env):
    hx = {"HX-Request": "true"}
    one = text(env.client.get("/state-history?per_page=2", headers=hx).get_data(as_text=True))
    assert "Page 1 / 2" in one and "run #3 scan" in one and "run #2 scan" in one and "run #1 scan" not in one
    two = text(env.client.get("/state-history?per_page=2&page=2", headers=hx).get_data(as_text=True))
    assert "Page 2 / 2" in two and "run #1 scan" in two and "run #3 scan" not in two


def test_the_workbench_compares_ledger_versions_under_the_one_rule(tmp_path):
    def with_nan(**kw):
        st = chip_state(**kw)
        st["qubits"]["qA2"]["T2echo"] = float("nan")
        return st
    e = build_env(tmp_path, [with_nan(), with_nan(t1=3.0e-5)])
    chip = e.chip_dir.name
    res = e.client.get(f"/diff/data?a=hist:{chip}/{e.refs[0]}&b=hist:{chip}/{e.refs[1]}&tab=state").get_json()
    assert res["ok"] and res["counts"]["total"] == 1, "a NaN on both sides is one value, not a change"


def test_the_ledger_panel_never_asks_the_browser_to_refilter(env):
    """The client used to ask for the panel again while its filter attribute
    differed from the browser's changes-only choice (docs/132); a value on
    ledger rows asked again forever (found in the real browser: 23 requests in
    4 s while the panel stood open). The filter is gone (S10 C6): an old URL
    still naming a mode gets the same rows and a root with no filter mark."""
    # S10 C6: "no mark, or the mode asked" -> no mark at all and one answer, the filter is deleted
    roots, rows = [], []
    for mode in ("only", "all"):
        body = env.client.get(f"/state/versions?changes={mode}").get_data(as_text=True)
        root = re.search(r'<div class="state-versions"[^>]*>', body).group(0)
        assert re.findall(r'\sdata-([\w-]+)=', root) == ["source"], root
        assert 'data-source="ledger"' in root
        roots.append(root)
        rows.append(re.findall(r'sv-check" value="([^"]+)"', body))
    assert roots[0] == roots[1] and rows[0] == rows[1] and rows[0]


def test_an_archive_lists_versions_but_offers_no_write_and_no_live_mark(env):
    """A run's own saved state opened read-only: the same ledger rows, Diff
    still offered, no Stage / Restore, and no row marked as on the live chip
    (there is none open); a direct Restore-live press is refused."""
    c = env.client
    assert c.post("/load", data={"folder": str(env.folders[2] / "quam_state")}).status_code in (200, 302)
    panel = c.get("/state/versions").get_data(as_text=True)
    assert 'data-source="ledger"' in panel and "run #3 scan" in text(panel)
    assert "sv-stage" not in panel and "sv-restore" not in panel
    assert "on this now" not in panel, "run #3's files are open, not a live chip"
    page = c.get("/state-history", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "/restore-live?" not in page and "/stage?" not in page and "on this now" not in page
    r = c.post(f"/state-history/{env.refs[1]}/restore-live")
    assert r.status_code in (400, 403, 409) and "read-only" in r.get_data(as_text=True)


def test_a_ledger_rows_confirms_name_its_time_in_the_pages_zone(env):
    """docs/301 (F5): the Load/Restore confirms said "13:30:24 UTC" beside a
    row that reads 22:30 -- the same version looked like two. The confirm
    names the row's instant in the page's zone, offset included."""
    from quam_state_manager.core import project_time
    from quam_state_manager.core.timefmt import local_text, to_utc
    project_time.set_zone(env.app.instance_path, "proj", "America/Los_Angeles")
    page = env.client.get("/state-history").get_data(as_text=True)
    rows = page.split('<span class="sh-time">')[1:]
    assert rows, page[:400]
    for row in rows:
        utc = re.search(r'data-utc="([^"]+)"', row).group(1)
        want = local_text(to_utc(utc), "America/Los_Angeles")
        confirms = re.findall(r'hx-confirm="([^"]*)"', row.split('<span class="sh-time">')[0])
        assert confirms and all(want in x for x in confirms if "version of" in x), (want, confirms)
        assert not any(" UTC " in x for x in confirms), confirms


def test_the_value_drawer_reaches_its_older_points(env, monkeypatch):
    """docs/301 F15: "N older not shown" named points the drawer had no way to
    show; its footer now offers Show all, which loads every point."""
    monkeypatch.setattr(routes_mod, "_VH_DRAWER_LIMIT", 1)
    c = env.client
    first = c.get("/field/history?path=qubits.qA1.T1").get_data(as_text=True)
    total = int(re.search(r"(\d+) of (\d+) change point", text(first)).group(2))
    assert total >= 2 and "older not shown" in first, text(first)[-300:]
    assert f"Show all {total}" in first and "FieldHistory.showAll(this)" in first
    assert 'data-path="qubits.qA1.T1"' in first
    every = c.get("/field/history?path=qubits.qA1.T1&all=1").get_data(as_text=True)
    assert "older not shown" not in every and "Show all" not in every
    assert re.search(rf"\b{total} change points", text(every)), text(every)[-300:]
    js = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "showAll: showAll" in js and '(all ? "&all=1" : "")' in js, "the button reaches the all=1 load"


@pytest.mark.parametrize("total", [5001, 6123])
def test_show_all_drawer_discloses_the_newest_points_cap(env, monkeypatch, total):
    requested = []

    def fake_history(ctx, path_map, *, limit=None, **kwargs):
        requested.append(limit)
        return {"mode": "ledger", "limit": limit}

    point = {"eid": 1, "provenance": "unknown", "display": "1", "t": None,
             "flag_text": [], "fill": "1", "value": 1, "usable": True,
             "is_current": False, "removed": False}

    def fake_view(answer, key, path):
        return {"dot_path": path, "tgt": {"holder": path, "has_current": True},
                "points": [point] * min(total, answer["limit"]), "total": total,
                "via": [], "via_since": None, "notes": [], "renames": [],
                "ledger": {"events": total}, "chart": [],
                "current_display": "1", "current_value": 1}

    monkeypatch.setattr(routes_mod, "_value_history", fake_history)
    monkeypatch.setattr(routes_mod, "_vh_drawer_view", fake_view)
    response = env.client.get("/field/history?path=qubits.qA1.T1&all=1")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert requested == [5000]
    assert body.count('class="vh-row ') == 5000
    footer = re.search(r'<p class="fh-foot vh-foot">(.*?)</p>', body, re.S).group(1)
    assert f"newest 5,000 of {total:,} shown" in text(footer)
    assert f"{total - 5000} older not shown" in text(footer)
    assert 'class="fh-show-all"' not in body


def test_the_report_trends_read_the_ledger(env):
    """docs/301 F22: the shareable report's Trends read what Chip Status >
    Trends reads. With the snapshot table empty and the ledger holding the
    chip's runs, it said "No parameter history is recorded for this chip"."""
    body = env.client.get("/chip-status/report/section/trends?redact=0&window=all").get_data(as_text=True)
    assert "No parameter history is recorded" not in body, text(body)[:300]
    fig = body[body.index('data-rep-metric="T1"'):]
    fig = fig[:fig.index("</figure>")]
    assert "<polyline" in fig and "qA1" in fig, text(fig)[:300]
    # a step, as the live chart draws it: a corner that keeps the earlier
    # value, then a vertical move to the next one
    pts = [tuple(float(v) for v in xy.split(","))
           for xy in re.search(r'<polyline[^>]*points="([^"]+)"', fig).group(1).split()]
    assert any(a[1] == b[1] and b[0] == c[0] and b[1] != c[1]
               for a, b, c in zip(pts, pts[1:], pts[2:])), pts


# ======================================================================
# 5. S10 C6: the review of C3 on these surfaces
# ======================================================================

class _Tags(HTMLParser):
    """Every start tag with its attributes and the text up to its end tag --
    what a browser parses, not what a regex hopes."""

    def __init__(self):
        super().__init__()
        self.tags, self._open = [], []

    def handle_starttag(self, tag, attrs):
        item = {"tag": tag, "attrs": dict(attrs), "text": ""}
        self.tags.append(item)
        self._open.append(item)

    def handle_data(self, data):
        if self._open:
            self._open[-1]["text"] += data

    def handle_endtag(self, tag):
        while self._open:
            if self._open.pop()["tag"] == tag:
                break


def _parsed(body: str) -> list[dict]:
    p = _Tags()
    p.feed(body)
    return p.tags


def _bookmark(env, label="known good", note=None):
    """A labelled Param History snapshot of the live chip as it is now."""
    with env.app.app_context():
        hm = routes_mod._history()
        meta = hm.check_and_snapshot(ctx_of(env)["path"], "manual", force=True)
        hm.annotate_snapshot(ctx_of(env)["path"], meta.timestamp, label=label,
                             **({"note": note} if note is not None else {}))
    return meta.timestamp


def _meta(env, ts):
    with env.app.app_context():
        return next(s for s in routes_mod._history().list_snapshots(ctx_of(env)["path"])
                    if s.timestamp == ts)


def test_a_pin_on_a_ledger_row_keeps_its_label_and_only_redraws_the_timeline(env):
    """C3's Pin on a State History ledger row posted only ``pinned``, and the
    label door turned the missing label into "clear": a bookmark labelled
    "known good" lost its label for good on one press. Its title also carried
    ts_local's <span>, which cut the attribute short (the button read
    '... UTC">Pin'). The press is replayed exactly as htmx sends it."""
    ts = _bookmark(env, note="before cooldown")
    body = env.client.get("/state-history?body=1&per_page=40", headers={"HX-Request": "true"}).get_data(as_text=True)
    pins = [t for t in _parsed(body) if t["tag"] == "button"
            and t["attrs"].get("hx-post", "").startswith(f"/state-history/{ts}/label")]
    assert len(pins) == 1, "the row carrying the bookmark offers its Pin"
    pin = pins[0]
    assert pin["text"].strip() == "Pin", repr(pin["text"])
    assert "<" not in pin["attrs"]["title"] and pin["attrs"]["title"].startswith("Keep the Param History snapshot of 20")
    assert pin["attrs"]["hx-target"] == "#state-history-body", "an open diff beside the timeline survives"
    vals = json.loads(pin["attrs"]["hx-vals"])
    r = env.client.post(pin["attrs"]["hx-post"], data=vals, headers={"HX-Request": "true"})
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "sh-timeline" in page and "table-header-row" not in page, "the timeline alone comes back"
    after = _meta(env, ts)
    assert (after.pinned, after.label, after.note) == (True, "known good", "before cooldown")
    # the explicit door still clears: a label that IS sent empty
    env.client.post(f"/state-history/{ts}/label", data={"label": ""}, headers={"HX-Request": "true"})
    assert (_meta(env, ts).label, _meta(env, ts).pinned) == (None, True)


@pytest.mark.parametrize("mode", ["unavailable", "building"])
@pytest.mark.parametrize("url", ["/api/history", "/state/versions", "/state-history?body=1"])
def test_an_unreadable_listing_says_the_older_rows_are_below(env, monkeypatch, url, mode):
    """A listing in ``unavailable`` draws the Param History snapshots as older
    rows (the mode table); C3 put the value drawer's "Nothing older is shown in
    its place." right above them. Its counts called them "0 recorded states"
    "from the change history" in every non-ledger mode (seen in Chrome)."""
    stamps = [_bookmark(env, label=None) for _ in range(2)]

    def broken(*a, **k):
        raise ValueError("corrupt ledger")
    if mode == "building":
        monkeypatch.setattr(hub_sync, "status", lambda _d: {"state": "building", "done": 1, "total": 3})
    else:
        monkeypatch.setattr(hub_versions, "_version_token", broken)
    body = env.client.get(url, headers={"HX-Request": "true"}).get_data(as_text=True)
    assert all(s in body for s in stamps), "the older rows are listed"
    assert "Nothing older is shown" not in body
    assert "recorded state" not in text(body) and "From the change history" not in text(body)
    assert re.search(r"\b2 older snapshots\b", text(body)), text(body)[:600]
    if mode == "unavailable":
        assert ("The change history could not be read (unreadable). Older Param History snapshots are "
                "listed below.") in text(body)


def test_a_bookmark_is_never_carried_by_a_later_row():
    """``_version_annotations`` fell back to the EARLIEST candidate after the
    snapshot when none was at or before it (its own docstring says "at or
    before"): a bookmark taken right after a run, then an SM restore of that
    same state later, put the label and its Pin on the restore row."""
    from types import SimpleNamespace
    live = "C:/chips/live"
    bookmark = SimpleNamespace(timestamp="20260101_120000_000000", label="known good", note=None,
                               pinned=False, state_hash="h1", source_path=live)
    hm = SimpleNamespace(_source_owner=lambda p: (p.lower(), p) if p else None)
    srcs = {bookmark.timestamp: {"kind": "this", "folder": None}}
    t = hub_versions._snapshot_instant(bookmark.timestamp)
    later = {"observed": (), "snapshot_chashes": {bookmark.timestamp: "c1"},
             "writes": [(9, t + 5_000_000, "c1", live)]}
    out, unattached = routes_mod._version_annotations(hm, later, [bookmark], srcs)
    assert out == {} and unattached == {bookmark.timestamp}, "a later restore never takes it"
    both = dict(later, writes=[(8, t - 1, "c1", live), (9, t + 5_000_000, "c1", live)])
    out, unattached = routes_mod._version_annotations(hm, both, [bookmark], srcs)
    assert out == {8: [bookmark]} and not unattached, "the newest write at or before it does"


def test_an_unattached_bookmark_is_listed_as_its_own_older_row(tmp_path):
    """The listing draws a bookmark that only a later row could carry as its
    own older row: a snapshot right after run #2 (the ledger holds that state,
    so it is no row of its own), an SM write, then a restore of run #2 through
    SM. Its note stays on its own row, never on the restore."""
    last = chip_state(t1=3.0e-5)
    last["extras"]["data_folder"] = str(tmp_path / "data")
    env = build_env(tmp_path, [chip_state(), last])
    ts = _bookmark(env, label=None, note="before cooldown")
    with HubStore(env.chip_dir) as store:
        seen = store.conn.execute("SELECT outcome FROM observed_snapshots WHERE ts=?", (ts,)).fetchone()
    assert seen is not None and seen[0] == "same_before", "the premise: run #2 already holds this state"
    apply_edit(env, "qubits.qA1.T1", 9e-05)
    r = env.client.post(f"/state-history/{env.refs[-1]}/restore-live?force_pending=1&force_align=1",
                        headers={"X-SM-Actor": "tester"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    panel = env.client.get("/state/versions").get_data(as_text=True)
    rows = panel.split('<li class="state-version-row')[1:]
    restore = [row for row in rows if "restored by" in text(row)]
    assert restore and all("before cooldown" not in row for row in restore), "a later restore never takes it"
    mine = [row for row in rows if f'value="{ts}"' in row]
    assert len(mine) == 1 and "before cooldown" in mine[0] and "older snapshot" in text(mine[0])
    page = env.client.get("/state-history?per_page=40", headers={"HX-Request": "true"}).get_data(as_text=True)
    entry = page.split(f'data-ts="{ts}"')[1].split('data-ts="')[0]
    assert "before cooldown" in entry and f"/state-history/{ts}/label" in entry


def test_the_drawers_all_is_capped_and_says_so(env, monkeypatch):
    """"All" in the History drawer read and drew the chip's whole ledger
    timeline (limit 2**31 - 1); it lists the newest few hundred and names
    where every one is."""
    monkeypatch.setattr(routes_mod, "_HISTORY_DRAWER_ALL_CAP", 2)
    body = env.client.get("/api/history?per_page=0").get_data(as_text=True)
    assert body.count('class="history-entry hp-ledger-row"') == 2
    assert "All shows the newest 2 of 3;" in text(body) and 'hx-get="/state-history"' in body
    monkeypatch.setattr(routes_mod, "_HISTORY_DRAWER_ALL_CAP", 500)
    body = env.client.get("/api/history?per_page=0").get_data(as_text=True)
    assert body.count('class="history-entry hp-ledger-row"') == 3 and "All shows the newest" not in body


def test_a_version_diff_racing_a_lost_ledger_explains(env, monkeypatch):
    """The per-row Diff read the version's content hash after its try block:
    a ledger lost between the two reads answered 500."""
    def gone(*a, **k):
        raise hub_versions.Unavailable("The change history could not be read (gone).")
    monkeypatch.setattr(hub_versions, "event_info", gone)
    # a server error is the browser's 500, not an exception in the test
    monkeypatch.setitem(env.app.config, "PROPAGATE_EXCEPTIONS", False)
    r = env.client.get(f"/state/versions/{env.refs[0]}/diff")
    assert r.status_code == 200 and "Diff failed" in r.get_data(as_text=True)
