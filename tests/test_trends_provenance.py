"""Every Trends point names the run it came from (customer feedback 2026-09-09).

    *"When I hover a point on a Trends chart, show the run number plus a short
     experiment name (a few words of the node name is enough, since every node
     is numbered). If the value did not come from a run — manual, or from
     somewhere outside — just say modified externally. That way a person can go
     look at the actual measurement. And if I CLICK a point that came from a
     run, can the dataset panel open?"*

Provenance is a property of the SNAPSHOT, not of the point, so the answer ships
as ONE map per response keyed by snapshot id — `_trend_points`' own docstring
records what a per-point field costs (61 bytes/point, up to 2.3 MB of HTML for
one section on a 419-snapshot chip), and this page fans every chart out over
every qubit. The pins below hold that shape, and hold the click honest: a uid
is minted only where it actually opens.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "trends_provenance_selfcheck.cjs"

_WIRING = {"network": {"host": "3.3.3.3", "cluster_name": "C9"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}


def _state(f01=6.0e9, t1=2.0e-5):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "T1": t1}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _seed_run(root: Path, run_id: int, name="03_resonator_spectroscopy_single",
              date="2026-09-01", hhmmss="010000") -> Path:
    run = root / date / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00",
                     "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    return run


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live,
            "hm": app.config["history_manager"], "tmp": tmp_path}


def _snap(env, state, trigger="manual", **kw):
    _write_chip(env["live"], state)
    meta = env["hm"].check_and_snapshot(str(env["live"]), trigger, force=True, **kw)
    assert meta is not None
    return meta


def _charts(body: str) -> list[dict]:
    m = re.search(r'id="topo-trends-data">(.*?)</script>', body, re.S)
    return json.loads(m.group(1)) if m else []


def _snaps(body: str) -> dict:
    m = re.search(r'id="topo-trends-snaps">(.*?)</script>', body, re.S)
    return json.loads(m.group(1)) if m else {}


class TestTheMapShape:
    """One map per response, keyed by snapshot — never four fields per point."""

    def test_points_stay_two_tuples(self, env):
        _snap(env, _state(f01=6.0e9))
        _snap(env, _state(f01=6.1e9), trigger="experiment",
              experiment_name="06_ramsey", run_id=31)
        body = env["client"].get("/topology/trends?metrics=f_01").get_data(as_text=True)
        charts = _charts(body)
        assert charts and charts[0]["series"]
        for s in charts[0]["series"]:
            for p in s["points"]:
                assert len(p) == 2, \
                    "provenance rides the snapshot map, never the point"

    def test_the_map_is_bounded_by_both_shapes(self, env):
        """The shape argument, as review round 1 corrected it.

        Entries must scale with SNAPSHOTS rather than with drawn POINTS — but
        the map only beats the per-point shape once it is also narrowed to the
        ids the response can actually look up. Measured unfiltered on a real
        5-qubit chip: 228 entries / 27.6 KB against 8 charted ids, i.e. 78% of
        the fragment nothing could read, and MORE than the ~4.1 KB the
        per-point spelling would have cost there. So the pin is both bounds,
        not the one that happened to hold for the fixture.
        """
        for i in range(5):
            _snap(env, _state(f01=6.0e9 + i * 1e6, t1=2.0e-5 + i * 1e-7))
        body = env["client"].get(
            "/topology/trends?metrics=f_01,T1").get_data(as_text=True)
        snaps = _snaps(body)
        charts = _charts(body)
        charted = {p[0] for c in charts for s in c["series"] for p in s["points"]}
        drawn = sum(len(s["points"]) for c in charts for s in c["series"])
        assert drawn > len(charted), \
            "fixture must draw the same snapshot id on both metrics"
        assert len(snaps) <= len(charted), \
            "never more entries than distinct ids the page draws"
        assert len(snaps) <= len(env["hm"].list_snapshots(env["live"])), \
            "and never more than the chip has snapshots"

    def test_a_snapshot_the_page_never_draws_is_not_shipped(self, env):
        """The charts are CHANGE POINTS, so a chip holds far more snapshots
        than the ids on the page — exactly the gap that made the map most of
        the real fragment. A ts nothing can look up must not travel."""
        _snap(env, _state(f01=6.0e9, t1=2.0e-5))
        for i in range(4):                      # T1 moves, f_01 does not
            _snap(env, _state(f01=6.0e9, t1=2.0e-5 + (i + 1) * 1e-7))
        _snap(env, _state(f01=6.4e9, t1=2.5e-5))
        body = env["client"].get(
            "/topology/trends?metrics=f_01").get_data(as_text=True)
        charted = {p[0] for c in _charts(body) for s in c["series"]
                   for p in s["points"]}
        all_ts = {m.timestamp for m in env["hm"].list_snapshots(env["live"])}
        assert len(all_ts) > len(charted), "fixture must hold undrawn snapshots"
        assert set(_snaps(body)) <= charted, \
            "the map must not carry a snapshot nothing on the page can read"

    def test_a_typed_path_that_charts_nothing_ships_no_map(self, env):
        """The worst unfiltered case: zero points, whole vocabulary anyway."""
        for i in range(4):
            _snap(env, _state(f01=6.0e9 + i * 1e6))
        body = env["client"].get(
            "/topology/trends?metrics=&path=qubits.qA1.not_a_leaf"
        ).get_data(as_text=True)
        assert not _snaps(body), "no drawn point ⇒ no provenance to ship"

    def test_the_curated_tier_answers_for_a_snapshot_the_leaf_index_lacks(self, env):
        """The leaf change-point index is the source of truth (it is the only
        tier that records the run FOLDER), but the curated `param_history`
        table reaches snapshots it may not have ingested. That fallback is the
        ONLY thing this pin can see, so it is driven directly."""
        import sqlite3
        _snap(env, _state(f01=6.0e9))
        hm, live = env["hm"], env["live"]
        conn = sqlite3.connect(hm._index_path(Path(live)))
        try:
            conn.execute(
                "INSERT INTO param_history (timestamp, qubit, property, value,"
                " raw_pointer, trigger, run_id, experiment)"
                " VALUES (?,?,?,?,?,?,?,?)",
                ("20990101_000000", "qA1", "f_01", 6.5e9, None,
                 "experiment", 501, "06_ramsey"))
            conn.commit()
        finally:
            conn.close()
        rows = {r["ts"]: r for r in hm.snapshot_provenance(live)}
        assert "20990101_000000" in rows, \
            "a curated-only timestamp must not vanish from the vocabulary"
        got = rows["20990101_000000"]
        assert got["run_id"] == 501 and got["experiment"] == "06_ramsey"
        assert got["folder"] is None, \
            "the curated table stores no folder — say so, never invent one"

    def test_every_charted_snapshot_id_is_in_the_map(self, env):
        _snap(env, _state(f01=6.0e9))
        _snap(env, _state(f01=6.1e9), trigger="experiment",
              experiment_name="06_ramsey", run_id=31)
        body = env["client"].get("/topology/trends?metrics=f_01").get_data(as_text=True)
        snaps = _snaps(body)
        for c in _charts(body):
            for s in c["series"]:
                for p in s["points"]:
                    assert p[0] in snaps, f"{p[0]} has no provenance entry"


class TestARunSnapshot:
    def test_run_short_and_uid(self, env):
        """The customer's ask, whole: the run number, a short node name, and a
        uid that opens the dataset."""
        c, data_root = env["client"], env["tmp"] / "data"
        run = _seed_run(data_root, 31)
        c.post("/workspace/add", data={"folder": str(data_root)})
        _snap(env, _state(f01=6.0e9))
        meta = _snap(env, _state(f01=6.1e9), trigger="experiment",
                     experiment_name="03_resonator_spectroscopy_single",
                     run_id=31, experiment_folder_path=str(run))
        snaps = _snaps(c.get("/topology/trends?metrics=f_01").get_data(as_text=True))
        got = snaps[meta.timestamp]
        assert got["run"] == 31
        assert got["node"] == "03_resonator_spectroscopy_single"
        assert got["short"] == "Res spec", "story.short_node_name, not a second shortener"
        assert got["uid"] == f"{routes_mod._folder_key(data_root)}:31"
        assert got["why"] is None, "a run says which run, never a why-sentence"

    def test_the_uid_round_trips_through_the_dataset_resolver(self, env):
        """Clickable only when it actually opens: the uid the hover offers must
        survive _split_dataset_uid AND resolve to a live store."""
        c, data_root = env["client"], env["tmp"] / "data"
        run = _seed_run(data_root, 31)
        c.post("/workspace/add", data={"folder": str(data_root)})
        meta = _snap(env, _state(f01=6.1e9), trigger="experiment",
                     experiment_name="03_resonator_spectroscopy_single",
                     run_id=31, experiment_folder_path=str(run))
        uid = _snaps(c.get("/topology/trends?metrics=f_01")
                     .get_data(as_text=True))[meta.timestamp]["uid"]
        with env["app"].test_request_context():
            assert routes_mod._split_dataset_uid(uid) == (
                routes_mod._folder_key(data_root), 31)
            resolved = routes_mod._resolve_run(uid)
        assert resolved is not None, "the uid must open a real store"
        store, rid, _label = resolved
        assert rid == 31 and store.get_run(31) is not None

    def test_short_name_covers_the_families_the_archive_uses(self):
        from quam_state_manager.core.story import short_node_name
        assert short_node_name("03_resonator_spectroscopy_single") == "Res spec"
        assert short_node_name("37b_two_qubit_interleaved_cz_rb") == "2Q IRB"
        assert short_node_name(None) == "", "no experiment ⇒ no short name, never 'None'"


class TestASnapshotWithNoRun:
    def test_auto_says_modified_externally(self, env):
        """The customer's own words for the one that matters — an mtime change
        from outside SM."""
        meta = _snap(env, _state(f01=6.0e9), trigger="auto")
        got = _snaps(env["client"].get("/topology/trends?metrics=f_01")
                     .get_data(as_text=True))[meta.timestamp]
        assert got["why"] == "Modified externally"
        assert got["run"] is None and got["uid"] is None, \
            "no run ⇒ nothing to open, and no fabricated run number"

    def test_the_other_triggers_say_the_true_thing(self, env):
        """Flattening save/manual/restore into 'modified externally' would be a
        lie about three things SM genuinely knows."""
        want = {"save": "Saved in the app", "manual": "Manual snapshot",
                "restore": "Restored from history"}
        metas = {t: _snap(env, _state(f01=6.0e9 + i * 1e6), trigger=t)
                 for i, t in enumerate(want)}
        snaps = _snaps(env["client"].get("/topology/trends?metrics=f_01")
                       .get_data(as_text=True))
        for trig, sentence in want.items():
            assert snaps[metas[trig].timestamp]["why"] == sentence

    def test_a_chip_with_no_run_provenance_at_all_renders(self, env):
        """The real 3-snapshot chip: honest why-sentences, no click, no error."""
        for i in range(3):
            _snap(env, _state(f01=6.0e9 + i * 1e6), trigger="auto")
        r = env["client"].get("/topology/trends?metrics=f_01")
        assert r.status_code == 200
        snaps = _snaps(r.get_data(as_text=True))
        assert snaps and all(v["uid"] is None and v["run"] is None
                             for v in snaps.values())


class TestTheUidIsOnlyOfferedWhenItOpens:
    def test_a_run_folder_outside_every_dataset_root_gets_no_uid(self, env):
        """The run number is still true and still shown; only the click goes."""
        c = env["client"]
        elsewhere = env["tmp"] / "elsewhere" / "2026-09-01" / "#99_x_010000"
        elsewhere.mkdir(parents=True)
        meta = _snap(env, _state(f01=6.2e9), trigger="experiment",
                     experiment_name="06_ramsey", run_id=99,
                     experiment_folder_path=str(elsewhere))
        got = _snaps(c.get("/topology/trends?metrics=f_01")
                     .get_data(as_text=True))[meta.timestamp]
        assert got["run"] == 99 and got["short"] == "Ramsey"
        assert got["uid"] is None, "an unregistered folder would 404 on click"

    def test_a_malformed_folder_string_does_not_raise(self, env):
        """A folder recorded by an old snapshot can be junk, or name a drive
        that is gone. Computing a hover hint must never 500 the section."""
        meta = _snap(env, _state(f01=6.3e9), trigger="experiment",
                     experiment_name="06_ramsey", run_id=7,
                     experiment_folder_path="\x00://not/a/path\x00")
        r = env["client"].get("/topology/trends?metrics=f_01")
        assert r.status_code == 200
        got = _snaps(r.get_data(as_text=True))[meta.timestamp]
        assert got["run"] == 7 and got["uid"] is None

    def test_the_helper_itself_swallows_a_bad_path(self, env):
        with env["app"].test_request_context():
            roots = routes_mod._uid_roots()
            assert routes_mod._snapshot_run_uid("\x00bad\x00", 5, roots) is None
            assert routes_mod._snapshot_run_uid(None, 5, roots) is None
            assert routes_mod._snapshot_run_uid("/some/where/#5_x", None, roots) is None


class TestTheColumnControl:
    def test_the_badges_sit_beside_the_trends_title(self, env):
        """Customer: "put the column control right next to the 'Trends' title
        as badge buttons". It lives in the SECTION markup, not in the fetched
        fragment, which is re-swapped on every metric toggle."""
        body = env["client"].get("/topology?view=trends").get_data(as_text=True)
        head = re.search(r'<div class="topo-trends-head">.*?</div>', body, re.S)
        assert head, "the Trends heading carries the column control"
        for n in (1, 2, 3):
            assert f'data-trend-cols="{n}"' in head.group(0)
            assert f"ChipTrends.setCols({n})" in head.group(0)
        assert 'aria-pressed="true"' in head.group(0)

    def test_one_column_is_the_default(self, env):
        """A stylesheet default of 1, so a page with JS disabled still stacks
        straight down."""
        css = (Path(routes_mod.__file__).parent / "static" / "style.css").read_text(
            encoding="utf-8")
        assert "repeat(var(--trends-cols, 1), minmax(0, 1fr))" in css
        # ...and a narrow pane overrides the PROPERTY, which an inline custom
        # property value can never beat.
        assert re.search(r"@media \(max-width: 64rem\) \{\s*"
                         r"\.topo-trends-grid \{ grid-template-columns: 1fr; \}", css)


class TestTheParamHistoryDrawerUid:
    def test_the_drawer_ships_a_uid_not_a_bare_run_id(self, env):
        """The drawer's click has always built "/dataset/<run_id>", which
        _split_dataset_uid refuses — so it landed on the 404 panel every time.
        Same server-minted uid, one spelling of the click."""
        c, data_root = env["client"], env["tmp"] / "data"
        run = _seed_run(data_root, 31)
        c.post("/workspace/add", data={"folder": str(data_root)})
        _snap(env, _state(f01=6.0e9))
        _snap(env, _state(f01=6.1e9), trigger="experiment",
              experiment_name="03_resonator_spectroscopy_single",
              run_id=31, experiment_folder_path=str(run))
        html = c.get("/param-history/expand?qubit=qA1&prop=f_01").get_data(as_text=True)
        m = re.search(r'id="phd-data" type="application/json">(.*?)</script>', html, re.S)
        assert m
        row = json.loads(m.group(1).replace("\\u003c", "<").replace("\\u003e", ">")
                         .replace("\\u0026", "&"))
        by_run = {p.get("run_id"): p for p in row["values"]}
        assert by_run[31]["uid"] == f"{routes_mod._folder_key(data_root)}:31"
        assert all("uid" in p for p in row["values"]), \
            "every point declares its uid, even when that uid is null"
        assert by_run[None]["uid"] is None

    def test_the_run_travels_with_the_uid_across_the_tier_split(self, env):
        """Review round 1, seen in a real browser: "click → open dataset #null".

        The uid is minted from the LEAF change-point index (the only tier that
        records the run folder); the point's own ``run_id`` is the CURATED
        ``param_history`` column, and that column is legitimately NULL for a
        snapshot whose meta names a run — the docs/132 reverse-order case,
        annotated by ``_enrich_run_fields`` after the rows were written. On the
        real 5-qubit chip 26 of 230 points on q1/f_01 sat in exactly that
        state. Gating the hint on one tier and numbering it from the other is
        what printed the word "null", so the run must ride WITH the uid.
        """
        import sqlite3
        c, data_root = env["client"], env["tmp"] / "data"
        run = _seed_run(data_root, 31)
        c.post("/workspace/add", data={"folder": str(data_root)})
        _snap(env, _state(f01=6.0e9))
        meta = _snap(env, _state(f01=6.1e9), trigger="experiment",
                     experiment_name="03_resonator_spectroscopy_single",
                     run_id=31, experiment_folder_path=str(run))
        hm, live = env["hm"], env["live"]
        # Populate the leaf tier FIRST, then strip the curated columns — the
        # exact divergence the customer's index is in.
        assert hm.snapshot_provenance(live)
        conn = sqlite3.connect(hm._index_path(Path(live)))
        try:
            conn.execute("UPDATE param_history SET run_id = NULL,"
                         " experiment = NULL, trigger = 'save'"
                         " WHERE timestamp = ?", (meta.timestamp,))
            conn.commit()
        finally:
            conn.close()
        html = c.get("/param-history/expand?qubit=qA1&prop=f_01").get_data(as_text=True)
        m = re.search(r'id="phd-data" type="application/json">(.*?)</script>', html, re.S)
        assert m
        row = json.loads(m.group(1).replace("\\u003c", "<").replace("\\u003e", ">")
                         .replace("\\u0026", "&"))
        pt = {p["timestamp"]: p for p in row["values"]}[meta.timestamp]
        assert pt["run_id"] is None, "fixture must reproduce the tier split"
        assert pt["uid"] == f"{routes_mod._folder_key(data_root)}:31", \
            "the leaf tier still mints the uid"
        assert pt["run"] == 31, \
            "…and the number the hint prints must come from the same tier"
        assert pt["node"] == "03_resonator_spectroscopy_single"

    def test_enrichment_reaches_the_curated_index_rows(self, env):
        """Root cause of that split: ``_enrich_run_fields``' UPDATE named the
        SnapshotMeta FIELD (``experiment_name``) where the table has a column
        called ``experiment``, so every enrichment raised "no such column" into
        its own best-effort except and the rows it exists to fill stayed NULL.
        """
        import sqlite3
        from types import SimpleNamespace
        c, data_root = env["client"], env["tmp"] / "data"
        run = _seed_run(data_root, 77)
        c.post("/workspace/add", data={"folder": str(data_root)})
        meta = _snap(env, _state(f01=6.2e9), trigger="save")
        hm, live = env["hm"], env["live"]
        target = hm.resolve_chip_dir(live)[0]
        assert hm._enrich_run_fields(
            target, meta.state_hash,
            SimpleNamespace(run_id=77, experiment_name="06_ramsey",
                            folder_path=str(run))), "the meta must be annotated"
        conn = sqlite3.connect(hm._index_path(Path(live)))
        try:
            got = conn.execute(
                "SELECT DISTINCT run_id, experiment FROM param_history"
                " WHERE timestamp = ?", (meta.timestamp,)).fetchall()
        finally:
            conn.close()
        assert got and all(r == (77, "06_ramsey") for r in got), \
            f"the enrichment never reached the curated rows: {got}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_trends_provenance_selfcheck_passes():
    """The client half, driven against the REAL chip-status.js under jsdom."""
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
