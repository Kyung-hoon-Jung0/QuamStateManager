"""Calibration log contract on generic synthetic ledger facts."""

import json
import gzip
import os
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
    assert 'data-path="enabled"' in card and '<span class="ba-op ba-op-add">+ added</span> <span class="ba-new">True</span>' in card


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


@pytest.mark.parametrize("flag,label", [(UNDONE, "UNDONE"), (PARTLY_UNDONE, "PARTLY_UNDONE")])
def test_undo_marking_and_named_target(world, flag, label):
    target = world["add"]({"v": 2}, kind="sm_apply", flags=flag)
    world["add"]({"v": 1}, kind="undo", undoes=[{"event": f"event-{target}", "units": ["unit-a"]}])
    cards = world["build"]()["cards"]
    assert cards[0]["undone"] == (flag == UNDONE)
    assert cards[0]["partly_undone"] == (flag == PARTLY_UNDONE)
    assert cards[1]["undoes"] == [{"event": f"event-{target}", "units": ["unit-a"], "kind": "sm_apply",
                                   "src": "apply", "day": DAY, "time": "12:00:00"}]
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert label in body
    # The undo names what it takes back -- its door and time -- and links to
    # that write's card, never a bare journal id.
    undo = _card_html(body, f'id="write-event-{target + 1}"')
    assert f'<a href="#write-event-{target}">the apply write of 12:00:00</a>' in undo
    assert f'id="write-event-{target}"' in body


def test_save_is_not_a_chip_write(world):
    unit = undo_journal.make_unit([ChangeEntry("v", 1, 2, "state")], ts=datetime.fromisoformat(f"{DAY}T12:00:00-04:00").timestamp())
    undo_journal.append_units(undo_journal.sidecar_path(world["inst"], world["chip"]), [unit])
    world["add"]({"v": 2}, kind="save")
    assert world["build"](active_path=world["chip"])["cards"] == []
    assert world["build"]()["counts"]["writes"] == 0


def agent_record(world, *, key="agent-a", run_id=None, node="scan", meta=True):
    end = datetime.fromisoformat(f"{DAY}T12:00:00-04:00").timestamp()
    record = {"key": key, "chip": "chipX", "actor": "by_agent", "node": node, "targets": ["qA1"],
              "ts": end, "run_id": run_id, "plan_id": "plan-a", "classification": "node_error"}
    story.record_agent_run(world["inst"], record)
    if meta:
        folder = world["inst"] / "agent_runs" / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(json.dumps(dict(record, ended=end, since=end-30, step=2,
            result={"classification": "node_error", "error": "the node failed", "run_id": run_id})))


def test_folderless_agent_failure_end_plan_and_step(world):
    agent_record(world)
    cards = world["build"]()["cards"]
    assert len(cards) == 1 and cards[0]["kind"] == "agent_run"
    assert cards[0]["author"] == "by_agent" and cards[0]["classification"] == "node_error"
    assert cards[0]["reason"] == "the node failed" and cards[0]["time"] == "12:00:00"
    assert cards[0]["plan_id"] == "plan-a" and cards[0]["step"] == 2
    assert world["build"]()["counts"]["runs"] == 1
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "by_agent ran scan on qA1 -- no run folder (node_error: the node failed)" in html
    assert "plan plan-a, step 2" in html


@pytest.mark.parametrize("run_id", [None, 1])
def test_folderless_no_duplicate_when_folder_lands(world, run_id):
    agent_record(world, run_id=run_id)
    world["add"]({"v": 1})
    assert [c["kind"] for c in world["build"]()["cards"]] == ["run"]


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
    assert "human:alice" in on and "sm_apply" in on and "value" in on
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
    assert card["gate"]["verdict"] == "pass" and card["figures"] == ["figure-a"]
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
    assert 'id="card-3"' in html and "Day totals" in html


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
    return card[card.rindex("<li>", 0, at):card.index("</li>", at)]


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
    assert cards["agent-b"]["reason"] == "reason not recorded"
    assert cards["agent-b"]["purpose"] == "the stated purpose"
    html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "by_agent ran scan on qA1 -- run folder #7 is not in the chip history (node_error: the node failed)" in html
    assert "by_agent ran other on qA2 -- no run folder (node_error: reason not recorded)" in html
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
    assert "by_agent ran scan on qA1 -- no run folder (node_error: reason not recorded)" in section
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
    assert ('<span class="jr-family" title="scan -- no run folder">scan<span class="muted jr-no-folder">'
            ' &middot; no run folder</span></span>') in summary
    assert '<span class="jr-targets">qA1</span>' in summary
    assert "&#10007; hardware_contention</span>" in summary
    assert '<span class="jr-run-link muted" title="no run folder">&ndash;</span>' in summary


def test_a_large_first_state_count_reads_grouped(world):
    world["add"]({f"k{i}": i for i in range(1200)})
    body = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert "1,200 values recorded; there is no earlier state to compare against." in body
