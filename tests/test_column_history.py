"""Column History + LiveEditUndo server surface (docs/20 v2).

The bulk-grid column header's 🕘 opens a comparison panel: rows = entities,
first column = a Param-History-style trend sparkline (snapshot tiers + runs,
change-point collapsed), then the current value and the last N matching
runs' values — each clickable to fill the grid cell, with a per-run
"Use all". The Review-tray sync contract: one /undo press removes exactly
one change_log GROUP from the tray; pre-apply snapshots power the explicit
"Revert last apply" affordance."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app
from tests.ledger_fixture import declare_root

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(f01_a=5.0e9, f01_b=6.0e9, off_a=0.08, off_b=0.11):
    return {
        "qubits": {
            "qA1": {"id": "qA1", "f_01": f01_a, "z": {"joint_offset": off_a}},
            "qA2": {"id": "qA2", "f_01": f01_b, "z": {"joint_offset": off_b}},
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1", "qA2"],
    }


def _write_chip(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


def _seed_run(root: Path, run_id: int, state: dict, *, name="08_spec",
              date="2026-12-30", hhmmss=None, wiring=None, patches=None) -> Path:
    """*patches*: the node's own record of what it wrote. A chip names (and
    opens) a run only when that run is shown to have WRITTEN the value
    (value_writer, 2026-09-29) -- the fixtures that assert a Data link give
    their run that proof."""
    hhmmss = hhmmss or f"{run_id % 24:02d}0000"
    run = root / date / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir(parents=True)
    # S10 C3: one fixed clock for every run -> the folder's own HHMMSS, so the
    # ledger orders runs by time (as real runs are), never by a tie-break
    t = f"{date}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": t, "run_end": t},
        "data": {"parameters": {"model": {"qubits": ["qA1", "qA2"]}},
                 "outcomes": {}},
        "id": run_id, "parents": [], "created_at": t,
        **({"patches": patches} if patches is not None else {}),
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state, wiring)
    return run


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _post_column(c, paths, label="Joint offset", unit="V", grid="qubit"):
    return c.post("/bulk/column-history", data={
        "grid": grid, "label": label, "unit": unit, "col_key": "z_joint",
        "paths": json.dumps(paths),
    })


_COL = {"qA1": "qubits.qA1.z.joint_offset",
        "qA2": "qubits.qA2.z.joint_offset"}


class TestColumnHistoryPanel:
    def test_runs_columns_values_uid_and_sparkline(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data"
        _seed_run(data_root, 31, _state(off_a=0.079, off_b=0.110))
        _seed_run(data_root, 32, _state(off_a=0.081, off_b=0.110))
        _seed_run(data_root, 33, _state(off_a=0.081, off_b=0.112))
        c.post("/workspace/add", data={"folder": str(data_root)})
        # S10 C3: runs scanned from a workspace root -> runs of a root linked
        # to the chip (the ledger reads only those); every By-run cell, its
        # change highlight and the current column are pinned exactly now
        declare_root(c, data_root)
        r = _post_column(c, _COL)
        assert r.status_code == 200
        html = r.data.decode()
        byrun = _byrun_part(html)
        heads, cells = _byrun(html)
        # run columns newest-first, each opening its own run's data
        key = routes_mod._folder_key(data_root)
        assert heads == [(33, f"{key}:33"), (32, f"{key}:32"), (31, f"{key}:31")]
        # each row's value at each run; highlighted where it differs from the
        # previous (older) run's value
        assert cells == {
            "qA1": [("0.081", False, False), ("0.081", True, False), ("0.079", False, False)],
            "qA2": [("0.112", True, False), ("0.11", False, False), ("0.11", False, False)],
        }
        # one Use all per run + per-value fill hooks
        assert byrun.count("ColumnHistory.useAll") == 3
        assert "ColumnHistory.useValue" in byrun
        # trend sparkline SVG rendered server-side on both rows (>=2 change
        # points each: 0.079 -> 0.081 and 0.11 -> 0.112)
        for row in ("qA1", "qA2"):
            piece = _rows(byrun)[row]
            assert "history-cell-spark" in piece and "hs-line" in piece, row
        # current column from the loaded store, not from any run
        assert '<td class="ch-current" title="0.08"><code>0.08</code></td>' in byrun

    def test_foreign_run_excluded(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data2"
        # S10 C3: a lone foreign-host run dropped by the scan -> an own run then
        # a foreign-host run in a linked root (a nameless chip's ledger takes
        # its identity from its first run); the foreign one is kept but
        # flagged, never a By-run column and never named as the writer
        _seed_run(data_root, 40, _state())
        _seed_run(data_root, 41, _state(off_a=0.5),
                  wiring={"network": {"host": "9.9.9.9", "cluster_name": "X"}})
        c.post("/workspace/add", data={"folder": str(data_root)})
        declare_root(c, data_root)
        html = _post_column(c, _COL).data.decode()
        key = routes_mod._folder_key(data_root)
        heads, cells = _byrun(html)
        byrun = _byrun_part(html)
        assert heads == [(40, f"{key}:40")], "the foreign run is not a By-run column"
        assert 'data-fill="0.5"' not in byrun
        assert "1 run of an uncertain chip identity is left out" in byrun
        newest = _chips(html)["qA1"][0]
        assert (newest["prov"], newest["fill"], newest["data"]) == ("run_uncertain_chip", "0.5", None)
        assert newest["by"] == "#41 (chip uncertain)" and "not named as writer" in newest["sub"]
        assert f"/dataset/{key}:41" not in _changes(html)

    def test_missing_leaf_renders_dash(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data3"
        state = _state()
        del state["qubits"]["qA2"]["z"]          # qA2 has no joint_offset here
        _seed_run(data_root, 51, state)
        c.post("/workspace/add", data={"folder": str(data_root)})
        # S10 C3: workspace-scanned run -> run of a linked root; the dash is
        # pinned on qA2's own cell (and not on qA1's) instead of anywhere
        declare_root(c, data_root)
        html = _post_column(c, _COL).data.decode()
        _heads, cells = _byrun(html)
        assert cells == {"qA1": [("0.08", False, False)],
                         "qA2": [(None, False, True)]}

    def test_tracked_column_uses_index_fastpath(self, env):
        """f_01 is a tracked prop: snapshots serve all rows from ONE SQL."""
        c = env["client"]
        hm = env["app"].config["history_manager"]
        _write_chip(env["live"], _state(f01_a=5.1e9))
        hm.check_and_snapshot(str(env["live"]), "manual", force=True)
        _write_chip(env["live"], _state(f01_a=5.2e9))
        hm.check_and_snapshot(str(env["live"]), "manual", force=True)
        out = hm.column_history(env["live"],
                                {"qA1": "qubits.qA1.f_01",
                                 "qA2": "qubits.qA2.f_01"})
        assert [v for _, v, *_ in out["qA1"]] == [5.1e9, 5.2e9]
        assert len(out["qA2"]) == 2

    def test_garbage_paths_are_safe(self, env):
        c = env["client"]
        r = _post_column(c, {"qA1": "no.such.path.at.all",
                             "x": "____", "qA2": "qubits.qA2.z.joint_offset"})
        assert r.status_code == 200        # read-only extraction, never a 500
        r2 = _post_column(c, {})
        assert r2.status_code == 400

    def test_header_buttons_and_sort_guards_present(self, env):
        c = env["client"]
        html = c.get("/bulk").data.decode()
        assert "bulk-col-hist" in html
        assert "ColumnHistory.open" in html
        js = Path("quam_state_manager/web/static/bulk-edit.js").read_text(
            encoding="utf-8")
        assert ".bulk-col-hist" in js, "sort guard for the clock"
        pjs = Path("quam_state_manager/web/static/pair-edit.js").read_text(
            encoding="utf-8")
        assert ".bulk-col-hist" in pjs


class TestBulkColMaxlen:
    def test_reserves_clock_room(self):
        """r11: natural column width = widest value + ~3ch reserve so the
        focused cell's 🕘 fits right after the text (cap 28 keeps the
        reserve on long values)."""
        cols = [{"label": "f 01", "section": "Qubit"}]
        grid = {"q1": [{"display": "5,100,000,000"}],
                "q2": [{"display": "6,000,000,000.25"}]}
        routes_mod._bulk_col_maxlen(cols, grid, ["q1", "q2"])
        assert cols[0]["maxlen"] == len("6,000,000,000.25") + 4
        long_grid = {"q1": [{"display": "x" * 60}]}
        cols2 = [{"label": "f 01", "section": "Qubit"}]
        routes_mod._bulk_col_maxlen(cols2, long_grid, ["q1"])
        assert cols2[0]["maxlen"] == 28


def _changes(html: str) -> str:
    """The Changes tab's slice of the panel (everything before the By-run
    wrapper) — lets asserts target one tab unambiguously."""
    return html.split("ch-view-byrun")[0]


def _byrun_part(html: str) -> str:
    """The By-run tab's slice of the panel (everything after its wrapper)."""
    head, sep, tail = html.partition("ch-view-byrun")
    assert sep, "the By-run tab is missing"
    return tail


def _rows(part: str) -> dict[str, str]:
    """{row id: that row's markup} of one tab slice."""
    return {piece.split('"', 1)[0]: piece
            for piece in part.split('<tr data-row="')[1:]}


def _chips(html: str) -> dict[str, list[dict]]:
    """S10 C3: the Changes tab per row, newest first -- each chip's ledger
    provenance, fill value, trigger dot, current badge, who-line and the
    dataset uid of its Data link (None when the chip names no writer)."""
    def one(rx, text):
        m = re.search(rx, text)
        return m.group(1) if m else None
    out = {}
    for row, piece in _rows(_changes(html)).items():
        out[row] = [{
            "prov": one(r'data-provenance="([^"]*)"', wrap),
            "fill": one(r'data-fill="([^"]*)"', wrap),
            "dot": one(r'ch-chip-dot ch-dot-(\w+)', wrap),
            "now": "ch-chip-now" in wrap,
            "by": one(r'<span class="vh-chip-by">([^<]*)</span>', wrap),
            "sub": one(r'<span class="vh-chip-sub">([^<]*)</span>', wrap) or "",
            "data": one(r'class="ch-chip-data" hx-get="/dataset/([^"]*)"', wrap),
        } for wrap in piece.split('<span class="ch-chipwrap">')[1:]]
    return out


def _byrun(html: str) -> tuple[list, dict[str, list]]:
    """S10 C3: the By-run tab as data -- the run columns newest first as
    ``(run id, dataset uid or None)``, and per row one
    ``(fill or None, highlighted as changed, rendered as missing)`` per run."""
    part = _byrun_part(html)
    heads = []
    for th in re.findall(r'<th class="ch-run">(.*?)</th>', part, re.S):
        uid = re.search(r'hx-get="/dataset/([^"]*)"', th)
        heads.append((int(re.search(r'>#(\d+)</(?:a|span)>', th).group(1)),
                      uid.group(1) if uid else None))
    cells = {}
    for row, piece in _rows(part).items():
        cells[row] = []
        for cls, _idx, body in re.findall(
                r'<td class="ch-val([^"]*)"\s+data-run-index="(\d+)"(.*?)</td>', piece, re.S):
            fill = re.search(r'data-fill="([^"]*)"', body)
            cells[row].append((fill.group(1) if fill else None,
                               "ch-changed" in cls, "ch-missing" in cls))
    return heads, cells


class TestColumnHistoryChanges:
    """r9 amendment: the default tab shows each row's OWN change points --
    manual applied edits included -- instead of a wall of per-run values
    that mostly never changed."""
    # S10 C3: the merged snapshot+runs series -> the chip's change ledger
    # (SM writes, states SM observed, runs of linked roots), the only source

    def test_manual_applied_edit_appears_as_save_chip(self, env):
        """THE report: a manual edit did not show in the column clock. An
        edit applied to live must surface as its own chip, said to be an SM
        write, beside the value it replaced."""
        c = env["client"]
        hm = env["app"].config["history_manager"]
        hm.check_and_snapshot(str(env["live"]), "manual", force=True)  # 0.08
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.z.joint_offset",
                         "value": "0.09"}],
            "expect_chip": "",
        })
        assert r.get_json()["ok"]
        assert c.post("/state/apply-to-live").status_code == 200
        html = _post_column(c, _COL).data.decode()
        ch = _changes(html)
        # S10 C3: a trigger-'save' snapshot chip -> the ledger's SM-write chip
        # (provenance sm, still the save dot) over the observed manual capture;
        # each chip's who-line and the current badge are pinned per chip
        assert [(p["prov"], p["fill"], p["dot"], p["now"]) for p in _chips(html)["qA1"]] == [
            ("sm", "0.09", "save", True),
            ("observed", "0.08", "auto", False),
        ], "both the old and the manually-applied value must be chips"
        newest, older = _chips(html)["qA1"]
        assert newest["by"] == "applied by a person" and newest["data"] is None
        assert "Written by SM (sm_apply" in ch
        assert older["by"] == "seen by SM (manual snapshot)" and "writer unknown" in older["sub"]

    def test_run_attributed_chip_carries_data_link(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data_chg"
        _seed_run(data_root, 31, _state(off_a=0.079, off_b=0.110),
                  patches=[{"op": "replace", "path": "/quam/qubits/qA1/z/joint_offset", "value": 0.079}])
        _seed_run(data_root, 32, _state(off_a=0.081, off_b=0.110))
        _seed_run(data_root, 33, _state(off_a=0.081, off_b=0.110))
        c.post("/workspace/add", data={"folder": str(data_root)})
        # S10 C3: workspace-scanned runs -> runs of a linked root; the Data
        # link is pinned to the proven writer's chip, and its absence to the
        # chip of the run whose saved state only carried the value
        declare_root(c, data_root)
        html = _post_column(c, _COL).data.decode()
        ch = _changes(html)
        key = routes_mod._folder_key(data_root)
        # 0.079 was WRITTEN by run 31 (its own patch) -- the chip links to
        # that run; 0.081 first appears in run 32 with no patch: not proven,
        # so no run is named and nothing is linked
        assert [(p["prov"], p["fill"], p["by"], p["data"]) for p in _chips(html)["qA1"]] == [
            ("run_saved", "0.081", "saved in #32 08_spec", None),
            ("run_proven", "0.079", "#31 08_spec", f"{key}:31"),
        ]
        assert ch.count("ch-chip-data") == 1
        assert f'hx-get="/dataset/{key}:31"' in ch

    def test_repeated_run_values_collapse_to_one_chip(self, env):
        """3 runs with the same value → ONE chip (the wall of identical
        values was the original complaint)."""
        c = env["client"]
        data_root = env["tmp"] / "data_same"
        for rid in (41, 42, 43):
            _seed_run(data_root, rid, _state(off_a=0.081, off_b=0.110))
        c.post("/workspace/add", data={"folder": str(data_root)})
        # S10 C3: workspace-scanned runs -> runs of a linked root; the three
        # runs are shown to be read (By run), so one chip is a collapse, not
        # two runs gone missing
        declare_root(c, data_root)
        html = _post_column(c, _COL).data.decode()
        ch = _changes(html)
        assert ch.count('data-fill="0.081"') == 1
        assert [p["fill"] for p in _chips(html)["qA1"]] == ["0.081"]
        assert [rid for rid, _uid in _byrun(html)[0]] == [43, 42, 41]

    def test_attribution_survives_beyond_byrun_window(self, env):
        """The Changes series reads MORE runs than the By-run tab shows: a
        value introduced by a run older than the 6 displayed columns keeps
        its run attribution (cell-popover consistency)."""
        c = env["client"]
        data_root = env["tmp"] / "data_wide"
        _seed_run(data_root, 51, _state(off_a=0.077, off_b=0.110),
                  patches=[{"op": "replace", "path": "/quam/qubits/qA1/z/joint_offset", "value": 0.077}])
        for rid in range(52, 59):                     # 52..58 keep the value
            _seed_run(data_root, rid, _state(off_a=0.081, off_b=0.110),
                      patches=([{"op": "replace", "path": "/quam/qubits/qA1/z/joint_offset", "value": 0.081}]
                               if rid == 52 else None))
        c.post("/workspace/add", data={"folder": str(data_root)})
        # S10 C3: workspace-scanned runs -> runs of a linked root; "newest 6
        # of 8 matching runs" -> the exact six By-run columns plus the
        # ledger's own count of all eight runs
        declare_root(c, data_root)
        html = _post_column(c, _COL).data.decode()
        ch, byrun = _changes(html), _byrun_part(html)
        key = routes_mod._folder_key(data_root)
        # introducers 51 + 52 are OUTSIDE the newest-6 (53..58) By-run window
        assert [(p["prov"], p["fill"], p["data"]) for p in _chips(html)["qA1"]] == [
            ("run_proven", "0.081", f"{key}:52"),
            ("run_proven", "0.077", f"{key}:51"),
        ]
        assert f'hx-get="/dataset/{key}:51"' in ch
        assert f'hx-get="/dataset/{key}:52"' in ch
        assert [rid for rid, _uid in _byrun(html)[0]] == [58, 57, 56, 55, 54, 53]
        assert f'/dataset/{key}:51"' not in byrun
        assert f'/dataset/{key}:52"' not in byrun
        assert "the newest 6 runs of this chip" in byrun
        assert "from the change ledger (8 events" in ch

    # S10 C3: deleted test_tracked_fastpath_chip_gets_uid_from_meta -> nothing; its whole subject was the old curated SQLite fast path and the run hint of an "experiment" snapshot's meta, and the ledger imports no experiment snapshot and has no tracked-column tier

    def test_tab_markup_and_js_pins(self, env):
        c = env["client"]
        html = _post_column(c, _COL).data.decode()
        assert 'data-view="changes"' in html and 'data-view="byrun"' in html
        assert "ColumnHistory.switchView" in html
        assert "ch-view ch-view-changes" in html
        assert 'ch-view ch-view-byrun" hidden' in html, \
            "By-run starts hidden; JS applies the remembered choice"
        js = Path("quam_state_manager/web/static/app.js").read_text(
            encoding="utf-8")
        assert "quam_colhist_view" in js and "switchView" in js

    def test_pair_grid_chips_safe(self, tmp_path):
        live = tmp_path / "chips" / "live"
        state = _state()
        state["qubit_pairs"] = {"qA1-qA2": {"gates": {"cz": {"amp": 0.25}}}}
        _write_chip(live, state)
        from quam_state_manager.web.app import create_app as _ca
        app = _ca(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        hm = app.config["history_manager"]
        hm.check_and_snapshot(str(live), "manual", force=True)      # 0.25
        state["qubit_pairs"]["qA1-qA2"]["gates"]["cz"]["amp"] = 0.30
        _write_chip(live, state)
        hm.check_and_snapshot(str(live), "manual", force=True)      # 0.30
        r = c.post("/bulk/column-history", data={
            "grid": "pair", "label": "cz amp", "unit": "", "col_key": "cz_amp",
            "paths": json.dumps({"qA1-qA2": "qubit_pairs.qA1-qA2.gates.cz.amp"}),
        })
        assert r.status_code == 200
        ch = _changes(r.data.decode())
        assert 'data-row="qA1-qA2"' in ch
        assert 'data-fill="0.25"' in ch and 'data-fill="0.3"' in ch


class TestReviewTraySync:
    """docs/20 v2 sync contract: one undo press = exactly one change_log
    GROUP disappearing from the Review tray."""

    def test_row_batch_gid_undone_in_one_press(self, env):
        c = env["client"]
        r = c.post("/field/edit-batch", json={
            "updates": [
                {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.09"},
                {"dot_path": "qubits.qA1.f_01", "value": "5.05e9"},
            ],
            "expect_chip": "",
        })
        assert r.status_code == 200 and r.get_json()["ok"]
        ctx = next(iter(env["app"].config["contexts"].values()))
        assert len(ctx["store"].change_log) == 2
        u = c.post("/undo")
        assert u.status_code == 200
        assert len(ctx["store"].change_log) == 0, \
            "one press reverts the whole row group"
        assert 'data-change-count="0"' in u.data.decode()

    def test_use_all_then_apply_all_is_one_group(self, env):
        """Use all fills N cells; the grid's Apply All posts one atomic batch
        PER ROW, joined into ONE gid (QA diagnostics-r2-15: the first row asks
        for a new group, the rest join it) — a single Ctrl+Z clears them all
        from Review. The real client is pinned in apply_all_group_selfcheck."""
        c = env["client"]
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.z.joint_offset", "value": "0.079"}],
            "expect_chip": "", "group": "new",
        })
        assert r.get_json()["ok"]
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA2.z.joint_offset", "value": "0.110"}],
            "expect_chip": "", "group": r.get_json()["group_id"],
        })
        assert r.get_json()["ok"]
        u = c.post("/undo")
        ctx = next(iter(env["app"].config["contexts"].values()))
        assert len(ctx["store"].change_log) == 0
        assert 'data-change-count="0"' in u.data.decode()

    def test_tray_carries_undo_button(self, env):
        html = env["client"].get("/bulk").data.decode()
        assert "tray-undo-btn" in html
        assert "LiveEditUndo.trigger" in html


class TestRevertLastApply:
    def test_pre_apply_snapshot_and_tray_affordance(self, env):
        c = env["client"]
        hm = env["app"].config["history_manager"]
        # stage an edit, then apply to live
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.z.joint_offset",
                         "value": "0.095"}],
            "expect_chip": "",
        })
        assert r.get_json()["ok"]
        pre_live = json.loads((env["live"] / "state.json").read_text())
        a = c.post("/state/apply-to-live")
        assert a.status_code == 200
        ctx = next(iter(env["app"].config["contexts"].values()))
        la = ctx.get("last_apply")
        assert la and la.get("pre_ts"), "last_apply memo with the pre-apply ts"
        # the pre-apply snapshot holds the live content BEFORE the apply
        hist_dir = hm._history_dir(Path(str(env["live"])))
        snap_state = json.loads(
            (hist_dir / la["pre_ts"] / "state.json").read_text(encoding="utf-8"))
        assert snap_state["qubits"]["qA1"]["z"]["joint_offset"] \
            == pre_live["qubits"]["qA1"]["z"]["joint_offset"]
        # live now has the applied value
        post_live = json.loads((env["live"] / "state.json").read_text())
        assert post_live["qubits"]["qA1"]["z"]["joint_offset"] == 0.095
        # the sync panel's History offers the explicit revert (clean state,
        # fresh memo) -- it moved there from the tray in ef07a90
        html = c.get("/state/review").data.decode()
        assert "tray-revert-apply" in html
        assert f"/state-history/{la['pre_ts']}/stage" in html

    def test_revert_stages_pre_apply_state(self, env):
        c = env["client"]
        c.post("/field/edit-batch", json={
            "updates": [{"dot_path": "qubits.qA1.z.joint_offset",
                         "value": "0.095"}],
            "expect_chip": "",
        })
        c.post("/state/apply-to-live")
        ctx = next(iter(env["app"].config["contexts"].values()))
        pre_ts = ctx["last_apply"]["pre_ts"]
        r = c.post(f"/state-history/{pre_ts}/stage")
        assert r.status_code == 200
        # staged back into the WORKING copy — live keeps the applied value
        # until the user applies the revert
        with ctx["store"]._lock:
            assert ctx["store"].state["qubits"]["qA1"]["z"]["joint_offset"] \
                == 0.08
        live_now = json.loads((env["live"] / "state.json").read_text())
        assert live_now["qubits"]["qA1"]["z"]["joint_offset"] == 0.095
