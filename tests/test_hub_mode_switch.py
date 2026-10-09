"""S10 C3: readable ledgers answer; permanent errors end without old value rows."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub_sync, hub_versions, value_history
from quam_state_manager.web import routes
from tests.hub_surface_fixture import SURFACES, no_runs  # noqa: F401
from tests.test_hub_drawer import chip_dir, chip_state, sm, write_chip  # noqa: F401

# S10 walk: old -> new, a note names its reason in plain words -- never the raw
# code ("could not be read (no_ledger)" was shown to a user as it stood)
PLAIN = {"no_chip_dir": "This chip's history folder cannot be found",
         "no_ledger": "No change history has been built for this chip yet",
         "unreadable": "The change history file could not be read"}


@pytest.mark.parametrize("surface,url", SURFACES + [("report", "/chip-status/report/section/trends?redact=0")])
def test_no_run_ledger_never_reads_snapshot_fallback(no_runs, monkeypatch, surface, url):
    with no_runs["app"].app_context():
        answer = routes._value_history(routes._active_ctx(), {"value": "qubits.qA1.T1"})
    assert answer["mode"] == "ledger"
    response = no_runs["client"].get(url, headers={"HX-Request": "true"})
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Older snapshot history" not in body
    if surface not in ("trends_paths", "changes_paths", "version_count"):
        assert "/hub/link-folder" in body
        assert "No data folder is linked to this chip" in body
    assert any(n["code"] == "no_folder_linked" and n["link"]["offer"]
               for n in answer["notes"]["value"])


@pytest.mark.parametrize("surface,url", SURFACES[:13] + [("report", "/chip-status/report/section/trends?redact=0")])
def test_unreadable_is_terminal_on_every_surface(no_runs, monkeypatch, surface, url):
    def broken(*args, **kwargs):
        raise ValueError("corrupt ledger")
    monkeypatch.setattr(value_history, "read", broken)
    monkeypatch.setattr(hub_versions, "_version_token", broken)
    response = no_runs["client"].get(url, headers={"HX-Request": "true"})
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Older snapshot history" not in body
    assert "data-eid=" not in body
    assert "data-vh-retry=" not in body and "load delay:" not in body
    if surface not in ("trends_paths", "changes_paths"):
        assert PLAIN["unreadable"] in body
        assert "Nothing older is shown in its place." in body


@pytest.mark.parametrize("trigger", ["manual", "auto", "save", "backup"])
@pytest.mark.parametrize("defer_index", [False, True])
def test_capture_promptly_reaches_observed_ledger(no_runs, trigger, defer_index):
    app = no_runs["app"]
    with app.app_context():
        ctx = routes._active_ctx()
        live = Path(ctx["path"])
        hm = routes._history()
        first = hm.check_and_snapshot(live, "manual", force=True)
        assert first is not None
        write_chip(live, chip_state(t1=4e-5), None)
        meta = hm.check_and_snapshot(live, trigger, force=True, defer_index=defer_index)
        assert meta is not None
        answer = routes._value_history(ctx, {"value": "qubits.qA1.T1"})
        assert answer["mode"] == "ledger"
        points = answer["rows"]["value"]["points"]
        assert points and points[-1]["value"] == 4e-5
        assert points[-1]["kind"] == "observed"
        hm._join_deferred_index()
    body = no_runs["client"].get("/field/history?path=qubits.qA1.T1").get_data(as_text=True)
    assert "writer unknown" in body and 'data-note="no_folder_linked"' in body


@pytest.mark.parametrize("mode", ["building", "preparing", "unavailable"])
def test_nonledger_versions_keep_paged_legacy_rows(no_runs, monkeypatch, mode):
    from quam_state_manager.core import ramcache
    app = no_runs["app"]
    with app.app_context():
        ctx = routes._active_ctx()
        hm = routes._history()
        for n in (1, 2, 3):
            write_chip(Path(ctx["path"]), chip_state(t1=n*1e-5), None)
            assert hm.check_and_snapshot(ctx["path"], "manual", force=True)
        snapshots = hm.list_snapshots(ctx["path"])
        directory = chip_dir(no_runs)
    if mode == "building":
        monkeypatch.setattr(hub_sync, "status", lambda d: {"state": "building"})
    else:
        def fail(*a, **k):
            if mode == "preparing":
                raise ramcache.Warming("test", "key", 0)
            raise ValueError("corrupt ledger")
        monkeypatch.setattr(hub_versions, "_version_token", fail)
    answer = hub_versions.read(directory, snapshots, limit=1, offset=1)
    assert answer["mode"] == mode and answer["total"] == 3
    assert len(answer["rows"]) == 1 and answer["rows"][0]["legacy"]
    assert answer["rows"][0]["ref"] == snapshots[1].timestamp
    for url in ("/state/versions", "/state-history?body=1", "/api/history"):
        body = no_runs["client"].get(url).get_data(as_text=True)
        assert snapshots[0].timestamp in body and "older snapshot" in body
        if url == "/state/versions":
            assert 'data-source="ledger"' in body
        # S10 C6: "could not be read" (beside "Nothing older is shown", above older rows) ->
        # the listing's own wording, the note may not deny the rows drawn under it
        if mode == "unavailable":
            assert "Older Param History snapshots are listed below." in body
            assert "Nothing older is shown" not in body
        else:
            assert "change history" in body


def test_unavailable_table_wait_preserves_terminal_mode():
    answer = {"mode": "unavailable", "reason": "unreadable"}
    assert routes._hub_waiting(SimpleNamespace(waiting=answer, answer={})) == answer


@pytest.mark.parametrize("reason", [None, "no_chip_dir", "no_ledger", "unreadable"])
def test_mode_table(no_runs, monkeypatch, reason):
    app = no_runs["app"]
    with app.app_context():
        ctx = dict(routes._active_ctx())
        if reason == "no_chip_dir":
            ctx["hub_chip_dir"] = None
            def missing(*a):
                raise OSError("history directory unavailable")
            monkeypatch.setattr(routes, "_hub_chip_dir", missing)
        elif reason == "no_ledger":
            directory = Path(ctx["hub_chip_dir"]).parent / "readonly"
            directory.mkdir()
            ctx.update(hub_chip_dir=str(directory), origin="archive", hub_no_folder=True)
        elif reason == "unreadable":
            def broken(*a, **k):
                raise ValueError("corrupt ledger")
            monkeypatch.setattr(value_history, "read", broken)
        answer = routes._value_history(ctx, {"value": "qubits.qA1.T1"})
        assert answer["mode"] == ("ledger" if reason is None else "unavailable")
        if reason:
            assert answer["reason"] == reason and not answer["rows"]
            message = routes._vh_wait_message(answer)
            assert PLAIN[reason] in message and f"({reason})" not in message
        else:
            assert answer["rows"]["value"]["points"] == []
            assert any(n["code"] == "no_folder_linked" for n in answer["notes"]["value"])


def test_archived_missing_ledger_offers_a_real_folder(no_runs, tmp_path):
    app = no_runs["app"]
    client = no_runs["client"]
    with app.app_context():
        ctx = routes._active_ctx()
        live = Path(ctx["path"])
        directory = Path(ctx["hub_chip_dir"])
        assert routes._history().check_and_snapshot(live, "manual", force=True)
    other = tmp_path / "other"
    write_chip(other, chip_state(name="other"), None)
    (other / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.2"}}), encoding="utf-8")
    assert client.post("/load", data={"folder": str(other)}).status_code in (200, 302)
    (directory / "ledger.sqlite").unlink()
    response = client.get("/param-history", query_string={"chip_key": directory.name, "since": "all"},
                          headers={"HX-Request": "true"})
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    # S10 walk: no_ledger end state -> built from the chip's own snapshots, Open still offered
    assert 'data-vh-mode="unavailable"' not in body and "could not be read" not in body
    assert (directory / "ledger.sqlite").is_file() and 'data-note="archive_built"' in body
    assert "Open this chip" in body and str(live) in body
    assert "/hub/link-folder" not in body and "load delay:" not in body
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert (directory / "ledger.sqlite").is_file()
    with app.app_context():
        assert routes._value_history(routes._active_ctx(), {})["mode"] == "ledger"


def test_column_reads_no_run_ledger_and_ends_on_unreadable(no_runs, monkeypatch):
    client = no_runs["client"]
    data = {"paths": json.dumps({"qA1": "qubits.qA1.T1"}), "label": "T1"}
    body = client.post("/bulk/column-history", data=data).get_data(as_text=True)
    assert 'data-note="no_folder_linked"' in body and "/hub/link-folder" in body
    assert "Older snapshot history" not in body
    def broken(*a, **k):
        raise ValueError("corrupt ledger")
    monkeypatch.setattr(value_history, "read", broken)
    body = client.post("/bulk/column-history", data=data).get_data(as_text=True)
    assert 'data-vh-mode="unavailable"' in body and PLAIN["unreadable"] in body
    assert "data-vh-retry=" not in body and "data-eid=" not in body


@pytest.mark.parametrize("url", ["/topology/trends?metrics=T1", "/param-history?since=all",
                                 "/topology/metric-meta", "/param-history/changes"])
def test_table_construction_error_is_terminal(no_runs, monkeypatch, url):
    import sqlite3
    from quam_state_manager.web import hub_status
    # S10 C5 (C3 review P2): any constructor error -> the table's own READ of the ledger
    # failing; a constructor bug is a real error (tests/test_s10_c5_review.py)
    def broken(*a, **k):
        raise sqlite3.DatabaseError("database disk image is malformed")
    monkeypatch.setattr(hub_status, "hub_index", SimpleNamespace(snapshot=broken))
    try:
        body = no_runs["client"].get(url, headers={"HX-Request": "true"}).get_data(as_text=True)
    except Exception as exc:  # noqa: BLE001 -- any escape is the defect this pins
        raise AssertionError(f"the read error escaped the route: {exc!r}") from None
    assert PLAIN["unreadable"] in body
    assert "Nothing older is shown in its place." in body
    assert "load delay:" not in body and "data-eid=" not in body


@pytest.mark.parametrize("url", ["/state/versions", "/state-history?body=1", "/api/history"])
def test_version_folder_binding_error_keeps_only_legacy_rows(no_runs, monkeypatch, url):
    with no_runs["app"].app_context():
        ctx = routes._active_ctx()
        meta = routes._history().check_and_snapshot(ctx["path"], "manual", force=True)
        assert meta is not None
    def broken(*a, **k):
        raise ValueError("corrupt folder view")
    monkeypatch.setattr(routes, "_vh_binding", broken)
    body = no_runs["client"].get(url).get_data(as_text=True)
    assert PLAIN["unreadable"] in body
    assert meta.timestamp in body and "older snapshot" in body
    assert "writer unknown" not in body


def test_deleted_live_ledger_is_unreadable(no_runs):
    directory = chip_dir(no_runs)
    (directory / "ledger.sqlite").unlink()
    with no_runs["app"].app_context():
        ctx = routes._active_ctx()
        answer = routes._value_history(ctx, {"value": "qubits.qA1.T1"})
        assert answer["mode"] == "unavailable" and answer["reason"] == "unreadable"
        versions = routes._versions_read(ctx, [])
        assert versions["mode"] == "unavailable" and versions["reason"] == "unreadable"
    body = no_runs["client"].get("/field/history?path=qubits.qA1.T1").get_data(as_text=True)
    assert PLAIN["unreadable"] in body and "data-vh-retry=" not in body


@pytest.mark.parametrize("mode", ["building", "preparing", "unavailable"])
@pytest.mark.parametrize("url", ["/state/versions", "/state-history?body=1", "/api/history"])
def test_nonledger_empty_list_does_not_deny_recorded_states(no_runs, monkeypatch, mode, url):
    from quam_state_manager.core import ramcache
    if mode == "building":
        monkeypatch.setattr(hub_sync, "status", lambda d: {"state": "building"})
    else:
        def fail(*a, **k):
            if mode == "preparing":
                raise ramcache.Warming("test", "key", 0)
            raise ValueError("corrupt ledger")
        monkeypatch.setattr(hub_versions, "_version_token", fail)
    body = no_runs["client"].get(url).get_data(as_text=True)
    assert "No recorded states" not in body and "No recorded versions" not in body
    assert "could not be read" in body if mode == "unavailable" else "change history" in body



def test_owned_backup_capture_refreshes_the_open_lane(no_runs):
    from quam_state_manager.core.hub_store import HubStore
    app = no_runs["app"]
    with app.app_context():
        ctx = routes._active_ctx()
        backup = Path(str(ctx["working_copy"].working_folder) + ".takelive_backup") / "saved" / "quam_state"
        write_chip(backup, chip_state(t1=4e-5), None)
        meta = routes._history().check_and_snapshot(backup, "manual", force=True, kind="backup")
        assert meta is not None
        with HubStore(ctx["hub_chip_dir"]) as store:
            row = store.conn.execute("SELECT outcome FROM observed_snapshots WHERE ts=?",
                                     (meta.timestamp,)).fetchone()
            assert row is not None, "the owned backup is offered before the next surface read"
        answer = routes._value_history(ctx, {"value": "qubits.qA1.T1"})
        points = answer["rows"]["value"]["points"]
        assert points and points[-1]["value"] == 4e-5 and points[-1]["kind"] == "observed"



def test_capture_releases_manager_lock_before_projection(no_runs, monkeypatch):
    import threading
    with no_runs["app"].app_context():
        hm = routes._history()
        ctx = routes._active_ctx()
    acquired = []
    real = hub_sync.kick
    def kick(cs):
        def reader():
            ok = hm._lock.acquire(timeout=0.5)
            acquired.append(ok)
            if ok:
                hm._lock.release()
        worker = threading.Thread(target=reader, daemon=True)
        worker.start()
        worker.join(2)
        real(cs)
    monkeypatch.setattr(hub_sync, "kick", kick)
    assert hm.check_and_snapshot(ctx["path"], "manual", force=True)
    assert acquired == [True], "projection must let a competing snapshot reader acquire the manager lock"


@pytest.mark.parametrize("url", ["/state/versions", "/state-history?body=1", "/api/history"])
def test_observed_version_keeps_bookmarks_without_duplicate_states(no_runs, url):
    app = no_runs["app"]
    with app.app_context():
        ctx = routes._active_ctx()
        hm = routes._history()
        first = hm.check_and_snapshot(ctx["path"], "manual", force=True)
        assert first is not None
        hm.annotate_snapshot(ctx["path"], first.timestamp, label="first", note="kept", pinned=True)
        second = hm.check_and_snapshot(ctx["path"], "manual", force=True)
        assert second is not None
        hm.annotate_snapshot(ctx["path"], second.timestamp, label="second", pinned=True)
        listing = routes._versions_read(ctx, hm.list_snapshots(ctx["path"]))
        assert listing["mode"] == "ledger" and listing["total"] == 1
        assert listing["rows"][0]["pinned"]
        assert {m.timestamp for m in listing["rows"][0]["annotations"]} == {first.timestamp, second.timestamp}
    body = no_runs["client"].get(url).get_data(as_text=True)
    assert "first / second" in body or "second / first" in body
    assert "kept" in body and ("Pinned" in body or "Unpin" in body)
    if url == "/state-history?body=1":
        assert f"/state-history/{first.timestamp}/label" in body
        assert f"/state-history/{second.timestamp}/label" in body


def test_snapshot_compare_uses_the_ledger_equality_rule(no_runs, monkeypatch):
    app = no_runs["app"]
    snapshots = []
    with app.app_context():
        ctx = routes._active_ctx()
        live = Path(ctx["path"])
        hm = routes._history()
        for number in (1, 1.0, 1.0 + 1e-12):
            state = chip_state(t1=2e-5)
            state["qubits"]["qA1"]["n_avg"] = number
            write_chip(live, state, None)
            snapshots.append(hm.check_and_snapshot(live, "manual", force=True).timestamp)
    calls = []
    compare = hub_versions.compare_n
    def checked(sides):
        calls.append(len(sides))
        return compare(sides)
    monkeypatch.setattr(hub_versions, "compare_n", checked)
    response = no_runs["client"].get("/diff/versions", query_string={"ts": snapshots})
    assert response.status_code == 200
    assert calls == [3]
    assert "qubits.qA1.n_avg" not in response.get_data(as_text=True)


def test_ledger_drawer_pages_states_and_refreshes_count(no_runs):
    import re
    with no_runs["app"].app_context():
        ctx = routes._active_ctx()
        live = Path(ctx["path"])
        hm = routes._history()
        stamps = []
        for number in range(3):
            write_chip(live, chip_state(t1=(number + 2) * 1e-5), None)
            stamps.append(hm.check_and_snapshot(live, "manual", force=True).timestamp)
    client = no_runs["client"]
    first = client.get("/api/history?per_page=2").get_data(as_text=True)
    second = client.get("/api/history?page=2&per_page=2").get_data(as_text=True)
    all_rows = client.get("/api/history?per_page=0").get_data(as_text=True)
    assert first.count('class="history-entry hp-ledger-row"') == 2
    assert second.count('class="history-entry hp-ledger-row"') == 1
    assert all_rows.count('class="history-entry hp-ledger-row"') == 3
    assert "Page 1 / 2" in first and "Page 2 / 2" in second
    assert 'value="0" selected>All' in all_rows
    for body in (first, second, all_rows):
        assert re.search(r'<span id="history-count" hx-swap-oob="true">3</span>', body)
    trends = client.get("/topology/trends").get_data(as_text=True)
    assert 'id="history-count"' not in trends, "Trends counts events, never the drawer's count"
    assert "3 recorded events" in trends


def test_ledger_state_history_keeps_snapshot_disk_usage(no_runs):
    with no_runs["app"].app_context():
        ctx = routes._active_ctx()
        assert routes._history().check_and_snapshot(ctx["path"], "manual", force=True)
    body = no_runs["client"].get("/state-history").get_data(as_text=True)
    assert "on disk" in body and "history-disk-line" in body


@pytest.mark.parametrize("url", ["/state/versions", "/state-history?body=1", "/api/history"])
def test_bookmark_of_sm_write_keeps_its_snapshot_actions(no_runs, monkeypatch, url):
    # the background snapshot hasher never finishes: the bookmark is placed
    # on its write without waiting for it (otherwise this pin races it)
    monkeypatch.setattr(hub_versions._Hashes, "_work", lambda self: None)
    client = no_runs["client"]
    client.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4e-5"})
    client.post("/state/apply-to-live")
    response = client.post("/state/archive", data={"tag": "kept write", "note": "bookmark"})
    assert "archive-ok" in response.get_data(as_text=True)
    with no_runs["app"].app_context():
        ctx = routes._active_ctx()
        hm = routes._history()
        snapshots = hm.list_snapshots(ctx["path"])
        bookmark = next(m for m in snapshots if m.label == "kept write")
        listing = routes._versions_read(ctx, snapshots)
        matches = [r for r in listing["rows"] if "kept write" in r["label"]]
        assert matches, "the covered bookmark must remain visible on its SM write"
        row = matches[0]
        assert row["badge"] == "SM write" and row["pinned"]
        assert bookmark.timestamp in {m.timestamp for m in row["annotations"]}
    body = client.get(url).get_data(as_text=True)
    assert "kept write" in body and "bookmark" in body
    if url == "/state-history?body=1":
        assert f"/state-history/{bookmark.timestamp}/label" in body


def test_archived_open_offer_never_names_a_folder_of_another_chip(no_runs, tmp_path):
    """The folder the newest captures came from now resolves to another chip
    (renamed since): opening it would build THAT chip's ledger, so no button."""
    app = no_runs["app"]
    client = no_runs["client"]
    with app.app_context():
        ctx = routes._active_ctx()
        live = Path(ctx["path"])
        directory = Path(ctx["hub_chip_dir"])
        assert routes._history().check_and_snapshot(live, "manual", force=True)
    other = tmp_path / "other"
    write_chip(other, chip_state(name="other"), None)
    (other / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.2"}}), encoding="utf-8")
    assert client.post("/load", data={"folder": str(other)}).status_code in (200, 302)
    # another chip now: another name, other qubits, another controller (the
    # identity ladder heals a rename alone back to the same chip)
    renamed = chip_state(name="renamed")
    renamed["qubits"] = {"qZ9": renamed["qubits"]["qA1"]}
    write_chip(live, renamed, None)
    (live / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.9"}}), encoding="utf-8")
    (directory / "ledger.sqlite").unlink()
    body = client.get("/param-history", query_string={"chip_key": directory.name, "since": "all"},
                      headers={"HX-Request": "true"}).get_data(as_text=True)
    # S10 walk: no_ledger end state -> built from ITS OWN history folder's snapshots
    assert 'data-vh-mode="unavailable"' not in body and 'data-note="archive_built"' in body
    assert "Open this chip" not in body and str(live) not in body


@pytest.mark.parametrize("reason", ["no_chip_dir", "no_ledger", "unreadable"])
def test_an_unavailable_answer_without_its_note_still_names_its_own_reason(reason):
    message = routes._vh_wait_message({"mode": "unavailable", "reason": reason})
    assert PLAIN[reason] in message and f"({reason})" not in message
    assert "Nothing older is shown in its place." in message


@pytest.mark.parametrize("url", ["/state-history?body=1", "/state/versions"])
def test_plain_copies_of_one_state_carry_one_pin(no_runs, url):
    """Many plain captures of one state are one ledger row; it carries ITS OWN
    snapshot's Pin -- never one button per identical copy."""
    with no_runs["app"].app_context():
        ctx = routes._active_ctx()
        hm = routes._history()
        stamps = [hm.check_and_snapshot(ctx["path"], "manual", force=True).timestamp for _ in range(4)]
        listing = routes._versions_read(ctx, hm.list_snapshots(ctx["path"]))
    assert listing["mode"] == "ledger" and listing["total"] == 1
    (row,) = listing["rows"]
    assert [m.timestamp for m in row["annotations"]] == [stamps[0]], \
        "the observed row carries the snapshot it was imported from, only"
    body = no_runs["client"].get(url).get_data(as_text=True)
    if url == "/state-history?body=1":
        # S10 C6: '/label"' -> '/label?body=1', the Pin now redraws only the timeline
        assert body.count("/label?body=1") == 1 and f"/state-history/{stamps[0]}/label?" in body


@pytest.mark.parametrize("url", ["/topology/trends?metrics=T1", "/topology/metric-meta",
                                 "/param-history/changes", "/param-history/expand?qubit=qA1&prop=T1",
                                 "/topology/trends/paths?q=T1", "/param-history/param-search?q=T1"])
def test_a_table_read_that_raises_is_terminal_never_a_500(no_runs, monkeypatch, url):
    """The mode read succeeded, then the table's own read of the ledger raised
    (a corrupt page): the surface ends unavailable, naming the reason -- never
    a 500, never rows, never another history in its place."""
    import sqlite3
    from quam_state_manager.web import hub_status

    # S10 C5 (C3 review P2): any RuntimeError from the table -> a failed ledger READ inside
    # it; a presenter's own bug is a real error now (tests/test_s10_c5_review.py)
    def broken(*a, **k):
        with hub_status.ledger_read():
            raise sqlite3.DatabaseError("database disk image is malformed")
    for name in ("part", "curated", "leaf_families", "path_rank"):
        monkeypatch.setattr(hub_status.LedgerTable, name, broken)
    try:
        response = no_runs["client"].get(url, headers={"HX-Request": "true"})
    except Exception as exc:  # noqa: BLE001 -- any escape is the defect this pins
        raise AssertionError(f"the table error escaped the route: {exc!r}") from None
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    if "/paths" in url or "param-search" in url:
        data = response.get_json()
        assert (data if isinstance(data, list) else data["results"]) == []
    else:
        assert PLAIN["unreadable"] in body
        assert "Nothing older is shown in its place." in body
        assert "load delay:" not in body and "data-eid=" not in body


@pytest.mark.parametrize("sync_on_open,expect", [(False, 0), (True, 1)])
def test_capture_keeps_the_testing_gate_for_a_chip_with_a_data_folder(no_runs, monkeypatch,
                                                                      sync_on_open, expect):
    """C3 review P2: a TESTING app syncs inline, so a capture on a chip whose
    declared data folder is a real archive must not ingest it unless the test
    asked (_hub_sync_open's gate); a chip with no data folder is kicked
    regardless (test_capture_promptly_reaches_observed_ledger)."""
    app = no_runs["app"]
    app.config["HUB_SYNC_ON_OPEN"] = sync_on_open
    kicks = []
    monkeypatch.setattr(hub_sync, "kick", lambda cs: kicks.append(cs))
    with app.app_context():
        ctx = routes._active_ctx()
        ctx["hub_roots"] = [("D:/a/declared/archive", "extras")]
        assert routes._history().check_and_snapshot(ctx["path"], "manual", force=True)
    assert len(kicks) == expect

# S10 C7: old -> new, live wait and count pins belong to mode selection.
@pytest.mark.parametrize("surface,url", SURFACES[:4])
@pytest.mark.parametrize("mode", ["building", "preparing"])
def test_transient_modes_remain_successful(no_runs, monkeypatch, surface, url, mode):
    from quam_state_manager.core import hub_sync, ramcache
    if mode == "building":
        monkeypatch.setattr(hub_sync, "status", lambda directory: {"state": "building", "done": 0, "total": 1})
    else:
        from quam_state_manager.core import hub_versions, value_history
        def warming(*args, **kwargs):
            raise ramcache.Warming("test", "key", 0)
        monkeypatch.setattr(value_history, "read", warming)
        monkeypatch.setattr(hub_versions, "_version_token", warming)
    response = no_runs["client"].get(url, headers={"HX-Request": "true"})
    assert response.status_code == 200
    # S10 final review: old -> new, "200" alone passed whatever a waiting surface
    # said -- each says ITS mode in S7's words, waits on its own, shows no rows
    body = response.get_data(as_text=True)
    words = {"building": "The change history is being built (0 of 1 runs)",
             "preparing": "Preparing the change history"}[mode]
    assert words in body, (surface, body[:400])
    if surface in ("versions", "state_history"):
        assert f'data-note="{mode}"' in body and "this list shows the older snapshot history" in body
    else:
        assert f'data-vh-mode="{mode}"' in body, (surface, body[:400])
        assert ("data-vh-retry=" in body) if surface == "drawer" else ('data-trends-updating="1"' in body)
    assert "data-eid=" not in body and "Try again" not in body, "a wait is not an end state"


@pytest.mark.parametrize("surface,url", [
    ("trends", "/topology/trends?metrics=T1"),
    ("sparklines", "/api/topology/sparklines/qA1"),
])
def test_a_ledger_becoming_unreadable_after_the_mode_check_is_terminal(sm, monkeypatch, surface, url):
    from quam_state_manager.core import value_history
    real = value_history.read
    def unreadable(directory, targets, **kwargs):
        if targets:
            raise ValueError("unreadable value")
        return real(directory, targets, **kwargs)
    monkeypatch.setattr(value_history, "read", unreadable)
    # S10 C3: old -> new, a table-time error ends unavailable without old rows.
    response = sm["client"].get(url)
    assert response.status_code == 200
    assert 'data-vh-mode="unavailable"' in response.get_data(as_text=True)
    assert "Nothing older is shown in its place." in response.get_data(as_text=True)


def test_no_run_version_count_is_stable_under_app_context(no_runs):
    import re
    import sqlite3
    client = no_runs["client"]
    # S10 final review: old -> new, "an int, the same twice" passed with the count
    # hard-coded to 12345 -- it is the TRUE count: the rows State History lists,
    # which are the ledger's state-bearing events (no older row on this chip)
    for t1 in (2e-5, 3e-5):
        with no_runs["app"].app_context():
            write_chip(Path(routes._active_ctx()["path"]), chip_state(t1=t1), None)
        assert client.post("/state-history/snapshot").status_code == 200
    body = client.get("/state-history?body=1&per_page=500").get_data(as_text=True)
    listed = len(re.findall(r'<div class="sh-entry[ "]', body))
    con = sqlite3.connect(f"file:{chip_dir(no_runs) / 'ledger.sqlite'}?mode=ro", uri=True)
    try:
        events = con.execute("SELECT COUNT(*) FROM events WHERE error IS NULL AND (flags & 4) = 0 AND "
                             "((kind = 'observed' AND state_hash IS NOT NULL) OR status = 'landed')").fetchone()[0]
    finally:
        con.close()
    with no_runs["app"].app_context():
        # S10 C7: old -> new, count consistency survives removal of instrumentation.
        count = routes._state_version_now(routes._active_ctx())["count"]
        assert isinstance(count, int)
        assert routes._state_version_now(routes._active_ctx())["count"] == count
    assert listed >= 2 and count == listed == events, (count, listed, events)
