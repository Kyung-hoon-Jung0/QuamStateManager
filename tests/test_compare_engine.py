"""P1a — core/compare.py: snapshot normalisation, the closed row-class enum,
the tolerance table, A2 auto-map branches, A3 flip policy, A5 coalescing,
summary extraction, M5 caching contract, A1 mapping persistence.

Synthetic tmp_path fixtures for every branch; real-fleet goldens live in
tests/test_compare_real_fleet.py (path-gated)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from quam_state_manager.core import compare as C
from quam_state_manager.core import compare_sources as cs
from quam_state_manager.core.loader import QuamStore

# ---------------------------------------------------------------------------
# synthetic chip builders
# ---------------------------------------------------------------------------

_CZ_CLASS = ("quam_builder.architecture.superconducting.custom_gates."
             "flux_tunable_transmon_pair.two_qubit_gates.CZGate")
_CR_CLASS = ("quam_builder.architecture.superconducting.custom_gates."
             "fixed_transmon_pair.two_qubit_gates.CRGate")


def make_qubit(f01: float = 6.25e9, grid: str | None = "0,0",
               *, rf: float | None = None, joint_offset=0.08,
               pulse_cls: str = "x180_DragCosine") -> dict:
    q = {
        "id": "q",
        "f_01": f01,
        "T1": 25e-6,
        "T2ramsey": 8e-6,
        "anharmonicity": -220e6,
        "xy": {
            "RF_frequency": rf if rf is not None else f01,
            "operations": {
                pulse_cls: {"amplitude": 0.115, "length": 40},
                "x180": f"#./{pulse_cls}",
            },
            "opx_output": "#/wiring/qubits/{name}/xy/opx_output",
        },
        "resonator": {
            "f_01": 7.64e9,
            "RF_frequency": 7.64e9,
            "operations": {"readout": {"amplitude": 0.042, "length": 1000}},
            "confusion_matrix": [[0.97, 0.03], [0.05, 0.95]],
        },
        "z": {"joint_offset": joint_offset},
    }
    if grid is not None:
        q["grid_location"] = grid
    return q


def make_cz_pair(control: str, target: str, *, moving="control",
                 psc=0.11, pst=0.22, confusion=None,
                 mutual=(0.001, 0.002), detuning=0.03) -> dict:
    pid = f"{control}-{target}"
    return {
        "id": pid,
        "qubit_control": f"#/qubits/{control}",
        "qubit_target": f"#/qubits/{target}",
        "moving_qubit": moving,
        "detuning": detuning,
        "confusion": confusion if confusion is not None else [
            [0.97, 0.01, 0.01, 0.01],
            [0.02, 0.94, 0.02, 0.02],
            [0.03, 0.03, 0.91, 0.03],
            [0.04, 0.04, 0.04, 0.88],
        ],
        "mutual_flux_bias": list(mutual),
        "extras": {"note": f"tuned for {pid}"},
        "macros": {
            "cz": "#./cz_unipolar",
            "cz_unipolar": {
                "id": "#./inferred_id",
                "__class__": _CZ_CLASS,
                "fidelity": {"StandardRB": {"average_gate_fidelity": 0.985},
                             "Bell_State": {"Fidelity": 0.96}},
                "phase_shift_control": psc,
                "phase_shift_target": pst,
                "moving_qubit": moving,
                "flux_pulse_qubit": {"amplitude": 0.2, "length": 48},
            },
        },
        "__class__": "quam_builder...FluxTunableTransmonPair",
    }


def make_cr_pair(control: str, target: str) -> dict:
    return {
        "id": f"{control}-{target}",
        "qubit_control": f"#/qubits/{control}",
        "qubit_target": f"#/qubits/{target}",
        "cross_resonance": {"intermediate_frequency": 150e6, "upconverter": 1},
        "macros": {"cr": {"__class__": _CR_CLASS,
                          "fidelity": {"StandardRB": 0.91}}},
        "__class__": "quam_builder...FixedFrequencyTransmonPair",
    }


def make_chip(qubits: dict[str, dict], pairs: dict[str, dict] | None = None,
              extra_state: dict | None = None) -> dict:
    for name, q in qubits.items():
        q["id"] = name
        xy = q.get("xy", {})
        if isinstance(xy.get("opx_output"), str):
            xy["opx_output"] = xy["opx_output"].format(name=name)
    state = {"qubits": qubits, "qubit_pairs": pairs or {},
             "active_qubit_names": sorted(qubits)}
    if extra_state:
        state.update(extra_state)
    return state


def make_wiring(qubits: list[str], host: str = "10.1.1.6") -> dict:
    return {
        "wiring": {"qubits": {q: {"xy": {"opx_output": {
            "delay": 0, "band": 1, "full_scale_power_dbm": -11}}}
            for q in qubits}},
        "network": {"host": host, "cluster_name": "c1"},
    }


def write_chip(folder: Path, state: dict, wiring: dict | None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    if wiring is not None:
        (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


@pytest.fixture()
def env():
    """Fresh pool + snapshot cache per test (never the process defaults)."""
    return cs.SourcePool(), C.SnapshotCache()


def resolve_pair(tmp_path, env, state_a, wiring_a, state_b, wiring_b,
                 names=("A", "B")):
    pool, cache = env
    fa = write_chip(tmp_path / names[0], state_a, wiring_a)
    fb = write_chip(tmp_path / names[1], state_b, wiring_b)
    a = cs.resolve_source(f"ws:{fa}", pool)
    b = cs.resolve_source(f"ws:{fb}", pool)
    return a, b


def rows_by_key(result: dict) -> dict[str, dict]:
    out = {}
    for g in result["groups"]:
        for r in g["rows"]:
            out[r["key"]] = r
    return out


def one_chip(f01=6.25e9, **kw):
    qs = {"q1": make_qubit(f01, "0,0", **kw), "q2": make_qubit(6.30e9, "1,0")}
    return make_chip(qs), make_wiring(list(qs))


# ===========================================================================
# param_specs lock-step (A8 refinement: core mirror of _FREQ_TWIN_RULES)
# ===========================================================================


def test_freq_twin_rules_mirror_locked_to_routes():
    from quam_state_manager.core import param_specs
    from quam_state_manager.web import routes
    assert param_specs.FREQ_TWIN_RULES == routes._FREQ_TWIN_RULES


# ===========================================================================
# tolerance engine
# ===========================================================================


class TestDimensionOf:
    @pytest.mark.parametrize("path,dim", [
        ("qubits.q1.f_01", "freq"),
        ("qubits.q1.anharmonicity", "freq"),
        ("qubits.q1.T1", "time"),
        ("qubits.q1.xy.operations.x180_DragCosine.length", "duration_ns"),
        ("qubits.q1.resonator.time_of_flight", "duration_ns"),
        ("qubits.q1.z.joint_offset", "volt"),
        ("qubit_pairs.p.mutual_flux_bias.0", "volt"),
        ("qubits.q1.gate_fidelity.averaged", "fidelity"),
        ("qubit_pairs.p.macros.cz.fidelity.StandardRB", "fidelity"),
        ("qubit_pairs.p.macros.cz.phase_shift_control", "phase"),
        ("qubits.q1.resonator.operations.readout.integration_weights_angle", "phase"),
        ("qubits.q1.xy.operations.x180.amplitude", "dimensionless"),
    ])
    def test_dimensions(self, path, dim):
        assert C.dimension_of(path) == dim


class TestToleranceTable:
    def test_freq_lab_boundary(self):
        assert C.values_within(6.25e9, 6.25e9 + 100.0, "freq", "lab")
        assert not C.values_within(6.25e9, 6.25e9 + 101.0, "freq", "lab")

    def test_freq_wide(self):
        assert C.values_within(6.25e9, 6.25e9 + 9e3, "freq", "wide")
        assert not C.values_within(6.25e9, 6.25e9 + 1.1e4, "freq", "wide")

    def test_time_lab_10ns(self):
        assert C.values_within(25e-6, 25e-6 + 9e-9, "time", "lab")
        assert not C.values_within(25e-6, 25e-6 + 1.1e-8, "time", "lab")

    def test_duration_int_exact_at_every_preset(self):
        for preset in ("exact", "lab", "wide"):
            assert not C.values_within(40, 41, "duration_ns", preset)
            assert C.values_within(40, 40, "duration_ns", preset)

    def test_duration_float_uses_threshold(self):
        assert C.values_within(40.0, 40.4, "duration_ns", "lab")
        assert not C.values_within(40.0, 40.6, "duration_ns", "lab")

    def test_volt_lab_1uv(self):
        assert C.values_within(0.08, 0.08 + 9e-7, "volt", "lab")
        assert not C.values_within(0.08, 0.08 + 2e-6, "volt", "lab")

    def test_fidelity_and_phase(self):
        assert C.values_within(0.985, 0.98505, "fidelity", "lab")
        assert not C.values_within(0.985, 0.987, "fidelity", "lab")
        assert C.values_within(0.11, 0.11 + 5e-7, "phase", "lab")
        assert not C.values_within(0.11, 0.111, "phase", "lab")

    def test_dimensionless_relative(self):
        assert C.values_within(1.0, 1.0 + 5e-10, "dimensionless", "lab")
        assert not C.values_within(1.0, 1.0 + 2e-9, "dimensionless", "lab")
        # abs floor near zero
        assert C.values_within(0.0, 5e-13, "dimensionless", "lab")

    def test_exact_preset_is_exact(self):
        assert not C.values_within(1.0, 1.0 + 1e-15, "dimensionless", "exact")
        assert C.values_within(40, 40.0, "duration_ns", "exact")  # numeric ==

    def test_describe_preset_has_thresholds(self):
        assert "±100 Hz" in C.describe_preset("lab")
        assert "±10 kHz" in C.describe_preset("wide")


# ===========================================================================
# snapshot normalisation
# ===========================================================================


class TestSnapshotSemantics:
    def _snap(self, state, wiring):
        store = QuamStore.from_dicts(state, wiring)
        return C.build_snapshot(store, "h", wiring_missing=not wiring)

    def test_ptr_kinds(self):
        state = {
            "refs": {"val": 5.0, "alias": "#./val"},
            "a": {"lit": 1, "abs": "#/refs/val", "dangle": "#/refs/nope",
                  "sub": {"rel": "#../lit"}},
        }
        snap = self._snap(state, {})
        assert snap.ptr_kind["a.lit"] == "literal"
        assert snap.ptr_kind["a.abs"] == "abs_ptr"
        assert snap.ptr_kind["a.sub.rel"] == "rel_ptr"
        assert snap.ptr_kind["a.dangle"] == "dangling"
        assert snap.ptr_kind["refs.alias"] == "self_ref"
        assert snap.flat_resolved["a.abs"] == 5.0
        assert snap.flat_resolved["a.sub.rel"] == 1
        assert "a.dangle" in snap.resolve_failed

    def test_self_ref_is_derived_with_source_leaf(self):
        state = {"q": {"f": 100.0, "alias": "#./f"}}
        snap = self._snap(state, {})
        assert "q.alias" in snap.derived
        assert snap.derived["q.alias"] == "q.f"
        assert snap.flat_resolved["q.alias"] == 100.0

    def test_abs_chain_to_self_ref_is_derived(self):
        state = {"q": {"f": 100.0, "alias": "#./f"},
                 "other": {"link": "#/q/alias"}}
        snap = self._snap(state, {})
        assert "other.link" in snap.derived
        assert snap.derived["other.link"] == "q.f"

    def test_container_pointer_reflattens(self):
        state = {"qubits": {"q1": {"xy": {"opx_output": "#/wiring/qubits/q1/xy/opx_output"}}}}
        wiring = make_wiring(["q1"])
        snap = self._snap(state, wiring)
        key = "qubits.q1.xy.opx_output"
        assert key in snap.container_ptrs
        assert key not in snap.flat_resolved
        assert snap.flat_resolved[key + ".delay"] == 0
        assert snap.flat_resolved[key + ".band"] == 1

    def test_lists_expand(self):
        state = {"p": {"confusion": [[1, 2], [3, 4]]}}
        snap = self._snap(state, {})
        assert snap.flat_raw["p.confusion.0.1"] == 2

    def test_pair_endpoints_recursive_and_orphans(self):
        # 2-hop: state → wiring pair entry → qubit (the variantb shape).
        state = {
            "qubits": {"qA1": {"id": "qA1"}, "qA2": {"id": "qA2"}},
            "qubit_pairs": {
                "coupler_qA1_qA2": {
                    "qubit_control": "#/wiring/qubit_pairs/qA1-A2/c/control_qubit",
                    "qubit_target": "#/wiring/qubit_pairs/qA1-A2/c/target_qubit",
                },
                "orphan_pair": {"qubit_control": "#/qubits/gone",
                                "qubit_target": "#/qubits/qA1"},
            },
        }
        wiring = {"wiring": {"qubit_pairs": {"qA1-A2": {"c": {
            "control_qubit": "#/qubits/qA2", "target_qubit": "#/qubits/qA1"}}}},
            "network": {"host": "h"}}
        snap = self._snap(state, wiring)
        assert snap.pair_endpoints["coupler_qA1_qA2"] == ("qA2", "qA1")
        assert snap.pair_endpoints["orphan_pair"] == (None, "qA1")
        assert snap.pair_orphans["orphan_pair"] == ["#/qubits/gone"]

    def test_structure_descriptor(self):
        qs = {"q1": make_qubit(grid="0,0"), "q2": make_qubit(grid="2,3")}
        state = make_chip(qs, {"q1-q2": make_cz_pair("q1", "q2")},
                          extra_state={"ports": {"mw_outputs": {"con1": {}},
                                                 "analog_outputs": {"con1": {}}}})
        snap = self._snap(state, make_wiring(["q1", "q2"]))
        st = snap.structure
        assert st["n_qubits"] == 2 and st["n_pairs"] == 1
        assert st["grid_bbox"] == (0, 0, 2, 3)
        assert st["gates"] == ["cz"]
        assert st["chip_type"] == "flux_tunable"
        assert st["instruments"] == ["analog_outputs/con1", "mw_outputs/con1"]

    def test_grid_parse_malformed(self):
        state = {"qubits": {"q1": {"grid_location": "oops"},
                            "q2": {"grid_location": "1,2"}}}
        snap = self._snap(state, {})
        assert snap.grid["q1"] is None
        assert snap.grid["q2"] == (1, 2)


# ===========================================================================
# row classification (closed enum) via full compare()
# ===========================================================================


class TestClassification:
    def _cmp(self, tmp_path, env, mutate, *, preset="lab", bucket=1,
             wiring_b="same", include_equal=False, **cmpkw):
        state_a, wiring_a = one_chip()
        state_b = copy.deepcopy(state_a)
        wb = copy.deepcopy(wiring_a) if wiring_b == "same" else wiring_b
        mutate(state_b, wb if isinstance(wb, dict) else {})
        a, b = resolve_pair(tmp_path, env, state_a, wiring_a, state_b, wb)
        return C.compare([a, b], env[0], bucket=bucket, preset=preset,
                         cache=env[1], include_equal_rows=include_equal,
                         **cmpkw)

    def test_identical_hero(self, tmp_path, env):
        state, wiring = one_chip()
        a, b = resolve_pair(tmp_path, env, state, wiring,
                            copy.deepcopy(state), copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        assert res["identical"] is True
        assert res["leaf_count"] > 0
        assert res["groups"] == []

    def test_modified_vs_within_tolerance(self, tmp_path, env):
        def drift(sb, wb):
            sb["qubits"]["q1"]["f_01"] += 50.0       # within lab ±100 Hz
            sb["qubits"]["q2"]["f_01"] += 5e5        # way beyond

        res = self._cmp(tmp_path, env, drift)
        rk = rows_by_key(res)
        assert rk["qubits.q1.f_01"]["cls"] == C.CLS_WITHIN
        assert rk["qubits.q2.f_01"]["cls"] == C.CLS_MODIFIED
        assert res["headline"]["changed"] >= 1
        assert res["headline"]["within_tolerance"] >= 1

    def test_exact_preset_flags_the_drift(self, tmp_path, env):
        def drift(sb, wb):
            sb["qubits"]["q1"]["f_01"] += 50.0

        res = self._cmp(tmp_path, env, drift, preset="exact")
        assert rows_by_key(res)["qubits.q1.f_01"]["cls"] == C.CLS_MODIFIED

    def test_added_removed_bucket1(self, tmp_path, env):
        def add_remove(sb, wb):
            sb["qubits"]["q1"]["new_field"] = 1.0
            del sb["qubits"]["q2"]["T1"]

        res = self._cmp(tmp_path, env, add_remove)
        rk = rows_by_key(res)
        assert rk["qubits.q1.new_field"]["cls"] == C.CLS_ADDED
        assert rk["qubits.q2.T1"]["cls"] == C.CLS_REMOVED

    def test_type_changed_int_float(self, tmp_path, env):
        def to_float(sb, wb):
            sb["qubits"]["q1"]["xy"]["operations"]["x180_DragCosine"]["length"] = 40.0

        res = self._cmp(tmp_path, env, to_float)
        key = "qubits.q1.xy.operations.x180_DragCosine.length"
        assert rows_by_key(res)[key]["cls"] == C.CLS_TYPE_CHANGED
        assert res["headline"]["meta"] >= 1
        assert res["headline"]["changed"] == 0

    def test_link_changed_literal_vs_pointer(self, tmp_path, env):
        # the downconverter fix-up shape: same value, reference rewired.
        state_a, wiring = one_chip()
        state_a["refs"] = {"shared": 0.115}
        state_b = copy.deepcopy(state_a)
        state_b["qubits"]["q1"]["xy"]["operations"]["x180_DragCosine"]["amplitude"] = "#/refs/shared"
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        key = "qubits.q1.xy.operations.x180_DragCosine.amplitude"
        assert rows_by_key(res)[key]["cls"] == C.CLS_LINK_CHANGED
        assert res["headline"]["changed"] == 0

    def test_schema_changed(self, tmp_path, env):
        def upgrade(sb, wb):
            sb["qubits"]["q1"]["__class__"] = "quam.NewTransmon"

        state_a, wiring = one_chip()
        state_a["qubits"]["q1"]["__class__"] = "quam.OldTransmon"
        state_b = copy.deepcopy(state_a)
        upgrade(state_b, {})
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        assert rows_by_key(res)["qubits.q1.__class__"]["cls"] == C.CLS_SCHEMA_CHANGED

    def test_provenance_excluded_from_changed(self, tmp_path, env):
        def prov(sb, wb):
            sb["qubits"]["q1"]["T1_updated_at"] = "2026-06-02"
            sb["qubits"]["q1"]["fit_load_id"] = 99
            sb["__package_versions__"] = {"quam": "0.5.0"}

        state_a, wiring = one_chip()
        state_a["qubits"]["q1"]["T1_updated_at"] = "2026-06-01"
        state_a["qubits"]["q1"]["fit_load_id"] = 12
        state_a["__package_versions__"] = {"quam": "0.4.2"}
        state_b = copy.deepcopy(state_a)
        prov(state_b, {})
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        rk = rows_by_key(res)
        for key in ("qubits.q1.T1_updated_at", "qubits.q1.fit_load_id",
                    "__package_versions__.quam"):
            assert rk[key]["cls"] == C.CLS_PROVENANCE, key
        assert res["headline"]["changed"] == 0
        assert res["headline"]["provenance"] == 3

    def test_derived_never_equal(self, tmp_path, env):
        # Same alias string both sides, runtime targets differ (183 kHz case):
        state_a, wiring = one_chip()
        state_a["qubits"]["q1"]["xy"]["intermediate_frequency"] = "#./inferred_if"
        state_a["qubits"]["q1"]["xy"]["inferred_if"] = 100e6
        state_b = copy.deepcopy(state_a)
        state_b["qubits"]["q1"]["xy"]["inferred_if"] = 100.183e6
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        rk = rows_by_key(res)
        row = rk["qubits.q1.xy.intermediate_frequency"]
        assert row["cls"] == C.CLS_DERIVED
        assert row["derived_src"][0] == "qubits.q1.xy.inferred_if"
        # ...and the source leaf itself shows the real modification.
        assert rk["qubits.q1.xy.inferred_if"]["cls"] == C.CLS_MODIFIED

    def test_unresolved_bulk_coalesces(self, tmp_path, env):
        # variantb shape: N identical dangling optional-default pointers must
        # coalesce into one attention group, not amber-spam N rows.
        state_a, wiring = one_chip()
        for q in state_a["qubits"].values():
            for i in range(3):
                q["xy"]["operations"][f"x90_v{i}"] = {
                    "amplitude": 0.05, "detuning": "#../x90_missing/detuning"}
        state_b = copy.deepcopy(state_a)
        state_b["qubits"]["q1"]["f_01"] += 5e5     # avoid identical hero
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        groups = res["attention"]["unresolved_groups"]
        assert groups and groups[0]["count"] == 6
        assert groups[0]["leaf"] == "detuning"
        rk = rows_by_key(res)
        assert not any(k.endswith("x90_v0.detuning") for k in rk)

    def test_not_in_source_missing_wiring(self, tmp_path, env):
        # A has wiring; B is the same state WITHOUT wiring.json: the state's
        # #/wiring/... pointers must classify not_in_source — never modified
        # (the 92-bogus-rows case). Never by value inequality.
        state, wiring = one_chip()
        fa = write_chip(tmp_path / "A", state, wiring)
        fb = write_chip(tmp_path / "B", copy.deepcopy(state), None)
        pool, cache = env
        a = cs.resolve_source(f"ws:{fa}", pool)
        b = cs.resolve_source(f"ws:{fb}", pool)
        res = C.compare([a, b], pool, cache=cache)
        assert res["headline"]["by_class"].get(C.CLS_NOT_IN_SOURCE, 0) >= 2
        assert res["headline"]["changed"] == 0
        rk = rows_by_key(res)
        for q in ("q1", "q2"):
            key = f"qubits.{q}.xy.opx_output"
            row = rk.get(key)
            assert row is not None and row["cls"] == C.CLS_NOT_IN_SOURCE, key

    def test_network_never_diffed(self, tmp_path, env):
        state, wiring = one_chip()
        wiring_b = make_wiring(["q1", "q2"], host="99.99.99.99")
        a, b = resolve_pair(tmp_path, env, state, wiring,
                            copy.deepcopy(state), wiring_b)
        res = C.compare([a, b], env[0], cache=env[1], include_equal_rows=True)
        assert not any(k.startswith("network") for k in rows_by_key(res))
        assert res["headline"]["changed"] == 0

    def test_bool_never_numeric(self, tmp_path, env):
        state_a, wiring = one_chip()
        state_a["flags"] = {"cal_ok": True, "mode": True}
        state_b = copy.deepcopy(state_a)
        state_b["flags"]["cal_ok"] = 1       # same logical value, wrong type
        state_b["flags"]["mode"] = False
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        rk = rows_by_key(res)
        assert rk["flags.cal_ok"]["cls"] == C.CLS_TYPE_CHANGED
        assert rk["flags.mode"]["cls"] == C.CLS_MODIFIED

    def test_three_sources_cells(self, tmp_path, env):
        pool, cache = env
        state, wiring = one_chip()
        s2 = copy.deepcopy(state)
        s2["qubits"]["q1"]["f_01"] += 5e5
        s3 = copy.deepcopy(state)
        s3["qubits"]["q1"]["f_01"] += 50.0
        fa = write_chip(tmp_path / "A", state, wiring)
        fb = write_chip(tmp_path / "B", s2, copy.deepcopy(wiring))
        fc = write_chip(tmp_path / "Cc", s3, copy.deepcopy(wiring))
        srcs = [cs.resolve_source(f"ws:{f}", pool) for f in (fa, fb, fc)]
        res = C.compare(srcs, pool, cache=cache)
        row = rows_by_key(res)["qubits.q1.f_01"]
        assert row["cells"][0] == "ref"
        assert row["cells"][1] == C.CLS_MODIFIED
        assert row["cells"][2] == C.CLS_WITHIN
        assert row["cls"] == C.CLS_MODIFIED     # worst across cells
        assert len(res["sources"]) == 3


# ===========================================================================
# coalescing (A5)
# ===========================================================================


class TestCoalescing:
    def test_one_sided_subtree_collapses_to_highest_ancestor(self, tmp_path, env):
        state_a, wiring = one_chip()
        state_b = copy.deepcopy(state_a)
        state_b["qubits"]["q1"]["coupler"] = {
            "decouple_offset": 0.1, "interaction_offset": 0.2,
            "opx_output": {"delay": 24, "band": 2, "offset": 0.0},
        }
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        g = next(g for g in res["groups"]
                 if g["section"] == "Qubits" and g["entity"] == "q1")
        col = [c for c in g["collapsed"] if c["root"] == "qubits.q1.coupler"]
        assert col and col[0]["count"] == 5 and col[0]["cls"] == C.CLS_ADDED
        # constituent rows are NOT materialised individually
        assert not any(r["key"].startswith("qubits.q1.coupler.")
                       for r in g["rows"])
        # ...but still counted
        assert g["counts"][C.CLS_ADDED] >= 5

    def test_singleton_one_sided_stays_a_row(self, tmp_path, env):
        state_a, wiring = one_chip()
        state_b = copy.deepcopy(state_a)
        state_b["qubits"]["q1"]["new_leaf"] = 3.5
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        assert rows_by_key(res)["qubits.q1.new_leaf"]["cls"] == C.CLS_ADDED

    def test_giant_collapse_carries_sub_summary(self, tmp_path, env):
        state_a, wiring = one_chip()
        state_b = copy.deepcopy(state_a)
        big = {f"grp{i}": {f"leaf{j}": j for j in range(60)} for i in range(11)}
        state_b["qubits"]["q1"]["mega"] = big          # 660 > 500 leaves
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        g = next(g for g in res["groups"]
                 if g["section"] == "Qubits" and g["entity"] == "q1")
        col = next(c for c in g["collapsed"] if c["root"] == "qubits.q1.mega")
        assert col["count"] == 660
        assert col["sub"]["grp0"] == 60 and len(col["sub"]) == 11

    def test_equal_subtrees_counted_and_collapsed(self, tmp_path, env):
        state_a, wiring = one_chip()
        state_b = copy.deepcopy(state_a)
        state_b["qubits"]["q1"]["f_01"] += 5e5
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = C.compare([a, b], env[0], cache=env[1])
        assert res["headline"]["equal"] > 10
        q2 = next(g for g in res["groups"]
                  if g["section"] == "Qubits" and g["entity"] == "q2")
        assert q2["equal_collapsed"], "fully-equal q2 should coalesce"
        assert q2["counts"].get(C.CLS_EQUAL, 0) > 0


# ===========================================================================
# bucket-② auto-map (A2)
# ===========================================================================


def _grid_chip(names_pos: dict[str, str]) -> tuple[dict, dict]:
    qs = {n: make_qubit(6.2e9 + i * 1e7, pos)
          for i, (n, pos) in enumerate(names_pos.items())}
    return make_chip(qs), make_wiring(list(qs))


class TestAutoMap:
    def _snaps(self, tmp_path, env, chip_a, chip_b):
        a, b = resolve_pair(tmp_path, env, chip_a[0], chip_a[1],
                            chip_b[0], chip_b[1])
        return (C.snapshot_for(a, env[0], env[1]),
                C.snapshot_for(b, env[0], env[1]))

    def test_grid_auto_disjoint_names(self, tmp_path, env):
        A = _grid_chip({"q1": "0,0", "q2": "1,0", "q3": "0,1", "q4": "2,2"})
        B = _grid_chip({"r1": "0,0", "r2": "1,0", "r3": "0,1", "r4": "2,2"})
        sa, sb = self._snaps(tmp_path, env, A, B)
        mr = C.auto_map_qubits(sa, sb)
        assert mr.status == "auto" and mr.method == "grid"
        assert mr.pairs == {"q1": "r1", "q2": "r2", "q3": "r3", "q4": "r4"}

    def test_grid_auto_subset_containment(self, tmp_path, env):
        A = _grid_chip({"q1": "0,0", "q2": "1,0", "q3": "0,1", "q4": "2,2"})
        B = _grid_chip({"r1": "0,0", "r3": "0,1", "r4": "2,2"})
        sa, sb = self._snaps(tmp_path, env, A, B)
        mr = C.auto_map_qubits(sa, sb)
        assert mr.status == "auto"
        assert mr.pairs == {"q1": "r1", "q3": "r3", "q4": "r4"}
        assert mr.unmatched_a == ["q2"]

    def test_crossed_names_reject_grid(self, tmp_path, env):
        # the variantb⊂LabA shape: B shares names but at DIFFERENT positions.
        A = _grid_chip({"q1": "0,0", "q2": "1,0", "q3": "0,1"})
        B = _grid_chip({"q2": "0,0", "q1": "1,0", "q3": "0,1"})
        sa, sb = self._snaps(tmp_path, env, A, B)
        mr = C.auto_map_qubits(sa, sb)
        assert mr.status != "auto"
        assert mr.method == "name"                 # fell back to names
        assert mr.pairs == {"q1": "q1", "q2": "q2", "q3": "q3"}
        assert mr.confidence["contained"] is True
        assert mr.confidence["name_consistent"] is False

    def test_mirror_redeclaration_caught(self, tmp_path, env):
        # dihedral mirror: same names, x-mirrored positions → grid pairing
        # crosses names → rejected, name branch (suggested).
        A = _grid_chip({"q1": "0,0", "q2": "2,0", "q3": "1,1"})
        B = _grid_chip({"q1": "2,0", "q2": "0,0", "q3": "1,1"})
        sa, sb = self._snaps(tmp_path, env, A, B)
        mr = C.auto_map_qubits(sa, sb)
        assert mr.status == "suggested" and mr.method == "name"

    def test_degenerate_line_distrusts_grid(self, tmp_path, env):
        # examplechip's auto-assigned 1×N line, disjoint names: grid would zip
        # them confidently — must NOT.
        A = _grid_chip({f"q{i}": f"{i},0" for i in range(4)})
        B = _grid_chip({f"r{i}": f"{i},0" for i in range(4)})
        sa, sb = self._snaps(tmp_path, env, A, B)
        mr = C.auto_map_qubits(sa, sb)
        assert mr.status == "manual-needed"        # no names shared either
        assert mr.pairs == {}                      # NEVER positional zip

    def test_name_fallback_is_intersection_never_zip(self, tmp_path, env):
        # deviceC (qB*) vs LabA (qA*): no grid trust, no shared names →
        # sorted-zip would pair qB1↔qA1 — forbidden.
        A = _grid_chip({"qA1": "0,0", "qA2": "1,0"})
        B = ({"qubits": {"qB1": make_qubit(6.2e9, None),
                         "qB2": make_qubit(6.3e9, None)},
              "qubit_pairs": {}}, make_wiring(["qB1", "qB2"]))
        sa, sb = self._snaps(tmp_path, env, A, B)
        mr = C.auto_map_qubits(sa, sb)
        assert mr.pairs == {}
        assert mr.status == "manual-needed"

    def test_identical_names_without_grid_is_suggested(self, tmp_path, env):
        # A2 binding: only the grid branch may auto-confirm.
        A = ({"qubits": {"q1": make_qubit(6.2e9, None)}, "qubit_pairs": {}},
             make_wiring(["q1"]))
        B = ({"qubits": {"q1": make_qubit(6.9e9, None)}, "qubit_pairs": {}},
             make_wiring(["q1"]))
        sa, sb = self._snaps(tmp_path, env, A, B)
        mr = C.auto_map_qubits(sa, sb)
        assert mr.status == "suggested" and mr.pairs == {"q1": "q1"}

    def test_compare_bucket2_needs_confirm_on_suggested(self, tmp_path, env):
        A = _grid_chip({f"q{i}": f"{i},0" for i in range(4)})   # degenerate
        B = _grid_chip({f"q{i}": f"{i},0" for i in range(4)})
        B[0]["qubits"]["q0"]["f_01"] += 5e5
        a, b = resolve_pair(tmp_path, env, A[0], A[1], B[0], B[1])
        res = C.compare([a, b], env[0], bucket=2, cache=env[1])
        assert res["needs_confirm"] is True
        assert res["mapping"]["status"] == "suggested"
        assert res["groups"] == []


# ===========================================================================
# pair mapping + A3 flip policy
# ===========================================================================


def _cz_chips(tmp_path, env, *, flip=True, permute_confusion=True,
              swap_phases=True, swap_mutual=True, relabel_moving=True,
              roles_agree=True):
    """Chip A with pair q1-q2; chip B with the physically-identical pair
    declared in the OPPOSITE orientation (q2-q1). When the transform flags
    are True, B's values are written as a flip-consistent re-declaration —
    the engine must find zero physics drift."""
    qs_a = {"q1": make_qubit(6.2e9, "0,0"), "q2": make_qubit(6.3e9, "1,0")}
    conf = [[0.97, 0.01, 0.012, 0.008],
            [0.02, 0.94, 0.021, 0.019],
            [0.03, 0.029, 0.91, 0.031],
            [0.04, 0.041, 0.039, 0.88]]
    pair_a = make_cz_pair("q1", "q2", moving="control", psc=0.11, pst=0.22,
                          confusion=conf, mutual=(0.001, 0.002))
    state_a = make_chip(qs_a, {"q1-q2": pair_a})

    qs_b = {"q1": make_qubit(6.2e9, "0,0"), "q2": make_qubit(6.3e9, "1,0")}
    qs_b["q1"]["T1"] = 26e-6          # keep A ≠ B (no identical-hero shortcut)
    P = (0, 2, 1, 3)
    conf_b = ([[conf[P[r]][P[c]] for c in range(4)] for r in range(4)]
              if permute_confusion else copy.deepcopy(conf))
    pair_b = make_cz_pair(
        "q2", "q1",
        moving=("target" if relabel_moving else "control") if roles_agree
        else "control" if relabel_moving else "target",
        psc=0.22 if swap_phases else 0.11,
        pst=0.11 if swap_phases else 0.22,
        confusion=conf_b,
        mutual=(0.002, 0.001) if swap_mutual else (0.001, 0.002))
    if not flip:
        pair_b = make_cz_pair("q1", "q2", moving="control", psc=0.11,
                              pst=0.22, confusion=conf, mutual=(0.001, 0.002))
    pid_b = "q2-q1" if flip else "q1-q2"
    pair_b["id"] = pid_b
    state_b = make_chip(qs_b, {pid_b: pair_b})
    wiring = make_wiring(["q1", "q2"])
    return resolve_pair(tmp_path, env, state_a, wiring,
                        state_b, copy.deepcopy(wiring))


class TestPairMapAndFlip:
    def _bucket2(self, env, a, b):
        return C.compare([a, b], env[0], bucket=2, cache=env[1],
                         qubit_map={"q1": "q1", "q2": "q2"})

    def test_direct_match_no_flip(self, tmp_path, env):
        a, b = _cz_chips(tmp_path, env, flip=False)
        res = self._bucket2(env, a, b)
        m = res["pair_map"]["matches"]["q1-q2"]
        assert m["pair_b"] == "q1-q2" and m["flipped"] is False

    def test_flipped_cz_matches_with_zero_phantom_drift(self, tmp_path, env):
        a, b = _cz_chips(tmp_path, env)
        res = self._bucket2(env, a, b)
        m = res["pair_map"]["matches"]["q1-q2"]
        assert m["pair_b"] == "q2-q1" and m["flipped"] is True
        # a flip-consistent re-declaration has NO physics drift:
        pair_rows = [r for g in res["groups"] if g["section"] == "Pairs"
                     for r in g["rows"]
                     if r["cls"] in (C.CLS_MODIFIED, C.CLS_WITHIN)]
        assert pair_rows == [], [r["key"] for r in pair_rows]

    def test_confusion_permute_is_actually_applied(self, tmp_path, env):
        # negative control: flipped declaration WITHOUT permuting confusion
        # = real physics difference the permute must expose (no silent pass).
        a, b = _cz_chips(tmp_path, env, permute_confusion=False)
        res = self._bucket2(env, a, b)
        conf_rows = [r for g in res["groups"] for r in g["rows"]
                     if ".confusion." in r["key"] and r["cls"] == C.CLS_MODIFIED]
        assert conf_rows, "un-permuted confusion must surface as modified"

    def test_phase_swap_applied(self, tmp_path, env):
        a, b = _cz_chips(tmp_path, env, swap_phases=False)
        res = self._bucket2(env, a, b)
        rows = [r for g in res["groups"] for r in g["rows"]
                if "phase_shift" in r["key"] and r["cls"] == C.CLS_MODIFIED]
        assert rows, "unswapped phases must surface as modified"

    def test_mutual_flux_bias_swap(self, tmp_path, env):
        a, b = _cz_chips(tmp_path, env, swap_mutual=False)
        res = self._bucket2(env, a, b)
        rows = [r for g in res["groups"] for r in g["rows"]
                if "mutual_flux_bias" in r["key"]
                and r["cls"] in (C.CLS_MODIFIED, C.CLS_WITHIN)]
        assert rows

    def test_moving_qubit_relabel(self, tmp_path, env):
        a, b = _cz_chips(tmp_path, env, relabel_moving=False)
        res = self._bucket2(env, a, b)
        rows = [r for g in res["groups"] for r in g["rows"]
                if r["key"].endswith("moving_qubit")
                and r["cls"] == C.CLS_MODIFIED]
        assert rows, "un-relabelled moving_qubit must surface"

    def test_flip_exclusions_annotated(self, tmp_path, env):
        a, b = _cz_chips(tmp_path, env)
        res = self._bucket2(env, a, b)
        excl = res["attention"]["flip_excluded"]
        assert excl.get("id") and excl.get("detuning") and excl.get("extras")
        rk = rows_by_key(res)
        assert "qubit_pairs.q1-q2.detuning" not in rk
        assert not any(".extras." in k for k in rk if k.startswith("qubit_pairs"))

    def test_flux_pulse_qubit_excluded_when_roles_disagree(self, tmp_path, env):
        a, b = _cz_chips(tmp_path, env, roles_agree=False)
        res = self._bucket2(env, a, b)
        assert res["attention"]["flip_excluded"].get("flux_pulse_qubit")
        rk = rows_by_key(res)
        assert not any("flux_pulse_qubit" in k for k in rk)

    def test_flux_pulse_qubit_compared_when_roles_agree(self, tmp_path, env):
        a, b = _cz_chips(tmp_path, env)          # roles agree
        res = self._bucket2(env, a, b)
        assert not res["attention"]["flip_excluded"].get("flux_pulse_qubit")

    def test_cr_never_flips(self, tmp_path, env):
        qs = {"q0": make_qubit(5.0e9, "0,0"), "q4": make_qubit(5.1e9, "1,0")}
        state_a = make_chip(copy.deepcopy(qs), {"q0-q4": make_cr_pair("q0", "q4")})
        state_b = make_chip(copy.deepcopy(qs), {"q4-q0": make_cr_pair("q4", "q0")})
        wiring = make_wiring(["q0", "q4"])
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        res = self._bucket2(env, a, b)
        assert res["pair_map"]["matches"] == {}
        assert "q0-q4" in res["pair_map"]["unmatched_a"]
        assert "q4-q0" in res["pair_map"]["unmatched_b"]

    def test_real_paths_carried_for_mapped_qubits(self, tmp_path, env):
        # U2: rows must expose per-source REAL names (qA3 ↔ q7 headers).
        A = _grid_chip({"qA3": "0,0", "qA4": "1,0", "qA5": "0,1"})
        B = _grid_chip({"q7": "0,0", "q8": "1,0", "q9": "0,1"})
        B[0]["qubits"]["q7"]["f_01"] = A[0]["qubits"]["qA3"]["f_01"] + 5e5
        a, b = resolve_pair(tmp_path, env, A[0], A[1], B[0], B[1])
        res = C.compare([a, b], env[0], bucket=2, cache=env[1])  # grid auto
        assert res["needs_confirm"] is False
        assert res["mapping"]["method"] == "grid"
        row = rows_by_key(res)["qubits.qA3.f_01"]
        assert row["real_paths"][1] == "qubits.q7.f_01"

    def test_bucket2_infra_excluded_from_headline(self, tmp_path, env):
        A = _grid_chip({"qA3": "0,0", "qA4": "1,0", "qA5": "0,1"})
        B = _grid_chip({"q7": "0,0", "q8": "1,0", "q9": "0,1"})
        B[1]["wiring"]["qubits"]["q7"] = {"xy": {"opx_output": {
            "delay": 99, "band": 1, "full_scale_power_dbm": -11}}}
        a, b = resolve_pair(tmp_path, env, A[0], A[1], B[0], B[1])
        res = C.compare([a, b], env[0], bucket=2, cache=env[1])
        assert res["needs_confirm"] is False
        # wiring rows exist but never in the ② headline
        assert res["headline"]["changed"] == 0


# ===========================================================================
# bucket ③
# ===========================================================================


class TestBucket3:
    def test_chip_cards_no_rows(self, tmp_path, env):
        A = _grid_chip({"q1": "0,0"})
        B = _grid_chip({"r1": "0,0"})
        a, b = resolve_pair(tmp_path, env, A[0], A[1], B[0], B[1])
        res = C.compare([a, b], env[0], bucket=3, cache=env[1])
        assert res["groups"] == []
        assert len(res["chip_cards"]) == 2
        card = res["chip_cards"][0]
        assert card["structure"]["n_qubits"] == 1
        f01 = card["metrics"]["f_01"]
        assert f01["values"] == {"q1": 6.2e9}
        assert f01["min"] == f01["max"] == 6.2e9


# ===========================================================================
# summary extraction
# ===========================================================================


class TestSummary:
    def _summary(self, tmp_path, env, state_a, state_b, wiring):
        a, b = resolve_pair(tmp_path, env, state_a, wiring,
                            state_b, copy.deepcopy(wiring))
        return C.compare([a, b], env[0], cache=env[1])["summary"]

    def _row(self, summary, entity, key):
        return next((r for r in summary
                     if r["entity"] == entity and r["key"] == key), None)

    def test_alias_pointer_resolution_non_dragcosine(self, tmp_path, env):
        # x180 alias points at a SQUARE pulse — the hardcoded-DragCosine
        # QueryEngine path would miss it; the alias template must not.
        qs = {"q1": make_qubit(6.2e9, "0,0", pulse_cls="x180_Square")}
        state = make_chip(qs)
        state_b = copy.deepcopy(state)
        state_b["qubits"]["q1"]["f_01"] += 5e5
        summary = self._summary(tmp_path, env, state, state_b,
                                make_wiring(["q1"]))
        row = self._row(summary, "q1", "x180_amplitude")
        assert row is not None and row["values"] == [0.115, 0.115]

    def test_delta_and_beyond_tolerance(self, tmp_path, env):
        state, wiring = one_chip()
        state_b = copy.deepcopy(state)
        state_b["qubits"]["q1"]["f_01"] += 5e5
        state_b["qubits"]["q2"]["f_01"] += 50.0
        summary = self._summary(tmp_path, env, state, state_b, wiring)
        r1 = self._row(summary, "q1", "f_01")
        assert r1["delta"][1] == pytest.approx(5e5)
        assert r1["beyond"][1] is True
        r2 = self._row(summary, "q2", "f_01")
        assert r2["beyond"][1] is False

    def test_readout_fidelity_from_confusion_diag(self, tmp_path, env):
        state, wiring = one_chip()
        state_b = copy.deepcopy(state)
        state_b["qubits"]["q1"]["f_01"] += 5e5
        summary = self._summary(tmp_path, env, state, state_b, wiring)
        row = self._row(summary, "q1", "readout_fidelity")
        assert row["values"][0] == pytest.approx((0.97 + 0.95) / 2)

    def test_divergence_flag(self, tmp_path, env):
        state, wiring = one_chip()
        state_b = copy.deepcopy(state)
        state_b["qubits"]["q1"]["xy"]["RF_frequency"] = \
            state_b["qubits"]["q1"]["f_01"] + 2e3          # > 1 kHz
        summary = self._summary(tmp_path, env, state, state_b, wiring)
        row = self._row(summary, "q1", "f01_rf_divergence")
        assert row is not None and row["values"] == [False, True]
        assert self._row(summary, "q2", "f01_rf_divergence") is None

    def test_all_null_rows_dropped(self, tmp_path, env):
        state, wiring = one_chip()
        for q in state["qubits"].values():
            q["z"]["joint_offset"] = None
        state_b = copy.deepcopy(state)
        state_b["qubits"]["q1"]["f_01"] += 5e5
        summary = self._summary(tmp_path, env, state, state_b, wiring)
        assert self._row(summary, "q1", "z_joint_offset") is None

    def test_pair_fidelity_canonicalization_nested(self, tmp_path, env):
        qs = {"q1": make_qubit(6.2e9, "0,0"), "q2": make_qubit(6.3e9, "1,0")}
        state = make_chip(qs, {"q1-q2": make_cz_pair("q1", "q2")})
        state_b = copy.deepcopy(state)
        state_b["qubits"]["q1"]["f_01"] += 5e5
        summary = self._summary(tmp_path, env, state, state_b,
                                make_wiring(["q1", "q2"]))
        row = self._row(summary, "q1-q2", "two_qubit_fidelity")
        v = row["values"][0]
        assert v["gate"] == "cz_unipolar"           # followed the macros.cz alias
        assert v["value"] == 0.985                  # nested avg gate fidelity
        assert v["clifford"] is False

    def test_pair_fidelity_bare_float_is_clifford(self, tmp_path, env):
        qs = {"q1": make_qubit(6.2e9, "0,0"), "q2": make_qubit(6.3e9, "1,0")}
        pair = make_cz_pair("q1", "q2")
        pair["macros"]["cz_unipolar"]["fidelity"] = {"StandardRB": 0.6512}
        state = make_chip(qs, {"q1-q2": pair})
        state_b = copy.deepcopy(state)
        state_b["qubits"]["q1"]["f_01"] += 5e5
        summary = self._summary(tmp_path, env, state, state_b,
                                make_wiring(["q1", "q2"]))
        v = self._row(summary, "q1-q2", "two_qubit_fidelity")["values"][0]
        assert v["value"] == 0.6512 and v["clifford"] is True

    def test_gate_inventory_row(self, tmp_path, env):
        qs = {"q1": make_qubit(6.2e9, "0,0"), "q2": make_qubit(6.3e9, "1,0")}
        state = make_chip(qs, {"q1-q2": make_cz_pair("q1", "q2")})
        state_b = copy.deepcopy(state)
        state_b["qubits"]["q1"]["f_01"] += 5e5
        summary = self._summary(tmp_path, env, state, state_b,
                                make_wiring(["q1", "q2"]))
        row = self._row(summary, "q1-q2", "gate_inventory")
        assert row["values"][0] == ["cz_unipolar"]


# ===========================================================================
# caching contract (M5)
# ===========================================================================


class TestCachingContract:
    def test_snapshots_shared_across_order_and_ref(self, tmp_path, env):
        pool, cache = env
        state, wiring = one_chip()
        s2 = copy.deepcopy(state)
        s2["qubits"]["q1"]["f_01"] += 5e5
        a, b = resolve_pair(tmp_path, env, state, wiring,
                            s2, copy.deepcopy(wiring))
        C.compare([a, b], pool, cache=cache)
        assert len(cache._entries) == 2
        snaps_before = dict(cache._entries)
        # reorder + move ref: no new snapshots, same objects reused.
        res = C.compare([b, a], pool, ref=1, cache=cache)
        assert dict(cache._entries) == snaps_before
        assert res["headline"]["changed"] == 1

    def test_ref_move_flips_added_removed(self, tmp_path, env):
        pool, cache = env
        state, wiring = one_chip()
        s2 = copy.deepcopy(state)
        s2["qubits"]["q1"]["extra"] = 1.0
        a, b = resolve_pair(tmp_path, env, state, wiring,
                            s2, copy.deepcopy(wiring))
        r0 = C.compare([a, b], pool, ref=0, cache=cache)
        r1 = C.compare([a, b], pool, ref=1, cache=cache)
        assert rows_by_key(r0)["qubits.q1.extra"]["cls"] == C.CLS_ADDED
        assert rows_by_key(r1)["qubits.q1.extra"]["cls"] == C.CLS_REMOVED

    def test_source_added_twice_stays_two_columns(self, tmp_path, env):
        pool, cache = env
        state, wiring = one_chip()
        s2 = copy.deepcopy(state)
        s2["qubits"]["q1"]["f_01"] += 5e5
        a, b = resolve_pair(tmp_path, env, state, wiring,
                            s2, copy.deepcopy(wiring))
        res = C.compare([a, b, a], pool, cache=cache)
        assert len(res["sources"]) == 3
        row = rows_by_key(res)["qubits.q1.f_01"]
        assert len(row["cells"]) == 3 and len(row["resolved"]) == 3
        assert row["cells"][2] == C.CLS_EQUAL      # a vs a

    def test_evicted_pool_entry_raises_lookup(self, tmp_path, env):
        pool, cache = env
        state, wiring = one_chip()
        folder = write_chip(tmp_path / "X", state, wiring)
        src = cs.resolve_source(f"ws:{folder}", pool)
        pool.clear()
        with pytest.raises(LookupError):
            C.compare([src, src], pool, cache=cache)


# ===========================================================================
# mapping persistence (A1)
# ===========================================================================


class TestMappingStore:
    def test_roundtrip(self, tmp_path):
        ms = C.MappingStore(tmp_path)
        ms.save("net1", "LabA", "deviceB", {"qA1": "qB1", "qA2": "qB2"},
                {"qA1", "qA2"}, {"qB1", "qB2"})
        rec = ms.load("net1", "LabA", "deviceB", {"qA1", "qA2"}, {"qB1", "qB2"})
        assert rec["pairs"] == {"qA1": "qB1", "qA2": "qB2"}
        assert rec["stale"] == {}

    def test_anchor_order_canonical_single_record(self, tmp_path):
        ms = C.MappingStore(tmp_path)
        ms.save("net1", "zeta", "alpha", {"z1": "a1"}, {"z1"}, {"a1"})
        data = json.loads((tmp_path / "compare_maps.json").read_text())
        assert len(data) == 1
        key = next(iter(data))
        assert key == "net1|alpha|zeta"
        # stored oriented alpha→zeta
        assert data[key]["pairs"] == {"a1": "z1"}
        # load in both orientations
        assert ms.load("net1", "zeta", "alpha", {"z1"}, {"a1"})["pairs"] == {"z1": "a1"}
        assert ms.load("net1", "alpha", "zeta", {"a1"}, {"z1"})["pairs"] == {"a1": "z1"}

    def test_network_token_isolates_records(self, tmp_path):
        ms = C.MappingStore(tmp_path)
        ms.save("netA", "x", "y", {"q1": "r1"}, {"q1"}, {"r1"})
        assert ms.load("netB", "x", "y", {"q1"}, {"r1"}) is None

    def test_stale_names_dimmed_rest_kept(self, tmp_path):
        # a chip growing/renaming one qubit must not orphan the mapping (A1).
        ms = C.MappingStore(tmp_path)
        ms.save("net1", "a", "b", {"q1": "r1", "q2": "r2"}, {"q1", "q2"},
                {"r1", "r2"})
        rec = ms.load("net1", "a", "b", {"q1", "q3"}, {"r1", "r2"})
        assert rec["pairs"] == {"q1": "r1"}
        assert rec["stale"] == {"q2": "r2"}

    def test_drop_origin_never_persists(self, tmp_path):
        ms = C.MappingStore(tmp_path)
        with pytest.raises(ValueError):
            ms.save("net1", "a", "b", {"q1": "r1"}, {"q1"}, {"r1"},
                    origins=("drop", "workspace"))
        assert not (tmp_path / "compare_maps.json").exists()

    def test_corrupt_file_degrades_to_fresh(self, tmp_path):
        (tmp_path / "compare_maps.json").write_text("{broken", encoding="utf-8")
        ms = C.MappingStore(tmp_path)
        assert ms.load("net1", "a", "b", set(), set()) is None
        ms.save("net1", "a", "b", {"q1": "r1"}, {"q1"}, {"r1"})
        assert ms.load("net1", "a", "b", {"q1"}, {"r1"})["pairs"] == {"q1": "r1"}
