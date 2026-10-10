"""Folder linking uses current workspace evidence and explicit decisions."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub, hub_sync, value_history
from quam_state_manager.core.history import load_chip_decisions, save_chip_decision, _data_folder_name
from quam_state_manager.core.hub_store import HubStore
from quam_state_manager.web import routes
from tests.test_hub_drawer import chip_state, run, write_chip, make_app, T0


@pytest.fixture
def env(tmp_path):
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    live = tmp_path / "live"
    write_chip(live, chip_state(), None)
    a, b, zero = (tmp_path / "data" / n for n in ("data_a", "data_b", "data_zero"))
    run(a, 1, chip_state())
    run(b, 2, chip_state())
    run(b, 3, chip_state(t1=3e-5), patches=[{"op": "replace", "path": "/quam/qubits/qA1/T1",
                                          "value": 3e-5, "old": 1e-5}])
    wrong = run(zero, 4, chip_state())
    wiring = wrong / "quam_state" / "wiring.json"
    wiring.write_text(json.dumps({"network": {"host": "127.0.0.2", "cluster_name": "C2"}}), encoding="utf-8")
    app = make_app(tmp_path)
    client = app.test_client()
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ws = app.config["workspace"]
    for root in (a, b, zero):
        ws.add_root(root)
    with app.app_context():
        ctx = routes._active_ctx()
        directory = routes._hub_chip_dir(live)
        # C2 runs independently of the no-roots projector change in C1.
        snap = directory / "20260101_115900_000000"
        write_chip(snap, chip_state(), None)
        with HubStore(directory) as store:
            assert hub_sync.attach_observed(store, {"ts": snap.name, "t_us": T0 - 60_000_000,
                                                    "trigger": "auto", "dir": str(snap)}) in ("added", "inserted")
        jobs = routes._alignment_jobs()
        jobs.request(routes._history(), live, ws, wait_s=5)
        jobs.join()
    yield SimpleNamespace(app=app, client=client, live=live, a=a, b=b, zero=zero,
                          ws=ws, directory=directory, ctx=ctx, jobs=jobs)
    jobs.join()
    hub.set_inline(old)


def payload(env, root=None):
    root = root or env.b
    return {"root": str(root), "fs_key": routes._hub_root_key(root), "chip_key": env.directory.name}


def test_candidate_bucketing_and_ranking(env):
    html = env.client.get("/hub/link-folder").get_data(as_text=True)
    keys = re.findall(r'data-root="([^"]+)"', html)
    assert keys == [routes._hub_root_key(r) for r in (env.b, env.a, env.zero)]
    assert "2 runs match this chip, 0 belong to other chips, 0 unreadable" in html
    # S10 walk (round 4): "1 belong to other chips" -> "1 belongs to another chip" (grouped, singular)
    assert "0 runs match this chip, 1 belongs to another chip, 0 unreadable" in html
    zero_row = html.split(f'data-root="{routes._hub_root_key(env.zero)}"')[1].split('class="hub-folder-row"')[0]
    assert 'hx-post="/hub/link-folder"' not in zero_row


def test_deepest_root_owns_evidence_and_prefix_boundaries(env, tmp_path):
    nested = env.b / "nested"
    (nested / "2026-01-01").mkdir(parents=True)
    roots = [env.b, nested, env.a]
    e = lambda p: SimpleNamespace(quam_state_path=p)
    scan = {"aligned": [e(nested / "run" / "quam_state"), e(env.a / "run" / "quam_state"),
                        e(Path(str(env.a) + "_suffix") / "quam_state")],
            "unknown": [e(env.b / "run" / "quam_state")], "renamed": [e(env.b / "old" / "quam_state")]}
    with env.app.app_context():
        rows = routes._hub_link_rows({**env.ctx, "path": str(nested / "state")}, env.directory,
                                    SimpleNamespace(root_folders=roots), scan)
    by_key = {r["fs_key"]: r for r in rows}
    assert by_key[routes._hub_root_key(nested)]["matches"] == 1
    assert by_key[routes._hub_root_key(env.b)]["matches"] == 0
    assert by_key[routes._hub_root_key(env.a)]["matches"] == 1
    assert by_key[routes._hub_root_key(env.b)]["unreadable"] == 1
    assert by_key[routes._hub_root_key(env.b)]["other"] == 1
    assert by_key[routes._hub_root_key(nested)]["inside"] is True


def test_candidates_exclude_decisions_registered_and_empty_roots(env, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    env.ws.add_root(empty)
    save_chip_decision(env.app.instance_path, env.directory.name, _data_folder_name(env.a), "different")
    save_chip_decision(env.app.instance_path, env.directory.name, "root:" + routes._hub_root_key(env.zero), "different")
    assert env.client.post("/hub/link-folder", data=payload(env)).status_code == 200
    html = env.client.get("/hub/link-folder").get_data(as_text=True)
    assert 'class="hub-folder-row"' not in html
    assert 'hx-post="/hub/unlink-folder"' in html
    assert "No Datasets folder holds runs of this chip." in html


@pytest.mark.parametrize("case", ["zero", "outside", "mismatched_root", "switch", "running"])
def test_link_refuses_stale_or_unproven_requests(env, tmp_path, monkeypatch, case):
    data = payload(env)
    if case == "zero":
        data = payload(env, env.zero)
    elif case == "outside":
        data = payload(env, tmp_path / "outside")
        # Isolate membership from the independent evidence refusal.
        monkeypatch.setattr(routes, "_hub_link_rows", lambda *a: [
            {"fs_key": data["fs_key"], "matches": 1}])
    elif case == "mismatched_root":
        data["root"] = str(env.a)
    elif case == "switch":
        switched = tmp_path / "switched"
        write_chip(switched, chip_state(name="other"), None)
        (switched / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.3"}}), encoding="utf-8")
        assert env.client.post("/load", data={"folder": str(switched)}).status_code in (200, 302)
        with env.app.app_context():
            assert routes._hub_chip_dir(routes._active_ctx()["path"]).name != env.directory.name
        monkeypatch.setattr(routes, "_hub_link_rows", lambda *a: [
            {"fs_key": data["fs_key"], "matches": 1}])
    else:
        monkeypatch.setattr(env.jobs, "request", lambda *a, **k: {"state": "running", "done": 1, "total": 3})
    assert env.client.post("/hub/link-folder", data=data).status_code == 409
    assert load_chip_decisions(env.app.instance_path) == {}
    assert not hub_sync.status(env.directory)["roots"]


def test_scan_chip_switch_is_rechecked_before_saving(env, monkeypatch):
    real = env.jobs.request
    def switch(*args, **kwargs):
        result = real(*args, **kwargs)
        env.ctx["origin"] = "archive"
        return result
    monkeypatch.setattr(env.jobs, "request", switch)
    assert env.client.post("/hub/link-folder", data=payload(env)).status_code == 409
    assert load_chip_decisions(env.app.instance_path) == {}


def test_pending_fragment_keeps_chip_and_refetches(env, monkeypatch):
    calls = []
    def pending(*a, **k):
        calls.append(k["wait_s"])
        return {"state": "running", "done": 2, "total": 5}
    monkeypatch.setattr(env.jobs, "request", pending)
    html = env.client.get("/hub/link-folder").get_data(as_text=True)
    assert "checking 2 of 5 runs" in html
    assert 'hx-trigger="every 1s"' in html and "chip_key=" in html
    assert env.directory.name in html and calls == [0.12]


def test_live_context_required(env):
    env.ctx["type"] = "other"
    assert env.client.get("/hub/link-folder").status_code == 409


def test_unique_label_is_not_evidence_for_two_roots(env):
    nested = env.b / "nested"
    nested.mkdir()
    label = _data_folder_name(env.b)
    decisions = {f"{env.directory.name}::{label}": "different"}
    assert routes._hub_root_decision(env.directory.name, env.b, [env.b, nested], decisions) is None
    assert routes._hub_root_decision(env.directory.name, env.b, [env.b], decisions) == "different"


def test_state_folder_hint_is_rendered(env):
    from flask import render_template
    with env.app.test_request_context("/hub/link-folder"):
        html = render_template("_hub_link_folder.html", scan={"state": "ready"}, chip_key=env.directory.name,
                               linked=[], rows=[{"path": str(env.a), "fs_key": routes._hub_root_key(env.a),
                                                "matches": 1, "other": 0, "unreadable": 0, "inside": True}])
    assert "this chip's state folder is inside it" in html


def test_link_writes_the_read_key_and_registers_then_shows_run_rows(env):
    response = env.client.post("/hub/link-folder", data=payload(env))
    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "hubLinked"
    key = routes._hub_root_key(env.b)
    assert load_chip_decisions(env.app.instance_path).get(f"{env.directory.name}::root:{key}") == "same"
    with env.app.app_context():
        assert (str(env.b), "decided_same") in routes._hub_roots_for(env.ctx)
        ans = routes._value_history(env.ctx, {"value": "qubits.qA1.T1"})
    status = hub_sync.status(env.directory)
    assert status["roots"]
    assert status["state"] in ("ready", "building") and status["roots"][0]["path"] == str(env.b)
    assert status["roots"][0]["sources"] == ["decided_same"]
    assert ans["mode"] in ("ledger", "building")
    if ans["mode"] == "ledger":
        assert ans["ledger"]["has_runs"]
        html = env.client.get("/field/history?path=qubits.qA1.T1").get_data(as_text=True)
        assert "#3" in html and "its own patch set it" in html


def test_link_builds_before_run_rows_are_read(env):
    env.app.config["HUB_SYNC_ON_OPEN"] = False
    assert env.client.post("/hub/link-folder", data=payload(env)).status_code == 200
    status = env.client.get("/hub/status").get_json()
    assert status["state"] == "building" and status["roots"][0]["path"] == str(env.b)
    html = env.client.get("/field/history?path=qubits.qA1.T1").get_data(as_text=True)
    assert "being built" in html and 'data-eid=' not in html
    with env.app.app_context():
        hub_sync.kick(hub_sync.sync_for(env.directory))
    html = env.client.get("/field/history?path=qubits.qA1.T1").get_data(as_text=True)
    assert "#3" in html and "its own patch set it" in html


def test_unlink_keeps_events_and_shows_no_folder_even_after_label_link(env):
    label = _data_folder_name(env.b)
    assert env.client.post("/param-history/decide", data={"chip_key": env.directory.name,
                           "data_folder": label, "decision": "same"}).status_code == 200
    with HubStore(env.directory) as store:
        before = [tuple(r) for r in store.conn.execute("SELECT * FROM events ORDER BY ord")]
    assert any("decided_same" in r["sources"] for r in hub_sync.status(env.directory)["roots"])
    response = env.client.post("/hub/unlink-folder", data=payload(env))
    assert response.status_code == 200 and response.headers["HX-Trigger"] == "hubLinked"
    assert not hub_sync.status(env.directory)["roots"]
    with HubStore(env.directory) as store:
        assert [tuple(r) for r in store.conn.execute("SELECT * FROM events ORDER BY ord")] == before
    html = env.client.get("/field/history?path=qubits.qA1.T1").get_data(as_text=True)
    # S10 C3: old -> new, unlink hides runs in the folder view and offers a link.
    assert 'data-note="unlinked_roots"' in html and "not part of this folder" in html
    assert 'data-note="no_folder_linked"' in html and "/hub/link-folder" in html
    assert "Older snapshot history" not in html and 'data-eid=' not in html
    with env.app.app_context():
        answer = routes._value_history(env.ctx, {"value": "qubits.qA1.T1"}, runs=10)
    assert answer["mode"] == "ledger" and not answer["ledger"]["has_runs"]
    assert answer["ledger"]["left_out"]["unlinked"] == 2 and not answer["runs"]
    for url in ("/topology/trends?metrics=T1", "/param-history?since=all&props=T1",
                "/param-history/expand?qubit=qA1&prop=T1", "/param-history/changes",
                "/api/topology/sparklines/qA1", "/chip-status/report/section/trends?redact=0",
                "/state/versions", "/state-history?body=1", "/api/history"):
        body = env.client.get(url, headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'data-note="unlinked_roots"' in body and "/hub/link-folder" in body, url


def test_unlink_refuses_a_declared_root(env):
    env.ctx["extras_data_roots"] = [str(env.b)]
    with env.app.app_context():
        routes._hub_sync_open(env.ctx)
    assert env.client.post("/hub/unlink-folder", data=payload(env)).status_code == 409
    assert hub_sync.status(env.directory)["roots"]


def test_decide_same_reregisters_only_the_open_chip(env, monkeypatch):
    calls = []
    sync_open = routes._hub_sync_open
    def register(ctx):
        calls.append(ctx)
        sync_open(ctx)
    monkeypatch.setattr(routes, "_hub_sync_open", register)
    data = {"chip_key": "closed-chip", "data_folder": _data_folder_name(env.a), "decision": "same"}
    assert env.client.post("/param-history/decide", data=data).status_code == 200
    assert not hub_sync.status(env.directory)["roots"] and calls == []
    data["chip_key"] = env.directory.name
    assert env.client.post("/param-history/decide", data=data).status_code == 200
    assert calls == [env.ctx]
    assert hub_sync.status(env.directory)["roots"][0]["path"] == str(env.a)


def test_decide_root_uses_the_shared_key(env):
    data = {"chip_key": env.directory.name, "data_folder": "root:" + str(env.b), "decision": "same"}
    response = env.client.post("/param-history/decide", data=data)
    assert response.status_code == 200
    assert response.get_json()["data_folder"] == "root:" + routes._hub_root_key(env.b)
    assert hub_sync.status(env.directory)["roots"][0]["path"] == str(env.b)


def test_link_checks_the_scan_again_at_press_time(env, monkeypatch):
    assert 'data-matches="2"' in env.client.get("/hub/link-folder").get_data(as_text=True)
    monkeypatch.setattr(env.jobs, "request", lambda *a, **k: {"state": "ready", "result": {}})
    assert env.client.post("/hub/link-folder", data=payload(env)).status_code == 409
    assert load_chip_decisions(env.app.instance_path) == {}


@pytest.mark.parametrize("surface", ["drawer", "column", "versions", "state_history", "trends", "grid", "changes", "cell"])
def test_no_roots_offer_on_html_surfaces(env, surface):
    urls = {"drawer": "/field/history?path=qubits.qA1.T1", "versions": "/state/versions",
            "state_history": "/state-history?body=1", "trends": "/topology/trends", "grid": "/param-history",
            "changes": "/param-history/changes", "cell": "/param-history/expand?qubit=qA1&prop=T1"}
    response = (env.client.post("/bulk/column-history", data={"grid": "qubit", "label": "Value", "unit": "",
                "col_key": "c", "paths": json.dumps({"qA1": "qubits.qA1.T1"})}) if surface == "column"
                else env.client.get(urls[surface]))
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-note="no_folder_linked"' in html
    assert "Link a data folder..." in html and 'hx-get="/hub/link-folder"' in html
    # S10 walk: "SM saw -- no runs." -> "SM saw, and no runs." (no ASCII double hyphen in UI text)
    assert "SM&#39;s own writes" in html and "the states SM saw, and no runs." in html


@pytest.mark.parametrize("surface", ["agent", "meta"])
def test_json_surfaces_offer_a_read_url(env, surface):
    url = "/api/agent/field-history?path=qubits.qA1.T1" if surface == "agent" else "/topology/metric-meta"
    response = env.client.get(url)
    assert response.status_code == 200
    data = response.get_json()
    assert data["link"] == {"offer": True, "url": "/hub/link-folder"}
    # S10 walk: "SM saw -- no runs." -> "SM saw, and no runs." (no ASCII double hyphen in UI text)
    assert "the states SM saw, and no runs." in json.dumps(data)
    assert env.client.post("/api/agent/hub/link-folder", data=payload(env)).status_code == 404


@pytest.mark.parametrize("url,method", [("/hub/link-folder", "get"), ("/hub/link-folder", "post"),
                                        ("/hub/unlink-folder", "post")])
def test_archive_link_doors_are_read_only(env, url, method):
    env.ctx["origin"] = "archive"
    response = getattr(env.client, method)(url, data=payload(env))
    assert response.status_code == 409 and "read-only" in response.get_data(as_text=True)


@pytest.mark.parametrize("url", ["/field/history?path=qubits.qA1.T1", "/state/versions", "/state-history?body=1",
                                "/topology/trends", "/topology/metric-meta", "/api/agent/field-history?path=qubits.qA1.T1"])
def test_archive_history_has_no_offer(env, url):
    env.ctx["origin"] = "archive"
    response = env.client.get(url)
    assert response.status_code == 200
    assert "no_folder_linked" not in response.get_data(as_text=True)
    assert "Link a data folder..." not in response.get_data(as_text=True)
    data = response.get_json(silent=True)
    assert not data or "link" not in data


@pytest.mark.parametrize("state,roots,runs,origin,offer", [
    ("ready", [], False, "live", True), ("degraded", [], False, "live", True),
    ("building", [], False, "live", False), ("idle", [], False, "live", False),
    ("ready", [{"path": "data"}], False, "live", False), ("ready", [], True, "live", False),
    ("ready", [], False, "archive", False)])
def test_note_condition(state, roots, runs, origin, offer):
    notes = value_history.notes({"state": state, "roots": roots}, {"has_runs": runs}, origin=origin)
    assert any(n["code"] == "no_folder_linked" for n in notes) is offer


def test_modal_focus_and_refresh_selfcheck():
    result = subprocess.run(["node", "tests/hub_link_folder_selfcheck.cjs"], capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
