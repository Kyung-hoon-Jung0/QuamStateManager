"""Calibration log contract on generic synthetic ledger facts."""

import json
import gzip
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub_index, hub_rules, hub_store, hub_sync, journal, story, undo_journal
from quam_state_manager.core.hub_store import HubStore, UNDONE, PARTLY_UNDONE
from quam_state_manager.core.loader import ChangeEntry
from quam_state_manager.core.ramcache import Warming
from quam_state_manager.web import journal_routes, routes
from quam_state_manager.web.app import create_app

# The real binding, before any fixture replaces it with a test ledger.
REAL_LEDGER_CONTEXT = journal_routes._ledger_context

DAY = "2026-10-03"


def _card_html(body, marker):
    """The one card element (``<details ...>`` to its ``</details>``) whose
    opening tag carries *marker*."""
    at = body.index(marker)
    start = body.rindex("<details", 0, at)
    return body[start:body.index("</details>", at)]


def _ba(old, new):
    """The before_after macro's set-row markup for two displayed values."""
    return (f'<span class="ba-old">{old}</span> <span class="ba-arrow">&rarr;</span> '
            f'<span class="ba-new">{new}</span>')


@pytest.mark.parametrize("code", [5, 6])
def test_ledger_startup_retries_busy_wal(tmp_path, monkeypatch, code):
    import sqlite3
    pauses = []
    monkeypatch.setattr(hub_store, "time", SimpleNamespace(monotonic=lambda: 0, sleep=pauses.append))
    class Connection:
        attempts = 0
        def execute(self, sql):
            assert sql == "PRAGMA journal_mode=WAL"
            self.attempts += 1
            if self.attempts == 1:
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = code
                raise error
    connection = Connection()
    try:
        hub_store._enable_wal(connection)
    except sqlite3.OperationalError:
        assert False, "a retryable startup error escaped"
    assert connection.attempts == 2 and pauses == [0.01]
    original = hub_store._enable_wal
    calls = []
    def enabled(connection):
        calls.append(connection)
        return original(connection)
    monkeypatch.setattr(hub_store, "_enable_wal", enabled)
    with HubStore(tmp_path / "ledger") as store:
        assert calls == [store.conn]
        assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.parametrize("code", [1, 5])
def test_ledger_startup_keeps_errors_and_timeout(monkeypatch, code):
    import sqlite3
    clock = iter([0, 1]) if code == 5 else iter([0, 0])
    pauses = []
    monkeypatch.setattr(hub_store, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=pauses.append))
    fault = sqlite3.OperationalError("the startup error")
    fault.sqlite_errorcode = code
    class Connection:
        attempts = 0
        def execute(self, sql):
            self.attempts += 1
            if self.attempts == 1:
                raise fault
    connection = Connection()
    caught = None
    try:
        hub_store._enable_wal(connection, timeout_s=1)
    except sqlite3.OperationalError as exc:
        caught = exc
    assert caught is fault
    assert connection.attempts == 1 and not pauses


@pytest.fixture
def world(tmp_path, monkeypatch):
    inst = tmp_path / "instance"
    app = create_app(testing=True, instance_path=str(inst))
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({"qubits": {}, "extras": {"name": "chipX"}}))
    (chip / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.1", "port": 1}}))
    client = app.test_client()
    assert client.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    with HubStore(tmp_path / "ledger") as store:
        root = store.register_root(tmp_path / "archive", "-04:00")
        ctx = hub_index.context(store, zone="America/New_York")
        monkeypatch.setattr(journal_routes, "_ledger_context", lambda: ctx)
        monkeypatch.setattr(journal_routes, "_chip_name", lambda: "chipX")
        monkeypatch.setattr(journal_routes, "_agent_chip_key", lambda: "chipX")
        monkeypatch.setattr(routes, "_display_zone", lambda: {"zone": "America/New_York"})
        previous = {}

        def add(doc, *, kind="run", actor="human:alice", src="apply", flags=0,
                undoes=None, instant=f"{DAY}T12:00:00-04:00", node="scan", run_id=None):
            nonlocal previous
            rank = store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] + 1
            fact = {"kind": kind, "t_utc_us": int(datetime.fromisoformat(instant).timestamp() * 1e6),
                    "ord": rank, "root_id": root,
                    "rel_path": f"{DAY}/#{rank}_{node}_120000",
                    "run_id": run_id or rank if kind == "run" else None,
                    "experiment": node, "actor": actor, "src": src, "flags": flags,
                    "targets": json.dumps(["qA1"]), "plan_id": "plan-a", "status": "finished",
                    "state_hash": str(rank)}
            flat = hub_rules.flatten(doc)
            eid = store.append(fact, hub_rules.diff(previous, flat), doc, flat)
            previous = flat
            if kind not in ("run", "save"):
                store.conn.execute("INSERT INTO sm_events(eid,sm_id,outcome,undoes) VALUES(?,?,?,?)",
                                   (eid, f"event-{eid}", "landed", json.dumps(undoes)))
                store.conn.commit()
            return eid

        def build(day=DAY, **kwargs):
            return story.build_day(inst, "chipX", day, ds=None, ledger=ctx, with_gates=False, **kwargs)

        yield {"store": store, "ctx": ctx, "add": add, "build": build, "client": client,
               "inst": inst, "app": app, "chip": chip}
    hub_index.close_readers()


def test_run_exact_rows_without_snapshot(world):
    world["add"]({"v": 1})
    world["add"]({"v": 2, "enabled": True})
    cards = world["build"]()["cards"]
    assert [c["run_id"] for c in cards] == [1, 2]
    assert cards[1]["writes"] == [
        {"path": "enabled", "old": None, "new": True, "op": "add", "proven": False},
        {"path": "v", "old": 1, "new": 2, "op": "set", "proven": False}]
    assert cards[1]["author"] == "human:alice"
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    card = _card_html(body, 'id="card-2"')
    assert 'data-path="v"' in card and _ba("1.0", "2.0") in card
    assert 'data-path="enabled"' in card and '<span class="ba-op ba-op-add">+ added</span> <span class="ba-new">true</span>' in card


@pytest.mark.parametrize("kind,src", [("sm_apply", "apply"), ("sm_apply", "keep_mine"),
    ("sm_apply", "auto_apply"), ("agent", "agent"), ("sm_apply", "approval"),
    ("sm_apply", "pull_apply"), ("sm_apply", "staged"), ("sm_apply", "dataset_apply"),
    ("restore", "restore"), ("undo", "undo"), ("redo", "redo"),
    ("autofit", "autofit"), ("sm_apply", "cli_set")])
def test_sm_write_actor_kind_and_every_entry(world, kind, src):
    world["add"]({"a": 1, "b": 2})
    world["add"]({"a": 3, "b": 4}, kind=kind, src=src, actor="by_agent")
    card = world["build"]()["cards"][-1]
    assert card["kind"] == "write" and card["author"] == "by_agent"
    assert card["event_kind"] == kind and card["src"] == src
    assert [(e["path"], e["old"], e["new"], e["actor"]) for e in card["entries"]] == [
        ("a", 1, 3, "by_agent"), ("b", 2, 4, "by_agent")]


@pytest.mark.parametrize("flag,label", [(UNDONE, "undone"), (PARTLY_UNDONE, "partly undone")])
def test_undo_marking_and_named_target(world, flag, label):
    target = world["add"]({"v": 2}, kind="sm_apply", flags=flag)
    world["add"]({"v": 1}, kind="undo", undoes=[{"event": f"event-{target}", "units": ["unit-a"]}])
    cards = world["build"]()["cards"]
    assert cards[0]["undone"] == (flag == UNDONE)
    assert cards[0]["partly_undone"] == (flag == PARTLY_UNDONE)
    assert cards[1]["undoes"] == [{"event": f"event-{target}", "units": ["unit-a"], "kind": "sm_apply",
                                   "src": "apply", "label": "Applied to the chip", "day": DAY, "time": "12:00:00"}]
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    written = _card_html(body, f'id="write-event-{target}"')
    assert f'<span class="jr-badge jr-undone">{label}</span>' in written
    # The undo names what it takes back -- its door and time -- and links to
    # that write's card, never a bare journal id.
    undo = _card_html(body, f'id="write-event-{target + 1}"')
    assert (f'Takes back\n    <a href="#write-event-{target}">the write of 12:00:00</a> (Applied to the chip)'
            in undo)
    assert "Undo: 1 value" in undo


def test_save_is_not_a_chip_write(world):
    # the server's own noon of that day: the old path would have shown it
    noon = datetime.strptime(DAY, "%Y-%m-%d").timestamp() + 12 * 3600
    unit = undo_journal.make_unit([ChangeEntry("v", 1, 2, "state")], ts=noon)
    undo_journal.append_units(undo_journal.sidecar_path(world["inst"], world["chip"]), [unit])
    world["add"]({"v": 1})
    cards = world["build"](active_path=world["chip"])["cards"]
    assert [c["kind"] for c in cards] == ["run"]
    assert world["build"]()["counts"]["writes"] == 0


def agent_record(world, *, key="agent-a", run_id=None, node="scan", meta=True, classification="node_error",
                 error="the node failed"):
    end = datetime.fromisoformat(f"{DAY}T12:00:00-04:00").timestamp()
    record = {"key": key, "chip": "chipX", "actor": "by_agent", "node": node, "targets": ["qA1"],
              "ts": end, "run_id": run_id, "plan_id": "plan-a", "classification": classification}
    story.record_agent_run(world["inst"], record)
    if meta:
        folder = world["inst"] / "agent_runs" / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps(dict(record, ended=end, since=end-30, step=2,
            result={"classification": classification, "error": error, "run_id": run_id})))


def test_folderless_agent_failure_end_plan_and_step(world):
    agent_record(world)
    cards = world["build"]()["cards"]
    assert len(cards) == 1 and cards[0]["kind"] == "agent_run"
    assert cards[0]["author"] == "by_agent" and cards[0]["classification"] == "node_error"
    assert cards[0]["reason"] == "the node failed" and cards[0]["time"] == "12:00:00"
    assert cards[0]["plan_id"] == "plan-a" and cards[0]["step"] == 2
    assert world["build"]()["counts"]["runs"] == 1
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "by_agent ran scan on qA1. No run folder: the node failed." in html
    assert '<p class="jr-agent-reason muted">the node failed</p>' in html
    assert "plan plan-a, step 2" in html


@pytest.mark.parametrize("run_id,classification", [(None, "ok"), (None, "unattributed"), (1, "node_error"), (1, "ok")])
def test_folderless_no_duplicate_when_folder_lands(world, run_id, classification):
    # a run number names its folder whatever the class; without one only an
    # attempt that finished (its folder landed late) is matched by node + time
    agent_record(world, run_id=run_id, classification=classification, error=None)
    world["add"]({"v": 1})
    cards = world["build"]()["cards"]
    assert [c["kind"] for c in cards] == ["run"]
    assert cards[0]["author"] == "by_agent"
    assert cards[0]["certainty"] == ("certain" if run_id is not None else "inferred")


def test_registry_only_and_index_only_records_survive(world):
    agent_record(world, key="agent-a", meta=False)
    agent_record(world, key="agent-b", node="other")
    story.agent_runs_index_path(world["inst"]).write_text(
        story.agent_runs_index_path(world["inst"]).read_text().splitlines()[0] + "\n")
    assert {c["id"] for c in world["build"]()["cards"]} == {"agent-a", "agent-b"}


def test_project_day_boundary_uses_instant(world):
    world["add"]({"v": 1}, instant=f"{DAY}T23:30:00-04:00")
    assert len(world["build"]()["cards"]) == 1
    world["ctx"] = hub_index.context(world["store"], zone="Asia/Seoul")
    result = story.build_day(world["inst"], "chipX", "2026-10-04", ds=None, ledger=world["ctx"], with_gates=False)
    assert len(result["cards"]) == 1 and result["cards"][0]["time"] == "12:30:00"
    assert story.build_day(world["inst"], "chipX", DAY, ds=None, ledger=world["ctx"], with_gates=False)["cards"] == []


def test_building_and_warming_hide_partial_day(world, monkeypatch):
    world["add"]({"v": 1})
    monkeypatch.setattr(hub_sync, "status", lambda _: {"state": "building", "done": 2, "total": 5})
    assert world["build"]()["cards"] == []
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "building the history (2/5)" in html and 'id="card-1"' not in html and "Day totals" not in html
    monkeypatch.setattr(hub_sync, "status", lambda _: {"state": "ready", "done": 5, "total": 5})
    def warming(*args, **kwargs):
        raise Warming("hub_index", "chipX", 0)
    monkeypatch.setattr(hub_index.INDEX_CACHE, "get", warming)
    result = world["build"]()
    assert result["history"]["state"] == "building" and result["cards"] == []


def test_degraded_names_unreadable_sources(world, monkeypatch):
    world["add"]({"v": 1})
    monkeypatch.setattr(hub_sync, "status", lambda _: {"state": "degraded", "unreadable": ["archive-a"],
        "roots": [{"path": "archive-b", "failed": ["run-a"]}], "errors": ["run-a: unreadable"]})
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "History is incomplete" in html and "archive-a" in html and "archive-b/run-a" in html
    assert 'id="card-1"' in html


@pytest.mark.parametrize("state", ["building", "unavailable", "missing"])
def test_incomplete_history_keeps_independent_journal_lines(world, monkeypatch, state):
    journal.append(world["inst"], "chipX", "first answer\nsecond answer", when=datetime.fromisoformat(f"{DAY}T01:00:00"))
    journal.append(world["inst"], "unassigned", "unassigned answer", when=datetime.fromisoformat(f"{DAY}T01:00:00"))
    world["add"]({"v": 1})
    if state == "missing":
        empty = world["inst"] / "unbuilt"
        empty.mkdir()
        monkeypatch.setattr(journal_routes, "_ledger_context", lambda: hub_index.context(SimpleNamespace(directory=empty), zone="UTC"))
    elif state == "unavailable":
        monkeypatch.setattr(journal_routes, "_ledger_context", lambda: hub_index.context(world["store"]))
    else:
        monkeypatch.setattr(hub_sync, "status", lambda _: {"state": "building", "done": 2, "total": 5})
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "first answer\nsecond answer" in html and "unassigned answer" in html
    assert "Recorded journal lines only." in html and "with run attachments unavailable" in html
    assert "JournalPage.adopt" in html and "data-jr-search" in html
    assert 'class="jr-counts"' not in html and 'id="card-1"' not in html and "No runs" not in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_building_journal_swap_selfcheck(world, monkeypatch, tmp_path):
    world["add"]({"v": 1})
    journal.append(world["inst"], "chipX", "needle answer", when=datetime.fromisoformat(f"{DAY}T01:00:00"))
    journal.append(world["inst"], "chipX", "other answer", when=datetime.fromisoformat(f"{DAY}T01:00:00"))
    journal.append(world["inst"], "unassigned", "needle unassigned", when=datetime.fromisoformat(f"{DAY}T01:00:00"))
    ready = tmp_path / "ready.html"
    ready.write_text(world["client"].get(f"/journal?day={DAY}").get_data(as_text=True), encoding="utf-8")
    monkeypatch.setattr(hub_sync, "status", lambda _: {"state": "building", "done": 2, "total": 5})
    building = tmp_path / "building.html"
    building.write_text(world["client"].get(f"/journal?day={DAY}").get_data(as_text=True), encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(["node", str(root / "tests/calibration_log_hub_selfcheck.cjs"), str(ready), str(building)],
                          cwd=root, env=os.environ, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode == 2:
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all checks passed" in proc.stdout


def test_report_same_day_function_redaction_and_exclusion(world, monkeypatch):
    world["add"]({"value": "127.0.0.1", "v": 2}, kind="sm_apply")
    journal.append(world["inst"], "chipX", "answer from host.example.internal at C:/private/archive",
                   when=datetime.fromisoformat(f"{DAY}T01:00:00"))
    seen = []
    original = journal_routes._build
    def spy(day, **kwargs):
        seen.append(day)
        return original(day, **kwargs)
    monkeypatch.setattr(journal_routes, "_build", spy)
    assert "calibration_log" in routes._REPORT_BUILDERS
    from quam_state_manager.core import chip_report
    assert chip_report.SECTION_BY_KEY["calibration_log"].available
    assert not chip_report.SECTION_BY_KEY["calibration_log"].default
    assert chip_report.parse_sections("raw,calibration_log,overview") == ["overview", "calibration_log", "raw"]
    client = world["client"]
    on = client.get("/chip-status/report/section/calibration_log?redact=1").get_data(as_text=True)
    off = client.get("/chip-status/report/section/calibration_log?redact=0").get_data(as_text=True)
    assert DAY in seen and "Could not be built" not in on
    assert "127.0.0.1" not in on and "127.0.0.1" in off
    for literal in ("host.example.internal", "C:/private/archive"):
        assert literal not in on and literal in off
    assert "human:alice" in on and "Applied to the chip" in on and "value" in on
    shell = '<html><head><title>Report</title></head><body>' + on + '</body></html>'
    included = client.post("/chip-status/report/finalize", json={
        "html": shell, "sections": ["calibration_log"], "redact": True},
        headers={"Origin": "http://localhost"})
    assert included.status_code == 200 and 'data-rep-sec="calibration_log"' in included.get_data(as_text=True)
    assert "host.example.internal" not in included.get_data(as_text=True)
    result = client.post("/chip-status/report/finalize", json={"html": shell, "sections": [], "redact": True},
                         headers={"Origin": "http://localhost"})
    assert result.status_code == 400 and "not checked: calibration_log" in result.get_json()["error"]
    excluded = client.get("/chip-status/report?sections=overview").get_data(as_text=True)
    assert 'data-rep-sec="calibration_log" data-rep-lazy' in excluded and "human:alice" not in excluded
    clean = client.post("/chip-status/report/finalize", json={
        "html": '<html><head><title>Report</title></head><body></body></html>', "sections": [], "redact": True},
        headers={"Origin": "http://localhost"})
    assert clean.status_code == 200 and "calibration_log" not in clean.get_data(as_text=True)


def test_report_refreshes_after_ledger_change(world):
    world["add"]({"v": 1}, kind="sm_apply")
    before = world["client"].get("/chip-status/report/section/calibration_log").get_data(as_text=True)
    world["add"]({"v": 987}, kind="sm_apply")
    after = world["client"].get("/chip-status/report/section/calibration_log").get_data(as_text=True)
    assert "987" not in before and "987" in after


def test_sm_original_entries_retain_each_actor(world):
    eid = world["add"]({"v": 2}, kind="sm_apply")
    original = [{"path": "values.0", "old": 1, "new": 2, "by": "human:alice"},
                {"path": "values.1", "old": 3, "new": 4, "by": "by_agent"}]
    world["store"].conn.execute("UPDATE sm_events SET entries=?,entries_n=? WHERE eid=?",
                                 (gzip.compress(json.dumps(original).encode()), 2, eid))
    world["store"].conn.commit()
    card = world["build"]()["cards"][0]
    assert [(e["path"], e["actor"], e["old"], e["new"]) for e in card["entries"]] == [
        ("values.0", "human:alice", 1, 2), ("values.1", "by_agent", 3, 4)]


def test_hub_preserves_gates_figures_claims_and_multiline_answers(world):
    world["add"]({"v": 1})
    event = world["store"].event(1)
    folder = Path(world["store"].conn.execute("SELECT path FROM roots").fetchone()[0]) / event["rel_path"]
    folder.mkdir(parents=True)
    (folder / "node.json").write_text(json.dumps({"metadata": {"run_start": f"{DAY}T12:00:00-04:00"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {"qA1": "successful"}}}))
    (folder / "data.json").write_text(json.dumps({"figures": {"figure-a": "figure-a.png"}}))
    story.claim_run(world["inst"], "chipX", 1, author="human:alice", note="the claim note")
    journal.append(world["inst"], "chipX", "first answer\nsecond answer", kind="agent", run_id=1,
                   when=datetime.fromisoformat(f"{DAY}T12:00:00"))
    calls = []
    def gate(run):
        calls.append(run["run_id"])
        return {"verdict": "pass", "reason": "the saved state passed", "anchor": "run"}
    def build():
        return story.build_day(world["inst"], "chipX", DAY, ds=None, ledger=world["ctx"], gate_compute=gate)
    card = build()["cards"][0]
    # figure names as the Datasets scanner names them (what /dataset/<uid>/fig/ serves)
    assert card["gate"]["verdict"] == "pass" and card["figures"] == ["figures.figure-a"]
    assert card["author"] == "human:alice" and card["note"] == "the claim note"
    assert card["journal"][0]["text"] == "first answer\nsecond answer"
    assert build()["cards"][0]["gate"] == card["gate"] and calls == [1]


def test_day_fragment_cache_invalidates_changes_and_filters(world):
    world["add"]({"v": 1})
    client = world["client"]
    before = client.get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert client.get(f"/journal/day?day={DAY}").get_data(as_text=True) == before
    world["add"]({"v": 2})
    after = client.get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert 'id="card-2"' not in before and 'id="card-2"' in after
    filtered = client.get(f"/journal/day?day={DAY}&q=absent-token").get_data(as_text=True)
    assert 'id="card-2"' not in filtered and 'jr-search-cache' in filtered


def test_large_sm_entries_load_from_primary_blob(world):
    import hashlib
    eid = world["add"]({"v": 2}, kind="sm_apply")
    original = [{"path": "v", "old": 1, "new": 2, "by": "by_agent"}]
    payload = json.dumps(original).encode()
    digest = hashlib.sha1(payload).hexdigest()
    folder = world["store"].directory
    (folder / f"events-{digest}.json.gz").write_bytes(gzip.compress(payload))
    (folder / "events.jsonl").write_text(json.dumps({"id": f"event-{eid}", "entries_blob": digest}) + "\n")
    world["store"].conn.execute("UPDATE sm_events SET entries_n=1 WHERE eid=?", (eid,))
    world["store"].conn.commit()
    assert world["build"]()["cards"][0]["entries"][0]["actor"] == "by_agent"


def test_plan_step_recovered_from_durable_plan(world):
    from quam_state_manager.core import agent_plans
    plan = agent_plans.add(world["inst"], "chipX", title="the plan", steps=[{"node": "scan", "targets": ["qA1"]}],
                           mode="ask-writes", created_by="human:alice")
    agent_plans.step_update(world["inst"], "chipX", plan["id"], 0, run_key="agent-a")
    end = datetime.fromisoformat(f"{DAY}T12:00:00-04:00").timestamp()
    story.record_agent_run(world["inst"], {"key": "agent-a", "chip": "chipX", "node": "scan", "actor": "by_agent",
        "plan_id": plan["id"], "ts": end, "targets": ["qA1"], "run_id": None})
    assert world["build"]()["cards"][0]["step"] == 0


def test_colliding_run_ids_keep_distinct_cards(world):
    world["add"]({"v": 1}, run_id=1)
    world["add"]({"v": 2}, run_id=1, instant=f"{DAY}T12:01:00-04:00")
    cards = world["build"]()["cards"]
    assert len(cards) == 2 and len({c["card_id"] for c in cards}) == 2
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert 'id="card-1-1"' in body and 'id="card-1-2"' in body


def test_missing_exact_entry_blob_is_said_on_that_card(world):
    world["add"]({"v": 1})
    eid = world["add"]({"v": 2}, kind="sm_apply")
    world["add"]({"v": 3})
    (world["store"].directory / "events.jsonl").write_text(
        json.dumps({"id": f"event-{eid}", "entries_blob": "missing"}) + "\n")
    world["store"].conn.execute("UPDATE sm_events SET entries_n=1 WHERE eid=?", (eid,))
    world["store"].conn.commit()
    data = world["build"]()
    # One unreadable entry list never hides the rest of the day: that card
    # says so and shows the ledger's own rows for the write instead.
    assert [c["kind"] for c in data["cards"]] == ["run", "write", "run"]
    assert data["history"]["state"] != "unavailable"
    write = data["cards"][1]
    assert write["entries_error"].startswith("the exact write entries are unavailable")
    assert [(e["path"], e["old"], e["new"], e["op"]) for e in write["entries"]] == [("v", 1, 2, "set")]
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    card = _card_html(html, f'id="write-event-{eid}"')
    assert "the exact write entries are unavailable" in card and "shown instead" in card
    assert 'id="card-3"' in html and 'class="jr-counts"' in html


# --------------------------------------------------------------- docs/281 review


def _rows_world(world):
    """A first state, then a run that sets/removes, a run that sets/adds, and
    an SM write whose recorded entries create and delete keys."""
    world["add"]({"keep": 0})
    world["add"]({"keep": 0, "f": 5168000000.0, "off": -7.995537540056441e-05, "gone": 3})
    world["add"]({"keep": 0, "f": 5169000000.0, "off": -7.9e-05, "plan": "target"})
    eid = world["add"]({"keep": 0, "f": 5169000000.0, "off": -7.9e-05, "plan": "control"},
                       kind="sm_apply", actor="human:alice")
    entries = [{"path": "plan", "old": "target", "new": "control", "by": "human:alice"},
               {"path": "added.key", "new": "target", "created": True, "by": "human:alice"},
               {"path": "removed.key", "old": 4, "deleted": True, "by": "by_agent"},
               {"path": "off", "old": -7.995537540056441e-05, "new": -7.9e-05, "by": "human:alice"}]
    world["store"].conn.execute("UPDATE sm_events SET entries=?,entries_n=? WHERE eid=?",
                                (gzip.compress(json.dumps(entries).encode()), len(entries), eid))
    world["store"].conn.commit()
    return eid


def _expected_set(old, new):
    """What every before->after surface prints for a numeric set: the values
    through groupdigits and the delta chip of core/value_delta (docs/76)."""
    from quam_state_manager.core import value_delta
    from quam_state_manager.core.units import group_digits
    d = value_delta.compute(old, new)
    chip = (f'<span class="val-delta delta-{d["dir"]}" title="{d["title"]}">{d["text"]}'
            f' <span class="val-delta-pct">({d["pct_text"]})</span></span>')
    return _ba(group_digits(old), group_digits(new)) + chip


ADDED = '<span class="ba-op ba-op-add">+ added</span> <span class="ba-new">target</span>'


def _removed(old):
    return f'<span class="ba-op ba-op-gone">&minus; removed</span> <span class="ba-old">was {old}</span>'


def _row(card, path):
    """The one change row (``<li>``) of *path* inside a card."""
    at = card.index(f'data-path="{path}"')
    return card[card.rindex("<li", 0, at):card.index("</li>", at)]


def test_run_card_rows_say_added_removed_and_read_through_value_delta(world):
    _rows_world(world)
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    card = _card_html(body, 'id="card-3"')
    assert ADDED in _row(card, "plan")                      # a key the run added
    assert _removed("3.0") in _row(card, "gone")            # a key the run removed
    assert _expected_set(5168000000.0, 5169000000.0) in _row(card, "f")
    assert "5,169,000,000.0" in card and "+1,000,000" in card
    assert _expected_set(-7.995537540056441e-05, -7.9e-05) in _row(card, "off")
    assert "None" not in card


def test_sm_write_rows_say_added_removed_and_read_through_value_delta(world):
    eid = _rows_world(world)
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    card = _card_html(body, f'id="write-event-{eid}"')
    assert ADDED in _row(card, "added.key")
    assert _removed("4") in _row(card, "removed.key")
    assert _ba("target", "control") in _row(card, "plan")   # text: no delta chip
    assert "val-delta" not in _row(card, "plan")
    assert _expected_set(-7.995537540056441e-05, -7.9e-05) in _row(card, "off")
    assert 'jr-author-by_agent">by_agent</span>' in _row(card, "removed.key")   # each entry's own actor
    assert "None" not in card


def test_report_log_rows_use_the_page_template(world):
    eid = _rows_world(world)
    section = world["client"].get("/chip-status/report/section/calibration_log?redact=0").get_data(as_text=True)
    write = section[section.index(f'id="write-event-{eid}"'):]
    write = write[:write.index("</article>")]
    run = section[section.index("run #3:"):]
    run = run[:run.index("</article>")]
    for part in (run, write):
        assert ADDED in part and _expected_set(-7.995537540056441e-05, -7.9e-05) in part
        assert '<ul class="jr-changes">' in part and "None" not in part
    assert _removed("3.0") in run and _removed("4") in write
    assert "First state in the chip history" in section


def test_first_ledger_event_is_a_starting_state_not_added_values(world):
    world["add"]({"a": 1, "b": {"c": 2}})
    world["add"]({"a": 2, "b": {"c": 2}})
    cards = world["build"]()["cards"]
    assert cards[0]["first_state_n"] == 2 and cards[0]["writes"] == []
    assert cards[1]["first_state_n"] is None
    assert [(w["path"], w["op"]) for w in cards[1]["writes"]] == [("a", "set")]
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    first = _card_html(body, 'id="card-1"')
    assert "First state in the chip history" in first and "2 values recorded" in first
    assert "ba-op-add" not in first and 'data-path="b.c"' not in first


def test_agent_run_with_a_recorded_folder_missing_from_the_history(world):
    world["add"]({"v": 1})
    agent_record(world, key="agent-a", run_id=7)
    story.record_agent_run(world["inst"], {
        "key": "agent-b", "chip": "chipX", "actor": "by_agent", "node": "other", "targets": ["qA2"],
        "ts": datetime.fromisoformat(f"{DAY}T12:05:00-04:00").timestamp(), "run_id": None,
        "classification": "node_error", "reason": "the stated purpose"})
    cards = {c["id"]: c for c in world["build"]()["cards"] if c["kind"] == "agent_run"}
    assert cards["agent-a"]["missing_run_id"] == 7 and cards["agent-b"]["missing_run_id"] is None
    # The failure text is never the purpose the agent stated.
    assert cards["agent-b"]["reason"] is None
    assert cards["agent-b"]["purpose"] == "the stated purpose"
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "by_agent ran scan on qA1. Its run folder #7 is not in the chip history." in html
    assert "by_agent ran other on qA2. No run folder: the node failed." in html
    assert "the stated purpose</p>" in html and "No run folder: the stated purpose" not in html
    assert "Purpose: the stated purpose" in html


def test_ledger_rows_of_every_run_are_read_only_for_agent_records(world, monkeypatch):
    from quam_state_manager.core import hub_query
    asked = []
    original = hub_query.timeline
    def spy(*args, **kwargs):
        asked.append(kwargs.get("include_runs"))
        return original(*args, **kwargs)
    monkeypatch.setattr(hub_query, "timeline", spy)
    world["add"]({"v": 1})
    world["build"]()
    agent_record(world)
    world["build"]()
    assert asked == [False, True]


def test_a_chip_without_a_history_dir_says_so(world, monkeypatch):
    monkeypatch.setattr(journal_routes, "_ledger_context", REAL_LEDGER_CONTEXT)
    monkeypatch.setattr(routes, "_hub_chip_dir", lambda path: None)
    app = world["app"]
    app.config["contexts"][app.config["active_context"]].pop("hub_chip_dir", None)
    journal.append(world["inst"], "chipX", "a recorded answer", when=datetime.fromisoformat(f"{DAY}T01:00:00"))
    response = world["client"].get(f"/journal/day?day={DAY}")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "The chip history is unavailable, so runs and SM writes cannot be listed." in html
    assert "a recorded answer" in html and "Day totals" not in html


def test_a_long_array_row_reads_as_its_length_not_its_hash(world):
    world["add"]({"keep": 0})
    world["add"]({"keep": 0, "trace": list(range(20))})
    world["add"]({"keep": 0, "trace": list(range(1, 21))})
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    added, changed = _row(_card_html(body, 'id="card-2"'), "trace"), _row(_card_html(body, 'id="card-3"'), "trace")
    assert '<span class="ba-op ba-op-add">+ added</span> <span class="ba-new"><span class="ba-array"' in added
    assert "[20 values]</span>" in changed and changed.count("[20 values]") == 2
    assert "contents changed" in changed
    for row in (added, changed):
        assert "_array" not in row and "&#34;_hash&#34;" not in row and '"_hash"' not in row


def test_report_lists_an_agent_only_day_and_says_when_history_is_building(world, monkeypatch):
    world["add"]({"v": 1})
    record = {"key": "agent-late", "chip": "chipX", "actor": "by_agent", "node": "scan", "targets": ["qA1"],
              "ts": datetime.fromisoformat("2026-10-05T09:00:00-04:00").timestamp(), "run_id": None,
              "classification": "node_error"}
    story.record_agent_run(world["inst"], record)
    section = world["client"].get("/chip-status/report/section/calibration_log?redact=0").get_data(as_text=True)
    assert "<h3>2026-10-05</h3>" in section
    assert "by_agent ran scan on qA1. No run folder: the node failed." in section
    monkeypatch.setattr(hub_sync, "status", lambda _: {"state": "building", "done": 2, "total": 5})
    section = world["client"].get("/chip-status/report/section/calibration_log?redact=0").get_data(as_text=True)
    assert "building the history (2/5): days that hold only runs or SM writes are not listed yet" in section
    assert "run #1:" not in section and "<h3>2026-10-05</h3>" not in section


def test_pre_ledger_undo_edits_are_counted_never_shown_as_writes(world):
    def unit_at(clock, path):
        ts = datetime.fromisoformat(f"{DAY}T{clock}-04:00").timestamp()
        undo_journal.append_units(undo_journal.sidecar_path(world["inst"], world["chip"]),
                                  [undo_journal.make_unit([ChangeEntry(path, 1, 2, "state")], ts=ts)])
    unit_at("11:00:00", "v")
    world["add"]({"v": 1})
    data = world["build"](active_path=world["chip"])
    assert data["unrecorded"] == {"n": 1, "since": None}
    assert all(c["kind"] != "write" for c in data["cards"])
    eid = world["add"]({"v": 2}, kind="sm_apply", instant=f"{DAY}T12:30:00-04:00")
    unit_at("12:29:59", "v")                  # the recorded write's own unit, stamped just before it
    own = undo_journal.load(undo_journal.sidecar_path(world["inst"], world["chip"]))[-1]["id"]
    world["store"].conn.execute("UPDATE sm_events SET units=? WHERE eid=?", (json.dumps([own]), eid))
    world["store"].conn.commit()
    unit_at("13:00:00", "w")                  # a save after recording began: not counted
    data = world["build"](active_path=world["chip"])
    assert data["unrecorded"] == {"n": 1, "since": f"{DAY} 12:30:00"}
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert ("SM's undo journal holds 1 edit from this day made before the chip history recorded SM writes "
            f"(it records them from {DAY} 12:30:00)") in html
    assert world["build"]("2026-10-02", active_path=world["chip"]).get("unrecorded") is None


def test_only_a_landed_sm_write_is_a_write_card(world):
    world["add"]({"v": 1})
    eid = world["add"]({"v": 2}, kind="sm_apply")
    world["store"].conn.execute("UPDATE sm_events SET outcome='failed' WHERE eid=?", (eid,))
    world["store"].conn.commit()
    assert [c["kind"] for c in world["build"]()["cards"]] == ["run"]


def test_day_fragment_refreshes_when_only_the_note_changes(world):
    ts = datetime.fromisoformat(f"{DAY}T11:00:00-04:00").timestamp()
    undo_journal.append_units(undo_journal.sidecar_path(world["inst"], world["chip"]),
                              [undo_journal.make_unit([ChangeEntry("v", 1, 2, "state")], ts=ts)])
    world["add"]({"v": 1})
    before = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "none recorded yet" in before
    world["add"]({"v": 2}, kind="sm_apply", instant="2026-10-04T09:00:00-04:00")   # another day
    after = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "none recorded yet" not in after and "it records them from 2026-10-04 09:00:00" in after


def test_run_parameters_read_through_the_same_before_after(world, monkeypatch):
    world["add"]({"v": 1})
    monkeypatch.setattr(story, "_params_diff", lambda cur, prev: [
        {"key": "num_averages", "old": 100, "new": 400}, {"key": "load_data_id", "old": None, "new": 1}])
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    card = _card_html(body, 'id="card-1"')
    assert _expected_set(100, 400) in card
    assert _ba("null", "1") in card and "None" not in card


def test_agent_run_row_scans_like_a_run_and_a_stopped_run_is_not_a_failure(world):
    when = datetime.fromisoformat(f"{DAY}T12:00:00-04:00").timestamp()
    for key, classification in (("agent-x", "hardware_contention"), ("agent-y", "cancelled")):
        story.record_agent_run(world["inst"], {"key": key, "chip": "chipX", "actor": "by_agent", "node": "scan",
                                               "targets": ["qA1"], "ts": when, "run_id": None,
                                               "classification": classification})
    data = world["build"]()
    by = {c["id"]: c for c in data["cards"]}
    assert by["agent-x"]["outcome"] == "failed" and by["agent-y"]["outcome"] == "cancelled"
    assert data["counts"]["runs"] == 2 and data["counts"]["failed"] == 1
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    summary = _card_html(html, 'id="agent-agent-x"')
    summary = summary[:summary.index("</summary>")]
    assert ('<span class="jr-family" title="by_agent ran scan on qA1. No run folder: the instrument was busy.">'
            'scan<span class="muted jr-no-folder"> &middot; no run folder</span></span>') in summary
    assert '<span class="jr-targets">qA1</span>' in summary
    assert "&#10007; busy</span>" in summary
    assert '<span class="jr-run-link muted" title="no run folder">&ndash;</span>' in summary


def test_a_large_first_state_count_reads_grouped(world):
    world["add"]({f"k{i}": i for i in range(1200)})
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "1,200 values recorded; there is no earlier state to compare against." in body


# ======================================================= docs/281 review round


def _instant_us(clock, day=DAY):
    return int(datetime.fromisoformat(f"{day}T{clock}-04:00").timestamp() * 1e6)


def _run_event(world, doc, *, clock, run_id, node="scan", root=None, prev=None, flags=0, error=None,
               day=DAY, targets=("qA1",)):
    """A run event the way the builder writes it (no actor)."""
    store = world["store"]
    rank = store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] + 1
    if root is None:
        root = store.conn.execute("SELECT MIN(root_id) FROM roots").fetchone()[0]
    fact = {"kind": "run", "t_utc_us": _instant_us(clock, day), "ord": rank, "root_id": root,
            "rel_path": f"{day}/#{run_id}_{node}_{clock.replace(':', '')}", "run_id": run_id,
            "experiment": node, "targets": json.dumps(list(targets)), "status": "finished",
            "state_hash": None if error else f"h{rank}", "flags": flags, "error": error}
    flat = hub_rules.flatten(doc) if doc is not None else (prev or {})
    rows = hub_rules.diff(prev or {}, flat) if doc is not None else []
    return store.append(fact, rows, doc if doc is not None else {}, flat), flat


def _agent_meta(world, key, *, node="scan", since_clock, ended_clock, classification, run_id=None,
                error=None, folder=None, run=None):
    since, ended = _instant_us(since_clock) / 1e6, _instant_us(ended_clock) / 1e6
    folder_path = world["inst"] / "agent_runs" / key
    folder_path.mkdir(parents=True, exist_ok=True)
    result = {"classification": classification, "error": error, "run_id": run_id}
    if run is not None:
        result["run"] = run
    record = {"key": key, "chip": "chipX", "node": node, "targets": ["qA1"], "actor": "by_agent",
              "plan_id": None, "since": since, "ended": ended, "status": "done", "result": result}
    if folder:
        record["folder"] = str(folder)
    (folder_path / "meta.json").write_text(json.dumps(record))


@pytest.mark.parametrize("classification", ["node_error", "hardware_contention", "cancelled", "timeout",
                                            "host_unreachable", "skipped"])
def test_a_failed_attempt_never_takes_a_persons_run(world, classification):
    # P0-1: a person runs `scan`; an agent's attempt that did not finish (or
    # failed because the person held the instrument) keeps its own card and
    # never becomes the author of the person's run
    _run_event(world, {"v": 1}, clock="12:00:00", run_id=41)
    _agent_meta(world, "agent-late", since_clock="12:00:30", ended_clock="12:01:00",
                classification=classification, error="the attempt's own error")
    cards = world["build"]()["cards"]
    run = next(c for c in cards if c["kind"] == "run")
    assert (run["author"], run["certainty"]) == ("unknown", "none")
    assert [c["id"] for c in cards if c["kind"] == "agent_run"] == ["agent-late"]


def test_a_late_folder_is_inferred_nearest_wins_inside_the_attempt(world):
    # P0-1: only a finished attempt with no run number is matched by node +
    # time, only to runs inside [since - 60 s, ended + 900 s], nearest wins,
    # each run is taken once, and the match is "inferred", never "certain"
    _, flat = _run_event(world, {"v": 1}, clock="11:58:00", run_id=1)       # before any attempt
    _, flat = _run_event(world, {"v": 2}, clock="12:05:00", run_id=2, prev=flat)
    _, flat = _run_event(world, {"v": 3}, clock="12:09:00", run_id=3, prev=flat)
    _run_event(world, {"v": 4}, clock="12:20:00", run_id=4, prev=flat)
    _agent_meta(world, "agent-1", since_clock="12:02:00", ended_clock="12:08:30", classification="ok")
    _agent_meta(world, "agent-3", since_clock="12:06:00", ended_clock="12:09:10", classification="ok")
    _agent_meta(world, "agent-4", since_clock="12:30:00", ended_clock="12:35:00", classification="ok")
    data = world["build"]()
    cards = {c["run_id"]: c for c in data["cards"] if c["kind"] == "run"}
    assert (cards[1]["author"], cards[1]["certainty"]) == ("unknown", "none")
    assert (cards[3]["author"], cards[3]["certainty"]) == ("by_agent", "inferred")     # 10 s from agent-3's end
    assert (cards[2]["author"], cards[2]["certainty"]) == ("by_agent", "inferred")     # agent-1 takes the next one
    # a run before the attempt even started is never its folder
    assert (cards[4]["author"], cards[4]["certainty"]) == ("unknown", "none")
    assert [c["id"] for c in data["cards"] if c["kind"] == "agent_run"] == ["agent-4"]


def test_one_record_names_one_of_two_same_numbered_runs(world, tmp_path):
    # P0-2: a run number is per data folder. A record that names its folder
    # (since this review) takes that folder's run even against the clock; an
    # older record without one is narrowed by its time; neither credits both.
    root_b = world["store"].register_root(tmp_path / "archive-b", "-04:00")
    _, flat = _run_event(world, {"v": 1}, clock="12:00:00", run_id=5)
    _run_event(world, {"v": 2}, clock="13:00:00", run_id=5, root=root_b, prev=flat)
    story.record_agent_run(world["inst"], {"key": "agent-a", "chip": "chipX", "actor": "by_agent",
        "node": "scan", "targets": ["qA1"], "run_id": 5, "ts": _instant_us("12:00:10") / 1e6, "classification": "ok"})
    cards = [c for c in world["build"]()["cards"] if c["kind"] == "run"]
    assert [(c["author"], c["certainty"]) for c in cards] == [("by_agent", "certain"), ("unknown", "none")]
    folder_b = Path(tmp_path / "archive-b") / f"{DAY}/#5_scan_130000"
    _agent_meta(world, "agent-a", since_clock="11:59:00", ended_clock="12:00:10", classification="ok",
                run_id=5, folder=folder_b)
    cards = [c for c in world["build"]()["cards"] if c["kind"] == "run"]
    assert [(c["author"], c["certainty"]) for c in cards] == [("unknown", "none"), ("by_agent", "certain")]


def test_claims_and_numbered_lines_belong_to_one_card(world, tmp_path):
    # P0-2: a claim made on a card keys that card (data folder + number); a
    # bare-number claim or "run #5" line from before applies only when one
    # folder holds that number -- never to two cards
    root_b = world["store"].register_root(tmp_path / "archive-b", "-04:00")
    _, flat = _run_event(world, {"v": 1}, clock="12:00:00", run_id=5)
    _run_event(world, {"v": 2}, clock="13:00:00", run_id=5, root=root_b, prev=flat)
    story.claim_run(world["inst"], "chipX", 5, author="human:bob")
    journal.append(world["inst"], "chipX", "about the fifth run", run_id=5, when=datetime(2026, 10, 4, 3, 0, 0))
    data = world["build"]()
    runs = [c for c in data["cards"] if c["kind"] == "run"]
    assert [c["author"] for c in runs] == ["unknown", "unknown"]
    assert not any(c["journal"] for c in runs) and [e["text"] for e in data["loose"]] == ["about the fifth run"]
    uid_b = runs[1]["card_uid"]
    r = world["client"].post("/journal/claim", json={"run_id": 5, "uid": uid_b, "who": "carol"},
                             headers={"Origin": "http://localhost"})
    assert r.status_code == 200
    runs = [c for c in world["build"]()["cards"] if c["kind"] == "run"]
    assert [c["author"] for c in runs] == ["unknown", "human:carol"]
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert f'data-uid="{uid_b}"' in html


def test_a_bare_number_claim_names_the_run_of_the_datasets_folder(world, tmp_path):
    # the old page only offered runs of the folder Datasets showed: a claim
    # made then names that folder's run, and only it
    from types import SimpleNamespace as NS
    root_b = world["store"].register_root(tmp_path / "archive-b", "-04:00")
    _, flat = _run_event(world, {"v": 1}, clock="12:00:00", run_id=5)
    _run_event(world, {"v": 2}, clock="13:00:00", run_id=5, root=root_b, prev=flat)
    folder_b = str(tmp_path / "archive-b" / f"{DAY}/#5_scan_130000")
    info = NS(folder_path=folder_b, run_start=None, run_end=None, run_duration_s=None, parameters={},
              qubits=["qA1"], qubit_pairs=[], outcomes={}, status="", figure_names=[], time="13:00:00")
    ds = NS(runs={5: info}, folder_path=str(tmp_path / "archive-b"),
            get_previous_same_experiment_id=lambda rid: None, get_run=lambda rid: None)
    story.claim_run(world["inst"], "chipX", 5, author="human:bob")
    runs = [c for c in story.build_day(world["inst"], "chipX", DAY, ds=ds, ledger=world["ctx"],
                                       with_gates=False)["cards"] if c["kind"] == "run"]
    assert [c["author"] for c in runs] == ["unknown", "human:bob"]


def test_report_hides_past_network_values_and_actor_hosts(world):
    # P0-3: the structural rule knew only the CURRENT documents; a log shows
    # past values too
    import gzip
    _, flat = _run_event(world, {"network": {"host": "127.0.0.1", "cluster_name": "keep_cluster"}, "v": 0},
                         clock="11:00:00", run_id=1)
    _, flat = _run_event(world, {"network": {"host": "opx-lab-old", "cluster_name": "secret_cluster_a"}, "v": 0},
                         clock="12:00:00", run_id=2, prev=flat)
    eid = world["add"]({"v": 2}, kind="sm_apply", actor="human:operator@lab-pc-7", instant=f"{DAY}T13:00:00-04:00")
    entries = [{"path": "network.cluster_name", "old": "old_cluster_b", "new": "keep_cluster",
                "by": "human:operator@lab-pc-7"}]
    world["store"].conn.execute("UPDATE sm_events SET entries=?,entries_n=? WHERE eid=?",
                                (gzip.compress(json.dumps(entries).encode()), 1, eid))
    world["store"].conn.commit()
    journal.append(world["inst"], "chipX", "moved to opx-lab-old today", when=datetime(2026, 10, 4, 2, 0, 0))
    planted = ("opx-lab-old", "secret_cluster_a", "old_cluster_b", "lab-pc-7")
    on = world["client"].get("/chip-status/report/section/calibration_log?redact=1").get_data(as_text=True)
    off = world["client"].get("/chip-status/report/section/calibration_log?redact=0").get_data(as_text=True)
    for literal in planted:
        assert literal not in on and literal in off, literal
    assert "human:operator@[hidden]" in on and "Applied to the chip" in on


def test_ledger_flags_reach_the_card(world):
    # P1-4: a run that may be another chip's collapses its rows and is counted
    # apart; every other ledger fact is a badge and one line
    from quam_state_manager.core import hub_store as hs
    _, flat = _run_event(world, {"v": 1, "w": 1}, clock="10:00:00", run_id=1)
    _, flat2 = _run_event(world, {"v": 9, "w": 9}, clock="11:00:00", run_id=2, prev=flat, flags=hs.CHIP_UNCERTAIN)
    _run_event(world, {"v": 1, "w": 1}, clock="12:00:00", run_id=3, prev=flat2,
               flags=hs.REVERTS_TO_EARLIER | hs.TIME_ASSUMED | hs.OVERLAPS_SM_WRITE | hs.SOURCE_GONE)
    _run_event(world, None, clock="13:00:00", run_id=4, error="state.json unreadable",
               flags=hs.CHIP_UNCERTAIN)
    data = world["build"]()
    cards = {c["run_id"]: c for c in data["cards"] if c["kind"] == "run"}
    assert cards[2]["other_chip"] and cards[2]["writes"] == [] and cards[2]["rows_hidden"] == 2
    assert [f["key"] for f in cards[3]["flags"]] == ["repeats", "over-sm-write", "time-assumed", "folder-gone"]
    assert [f["key"] for f in cards[4]["flags"]] == ["no-state"]
    assert data["counts"]["runs"] == 3 and data["counts"]["other_chip"] == 1
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    other = _card_html(html, 'id="card-2"')
    assert '<span class="jr-badge jr-flag jr-flag-other-chip"' in other and "jr-changes" not in other
    assert "2 changed values not listed: this run may be another chip's." in other
    assert "repeats earlier</span>" in _card_html(html, 'id="card-3"')
    assert "maybe another chip's</span>" in html


def test_a_ledger_nobody_updates_is_labelled_and_a_missing_one_never_polls(world, monkeypatch):
    # P1-5
    _run_event(world, {"v": 1}, clock="11:00:00", run_id=1)
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "As last recorded: this window is not updating the chip history" in html
    assert "Day totals" not in html and ">As last recorded</span>" in html
    empty = world["inst"] / "never-built"
    empty.mkdir()
    monkeypatch.setattr(journal_routes, "_ledger_context",
                        lambda: hub_index.context(SimpleNamespace(directory=empty), zone="America/New_York"))
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "This window has not built a chip history for this chip" in html and "every 2s" not in html
    monkeypatch.setattr(hub_sync, "status", lambda _: {"state": "building", "done": 1, "total": 3})
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "building the history (1/3)" in html and 'hx-trigger="every 2s"' in html


def test_the_first_state_is_the_first_event_with_a_state(world):
    # P1-6: an oldest run that saved no state is not the starting state
    _run_event(world, None, clock="10:00:00", run_id=1, error="no saved state")
    _run_event(world, {"a": 1, "b": 2}, clock="11:00:00", run_id=2)
    cards = {c["run_id"]: c for c in world["build"]()["cards"] if c["kind"] == "run"}
    assert cards[2]["first_state_n"] == 2 and cards[2]["writes"] == []
    assert cards[1]["first_state_n"] is None


def test_report_leaves_out_lines_of_a_session_that_named_no_chip(world):
    # P1-7
    _run_event(world, {"v": 1}, clock="11:00:00", run_id=1)
    # both on the report's own day, so only the rule keeps the first one out
    when = datetime.fromtimestamp(_instant_us("12:00:00") / 1e6)
    journal.append(world["inst"], "unassigned", "tuned another device", when=when)
    journal.append(world["inst"], "chipX", "a line of this chip", when=when)
    assert [e["text"] for e in world["build"]()["unassigned"]] == ["tuned another device"]
    section = world["client"].get("/chip-status/report/section/calibration_log?redact=0").get_data(as_text=True)
    assert "tuned another device" not in section and "a line of this chip" in section


def test_a_very_large_day_sends_rows_and_fetches_bodies(world, monkeypatch):
    # P1-8: past LAZY_CARDS a day sends each card's row; a body arrives on open
    monkeypatch.setattr(journal_routes, "LAZY_CARDS", 2)
    _, flat = _run_event(world, {"v": 1}, clock="10:00:00", run_id=1)
    _, flat = _run_event(world, {"v": 2, "p": 7}, clock="11:00:00", run_id=2, prev=flat)
    _run_event(world, {"v": 3, "p": 7}, clock="12:00:00", run_id=3, prev=flat)
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    card = _card_html(html, 'id="card-2"')
    assert f'hx-get="/journal/card?day={DAY}&amp;card=card-2"' in card
    assert 'hx-trigger="toggle once from:closest details"' in card and "jr-changes" not in card
    assert "added 7" in card or "p added" in card         # the search text still covers the rows
    body = world["client"].get(f"/journal/card?day={DAY}&card=card-2").get_data(as_text=True)
    assert body.startswith('\n        <div class="jr-body">') or '<div class="jr-body">' in body
    assert '<ul class="jr-changes">' in body and _ba("1.0", "2.0") in body
    gone = world["client"].get(f"/journal/card?day={DAY}&card=card-99").get_data(as_text=True)
    assert "no longer on this day" in gone
    report = world["client"].get("/chip-status/report/section/calibration_log?redact=0").get_data(as_text=True)
    assert "jr-body-lazy" not in report and '<ul class="jr-changes">' in report


def test_gates_are_checked_in_the_background_for_a_person(world, monkeypatch):
    # P1-8: a page never waits on hundreds of state files; the card says the
    # gate is being checked and the next build has it
    import time as _time
    world["app"].config["JOURNAL_GATE_WAIT"] = False
    started = []
    def slow_gate(run):
        started.append(run["run_id"])
        return {"verdict": "pass", "reason": "checked", "family": None, "anchor": "run"}
    monkeypatch.setattr(story, "_compute_gate", slow_gate)
    _run_event(world, {"v": 1}, clock="11:00:00", run_id=1)
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "gate ...</span>" in html and "Checking 1 gate in the background" in html
    deadline = _time.monotonic() + 30
    while story.gates_pending() and _time.monotonic() < deadline:
        _time.sleep(0.05)
    _time.sleep(0.1)
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "gate pass</span>" in html and "Checking" not in html and started == [1]


def test_a_run_folder_is_read_once_until_its_files_change(world, monkeypatch):
    # P1-8: node.json / data.json are read per (mtime, size), not per render
    _run_event(world, {"v": 1}, clock="11:00:00", run_id=1)
    folder = Path(world["store"].conn.execute("SELECT path FROM roots").fetchone()[0]) / f"{DAY}/#1_scan_110000"
    folder.mkdir(parents=True)
    (folder / "node.json").write_text(json.dumps({"data": {"outcomes": {"qA1": "successful"}}}))
    reads = []
    original = story._read_json_dict
    monkeypatch.setattr(story, "_read_json_dict", lambda p: reads.append(p) or original(p))
    assert world["build"]()["cards"][0]["outcome"] == "ok"
    world["build"]()
    assert len([p for p in reads if p.endswith("node.json")]) == 1
    import os as _os, time as _time
    (folder / "node.json").write_text(json.dumps({"data": {"outcomes": {"qA1": "failed"}}, "pad": 1}))
    _os.utime(folder / "node.json", ns=(_time.time_ns(), _time.time_ns() + 10**9))
    assert world["build"]()["cards"][0]["outcome"] == "failed"


def test_many_agent_records_match_by_index_not_by_scan(world, monkeypatch):
    # P1-8: records are matched through a node index, not record x run
    from quam_state_manager.core import run_terms
    calls = []
    original = run_terms.same_node
    monkeypatch.setattr(run_terms, "same_node", lambda a, b: calls.append(1) or original(a, b))
    flat = {}
    for i in range(60):
        _, flat = _run_event(world, {"v": i}, clock=f"1{i // 30}:{i % 30:02d}:00", run_id=i + 1, prev=flat,
                             node=f"node_{i % 3}")
    for k in range(40):
        _agent_meta(world, f"a{k}", node=f"node_{k % 3}", since_clock="09:00:00", ended_clock="09:01:00",
                    classification="ok")
    world["build"]()
    assert len(calls) <= 3 * 3          # one pass of each node over the distinct node names


def test_ints_read_as_the_open_chip_stores_them(world):
    # P2: the ledger keeps numbers as REAL; the open chip says which are ints
    chip = world["chip"].parent / "chip-ints"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({"qubits": {}, "extras": {"name": "chipX"},
                                                "x180": {"length": 48, "amp": 0.5}}))
    (chip / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.1", "port": 1}}))
    assert world["client"].post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    _, flat = _run_event(world, {"x180": {"length": 40, "amp": 0.25}}, clock="11:00:00", run_id=1)
    _run_event(world, {"x180": {"length": 48, "amp": 1.0}}, clock="12:00:00", run_id=2, prev=flat)
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    card = _card_html(html, 'id="card-2"')
    assert _ba("40", "48") in _row(card, "x180.length")
    assert _ba("0.25", "1.0") in _row(card, "x180.amp")        # a float stays a float


def test_parameters_compare_within_the_runs_own_folder(world, tmp_path):
    # P2: a run of another folder is compared with its own folder's previous run
    from types import SimpleNamespace as NS
    root_b = world["store"].register_root(tmp_path / "archive-b", "-04:00")
    _, flat = _run_event(world, {"v": 1}, clock="11:00:00", run_id=4, root=root_b)
    _run_event(world, {"v": 2}, clock="12:00:00", run_id=5, root=root_b, prev=flat)
    for rid, clock, n in ((4, "110000", 200), (5, "120000", 100)):
        folder = tmp_path / "archive-b" / DAY / f"#{rid}_scan_{clock}"
        folder.mkdir(parents=True)
        (folder / "node.json").write_text(json.dumps({"data": {"parameters": {"model": {"num_averages": n}}}}))
    a_runs = {4: {"run_id": 4, "experiment_name": "scan", "folder_path": str(tmp_path / "a" / "#4"),
                  "parameters": {"num_averages": 300}}}
    ds = NS(get_run=lambda rid: a_runs.get(rid), get_previous_same_experiment_id=lambda rid: rid - 1,
            folder_path=str(tmp_path / "a"))
    card = [c for c in story.build_day(world["inst"], "chipX", DAY, ds=ds, ledger=world["ctx"], with_gates=False)["cards"]
            if c["run_id"] == 5][0]
    assert card["prev_run_id"] == 4 and card["params_diff"] == [{"key": "num_averages", "old": 200, "new": 100}]


def test_a_journal_line_belongs_to_one_project_day(world):
    # P2: a 23:30 run in a -04:00 lab; the line filed by this PC's calendar
    # on the next day attaches to the run and is not loose on the next day
    _run_event(world, {"v": 1}, clock="23:30:00", run_id=1)
    when = datetime.fromtimestamp(_instant_us("23:31:00") / 1e6)
    journal.append(world["inst"], "chipX", "tuned the readout", kind="agent", when=when)
    d3, d4 = world["build"](DAY), world["build"]("2026-10-04")
    assert [e["text"] for c in d3["cards"] for e in c.get("journal") or []] == ["tuned the readout"]
    assert [e["text"] for e in d4["loose"]] == []
    assert d3["cards"][0]["journal"][0]["time"] == "23:31:00"          # the project clock


def test_a_partly_undone_write_marks_the_rows_taken_back(world):
    # P2
    import gzip
    world["add"]({"a": 1, "b": 1})
    eid = world["add"]({"a": 2, "b": 2}, kind="sm_apply", flags=PARTLY_UNDONE)
    entries = [{"path": "a", "old": 1, "new": 2, "by": "human:alice"}, {"path": "b", "old": 1, "new": 2, "by": "human:alice"}]
    world["store"].conn.execute("UPDATE sm_events SET entries=?,entries_n=? WHERE eid=?",
                                (gzip.compress(json.dumps(entries).encode()), 2, eid))
    world["store"].conn.commit()
    world["add"]({"a": 1, "b": 2}, kind="undo", undoes=[{"event": f"event-{eid}", "units": ["u1"]}])
    card = next(c for c in world["build"]()["cards"] if c.get("eid") == eid)
    assert [(e["path"], e["taken_back"]) for e in card["entries"]] == [("a", True), ("b", False)]
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    body = _card_html(html, f'id="write-event-{eid}"')
    assert '<li class="jr-taken-back">' in _row(body, "a") and "jr-taken-back" not in _row(body, "b")


def test_a_step_that_never_ran_is_not_a_run(world):
    # P2
    story.record_agent_run(world["inst"], {"key": "agent-s", "chip": "chipX", "actor": "by_agent", "node": "scan",
                                           "targets": ["qA1"], "ts": _instant_us("12:00:00") / 1e6, "run_id": None,
                                           "classification": "skipped"})
    data = world["build"]()
    assert data["counts"]["runs"] == 0 and data["counts"]["failed"] == 0
    assert data["cards"][0]["sentence"] == "by_agent did not run scan on qA1: it was skipped."


def test_the_log_section_never_takes_the_reports_shared_lock(world, monkeypatch):
    # P2: building every day must not hold the other sections
    class Taken:
        def __enter__(self):
            raise AssertionError("the shared report lock was taken")
        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(routes, "_REPORT_HEAVY", Taken())
    _run_event(world, {"v": 1}, clock="11:00:00", run_id=1)
    r = world["client"].get("/chip-status/report/section/calibration_log?redact=0")
    assert r.status_code == 200 and "run #1:" in r.get_data(as_text=True)


def test_plain_words_on_cards_one_who_and_values_vs_writes(world):
    # P3: compact English, one "who" per card, "N writes (M values)"
    import gzip
    world["add"]({"a": 1, "b": 1})
    eid = world["add"]({"a": 2, "b": 3, "c": 4}, kind="sm_apply", src="keep_mine", actor="human:alice")
    entries = [{"path": "a", "old": 1, "new": 2, "by": "human:alice"},
               {"path": "b", "old": 1, "new": 3, "by": "human:alice"},
               {"path": "c", "new": 4, "created": True, "by": "by_agent"}]
    world["store"].conn.execute("UPDATE sm_events SET entries=?,entries_n=? WHERE eid=?",
                                (gzip.compress(json.dumps(entries).encode()), 3, eid))
    world["store"].conn.commit()
    world["add"]({"a": 2, "b": 3, "c": 5, "d": 1}, kind="sm_apply", actor="human:alice")
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    card = _card_html(html, f'id="write-event-{eid}"')
    assert "Kept mine, overwrote live: 3 values</span>" in card
    assert "jr-author-human" not in _row(card, "a") and 'jr-author-by_agent">by_agent</span>' in _row(card, "c")
    assert "<b>2</b> writes (5 values)</span>" in html
    for jargon in ("sm_apply", "keep_mine", "UNDONE", "node_error", " -- "):
        assert jargon not in html, jargon


def test_search_text_is_what_the_card_shows(world):
    # P3: never instants, ledger ids or folder hashes
    world["add"]({"v": 1})
    world["add"]({"v": 2}, kind="sm_apply", flags=UNDONE)
    data = None
    with world["app"].test_request_context():
        data = journal_routes._build(DAY, filters={"author": "", "q": ""})
    run, write = data["cards"][0], data["cards"][1]
    for c in (run, write):
        assert not re.search(r"\d{7,}", c["search_hay"]), c["search_hay"]     # no instants
        assert "event-" not in c["search_hay"] and c["card_uid" if c["kind"] == "run" else "id"] not in c["search_hay"]
    assert "#1" in run["search_hay"] and "undone" in write["search_hay"]
    assert "Applied to the chip" in write["search_hay"]


def test_report_section_checks_refuse_unknown_and_unavailable(world, monkeypatch):
    # P3: the section route and the final pass both refuse a key that is not
    # an available section (an unavailable one says its note)
    from quam_state_manager.core import chip_report as cr
    client = world["client"]
    r = client.get("/chip-status/report/section/nope")
    assert r.status_code == 404 and r.get_data(as_text=True) == "no such section"
    monkeypatch.setattr(cr, "AVAILABLE_KEYS", tuple(k for k in cr.AVAILABLE_KEYS if k != "raw"))
    monkeypatch.setitem(cr.SECTION_BY_KEY, "raw", cr.Section("raw", "Raw", "d", available=False, note="held back"))
    r = client.get("/chip-status/report/section/raw")
    assert r.status_code == 404 and r.get_data(as_text=True) == "held back"
    r = client.post("/chip-status/report/finalize", json={"html": "<html></html>", "sections": ["raw"], "redact": True},
                    headers={"Origin": "http://localhost"})
    assert r.status_code == 400 and r.get_json()["error"] == "not an available section: raw"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_review_client_selfcheck():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(["node", str(root / "tests/calibration_log_review_selfcheck.cjs")], cwd=root,
                          env=os.environ, capture_output=True, text=True, encoding="utf-8", timeout=60)
    if proc.returncode == 2:
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all checks passed (9 checks)" in proc.stdout
