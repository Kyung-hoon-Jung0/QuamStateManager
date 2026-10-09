"""S10 C5: the C3 review's findings on the Chip Status / Param History surfaces.

P1 -- an archived chip whose change ledger holds less than Param History holds
of it (nothing at all, or no runs while Param History holds run captures)
says so on its grid and its cell drawer and offers the press that builds the
ledger (Open this chip), never a silently empty history.

P2 -- only a failed READ of the ledger's rows ends Trends, the metric meta,
Changes, the cell drawer or the table build as "could not be read
(unreadable)"; a bug in the code that presents a successful read is a real
error (logged, a 500).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from tests.test_hub_drawer import _inline, chip_state, make_app, write_chip  # noqa: F401
from tests.test_hub_fallback_tripwire import no_runs  # noqa: F401


def _archived(tmp_path, *, empty_ledger: bool):
    """A chip with a manual capture of its folder and three run captures
    (Param History's identity-ingested runs, no data folder linked), then
    another chip opened: the first one is ARCHIVED. ``empty_ledger``: its
    ledger is replaced by an empty one (a chip not opened since the ledger
    existed); else it keeps the observed states its open imported."""
    live = tmp_path / "chip"
    write_chip(live, chip_state(t1=1.0e-5), None)
    app = make_app(tmp_path)
    client = app.test_client()
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    with app.app_context():
        hm = routes._history()
        directory = Path(routes._hub_chip_dir(routes._active_ctx()["path"]))
        assert hm.check_and_snapshot(str(live), "manual", force=True) is not None
        for i, t1 in enumerate((2.0e-5, 3.0e-5, 4.0e-5), start=1):
            write_chip(live, chip_state(t1=t1), None)
            meta = hm.check_and_snapshot(str(live), "experiment", experiment_name="05_T1", run_id=i,
                                         experiment_folder_path=str(tmp_path / "runs" / f"#{i}"),
                                         kind="exp", force=True)
            assert meta is not None
    other = tmp_path / "other"
    write_chip(other, chip_state(name="other"), None)
    (other / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.2"}}), encoding="utf-8")
    assert client.post("/load", data={"folder": str(other)}).status_code in (200, 302)
    if empty_ledger:
        from quam_state_manager.core import hub_index
        from quam_state_manager.core.hub_store import HubStore
        hub_index.close_readers()
        (directory / "ledger.sqlite").unlink()
        with HubStore(directory):
            pass
    return client, directory, live


def _note(html: str) -> str:
    import re
    m = re.search(r'<p class="vh-note[^"]*" data-note="archive_short"[^>]*>(.*?)</p>', html, re.S)
    return m.group(1) if m else ""


@pytest.mark.parametrize("empty_ledger", [True, False])
def test_an_archived_ledger_short_of_its_captures_says_so_and_offers_to_open(tmp_path, empty_ledger):
    client, directory, live = _archived(tmp_path, empty_ledger=empty_ledger)
    grid = client.get("/param-history", query_string={"chip_key": directory.name, "since": "all",
                                                       "props": "T1"},
                      headers={"HX-Request": "true"}).get_data(as_text=True)
    drawer = client.get("/param-history/expand", query_string={"chip_key": directory.name,
                                                               "qubit": "qA1", "prop": "T1"},
                        headers={"HX-Request": "true"}).get_data(as_text=True)
    for body in (grid, drawer):
        note = _note(body)
        if empty_ledger:
            assert "holds nothing yet" in note and "4 captures of it (3 from runs)" in note, note
        else:
            assert "3 run captures of this chip that its change ledger does not" in note, note
        assert "Open this chip to build its ledger, then link the folder" in note, note
        assert f'name="folder" value="{live}">Open this chip</button>' in body
        assert 'data-vh-mode="unavailable"' not in body


def test_an_archived_ledger_holding_its_runs_says_nothing_more(tmp_path):
    client, directory, _live = _archived(tmp_path, empty_ledger=False)
    from quam_state_manager.core import hub_index
    hub_index.close_readers()
    import sqlite3
    with sqlite3.connect(str(directory / "ledger.sqlite")) as conn:   # the ledger now holds a run
        conn.execute("UPDATE events SET kind='run', run_id=1 WHERE eid=(SELECT MAX(eid) FROM events)")
    grid = client.get("/param-history", query_string={"chip_key": directory.name, "since": "all"},
                      headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'data-note="archive_short"' not in grid


# ── P2: only a failed ledger READ is an unreadable ledger ────────────────────
# A bug in the code that presents a successful read is a real error (logged, a
# 500) -- never "The change history could not be read (unreadable)".

_PRESENTERS = [
    ("/topology/trends?metrics=T1", "_topology_trends_html"),
    ("/topology/metric-meta", "_hub_metric_meta"),
    ("/param-history/changes", "_hub_param_changes"),
    ("/param-history/expand?qubit=qA1&prop=T1", "_hub_param_history_expand"),
]


def _bug(*_a, **_k):
    raise RuntimeError("presenter bug")


@pytest.mark.parametrize("url,presenter", _PRESENTERS)
def test_a_presenter_bug_after_a_good_ledger_read_is_a_real_error(no_runs, monkeypatch, caplog,
                                                                  url, presenter):
    app = no_runs["app"]
    app.config["PROPAGATE_EXCEPTIONS"] = False
    monkeypatch.setattr(routes, presenter, _bug)
    response = no_runs["client"].get(url, headers={"HX-Request": "true"})
    body = response.get_data(as_text=True)
    assert response.status_code == 500, (response.status_code, body[:300])
    assert "could not be read" not in body and 'data-vh-mode="unavailable"' not in body
    assert any(r.exc_info and "presenter bug" in str(r.exc_info[1]) for r in caplog.records), \
        "the error is logged with its traceback"


@pytest.mark.parametrize("url", ["/topology/trends?metrics=T1", "/param-history?since=all",
                                 "/topology/metric-meta", "/param-history/changes"])
def test_a_bug_building_the_table_is_a_real_error(no_runs, monkeypatch, url):
    from quam_state_manager.web import hub_status
    no_runs["app"].config["PROPAGATE_EXCEPTIONS"] = False
    monkeypatch.setattr(hub_status.LedgerTable, "__init__", _bug)
    response = no_runs["client"].get(url, headers={"HX-Request": "true"})
    body = response.get_data(as_text=True)
    assert response.status_code == 500 and "could not be read" not in body, body[:300]


def test_a_failed_ledger_read_is_unreadable_and_says_why():
    import sqlite3
    from quam_state_manager.web import hub_status
    with pytest.raises(hub_status.LedgerUnreadable, match="DatabaseError: malformed"):
        with hub_status.ledger_read():
            raise sqlite3.DatabaseError("malformed")
    from quam_state_manager.core import ramcache
    got = None
    try:                                             # a wait stays a wait
        with hub_status.ledger_read():
            raise ramcache.Warming("x", "y", 0)
    except Exception as exc:  # noqa: BLE001 -- which one arrives is the pin
        got = exc
    assert type(got) is ramcache.Warming, repr(got)


@pytest.mark.parametrize("module,name", [("hub_query", "timeline"), ("value_history", "undone_paths")])
def test_a_changes_page_read_that_fails_is_unreadable(no_runs, monkeypatch, module, name):
    """The Changes page reads the ledger itself (the timeline; what a later
    undo took back of an SM write): either failing is an unreadable ledger,
    said and never asked again."""
    import importlib
    import sqlite3
    client = no_runs["client"]
    # an SM write, so the page reads what an undo took back of it
    assert client.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4.5e-5"}).status_code == 200
    assert client.post("/state/apply-to-live", headers={"X-SM-Actor": "operator"}).status_code == 200
    # setup check on another page of the feed (a page read once is kept)
    assert "applied by operator" in client.get("/param-history/changes?prefix=qubits",
                                               headers={"HX-Request": "true"}).get_data(as_text=True)

    def corrupt(*_a, **_k):
        raise sqlite3.DatabaseError("database disk image is malformed")
    monkeypatch.setattr(importlib.import_module("quam_state_manager.core." + module), name, corrupt)
    try:
        response = no_runs["client"].get("/param-history/changes", headers={"HX-Request": "true"})
    except Exception as exc:  # noqa: BLE001 -- any escape is the defect this pins
        raise AssertionError(f"the read error escaped the route: {exc!r}") from None
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "could not be read (unreadable)" in body and "load delay:" not in body
