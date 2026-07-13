"""Regression tests for the pre-delivery audit fixes.

Covers the two backend-logic fixes shipped from the pre-delivery quality audit:

* Fix 2 — ``restore-live`` must ABORT (never overwrite the live chip) when the
  pre-restore backup silently fails. ``check_and_snapshot`` reports failure BOTH
  by raising AND by returning ``None`` (unreadable mtime / OSError writing the
  snapshot). With ``force=True`` the dedup no-op path is bypassed, so ``None``
  unambiguously means "no backup taken".
* Fix 3 — editing a leaf that HOLDS a QUAM pointer must write the resolved
  target as its real (numeric) type in value-mode, never stringify the pointer
  (which severs the shared-value link and breaks ``Quam.load``).
* Fix 5 — the render-time chip token is baked into the page so every edit
  surface can send it as ``expect_chip`` (server-side 409 gate is already tested
  in ``test_chip_apply_guard.py``).
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.core import history as H
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine
from quam_state_manager.web.app import create_app


def _iw(state, wiring):
    return QueryEngine(QuamStore.from_dicts(state, wiring)).get_instrument_wiring()


def _make_live(tmp_path, f01=6.0e9):
    live = tmp_path / "live" / "LabA"
    live.mkdir(parents=True)
    state = {"qubits": {"q1": {"id": "q1", "f_01": f01, "xy": {"operations": {}}}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    wiring = {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "MW/1/2"}}}},
              "network": {"host": "10.0.0.1"}}
    (live / "state.json").write_text(json.dumps(state, indent=4))
    (live / "wiring.json").write_text(json.dumps(wiring, indent=4))
    return live


def _make_live_with_pointer(tmp_path):
    """A live chip whose x90 amplitude is a pointer to x180's amplitude — the
    exact shape (thousands of instances) the pointer-stringify bug corrupts."""
    live = tmp_path / "live" / "LabP"
    live.mkdir(parents=True)
    ops = {
        "x180_DragCosine": {"amplitude": 0.163, "length": 40},
        "x90_DragCosine": {"amplitude":
                           "#/qubits/q1/xy/operations/x180_DragCosine/amplitude",
                           "length": 40},
    }
    state = {"qubits": {"q1": {"id": "q1", "f_01": 6.0e9, "xy": {"operations": ops}}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    wiring = {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "MW/1/2"}}}},
              "network": {"host": "10.0.0.1"}}
    (live / "state.json").write_text(json.dumps(state, indent=4))
    (live / "wiring.json").write_text(json.dumps(wiring, indent=4))
    return live


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst"))


def _snapshot_ts(client):
    client.post("/api/history/snapshot")
    import re
    html = client.get("/state-history").data.decode()
    return re.findall(r'data-ts="([^"]+)"', html)


class TestRestoreLiveNoBackupAborts:
    """Fix 2: a None-returning pre-restore snapshot must abort the restore."""

    def test_abort_and_live_unchanged(self, app, tmp_path, monkeypatch):
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        ts = _snapshot_ts(client)[0]

        # Diverge live so a restore would be a real overwrite.
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "7.0e9"})
        client.post("/save")
        client.post("/state/apply-to-live", data={"force": "1"})
        assert json.loads((live / "state.json").read_text())["qubits"]["q1"]["f_01"] == 7.0e9

        # Simulate the pre-restore backup silently failing (returns None).
        monkeypatch.setattr(H.HistoryManager, "check_and_snapshot",
                            lambda self, *a, **k: None)
        r = client.post(f"/state-history/{ts}/restore-live", data={"force": "1"})

        assert r.status_code == 500
        assert b"reversible" in r.data.lower()
        # Crucially: the live chip was NOT overwritten with the old snapshot.
        assert json.loads((live / "state.json").read_text())["qubits"]["q1"]["f_01"] == 7.0e9


class TestPointerLeafValueMode:
    """Fix 3: numeric edit of a pointer-valued leaf writes the resolved target."""

    def test_edit_writes_float_target_not_stringified_pointer(self, app, tmp_path):
        live = _make_live_with_pointer(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})

        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.xy.operations.x90_DragCosine.amplitude",
            "value": "0.09",
        })
        assert r.get_json()["ok"] is True

        name = list(app.config["contexts"].keys())[0]
        store = app.config["contexts"][name]["store"]
        ops = store.state["qubits"]["q1"]["xy"]["operations"]
        # target got the float (value-mode); pointer link preserved; NO stringify.
        assert ops["x180_DragCosine"]["amplitude"] == 0.09
        assert isinstance(ops["x180_DragCosine"]["amplitude"], float)
        assert ops["x90_DragCosine"]["amplitude"] == \
            "#/qubits/q1/xy/operations/x180_DragCosine/amplitude"


class TestLegacyEditRoutesHardened:
    """Security: /qubit/<n>/edit and /pair/<n>/edit now enforce the same
    server-side read-only policy + pointer resolution as /field/edit, so they
    aren't an open side door around the durable safety layer."""

    def test_membership_array_element_edit_rejected(self, app, tmp_path):
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        # active_qubit_names is a chip-membership array — editing an element must
        # be rejected by policy (previously silently corrupted the chip identity).
        r = client.post("/qubit/q1/edit",
                        data={"dot_path": "active_qubit_names.0", "value": "HACKED"})
        assert r.status_code == 400
        name = list(app.config["contexts"].keys())[0]
        store = app.config["contexts"][name]["store"]
        assert store.state["active_qubit_names"] == ["q1"]

    def test_non_finite_value_is_400_not_500(self, app, tmp_path):
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        r = client.post("/qubit/q1/edit",
                        data={"dot_path": "qubits.q1.f_01", "value": "inf"})
        assert r.status_code == 400  # parse now inside the try → clean 400, not a 500

    def test_normal_inspector_edit_still_works(self, app, tmp_path):
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        r = client.post("/qubit/q1/edit",
                        data={"dot_path": "qubits.q1.f_01", "value": "5.5e9"})
        assert r.status_code == 200
        name = list(app.config["contexts"].keys())[0]
        store = app.config["contexts"][name]["store"]
        assert store.state["qubits"]["q1"]["f_01"] == 5.5e9

    def test_inspector_edit_rejects_stale_chip_token(self, app, tmp_path):
        """A stale tab's inspector edit (its render-time chip token no longer
        matches the loaded chip) must be rejected with 409, not written onto the
        wrong chip — closing the opt-in gap the bulk/all-values grids already had."""
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        tok = client.get("/chip/active-token").get_json()["token"]
        assert tok, "loaded chip must expose a chip-identity token"
        name = list(app.config["contexts"].keys())[0]
        store = app.config["contexts"][name]["store"]

        # Wrong token → 409, NOT applied.
        r = client.post("/qubit/q1/edit",
                        data={"dot_path": "qubits.q1.f_01", "value": "9.9e9",
                              "expect_chip": tok + "-STALE"})
        assert r.status_code == 409
        assert store.state["qubits"]["q1"]["f_01"] != 9.9e9

        # Matching token → applied.
        r2 = client.post("/qubit/q1/edit",
                         data={"dot_path": "qubits.q1.f_01", "value": "5.5e9",
                               "expect_chip": tok})
        assert r2.status_code == 200
        assert store.state["qubits"]["q1"]["f_01"] == 5.5e9

        # force_chip bypasses the gate (explicit override).
        r3 = client.post("/qubit/q1/edit",
                         data={"dot_path": "qubits.q1.f_01", "value": "6.6e9",
                               "expect_chip": tok + "-STALE", "force_chip": "1"})
        assert r3.status_code == 200
        assert store.state["qubits"]["q1"]["f_01"] == 6.6e9


class TestParamHistoryDrawerEscapesScript:
    """Security: the Param History drawer embeds row JSON via script_json (not
    |safe), so a </script> in a third-party property name can't break out and be
    force-executed by the drawer's script re-runner."""

    def test_script_break_out_is_escaped(self, app):
        import json as _json

        from flask import render_template_string
        payload = _json.dumps({"prop": "#/x</script><script>alert(1)</script>"})
        with app.test_request_context():
            out = render_template_string(
                "{{ row_json | script_json }}", row_json=payload)
        assert "</script>" not in out
        assert "\\u003c/script" in out


class TestInstrumentWiringCluster:
    """Instrument Wiring diagram: crashes, omissions, and misleading empty racks
    on real hardware shapes (CR gates, null channels, OPX+/Octave, input LO)."""

    def test_null_channel_does_not_crash(self):
        # A JSON null channel (real KRISS_CR pairs carry "coupler": null) must not
        # crash get_instrument_wiring into a blank rack.
        r = _iw(
            {"qubits": {}, "qubit_pairs": {"p1": {"coupler": None}}},
            {"wiring": {"qubit_pairs":
                        {"p1": {"c": {"opx_output": "#/ports/analog_outputs/con1/7/1"}}}}},
        )
        assert "con1" in r["controllers"]

    def test_cross_resonance_line_is_collected(self):
        # CR drive lines (the Generate wizard produces them) must appear — else the
        # whole CR FEM is silently absent from the rack.
        r = _iw(
            {"qubits": {}, "qubit_pairs": {"q1-2": {"qubit_control": "q1", "qubit_target": "q2"}}},
            {"wiring": {"qubit_pairs":
                        {"q1-2": {"cr": {"opx_output": "#/ports/mw_outputs/con1/2/1"}}}}},
        )
        assert "2" in r["controllers"]["con1"]["fems"]
        cr = r["controllers"]["con1"]["fems"]["2"]["output_ports"]["1"][0]
        assert cr["role"] == "cr"
        assert cr["qubit_control"] == "q1" and cr["qubit_target"] == "q2"

    def test_input_lo_pointer_is_resolved(self):
        # The post-build fix-up rewrites an input port's downconverter_frequency as a
        # pointer to the paired output's upconverter_frequency — resolve it to the
        # number instead of showing the raw pointer string in the popup.
        r = _iw(
            {"qubits": {"q1": {"resonator": {"f_01": 7.4e9}}}, "qubit_pairs": {}},
            {"wiring": {"qubits": {"q1": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1"}}}},
             "ports": {
                 "mw_inputs": {"con1": {"1": {"1": {
                     "downconverter_frequency": "#/ports/mw_outputs/con1/1/1/upconverter_frequency"}}}},
                 "mw_outputs": {"con1": {"1": {"1": {"upconverter_frequency": 7.46e9}}}}}},
        )
        inp = r["controllers"]["con1"]["fems"]["1"]["input_ports"]["1"][0]
        assert inp["lo_frequency"] == 7.46e9

    def test_opxplus_5part_ref_is_flagged_not_silently_empty(self):
        # A 5-part OPX+ ref can't be placed; stats must signal "seen but not placed"
        # so the UI shows an honest message rather than "no wiring".
        r = _iw(
            {"qubits": {"q1": {}}, "qubit_pairs": {}},
            {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "#/ports/analog_outputs/con1/5"}}}}},
        )
        assert r["controllers"] == {}
        assert r["stats"]["refs_seen"] > 0 and r["stats"]["refs_placed"] == 0

    def test_octave_shape_detected(self):
        r = _iw(
            {"qubits": {"q1": {}}, "qubit_pairs": {}},
            {"wiring": {"qubits": {"q1": {"xy": {"opx_output_I": "x", "opx_output_Q": "y"}}}}},
        )
        assert r["stats"]["octave_detected"] is True

    def test_normal_chip_still_renders_and_resolves_output_lo(self):
        r = _iw(
            {"qubits": {"q1": {"xy": {"RF_frequency": 5e9}, "f_01": 5e9}}, "qubit_pairs": {}},
            {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/1"}}}},
             "ports": {"mw_outputs": {"con1": {"1": {"1": {
                 "band": 2, "upconverter_frequency": 5.1e9}}}}}},
        )
        xy = r["controllers"]["con1"]["fems"]["1"]["output_ports"]["1"][0]
        assert xy["lo_frequency"] == 5.1e9 and xy["band"] == 2
        assert r["stats"]["refs_placed"] == 1


class TestInstrumentBuildErrorIsVisible:
    """A get_instrument_wiring failure surfaces instrument_error to the template,
    not the empty-rack sentinel that reads as 'your chip has no wiring'."""

    def test_error_passed_to_template(self, app, tmp_path, monkeypatch):
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        from quam_state_manager.core.query import QueryEngine as _QE
        monkeypatch.setattr(_QE, "get_instrument_wiring",
                            lambda self: (_ for _ in ()).throw(RuntimeError("boom-xyz")))
        html = client.get("/instrument").data.decode()
        assert "boom-xyz" in html  # embedded for the visible red banner
        assert "instrumentError" in html


def _seed_run(root, rid, date, hh="010000", name="ramsey", status="successful", fit=None):
    from pathlib import Path as _P
    d = _P(root) / date
    d.mkdir(parents=True, exist_ok=True)
    run = d / f"#{rid}_{name}_{hh}"
    run.mkdir()
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": status, "run_start": f"{date}T01:00:00"},
        "data": {"parameters": {"model": {"qubits": [f"q{rid}"]}}, "outcomes": {}},
        "id": rid, "parents": [], "created_at": f"{date}T01:00:00"}))
    (run / "data.json").write_text(json.dumps({"fit_results": fit or {}}))
    return run


class TestDatasetsWrongData:
    """Datasets discovery: crashes/omissions that hide or stale real run data."""

    def test_corrupt_tags_key_does_not_hide_folder(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        root = tmp_path / "data"
        _seed_run(root, 1, "2026-07-01")
        (root / "quashboard_tags.json").write_text(
            json.dumps({"tags": {"abc": ["x"], "1": ["good"]}, "notes": {"zz": "n"}}))
        ds = DatasetStore(root)  # must NOT raise → folder stays visible
        assert 1 in ds.runs
        assert ds.runs[1].tags == ["good"]

    def test_force_rescan_picks_up_in_place_writeback(self, tmp_path):
        import os
        from quam_state_manager.core.dataset import DatasetStore
        root = tmp_path / "data"
        run = _seed_run(root, 7, "2026-07-05", status="running", fit={})
        ds = DatasetStore(root)
        assert ds.runs[7].status == "running"
        # Fit-result writeback rewrites node.json/data.json in place (no date-dir bump).
        nj = json.loads((run / "node.json").read_text())
        nj["metadata"]["status"] = "successful"
        (run / "node.json").write_text(json.dumps(nj))
        os.utime(run / "node.json", (9e9, 9e9))
        (run / "data.json").write_text(json.dumps({"fit_results": {"q7": {"T1": 8e-6}}}))
        os.utime(run / "data.json", (9e9, 9e9))
        assert ds.rescan_if_stale() is False       # mtime-gated: the no-op the button hit
        ds.force_rescan()                           # explicit button forces a real re-read
        assert ds.runs[7].status == "successful"
        assert ds.runs[7].fit_results

    def test_duplicate_run_id_survivor_not_falsely_vanished(self, tmp_path):
        import shutil
        from quam_state_manager.core.dataset import DatasetStore
        root = tmp_path / "data"
        _seed_run(root, 5, "2026-07-09")
        _seed_run(root, 5, "2026-07-10")
        ds = DatasetStore(root)
        assert 5 in ds.runs
        indexed = ds.runs[5].folder_path.parent.name
        other = "2026-07-09" if indexed == "2026-07-10" else "2026-07-10"
        shutil.rmtree(root / other)     # delete the SHADOWED copy
        ds.force_rescan()
        assert 5 in ds.runs             # survivor must not be evaporated


class TestUnsavedEditsCloseGuard:
    """Closing the window/tab with in-memory change_log edits used to lose them
    silently (they live nowhere on disk until Save). any_unsaved_changes powers
    the desktop close confirm; the tray's data-change-count powers beforeunload."""

    def test_any_unsaved_changes_tracks_change_log(self, app, tmp_path):
        from quam_state_manager.web.routes import any_unsaved_changes
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        assert any_unsaved_changes(app) is False           # clean
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "7e9"})
        assert any_unsaved_changes(app) is True            # in-memory edit at risk
        client.post("/save")
        assert any_unsaved_changes(app) is False           # written to the working copy

    def test_tray_exposes_change_count_for_beforeunload(self, app, tmp_path):
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        client.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "7e9"})
        html = client.get("/").data.decode()
        import re
        m = re.search(r'data-change-count="(\d+)"', html)
        assert m and int(m.group(1)) >= 1


class TestCliWritePathHardened:
    """The CLI writes LIVE files directly (no working copy). It must apply the SAME
    resolve-pointer + read-only guards as the web /field/edit path (core.edit_policy)
    — it used to stringify pointer leaves and overwrite identity keys straight to disk."""

    def _chip(self, tmp_path):
        live = tmp_path / "chip"
        live.mkdir()
        (live / "state.json").write_text(json.dumps({
            "qubits": {"q1": {"id": "q1", "xy": {"operations": {
                "x180": {"amplitude": 0.163},
                "x90": {"amplitude": "#/qubits/q1/xy/operations/x180/amplitude"}}}}},
            "qubit_pairs": {}, "active_qubit_names": ["q1"]}))
        (live / "wiring.json").write_text(json.dumps({"network": {}}))
        return live

    def test_cli_set_rejects_identity_key(self, tmp_path):
        from typer.testing import CliRunner
        from quam_state_manager.cli import app
        live = self._chip(tmp_path)
        res = CliRunner().invoke(app, ["set", "qubits.q1.id", "HACKED", "-f", str(live), "--save"])
        assert res.exit_code != 0
        assert json.loads((live / "state.json").read_text())["qubits"]["q1"]["id"] == "q1"

    def test_cli_set_pointer_leaf_writes_float_target(self, tmp_path):
        from typer.testing import CliRunner
        from quam_state_manager.cli import app
        live = self._chip(tmp_path)
        res = CliRunner().invoke(
            app, ["set", "qubits.q1.xy.operations.x90.amplitude", "0.09", "-f", str(live), "--save"])
        assert res.exit_code == 0
        ops = json.loads((live / "state.json").read_text())["qubits"]["q1"]["xy"]["operations"]
        assert ops["x180"]["amplitude"] == 0.09              # value-mode → target got the float
        assert ops["x90"]["amplitude"].startswith("#")       # pointer link preserved, not stringified

    def test_cli_set_nonfinite_is_clean_error(self, tmp_path):
        from typer.testing import CliRunner
        from quam_state_manager.cli import app
        live = self._chip(tmp_path)
        res = CliRunner().invoke(app, ["set", "qubits.q1.xy.operations.x180.amplitude", "inf", "-f", str(live)])
        assert res.exit_code == 1   # clean typer.Exit(1), not an uncaught traceback


class TestAGroupCorrectness:
    """A-group (accuracy / data-loss) fixes: crashes on real-shaped data + silent
    wrong-value writes."""

    def test_get_topology_survives_mixed_and_prefers_physical_fidelity(self):
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.query import QueryEngine
        state = {"qubits": {"q1": {}, "q2": {}}, "qubit_pairs": {"q1-2": {
            "qubit_control": "q1", "qubit_target": "q2", "macros": {
                "cz_good": {"fidelity": {"Bell_State": {"Fidelity": 0.96}}},
                "cz_broken": {"fidelity": {"Bell_State": {"Fidelity": 22.7}}},   # failed fit
                "cz_dangling": {"fidelity": {"Bell_State": {"Fidelity": "#/x"}}}}}}}  # dangling ptr
        r = QueryEngine(QuamStore.from_dicts(state, {"network": {}})).get_topology()
        # No 500 (mixed str+float), and the real 0.96 wins over the unphysical 22.7.
        assert r["edges"][0]["cz_fidelity"] == 0.96

    def test_search_prefix_symmetry_across_add_and_remove(self):
        from quam_state_manager.core.search_index import SearchIndex
        idx = SearchIndex.build({"qubits": {"qA1": {"xy": {"operations": {}}}}}, wiring_keys=set())
        idx.add_entry("qubits.qA1.zz_drive_amp", 0.5)
        assert any("zz_drive_amp" in r.dot_path for r in idx.search("zz"))   # findable by key
        idx.remove_entry("qubits.qA1.zz_drive_amp")
        assert not any(r.dot_path == "qubits.qA1.zz_drive_amp" for r in idx.search("zz"))  # no ghost

    def test_table_sort_tolerates_dangling_pointer_column(self, app, tmp_path):
        import json as _j
        live = tmp_path / "chip"
        live.mkdir()
        (live / "state.json").write_text(_j.dumps({"qubits": {
            "q1": {"id": "q1", "f_01": 6e9, "xy": {"operations": {}}},
            "q2": {"id": "q2", "f_01": "#/does/not/exist", "xy": {"operations": {}}}},
            "qubit_pairs": {}, "active_qubit_names": ["q1", "q2"]}))
        (live / "wiring.json").write_text(_j.dumps({"network": {}}))
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        r = client.get("/table?sort=f_01&dir=asc")   # dangling ptr in the sorted column
        assert r.status_code == 200                   # no 500 (str vs float TypeError)


class TestReapplyStashComposition:
    def test_delete_then_recreate_across_captures_becomes_replace(self):
        from quam_state_manager.web.routes import _merge_reapply
        # delete pulse (capture 1, stashed on Save) → recreate same op (capture 2):
        # must compose to 'replace', not a bare 'create' that KeyErrors on replay
        # (pulled live still has the original) and drops the user's recreated pulse.
        r = _merge_reapply({"q.op": ("delete", None)}, {"q.op": ("create", {"class": "Gaussian"})})
        assert r["q.op"][0] == "replace"
        assert _merge_reapply({"q.op": ("create", 1)}, {"q.op": ("delete", None)}) == {}  # net nothing
        assert _merge_reapply({"q.op.amp": ("set", 1)}, {"q.op": ("delete", None)}) == {"q.op": ("delete", None)}


class TestDiagnosticsCoverage:
    def test_dangling_ports_section_pointer_is_surfaced(self):
        from quam_state_manager.core.diagnostics import lint_state
        from quam_state_manager.core.loader import QuamStore
        # A downconverter pointer to a NON-EXISTENT output was hidden by the blanket
        # '#/ports/' skip (which only _port_findings-covered wiring leaves should hit).
        store = QuamStore.from_dicts(
            {"qubits": {}, "qubit_pairs": {}},
            {"wiring": {"qubits": {}}, "ports": {
                "mw_inputs": {"con1": {"1": {"1": {
                    "downconverter_frequency": "#/ports/mw_outputs/con1/1/9/upconverter_frequency"}}}},
                "mw_outputs": {"con1": {"1": {"1": {"upconverter_frequency": 7e9}}}}}})
        findings = lint_state(store)
        assert any(f.category == "dangling_pointer" and "downconverter" in f.jump_path
                   for f in findings)


class TestInteractiveContracts:
    """Click-contract recipes stage calibration values — a wrong recipe/target match
    on a standalone-named run stages the wrong value."""

    def test_fit_target_matches_standalone_and_graph_names(self):
        from quam_state_manager.core.fit_targets import _match
        assert _match("1Q_03_resonator_spectroscopy")          # graph-prefixed (was ok)
        assert _match("03_resonator_spectroscopy_single")      # standalone (was empty → no Apply)

    def test_gef_gate_recognizes_standalone_gef_names(self):
        from quam_state_manager.core.interactive_plots.registry import _normalize_node_name
        # the fixed _is_gef gates on the normalized 'gef_readout' marker, which is
        # what standalone "30(a)_gef_readout_*" runs normalize to (gef_ first).
        for nm in ("30a_gef_readout_power_optimization", "30_gef_readout_frequency_optimization"):
            assert _normalize_node_name(nm).startswith("gef_readout")


class TestGenerateBuildGuard:
    """Build must not silently overwrite an existing chip, and must detect stray
    JSON RECURSIVELY (QUAM's loader rglobs)."""

    def test_flags_existing_chip_and_recursive_stray(self, tmp_path):
        from quam_state_manager.web.routes import _build_output_guard
        assert _build_output_guard(str(tmp_path)) is None            # empty → build allowed
        (tmp_path / "state.json").write_text("{}")
        (tmp_path / "wiring.json").write_text("{}")
        g = _build_output_guard(str(tmp_path))
        assert g and g["needs_confirm"] and g["existing_chip"]       # existing chip → confirm
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "node.json").write_text("{}")
        (tmp_path / ".regen").mkdir()
        (tmp_path / ".regen" / "spec.json").write_text("{}")
        g2 = _build_output_guard(str(tmp_path))
        assert "sub/node.json" in g2["conflict_files"]               # recursive stray caught
        assert not any(".regen" in c for c in g2["conflict_files"])  # dot-dir exempt


class TestP4CoverageFixes:
    """P4 coverage-gap spike fixes."""

    def test_csv_export_neutralizes_formula_injection(self):
        from quam_state_manager.core.report_card import csv_safe_cell
        assert csv_safe_cell("=HYPERLINK(1)").startswith("'")   # formula → quoted
        assert csv_safe_cell("@cmd").startswith("'")
        assert csv_safe_cell("-HYPERLINK(1)").startswith("'")   # + / - non-number → quoted
        assert csv_safe_cell("-5.0") == "-5.0"                  # legit negative → numeric
        assert csv_safe_cell("5e9") == "5e9"
        assert csv_safe_cell("normal") == "normal"

    def test_scheduler_lock_covers_regenerate_and_fit_audit(self):
        from quam_state_manager.web.routes import _SCHEDULER_MUTATOR_ENDPOINTS
        for ep in ("main.regenerate_build", "main.regenerate_reconstruct", "main.fit_audit_run"):
            assert ep in _SCHEDULER_MUTATOR_ENDPOINTS

    def test_frozen_instance_path_is_dev_none(self):
        # source runs keep the repo instance/ (None); frozen would relocate.
        from quam_state_manager.main import _user_instance_path
        assert _user_instance_path() is None


class TestPerf:
    """P2 performance batch (behavioural regressions, not timing assertions)."""

    def test_workbench_match_caches_verdict_on_unchanged_mtimes(self, app, tmp_path, monkeypatch):
        from quam_state_manager.core import path_match
        from quam_state_manager.web import routes
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        calls = {"n": 0}
        real = path_match.verdict

        def counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(path_match, "verdict", counting)
        routes._workbench_match_cache.clear()
        client.get("/workbench/match")
        client.get("/workbench/match")   # unchanged folders/mtimes → cache hit
        assert calls["n"] == 1

    def test_fast_candidates_rebuild_on_workspace_version_bump(self, app, monkeypatch):
        # fast=True polls must still surface a new candidate dir once the scanner
        # discovers it (bumps ws.version) — otherwise a new chip dir under an
        # existing root is invisible to polls until a full /datasets render.
        from quam_state_manager.core.history import HistoryManager
        from quam_state_manager.core.scanner import Workspace
        from quam_state_manager.web import routes
        ws = Workspace()
        app.config["workspace"] = ws
        calls = {"n": 0}

        def counting(_workspace):
            calls["n"] += 1
            return "STABLE_TOKEN"

        monkeypatch.setattr(HistoryManager, "_workspace_token", staticmethod(counting))
        with app.test_request_context():
            routes._dataset_candidates_cache.clear()
            routes._dataset_candidate_folders(fast=True)   # builds → 1 token compute
            n = calls["n"]
            routes._dataset_candidate_folders(fast=True)   # same version → cache hit
            assert calls["n"] == n                          # no recompute
            ws._version += 1                                # scanner found a new dir
            routes._dataset_candidate_folders(fast=True)   # version moved → rebuild
            assert calls["n"] > n


class TestConcurrencyAndSafeIO:
    """P1 race / safe-io batch."""

    def test_apply_rolls_state_back_when_wiring_replace_fails(self, tmp_path, monkeypatch):
        # write_state_wiring must not leave a torn pair (NEW state + OLD wiring) if
        # the wiring replace fails after the state replace landed — roll state back.
        from quam_state_manager.core import safe_io
        safe_io.write_state_wiring(tmp_path, {"v": "OLD"}, {"w": "OLD"})
        real_replace = safe_io._replace_into_place

        def failing_replace(tmp, dst):
            if dst.name == "wiring.json":
                raise OSError("simulated wiring lock")
            return real_replace(tmp, dst)

        monkeypatch.setattr(safe_io, "_replace_into_place", failing_replace)
        with pytest.raises(OSError):
            safe_io.write_state_wiring(tmp_path, {"v": "NEW"}, {"w": "NEW"})
        # Consistent OLD+OLD pair, NOT torn NEW+OLD.
        assert json.loads((tmp_path / "state.json").read_text())["v"] == "OLD"
        assert json.loads((tmp_path / "wiring.json").read_text())["w"] == "OLD"

    def test_workspace_tree_rebinds_atomically(self, tmp_path):
        # add_root/remove_root must rebind self.tree (new dict), never mutate the old
        # one in place — a reader iterating the old reference then can't hit
        # 'dict changed size during iteration'.
        from quam_state_manager.core.scanner import Workspace
        ws = Workspace()
        (tmp_path / "r1").mkdir()
        (tmp_path / "r2").mkdir()
        ws.add_root(tmp_path / "r1")
        snap = ws.tree
        ws.add_root(tmp_path / "r2")
        assert ws.tree is not snap                                  # rebind, not in-place
        k1 = str((tmp_path / "r1").resolve())
        assert k1 in snap and str((tmp_path / "r2").resolve()) not in snap
        snap2 = ws.tree
        ws.remove_root(tmp_path / "r1")
        assert ws.tree is not snap2 and k1 in snap2 and k1 not in ws.tree


class TestSchedulerHardwareSafety:
    """Scheduler guards that keep an experiment from running on the OPX when the
    user didn't ask for it (or keep editing locked while one still is)."""

    def test_remove_item_refuses_running(self, tmp_path):
        from quam_state_manager.core import scheduler
        it = scheduler.add_item(tmp_path, {"file": "f.py", "name": "a", "kind": "node",
                                           "has_hook": True, "targets_name": "qubits"})
        st = scheduler.load_queue(tmp_path)
        st["queue"][0]["status"] = "running"
        scheduler.save_queue(tmp_path, st)
        scheduler.remove_item(tmp_path, it["id"])
        assert any(x["id"] == it["id"] for x in scheduler.load_queue(tmp_path)["queue"])

    def test_expand_refuses_running(self, tmp_path):
        from quam_state_manager.core import scheduler
        it = scheduler.add_item(tmp_path, {"file": "f.py", "name": "a", "kind": "node",
                                           "has_hook": True, "targets_name": "qubits"})
        st = scheduler.load_queue(tmp_path)
        st["queue"][0]["status"] = "running"
        scheduler.save_queue(tmp_path, st)
        made = scheduler.expand_per_qubit(tmp_path, it["id"], ["q1", "q2"])
        assert made == 0
        q = scheduler.load_queue(tmp_path)["queue"]
        assert len(q) == 1 and q[0]["id"] == it["id"]   # original intact, no dupes

    def test_settings_locked_for_critical_keys_while_active(self, app, monkeypatch):
        from quam_state_manager.core import scheduler
        inst = app.instance_path
        scheduler.save_settings(inst, {"global_simulate": True,
                                       "quam_state_path": "/chip/A"})
        client = app.test_client()
        monkeypatch.setattr(scheduler, "is_active", lambda _p: True)
        # Un-ticking Dry run mid-run → 409 (would flip the queue to LIVE hardware).
        r = client.post("/scheduler/settings", json={"global_simulate": False,
                                                     "quam_state_path": "/chip/A"})
        assert r.status_code == 409
        assert scheduler.load_settings(inst)["global_simulate"] is True  # unchanged
        # A non-critical change (timeout) is still allowed while running.
        r2 = client.post("/scheduler/settings", json={"global_simulate": True,
                                                      "quam_state_path": "/chip/A",
                                                      "default_timeout_s": 42})
        assert r2.status_code == 200

    def test_start_persists_posted_settings_before_run(self, app, monkeypatch):
        from quam_state_manager.core import scheduler
        inst = app.instance_path
        scheduler.save_settings(inst, {"quam_state_path": "/chip/OLD"})
        started = {}
        monkeypatch.setattr(scheduler, "start", lambda p: started.setdefault("p", p) or {"status": "running"})
        monkeypatch.setattr(scheduler, "set_refresh_hook", lambda *a, **k: None)
        client = app.test_client()
        # force=1 skips preflight; the POSTed path must be persisted before start so
        # the validated values equal the executed ones.
        client.post("/scheduler/start", json={"force": True, "quam_state_path": "/chip/NEW"})
        assert scheduler.load_settings(inst)["quam_state_path"] == "/chip/NEW"
        assert started.get("p")  # start was actually invoked

    def test_start_does_not_persist_settings_while_active(self, app, monkeypatch):
        # A racing/double-submit Start DURING a live run must NOT write new critical
        # settings to disk — the worker re-reads per item, so that would flip the
        # rest of the queue to a new chip / LIVE mode, bypassing the mid-run lock.
        from quam_state_manager.core import scheduler
        inst = app.instance_path
        scheduler.save_settings(inst, {"quam_state_path": "/chip/OLD",
                                       "global_simulate": True})
        monkeypatch.setattr(scheduler, "is_active", lambda _p: True)
        monkeypatch.setattr(scheduler, "start", lambda p: {"status": "running"})
        monkeypatch.setattr(scheduler, "set_refresh_hook", lambda *a, **k: None)
        client = app.test_client()
        client.post("/scheduler/start", json={"force": True,
                                              "quam_state_path": "/chip/NEW",
                                              "global_simulate": False})
        s = scheduler.load_settings(inst)
        assert s["quam_state_path"] == "/chip/OLD"      # unchanged mid-run
        assert s["global_simulate"] is True


class TestChipTokenBakedIntoPage:
    """Fix 5: the render-time chip token is exposed for edit-surface stamping."""

    def test_ctx_and_page_carry_token(self, app, tmp_path):
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})

        # The /chip/active-token endpoint and the baked window.__chipToken agree.
        tok = client.get("/chip/active-token").get_json()["token"]
        assert tok
        page = client.get("/").data.decode()
        assert "window.__chipToken" in page
        assert tok in page


class TestPostMutationCtxRace:
    """Group-B race: post-mutation cache invalidation / dirty-flagging must bind
    to the request-CAPTURED context, not re-resolve the live-active one — a
    concurrent /load flip would otherwise invalidate the wrong chip's caches and
    mark the wrong chip dirty while the mutated chip's state stays stale."""

    class _FakeEngine:
        def __init__(self):
            self.invalidated = False

        def invalidate_cache(self):
            self.invalidated = True

    class _FakeIndex:
        def __init__(self):
            self.invalidated = False

        def invalidate(self):
            self.invalidated = True

    class _FakeStore:
        wiring = {"network": {"host": "h"}}

    def _ctx(self):
        return {"engine": self._FakeEngine(), "pulse_index": self._FakeIndex(),
                "store": self._FakeStore()}

    def test_invalidate_uses_passed_ctx_not_active(self):
        from quam_state_manager.web import routes as R
        captured = self._ctx()
        other = self._ctx()
        # No app context is touched when a ctx is passed explicitly.
        R._invalidate_engine_cache(captured)
        assert captured["engine"].invalidated is True
        assert captured["pulse_index"].invalidated is True
        assert "wiring_json" in captured
        # The other (e.g. a chip made active by a concurrent /load) is untouched.
        assert other["engine"].invalidated is False
        assert other["pulse_index"].invalidated is False

    def test_set_working_dirty_uses_passed_ctx_not_active(self):
        from quam_state_manager.web import routes as R
        captured = {"working_dirty": False}
        other = {"working_dirty": False}
        R._set_working_dirty(True, captured)
        assert captured["working_dirty"] is True
        assert other["working_dirty"] is False


class TestActiveTokenLoadedContract:
    """The plot-apply popup and Explorer navigation now treat the ACTIVE context
    as authoritative and only fall back to the load-path box when nothing is
    loaded — they key on /chip/active-token's `loaded` + `path` fields, so pin
    that contract."""

    def test_no_chip_loaded(self, app):
        client = app.test_client()
        j = client.get("/chip/active-token").get_json()
        assert j["loaded"] is False
        assert j["path"] == ""

    def test_loaded_chip_reports_path(self, app, tmp_path):
        live = _make_live(tmp_path)
        client = app.test_client()
        client.post("/load", data={"folder": str(live)})
        j = client.get("/chip/active-token").get_json()
        assert j["loaded"] is True
        assert j["path"] == str(live)
        assert j["token"]     # fingerprint still exposed for the 409 pre-check
