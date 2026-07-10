"""P0 quick wins of the Compare-hub redesign (docs/49).

Covers the four independent fixes:

1. ``core/param_specs.py`` extraction (A8) — the 7-symbol re-import shim in
   ``web/routes.py`` must expose the *same objects* as the new module.
2. Honest labels in ``_load_compare_stores`` — ``chip_name_for``-based names,
   archive-run snapshot timestamps, shortest-distinguishing-suffix dedup
   (the old ``Path(p).parent.name`` rendered two different chips under
   ``quam_states/`` both as "quam_states").
3. ``_detect_workspace_chips`` — a chip with no live ``<chip>/quam_state``
   resolves to its NEWEST archived run (old code silently took the oldest),
   and the row carries the resolved snapshot time.
4. Tolerance on the /chip-compare Differences tab — ``Differ.multi_diff``
   grows a relative-tolerance mode (int-vs-float alone is not a difference).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.differ import Differ, _has_difference
from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# Minimal synth (same shape as test_chip_compare_routes.py).
# ---------------------------------------------------------------------------


def _make_state(f_01: float = 6.25e9, t1: float = 8834) -> dict:
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": f_01,
                "T1": t1,
                "T2ramsey": 1.5e-6,
                "anharmonicity": -220e6,
                "grid_location": "0,2",
                "xy": {
                    "RF_frequency": f_01,
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40},
                    },
                },
                "resonator": {
                    "f_01": 7.64e9,
                    "RF_frequency": 7.64e9,
                    "operations": {
                        "readout": {"amplitude": 0.042, "length": 1000, "threshold": -1e-4},
                    },
                },
                "z": {"joint_offset": 0.081},
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _make_wiring() -> dict:
    return {
        "wiring": {
            "qubits": {
                "qA1": {
                    "xy": {"opx_output": "MW-FEM/1/2"},
                    "z": {"opx_output": "LF-FEM/5/1"},
                },
            },
        },
        "network": {"host": "10.1.1.18"},
    }


def _write_quam(folder: Path, *, f_01: float = 6.25e9, t1: float = 8834) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps(_make_state(f_01, t1), indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(
        json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    return app.test_client()


# ===========================================================================
# 1. param_specs extraction shim (docs/49 A8)
# ===========================================================================


class TestParamSpecsShim:
    SYMBOLS = [
        "_QUBIT_PROPERTY_MAP",
        "_BULK_COLUMNS_SPEC",
        "_PAIR_PROPERTY_MAP",
        "_COMPARE_PROPS",
        "_TABLE_PROP_GROUPS",
        "_ALL_QUBIT_PROPS",
        "_ALL_TABLE_PROPS",
    ]

    def test_routes_reexports_are_the_same_objects(self):
        """Every consumer importing from routes must see param_specs' objects."""
        from quam_state_manager.core import param_specs
        from quam_state_manager.web import routes
        for name in self.SYMBOLS:
            assert getattr(routes, name) is getattr(param_specs, name), name

    def test_derived_symbols_consistent(self):
        """The derived views must stay in lock-step with _QUBIT_PROPERTY_MAP."""
        from quam_state_manager.core import param_specs as ps
        expected_all = [k for _, k, _ in ps._QUBIT_PROPERTY_MAP if k != "id"]
        assert ps._ALL_QUBIT_PROPS == expected_all
        assert ps._ALL_TABLE_PROPS == [
            p for g in ps._TABLE_PROP_GROUPS for p in g["props"]
        ]
        # Same members, grouped: no prop lost or invented by the grouping.
        assert sorted(ps._ALL_TABLE_PROPS) == sorted(expected_all)

    def test_param_specs_is_import_light(self):
        """Pure-data module: importing it must not drag in Flask/routes."""
        import importlib
        import sys
        saved = {
            k: sys.modules.pop(k) for k in list(sys.modules)
            if k.startswith("quam_state_manager") or k == "flask"
        }
        try:
            importlib.import_module("quam_state_manager.core.param_specs")
            assert "flask" not in sys.modules
            assert "quam_state_manager.web.routes" not in sys.modules
        finally:
            sys.modules.update(saved)

    def test_freq_twin_rules_stays_in_routes(self):
        """docs/49 A8: _FREQ_TWIN_RULES is NOT moved (divergence badge must
        reuse it from routes, not a duplicate)."""
        from quam_state_manager.core import param_specs
        from quam_state_manager.web import routes
        assert hasattr(routes, "_FREQ_TWIN_RULES")
        assert not hasattr(param_specs, "_FREQ_TWIN_RULES")


# ===========================================================================
# 2. Honest compare-source labels
# ===========================================================================


class TestCompareSourceLabels:
    def test_flat_chip_folder_uses_its_own_name(self, tmp_path):
        """state.json directly in <root>/<chip>/ → label = chip, not root."""
        from quam_state_manager.web.routes import _compare_source_label
        qs = _write_quam(tmp_path / "quam_states" / "LabA")
        assert _compare_source_label(str(qs)) == "LabA"

    def test_live_quam_state_folder_uses_chip_name(self, tmp_path):
        from quam_state_manager.web.routes import _compare_source_label
        qs = _write_quam(tmp_path / "LabA" / "quam_state")
        assert _compare_source_label(str(qs)) == "LabA"

    def test_archive_run_label_has_chip_run_and_timestamp(self, tmp_path):
        from quam_state_manager.web.routes import _compare_source_label
        qs = _write_quam(
            tmp_path / "LabA" / "2026-02-19" / "#12_08_ramsey_163045" / "quam_state")
        assert _compare_source_label(str(qs)) == "LabA #12 · 2026-02-19 16:30:45"

    def test_two_same_named_chips_get_distinguishing_suffix(self, tmp_path):
        """The headline P0 case: same chip name under two different parents."""
        from quam_state_manager.web.routes import _dedupe_compare_labels
        a = _write_quam(tmp_path / "labA" / "LabA")
        b = _write_quam(tmp_path / "labB" / "LabA")
        labels = _dedupe_compare_labels(["LabA", "LabA"], [str(a), str(b)])
        assert len(set(labels)) == 2
        assert labels[0] == "LabA (labA)"
        assert labels[1] == "LabA (labB)"

    def test_dedup_leaves_noncolliding_labels_alone(self, tmp_path):
        from quam_state_manager.web.routes import _dedupe_compare_labels
        a = _write_quam(tmp_path / "labA" / "LabA")
        b = _write_quam(tmp_path / "labB" / "deviceC")
        labels = _dedupe_compare_labels(["LabA", "deviceC"], [str(a), str(b)])
        assert labels == ["LabA", "deviceC"]

    def test_dedup_same_folder_added_twice_stays_identical(self, tmp_path):
        """Identical resolved paths are genuinely the same source — honest."""
        from quam_state_manager.web.routes import _dedupe_compare_labels
        a = _write_quam(tmp_path / "labA" / "LabA")
        labels = _dedupe_compare_labels(["LabA", "LabA"], [str(a), str(a)])
        assert labels == ["LabA", "LabA"]

    def test_dedup_three_way_collision(self, tmp_path):
        from quam_state_manager.web.routes import _dedupe_compare_labels
        paths = [str(_write_quam(tmp_path / d / "x" / "LabA"))
                 for d in ("a", "b")]
        paths.append(str(_write_quam(tmp_path / "a" / "y" / "LabA")))
        labels = _dedupe_compare_labels(["LabA"] * 3, paths)
        assert len(set(labels)) == 3

    def test_hub_basket_renders_distinct_labels(self, client, tmp_path):
        """End-to-end: two flat same-named chips render two distinct labels
        (P4: the surface is the hub basket; same dedup rule)."""
        a = _write_quam(tmp_path / "labA" / "LabA", f_01=6.25e9)
        b = _write_quam(tmp_path / "labB" / "LabA", f_01=6.30e9)
        resp = client.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=1")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "LabA (labA)" in html
        assert "LabA (labB)" in html

    def test_extract_run_id_prefers_hash_marker(self):
        """Chip names with digits (examplechip9q) must not shadow the run id."""
        from quam_state_manager.web.routes import _extract_run_id
        assert _extract_run_id("examplechip9q #10691 · 2026-02-19 16:30:45") == 10691
        assert _extract_run_id("#10691_08_ramsey_163045") == 10691  # legacy
        assert _extract_run_id("no digits here") is None


# ===========================================================================
# 3. _detect_workspace_chips — newest-run resolution
# ===========================================================================


def _write_run(root: Path, chip: str, date: str, run_dir: str,
               *, f_01: float = 6.25e9) -> Path:
    qs = _write_quam(root / chip / date / run_dir / "quam_state", f_01=f_01)
    return qs


class TestDetectWorkspaceChipsNewestRun:
    def _ws_for(self, root: Path):
        from quam_state_manager.core.scanner import Workspace
        ws = Workspace()
        ws.add_root(root)
        return ws

    def test_no_live_folder_resolves_to_newest_run(self, tmp_path):
        from quam_state_manager.web.routes import _detect_workspace_chips
        root = tmp_path / "data"
        old = _write_run(root, "chipX", "2026-01-05", "#1_ramsey_100000")
        new = _write_run(root, "chipX", "2026-03-10", "#7_rabi_153000")
        rows = _detect_workspace_chips(self._ws_for(root))
        row = next(r for r in rows if r["name"] == "chipX")
        assert Path(row["path"]) == new.resolve()
        assert Path(row["path"]) != old.resolve()

    def test_newest_run_label_includes_snapshot_time(self, tmp_path):
        from quam_state_manager.web.routes import _detect_workspace_chips
        root = tmp_path / "data"
        _write_run(root, "chipX", "2026-01-05", "#1_ramsey_100000")
        _write_run(root, "chipX", "2026-03-10", "#7_rabi_153000")
        rows = _detect_workspace_chips(self._ws_for(root))
        row = next(r for r in rows if r["name"] == "chipX")
        assert row["snapshot_ts"] == "2026-03-10 15:30:00"

    def test_same_date_orders_by_time_of_day(self, tmp_path):
        from quam_state_manager.web.routes import _detect_workspace_chips
        root = tmp_path / "data"
        _write_run(root, "chipX", "2026-03-10", "#3_ramsey_090000")
        late = _write_run(root, "chipX", "2026-03-10", "#4_rabi_213000")
        rows = _detect_workspace_chips(self._ws_for(root))
        row = next(r for r in rows if r["name"] == "chipX")
        assert Path(row["path"]) == late.resolve()

    def test_live_folder_still_preferred(self, tmp_path):
        from quam_state_manager.web.routes import _detect_workspace_chips
        root = tmp_path / "data"
        _write_run(root, "chipX", "2026-03-10", "#7_rabi_153000")
        live = _write_quam(root / "chipX" / "quam_state")
        rows = _detect_workspace_chips(self._ws_for(root))
        row = next(r for r in rows if r["name"] == "chipX")
        assert Path(row["path"]) == live.resolve()
        assert row["snapshot_ts"] == ""

    def test_picker_shows_snapshot_time(self, client, tmp_path, monkeypatch):
        """The hub's Workspace picker option names the resolved run (P4:
        /chip-compare redirects; the hub select carries the same row)."""
        from quam_state_manager.web import routes
        rows = [{"key": "chipX", "name": "chipX",
                 "path": str(tmp_path / "p"), "snapshot_ts": "2026-03-10 15:30:00"}]
        monkeypatch.setattr(routes, "_detect_workspace_chips", lambda ws: rows)
        html = client.get("/compare-hub").data.decode()
        assert "chipX — run 2026-03-10 15:30:00" in html


# ===========================================================================
# 4. Tolerance on the /chip-compare Differences tab
# ===========================================================================


class TestHasDifferenceTolerance:
    def _vals(self, *xs):
        return [{"label": f"s{i}", "value": x} for i, x in enumerate(xs)]

    def test_exact_mode_unchanged_int_vs_float_differs(self):
        # Historical (/compare) behavior: type mismatch alone is a diff.
        assert _has_difference(self._vals(40, 40.0)) is True

    def test_tolerant_int_vs_float_equal(self):
        assert _has_difference(self._vals(40, 40.0), tolerance=1e-9) is False

    def test_relative_boundary(self):
        # 2e-9 relative gap > 1e-9 tolerance → difference…
        assert _has_difference(self._vals(1.0, 1.0 + 2e-9), tolerance=1e-9) is True
        # …5e-10 relative gap < 1e-9 tolerance → equal.
        assert _has_difference(self._vals(1.0, 1.0 + 5e-10), tolerance=1e-9) is False

    def test_abs_floor_near_zero(self):
        # Relative test degenerates near zero — the abs floor absorbs it.
        assert _has_difference(self._vals(0.0, 5e-13), tolerance=1e-9) is False
        assert _has_difference(self._vals(0.0, 1e-6), tolerance=1e-9) is True

    def test_tolerance_zero_still_merges_int_float(self):
        # tolerance=0 = numerically exact, but 40 == 40.0 numerically.
        assert _has_difference(self._vals(40, 40.0), tolerance=0.0) is False
        assert _has_difference(self._vals(40, 41), tolerance=0.0) is True

    def test_non_numeric_values_stay_exact(self):
        assert _has_difference(self._vals("a", "b"), tolerance=1e-9) is True
        assert _has_difference(self._vals("a", "a"), tolerance=1e-9) is False
        # bool is not treated as a number.
        assert _has_difference(self._vals(True, 1), tolerance=1e-9) is True

    def test_none_gap_still_counts(self):
        assert _has_difference(self._vals(1.0, None), tolerance=1e-9) is True


class TestChipCompareDiffTolerance:
    def _two_chips(self, tmp_path, f_a, f_b):
        a = _write_quam(tmp_path / "chip_a" / "quam_state", f_01=f_a)
        b = _write_quam(tmp_path / "chip_b" / "quam_state", f_01=f_b)
        return [str(a), str(b)]

    def test_sub_tolerance_drift_hidden_by_default(self, client, tmp_path):
        # 1 Hz on 6.25 GHz = 1.6e-10 relative < default 1e-9.
        paths = self._two_chips(tmp_path, 6.25e9, 6.25e9 + 1)
        qs = "&".join(f"paths={p}" for p in paths)
        html = client.get(f"/chip-compare/diff?{qs}").data.decode()
        assert "f_01" not in html

    def test_tolerance_zero_shows_the_drift(self, client, tmp_path):
        paths = self._two_chips(tmp_path, 6.25e9, 6.25e9 + 1)
        qs = "&".join(f"paths={p}" for p in paths)
        html = client.get(f"/chip-compare/diff?{qs}&tolerance=0").data.decode()
        assert "f_01" in html

    def test_real_difference_survives_default_tolerance(self, client, tmp_path):
        paths = self._two_chips(tmp_path, 6.25e9, 6.30e9)
        qs = "&".join(f"paths={p}" for p in paths)
        html = client.get(f"/chip-compare/diff?{qs}").data.decode()
        assert "f_01" in html

    def test_bad_tolerance_falls_back_to_default(self, client, tmp_path):
        paths = self._two_chips(tmp_path, 6.25e9, 6.30e9)
        qs = "&".join(f"paths={p}" for p in paths)
        resp = client.get(f"/chip-compare/diff?{qs}&tolerance=abc")
        assert resp.status_code == 200
        assert "f_01" in resp.data.decode()

    def test_hub_exposes_strictness_presets(self, client, tmp_path):
        """P4: the tabs' free tolerance input is superseded by the hub's
        3-preset strictness control (the /chip-compare/diff FRAGMENT keeps
        honoring ?tolerance= — pinned above)."""
        paths = self._two_chips(tmp_path, 6.25e9, 6.30e9)
        resp = client.get(f"/compare-hub?src=ws:{paths[0]}&src=ws:{paths[1]}")
        html = resp.data.decode()
        assert "Lab default" in html
        assert "Exact" in html and "Wide" in html

    def test_compare_route_keeps_exact_semantics(self, tmp_path):
        """multi_diff without tolerance (as /compare calls it) is unchanged."""
        from quam_state_manager.core.scanner import Workspace
        paths = self._two_chips(tmp_path, 6.25e9, 6.25e9 + 1)
        ws = Workspace()
        stores = [ws.load_store(p) for p in paths]
        rows = Differ().multi_diff(stores, ["a", "b"], ["f_01"])
        assert any(r["property"] == "f_01" for r in rows)
