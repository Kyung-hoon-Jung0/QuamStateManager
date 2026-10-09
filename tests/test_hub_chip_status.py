"""docs/283 (S8) -- Chip Status Trends, the metric meta, Param History and its
Changes read the chip's change ledger through S7's one reader.

Pins, on generic synthetic chips and run folders in ``tmp_path`` (the S7
fixtures of ``test_hub_drawer``: run #1 the genesis state; #2 sets ``T1`` BY
ITS OWN PATCH; #3 moves ``f_01`` with no patch; #4 retargets ``x180``):

* one reader: every surface's values come from ``routes._value_history``;
* a run is named as the writer of a value only when its own patch proves it
  -- a value made of several leaves only when EVERY leaf change is proven;
  every other point says what the ledger knows, in S7's words;
* SM writes name actor and kind; an undone write is marked, per path;
* building / preparing / idle / degraded are said, never a partial answer;
  a chip with no ledger of its runs keeps the old path, labelled;
* faults: a foreign-chip run, a retargeted pointer, a NaN-only metric, a
  deleted run folder -- each checked by what the surface SAYS.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_sync, value_history
from quam_state_manager.web import routes
from tests.test_hub_drawer import (  # noqa: F401 -- _inline is an autouse fixture
    T0, _inline, chip_dir, chip_state, iso, make_app, patch, run, sm, write_chip)

TRENDS = "/topology/trends?metrics=T1"
META = "/topology/metric-meta"
GRID = "/param-history?since=all"
CHANGES = "/param-history/changes"
SURFACES = (TRENDS, META, GRID, CHANGES)


def charts(response) -> list[dict]:
    m = re.search(r'<script[^>]*id="topo-trends-data"[^>]*>(.*?)</script>',
                  response.data.decode(), re.S)
    assert m, response.data.decode()[-1500:]
    return json.loads(m.group(1))


def series_of(response, entity, metric=None) -> dict:
    found = [s for c in charts(response) for s in c["series"]
             if s["entity"] == entity and (metric is None or c["metric"] == metric)]
    assert len(found) == 1, found
    return found[0]


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def groups(html: str) -> list[str]:
    """The Changes page's groups, as text, newest first."""
    return [text(g) for g in html.split('<div class="ph-change-group">')[1:]]


def load(tmp_path, runs, live_state, *, sync=True):
    """A chip whose data folder holds *runs* (``[(state, patches)]``)."""
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    for i, (state, patches) in enumerate(runs, start=1):
        run(data, i, state, patches=patches)
    write_chip(live, live_state, data)
    app = make_app(tmp_path, sync=sync)
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "data": data, "tmp": tmp_path}


def matrix_state(cm, **kw):
    state = chip_state(**kw)
    state["qubits"]["qA1"]["resonator"]["confusion_matrix"] = cm
    return state


# ======================================================================
# 1. one reader for every surface
# ======================================================================

class TestOneReader:
    def test_trends_the_grid_and_the_meta_read_through_the_value_history(self, sm, monkeypatch):
        seen = []
        real = routes._value_history

        def spy(ctx, paths, **kw):
            seen.extend(paths.values())
            return real(ctx, paths, **kw)
        monkeypatch.setattr(routes, "_value_history", spy)
        got = {}
        for url in (TRENDS, GRID + "&props=T1", META):
            seen.clear()
            got[url] = sm["client"].get(url)
            assert got[url].status_code == 200
            assert "qubits.qA1.T1" in seen, url + " read qA1 T1 through the value history"
        trends, grid = got[TRENDS], got[GRID + "&props=T1"]
        assert got[META].get_json()["mode"] == "ledger"
        t1 = series_of(trends, "qA1")
        assert [p[1] for p in t1["points"]] == [1e-5, 3e-5, 3e-5]
        assert "Older snapshot history" not in trends.data.decode() + grid.data.decode()

    def test_an_alias_is_drawn_from_the_holder_it_named_then(self, sm):
        r = sm["client"].get("/topology/trends", query_string={
            "metrics": "", "path": "qubits.qA1.xy.operations.x180.amplitude"})
        s = series_of(r, "qA1")
        held = s.get("held") or {}
        assert [v for ts, v in s["points"] if ts not in held] == [0.3, 0.25], \
            "x180_Gauss's value while x180 named it, then x180_DragCosine's"
        assert s["attr"][s["points"][-1][0]]["sub"] == "writer not proven"

    def test_the_typeahead_offers_numeric_families_of_the_ledger(self, sm):
        c = sm["client"]
        assert any(r["path"] == "qubits.*.T1" for r in c.get("/topology/trends/paths?q=T1").get_json())
        assert c.get("/topology/trends/paths?q=flag").get_json() == [], \
            "a boolean family has no numeric trend: never offered"

    def test_a_wildcard_family_matches_whole_segments(self, sm):
        from quam_state_manager.web.hub_status import LedgerTable
        with sm["app"].test_request_context():
            _ans, table = routes._hub_status_table(routes._active_ctx())
            assert isinstance(table, LedgerTable)
            assert table.leaf_matching_paths("qubits.*.T1") == ["qubits.qA1.T1", "qubits.qA2.T1"]
            assert table.leaf_matching_paths("qubits.*.amplitude") == [], \
                "* stands for ONE segment, never a run of them"


# ======================================================================
# 2. a run is the writer only on proof
# ======================================================================

class TestWriterOnlyOnProof:
    def test_the_meta_names_a_run_only_on_its_own_patch(self, sm):
        d = sm["client"].get(META).get_json()
        proven, unproven, first = d["q"]["T1"]["qA1"], d["q"]["f_01"]["qA1"], d["q"]["T1"]["qA2"]
        assert proven["run"] == 2 and proven["writer"]["run"] == 2
        assert proven["sub"] == "its own patch set it"
        assert unproven["run"] is None and unproven["writer"] is None
        assert unproven["saved_run"] == 3 and unproven["sub"] == "writer not proven"
        assert first["run"] is None and first["first"] and "writer unknown" in first["sub"]

    def test_a_matrix_is_proven_only_when_every_leaf_change_is(self, tmp_path):
        base = [[0.9, 0.1], [0.1, 0.9]]
        moved = [[0.8, 0.2], [0.1, 0.9]]
        cm = "qubits.qA1.resonator.confusion_matrix"
        env = load(tmp_path, [
            (matrix_state(base), None),
            # the run's patch sets ONE of the two elements it moved (the
            # later one in path order: no "last leaf" shortcut can pass)
            (matrix_state(moved), [patch(cm + ".0.1", 0.2, 0.1)]),
        ], matrix_state(moved))
        meta = env["client"].get(META).get_json()["q"]["assignment_fidelity"]["qA1"]
        assert meta["run"] is None and meta["writer"] is None and meta["sub"] == "writer not proven", \
            "one proven leaf never makes the run the writer of the whole matrix"
        s = series_of(env["client"].get("/topology/trends?metrics=assignment_fidelity"), "qA1")
        last = [p for p in s["points"] if p[0] not in (s.get("held") or {})][-1]
        assert last[1] == pytest.approx(0.85)
        assert s["attr"][last[0]]["provenance"] == "run_saved"

    def test_a_matrix_whose_every_change_is_patched_names_its_run(self, tmp_path):
        base = [[0.9, 0.1], [0.1, 0.9]]
        moved = [[0.8, 0.2], [0.1, 0.9]]
        cm = "qubits.qA1.resonator.confusion_matrix"
        env = load(tmp_path, [
            (matrix_state(base), None),
            (matrix_state(moved), [patch(cm + ".0.0", 0.8, 0.9), patch(cm + ".0.1", 0.2, 0.1)]),
        ], matrix_state(moved))
        meta = env["client"].get(META).get_json()["q"]["assignment_fidelity"]["qA1"]
        assert meta["run"] == 2 and meta["sub"] == "its own patch set it"

    def test_a_run_of_another_chip_is_never_the_writer(self, sm):
        run(sm["data"], 5, chip_state(t1=9e-5, name="another-device"),
            patches=[patch("qubits.qA1.T1", 9e-5, 3e-5)])
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        entry = sm["client"].get(META).get_json()["q"]["T1"]["qA1"]
        assert entry["run"] is None and entry["writer"] is None
        assert entry["provenance"] == "run_uncertain_chip" and "chip uncertain" in entry["label"]
        body = sm["client"].get(CHANGES).data.decode()
        assert "#5 (chip uncertain)" in groups(body)[0] and "/dataset/" not in body.split(
            '<div class="ph-change-group">')[1], "no data link for a run of another chip"

    def test_changes_name_the_writer_per_row(self, sm):
        g = groups(sm["client"].get(CHANGES).data.decode())
        by_run = {re.search(r"(run #\d|first recorded in #\d)", x).group(1): x for x in g}
        assert "qubits.qA1.T1" in by_run["run #2"] and "its own patch set it" in by_run["run #2"]
        assert "qubits.qA1.f_01" in by_run["run #3"] and "writer not proven" in by_run["run #3"]
        assert "its own patch set it" not in by_run["run #3"], \
            "a row the run's patch did not set never says the run set it"
        assert "ledger start; writer unknown" in by_run["first recorded in #1"]

    def test_the_grid_drawer_opens_a_run_only_on_proof(self, sm):
        html = sm["client"].get("/param-history/expand?qubit=qA1&prop=f_01").data.decode()
        data = json.loads(re.search(r'id="phd-data" type="application/json">(.*?)</script>',
                                    html, re.S).group(1))
        assert data["ledger"] and [(v["value"], v["sub"], v["uid"], v["run"]) for v in data["values"]] == [
            (5.0e9, "ledger start; writer unknown", None, None), (5.1e9, "writer not proven", None, None)]
        html = sm["client"].get("/param-history/expand?qubit=qA1&prop=T1").data.decode()
        data = json.loads(re.search(r'id="phd-data" type="application/json">(.*?)</script>',
                                    html, re.S).group(1))
        proven = data["values"][-1]
        assert proven["sub"] == "its own patch set it" and proven["run"] == 2 and proven["uid"]


# ======================================================================
# 3. SM writes and undo
# ======================================================================

class TestSmWrites:
    def test_an_sm_write_names_actor_and_kind_and_an_undo_is_marked(self, sm):
        c = sm["client"]
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4.5e-5"}).status_code == 200
        assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "alice"}).status_code == 200
        entry = c.get(META).get_json()["q"]["T1"]["qA1"]
        assert entry["actor"] == "human:alice" and entry["kind"] == "sm_apply"
        assert entry["label"] == "applied by alice" and entry["run"] is None
        assert c.post("/undo", headers={"X-SM-Actor": "alice"}).status_code == 200
        attrs = series_of(c.get(TRENDS), "qA1")["attr"].values()
        assert any(a["label"] == "applied by alice (undone)" for a in attrs)
        assert any(a["label"] == "undo by alice" for a in attrs)
        assert "applied by alice (undone)" in c.get(CHANGES).data.decode()

    def test_a_partly_undone_write_marks_only_the_row_taken_back(self, sm):
        c = sm["client"]
        for path, val in (("qubits.qA1.T1", "4.5e-5"), ("qubits.qA2.T1", "5.5e-5")):
            assert c.post("/field/edit", data={"dot_path": path, "value": val}).status_code == 200
        assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "op"}).status_code == 200
        assert c.post("/undo", headers={"X-SM-Actor": "op"}).status_code == 200      # takes back qA2
        html = c.get(CHANGES).data.decode()
        apply = next(g for g in html.split('<div class="ph-change-group">')[1:]
                     if "applied by op" in g)
        rows = re.findall(r"<tr( class=\"vh-undone\")?>\s*<td class=\"ph-change-path\">.*?>(qubits\.[^<]+)</button>",
                          apply, re.S)
        assert {p: bool(u) for u, p in rows} == {"qubits.qA1.T1": False, "qubits.qA2.T1": True}
        assert "partly undone" in text(apply)

    def test_the_meta_takes_the_newest_change_in_ledger_order(self, tmp_path, monkeypatch):
        state = matrix_state([[0.9, 0.1], [0.1, 0.9]])
        moved = matrix_state([[0.8, 0.1], [0.1, 0.9]])
        for st in (state, moved):        # the runs saved the live file's own content
            st["extras"]["data_folder"] = str(tmp_path / "data")
        env = load(tmp_path, [(state, None), (moved, None)], moved)
        # an SM write whose own clock reads EARLIER than run #2's instant
        monkeypatch.setattr(hub, "_now", lambda: (T0 + 15_000_000, iso(T0 + 15_000_000)))
        c = env["client"]
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.resonator.confusion_matrix.1.1",
                                           "value": "0.7"}).status_code == 200
        assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "alice"}).status_code == 200
        entry = c.get(META).get_json()["q"]["assignment_fidelity"]["qA1"]
        assert entry["kind"] == "sm_apply" and entry["label"] == "applied by alice"


# ======================================================================
# 4. the grid, its windows and its Source filter
# ======================================================================

class TestGrid:
    def _cells(self, sm, monkeypatch, query):
        captured = {}
        real = routes.render_template

        def spy(template, **kw):
            if "cells" in kw:
                captured.update(kw)
            return real(template, **kw)
        monkeypatch.setattr(routes, "render_template", spy)
        assert sm["client"].get(query).status_code == 200
        return captured

    def test_the_source_filter_keeps_every_run_event_and_maps_sm_writes(self, sm, monkeypatch):
        c = sm["client"]
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4.5e-5"}).status_code == 200
        assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "alice"}).status_code == 200
        got = self._cells(sm, monkeypatch, GRID + "&props=T1&triggers=experiment")
        vals = [v for v in got["cells"]["qA1", "T1"]["values"] if not v.get("held")]
        assert [v["point"]["run_id"] for v in vals] == [1, 2], \
            "a run's event is an experiment event whether or not its patch proves the value"
        got = self._cells(sm, monkeypatch, GRID + "&props=T1&triggers=save")
        assert ("qA1", "T1") in got["cells"], "an SM write is a Save"
        vals = [v for v in got["cells"]["qA1", "T1"]["values"] if not v.get("held")]
        assert [(v["value"], v["point"]["kind"]) for v in vals] == [(4.5e-5, "sm_apply")]
        assert got["summary"]["by_trigger"] == {"save": 1}

    def test_a_value_in_force_when_the_window_opens_is_carried_in(self, sm, monkeypatch):
        since = "20260101_120025_000"           # after #2 set T1, before #4
        got = self._cells(sm, monkeypatch, "/param-history?props=T1&since=" + since)
        vals = got["cells"]["qA1", "T1"]["values"]
        assert vals[0] == {"timestamp": since, "value": 3e-5, "trigger": "experiment",
                           "held": "20260101_120020_e2"}
        assert "hs-line" in got["cells"]["qA1", "T1"]["svg_inner"], "an unchanged value still draws its line"

    def test_the_auto_import_gate_still_counts_snapshots(self, sm):
        html = sm["client"].get(GRID).data.decode()
        assert 'data-snapshot-total="0"' in html, \
            "the ledger's events never stand in for the snapshot importer's count"

    def test_an_archived_chip_reads_its_own_ledger(self, sm, tmp_path):
        with sm["app"].test_request_context():
            key = routes._history()._key_for(Path(routes._active_ctx()["path"]))
        other = tmp_path / "chips" / "other"
        write_chip(other, chip_state(t1=7e-5, name="second-device"), None)
        c = sm["client"]
        assert c.post("/load", data={"folder": str(other)}).status_code in (200, 302)
        with sm["app"].test_request_context():
            assert routes._history()._key_for(Path(routes._active_ctx()["path"])) != key, \
                "the open chip is another chip: the first one is now archived"
        html = c.get(f"/param-history?chip_key={key}&props=T1&since=all").data.decode()
        assert "Older snapshot history" not in html
        assert '<th class="phg-qubit">qA1</th>' in html and "recorded changes shown" in html


# ======================================================================
# 5. Changes
# ======================================================================

class TestChanges:
    def test_changes_read_the_timeline_with_a_path_prefix(self, sm, monkeypatch):
        from quam_state_manager.core import hub_query
        calls = []
        real = hub_query.timeline

        def spy(binding, **kw):
            calls.append(kw)
            return real(binding, **kw)
        monkeypatch.setattr(hub_query, "timeline", spy)
        body = sm["client"].get(CHANGES + "?prefix=QUBITS.qa1.T").data.decode()
        assert calls and calls[0]["path"] == "QUBITS.qa1.T" and calls[0]["path_prefix"]
        assert ">qubits.qA1.T1</button>" in body and ">qubits.qA1.f_01</button>" not in body

    def test_one_event_opens_whole_and_pages_never_skip_one(self, sm, monkeypatch):
        c = sm["client"]
        body = c.get(CHANGES + "?at=1&prefix=qubits.qA1.T1").data.decode()
        g = groups(body)
        assert len(g) == 1 and "first recorded in #1" in g[0] and "run #2" not in body, \
            "?at= opens THAT event, not the newest one touching the path"
        monkeypatch.setattr(routes, "_CHANGES_SNAPS", 1)
        first = c.get(CHANGES).data.decode()
        assert "run #4" in groups(first)[0]
        link = re.search(r"/param-history/changes\?before=([^\"&]+)", first)
        assert link, "an older page is offered"
        cursor = link.group(1)
        second = c.get(CHANGES, query_string={"before": cursor}).data.decode()
        assert len(groups(second)) == 1 and "run #3" in groups(second)[0]

    def test_text_booleans_and_removals_are_changes_too(self, sm):
        state = chip_state(t1=3e-5, f01=5.1e9, amp=0.25)
        state["qubits"]["qA1"]["flag"] = "ready"
        del state["qubits"]["qA1"]["T1"]
        run(sm["data"], 5, state)
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        page = groups(sm["client"].get(CHANGES + "?at=5").data.decode())
        assert page, "run #5's changes are listed"
        g = page[0]
        assert "true" in g and "ready" in g and "removed" in g and "writer not proven" in g

    @pytest.mark.parametrize("query", ("before=invalid", "at=invalid"))
    def test_an_invalid_page_reference_is_a_readable_refusal(self, sm, query):
        r = sm["client"].get(CHANGES + "?" + query)
        assert r.status_code == 400 and "open Changes again" in r.data.decode()

    def test_the_typeahead_offers_the_ledgers_paths(self, sm):
        got = sm["client"].get("/param-history/param-search?q=T1").get_json()["results"]
        assert {r["path"] for r in got} == {"qubits.qA1.T1", "qubits.qA2.T1"}

    def test_a_full_page_after_a_swap_is_still_a_full_page(self, sm):
        c = sm["client"]
        partial = c.get(CHANGES, headers={"HX-Request": "true"}).data.decode()
        full = c.get(CHANGES).data.decode()
        assert "<html" not in partial and "<html" in full


# ======================================================================
# 6. what every surface says while the ledger is not an answer
# ======================================================================

def _said(body: str) -> str:
    return body if body.lstrip().startswith("{") else text(body)


class TestModes:
    @pytest.mark.parametrize("url", SURFACES)
    def test_building_is_said_and_asked_again_without_rows(self, sm, monkeypatch, url):
        monkeypatch.setattr(hub_sync, "status", lambda _d: {"state": "building", "done": 2, "total": 9})
        r = sm["client"].get(url)
        body = r.data.decode()
        assert r.status_code == 200 and "being built (2 of 9 runs)" in body
        assert "Older snapshot history" not in body
        assert '"series"' not in body and "ph-change-group" not in body and "history-cell" not in body
        if not url.startswith("/topology/metric-meta"):
            assert re.search(r'hx-trigger="load delay:2000ms"', body), "the surface asks again by itself"
        else:
            assert r.get_json()["updating"] and r.get_json()["mode"] == "building"

    @pytest.mark.parametrize("url", SURFACES)
    def test_preparing_is_said_in_the_same_words(self, sm, monkeypatch, url):
        from quam_state_manager.core.ramcache import Warming

        def busy(*_a, **_k):
            raise Warming("test", "device", 0)
        monkeypatch.setattr(value_history, "read", busy)
        body = sm["client"].get(url).data.decode()
        assert "Preparing the change history" in body and "Older snapshot history" not in body

    @pytest.mark.parametrize("url", SURFACES)
    def test_a_busy_read_after_the_mode_check_is_never_an_empty_answer(self, sm, monkeypatch, url):
        from quam_state_manager.core import hub_query
        from quam_state_manager.core.ramcache import Warming
        real = routes._value_history

        def busy(ctx, paths, **kw):
            return {"mode": "preparing"} if paths else real(ctx, paths, **kw)

        def busy_timeline(*_a, **_k):
            raise Warming("test", "device", 0)
        monkeypatch.setattr(routes, "_value_history", busy)
        monkeypatch.setattr(hub_query, "timeline", busy_timeline)
        body = sm["client"].get(url).data.decode()
        assert "Preparing the change history" in body
        assert '"series"' not in body and "ph-change-group" not in body and "history-cell" not in body

    @pytest.mark.parametrize("url", SURFACES)
    def test_idle_is_said(self, sm, monkeypatch, url):
        real = hub_sync.status
        monkeypatch.setattr(hub_sync, "status", lambda d: {**real(d), "state": "idle"})
        assert "not being kept current in this window" in sm["client"].get(url).data.decode()

    @pytest.mark.parametrize("url", SURFACES)
    def test_degraded_says_changes_may_be_missing(self, sm, monkeypatch, url):
        real = hub_sync.status
        monkeypatch.setattr(hub_sync, "status", lambda d: {**real(d), "state": "degraded", "failed": 1})
        body = sm["client"].get(url).data.decode()
        assert "This history may be missing changes" in body and "1 run could not be read into it" in body

    def test_an_empty_grid_still_says_idle(self, sm, monkeypatch):
        real = hub_sync.status
        monkeypatch.setattr(hub_sync, "status", lambda d: {**real(d), "state": "idle"})
        assert "not being kept current in this window" in sm["client"].get(
            "/param-history?props=").data.decode()

    @pytest.mark.parametrize("url", SURFACES)
    def test_a_chip_with_no_ledger_keeps_the_old_path_labelled(self, tmp_path, url):
        live = tmp_path / "chip"
        write_chip(live, chip_state(), None)
        app = make_app(tmp_path, sync=False)
        c = app.test_client()
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        r = c.get(url)
        assert r.status_code == 200
        if url == META:
            # S10 C3: old -> new, no-folder chips read their ledger with a link offer.
            assert r.get_json()["mode"] == "ledger"
            assert r.get_json()["link"]["offer"]
            return
        body = r.data.decode()
        root = 'id="topo-trends"' if url == TRENDS else 'id="param-history-root"'
        # S10 C3: old -> new, the no-folder note stays inside the swapped root.
        assert body.find('data-note="no_folder_linked"') > body.find(root) >= 0, \
            "the label is inside the swapped root (a swap replaces it, never stacks it)"


# ======================================================================
# 7. faults, by what the surface says
# ======================================================================

class TestFaults:
    def test_a_nan_only_metric_draws_no_trend_and_says_why(self, tmp_path):
        state = chip_state(t1=float("nan"))
        state["qubits"]["qA2"]["T1"] = float("nan")
        env = load(tmp_path, [(state, None), (state, None)], state)
        r = env["client"].get(TRENDS)
        assert "No finite numeric value recorded" in r.data.decode()
        assert not charts(r)[0]["series"]
        meta = json.loads(env["client"].get(META).data,
                          parse_constant=lambda v: pytest.fail("non-finite JSON: " + v))
        cell = meta["q"]["T1"]["qA1"]
        assert cell.get("nonfinite") == "nan" and "value" not in cell, cell

    def test_a_deleted_run_folder_keeps_its_proof_and_loses_its_link(self, sm):
        source = next((sm["data"] / "2026-01-01").glob("#2_*"))
        shutil.rmtree(source)
        sync = hub_sync.sync_for(chip_dir(sm))
        sync.request(full=True)
        hub_sync._kick(sync)
        entry = sm["client"].get(META).get_json()["q"]["T1"]["qA1"]
        assert entry["run"] == 2 and entry["writer"]["uid"] is None
        assert "run folder deleted" in entry["flags"]
        s = series_of(sm["client"].get(TRENDS), "qA1")
        attr = s["attr"]["20260101_120020_e2"]
        assert attr.get("uid") is None and "run folder deleted" in attr["flag_text"]

    def test_a_new_run_moves_every_cached_answer(self, sm):
        c = sm["client"]
        for url in SURFACES:
            assert c.get(url).status_code == 200
        run(sm["data"], 5, chip_state(t1=9e-5, f01=5.1e9, amp=0.25))
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        assert c.get(META).get_json()["q"]["T1"]["qA1"]["saved_run"] == 5
        assert 9e-5 in [p[1] for p in series_of(c.get(TRENDS), "qA1")["points"]]
        assert "run #5" in groups(c.get(CHANGES).data.decode())[0]

    def test_an_aliased_matrix_keeps_its_logical_paths(self, tmp_path):
        a = chip_state()
        a["matrices"] = {"first": [[0.9, 0.1], [0.1, 0.9]], "second": [[0.8, 0.2], [0.2, 0.8]]}
        a["qubits"]["qA1"]["resonator"]["confusion_matrix"] = "#/matrices/first"
        b = json.loads(json.dumps(a))
        b["qubits"]["qA1"]["resonator"]["confusion_matrix"] = "#/matrices/second"
        env = load(tmp_path, [(a, None), (b, None)], b)
        q = env["client"].get(META).get_json()["q"]
        assert "qA1" in q.get("assignment_fidelity", {}), "the aliased matrix is enumerated"
        entry = q["assignment_fidelity"]["qA1"]
        assert entry["saved_run"] == 2 and entry["run"] is None
        s = series_of(env["client"].get("/topology/trends?metrics=assignment_fidelity"), "qA1")
        assert [p[1] for p in s["points"] if p[0] not in (s.get("held") or {})] == [0.9, 0.8]


# ======================================================================
# 8. the page's own JS says the same (jsdom)
# ======================================================================

def test_the_chip_status_selfcheck():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    r = subprocess.run([node, str(Path(__file__).with_name("hub_chip_status_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8", timeout=120,
                       cwd=str(Path(__file__).resolve().parents[1]))
    if r.returncode == 2 and "jsdom not installed" in r.stdout:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"all \d+ checks passed", r.stdout), r.stdout
