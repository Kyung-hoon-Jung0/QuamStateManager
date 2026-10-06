"""A qubit renamed on the Re-generate wizard is the same qubit under a new id.

The merge matched source and rebuild by PATH, so renaming q1, q2 to q0, q1 on
the wizard carried the old q1's calibration onto the new q1 -- the qubit that
used to be q2 -- gave q0 build defaults, and reported only the old q2's values
as lost. The wizard now sends ``qubit_sources`` ({current id: source id}) and
the source chip is re-expressed in the rebuilt ids before anything matches.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import regenerate
from quam_state_manager.core.regen_merge import (
    merge_states,
    rename_source_qubits,
    source_renames,
)


def _chip(z_ports: dict, f01: dict, pairs=()):
    """A modern two-hop chip: each qubit's z line points through wiring at an
    LF port (``{qubit: port}``); ``pairs`` are ``(control, target, phase)``,
    their members referenced through ``wiring.qubit_pairs``."""
    qubits, wq, ports = {}, {}, {}
    for q, p in z_ports.items():
        qubits[q] = {
            "id": q, "f_01": f01[q],
            "z": {"opx_output": f"#/wiring/qubits/{q}/z/opx_output"},
            "xy": {"operations": {"x180": {"amplitude": f01[q] / 1e10},
                                  "x90": f"#/qubits/{q}/xy/operations/x180"}},
        }
        wq[q] = {"z": {"opx_output": f"#/ports/analog_outputs/con1/5/{p}"}}
        ports[str(p)] = {"port_id": p, "delay": 30 + p}
    qp, wp = {}, {}
    for c, t, phase in pairs:
        pid = f"{c}-{t}"
        qp[pid] = {"id": pid,
                   "qubit_control": f"#/wiring/qubit_pairs/{pid}/control_qubit",
                   "qubit_target": f"#/wiring/qubit_pairs/{pid}/target_qubit",
                   "macros": {"cz": {"phase_shift_control": phase}}}
        wp[pid] = {"control_qubit": f"#/qubits/{c}", "target_qubit": f"#/qubits/{t}"}
    state = {"qubits": qubits, "qubit_pairs": qp,
             "ports": {"analog_outputs": {"con1": {"5": ports}}},
             "active_qubit_names": list(z_ports)}
    wiring = {"wiring": {"qubits": wq, "qubit_pairs": wp}, "network": {}}
    return state, wiring


def _fresh(z_ports: dict, pairs=()):
    """What the build writes: the rebuilt structure, defaults everywhere."""
    s, w = _chip(z_ports, {q: 5.0e9 for q in z_ports},
                 [(c, t, 0.0) for c, t in pairs])
    for p in s["ports"]["analog_outputs"]["con1"]["5"].values():
        p["delay"] = 0
    return s, w


def _merge(old, new, sources):
    (os_, ow), (ns, nw) = old, new
    ren = source_renames(sources, os_, ns["qubits"])
    os2, ow2, qr, pr = rename_source_qubits(os_, ow, ren, ns, nw)
    r = merge_states(os2, ns, old_wiring=ow2, new_wiring=nw)
    return r, qr, pr


class TestAShiftRenameCarriesEachQubitsOwnCalibration:
    """The customer's ask: q1, q2, q3 -> q0, q1, q2 on the same lines."""

    OLD = ({"q1": 1, "q2": 2, "q3": 3}, {"q1": 4.1e9, "q2": 4.2e9, "q3": 4.3e9})

    def test_each_value_lands_on_the_qubit_it_was_measured_on(self):
        old = _chip(*self.OLD, pairs=[("q1", "q2", 0.11), ("q2", "q3", 0.22)])
        new = _fresh({"q0": 1, "q1": 2, "q2": 3}, pairs=[("q0", "q1"), ("q1", "q2")])
        r, qr, pr = _merge(old, new, {"q0": "q1", "q1": "q2", "q2": "q3"})
        q = r.merged["qubits"]
        assert (q["q0"]["f_01"], q["q1"]["f_01"], q["q2"]["f_01"]) == (4.1e9, 4.2e9, 4.3e9)
        assert q["q1"]["xy"]["operations"]["x180"]["amplitude"] == 0.42
        # the id field is the rebuilt one, and a pointer stays the build's own
        assert q["q0"]["id"] == "q0"
        assert q["q0"]["xy"]["operations"]["x90"] == "#/qubits/q0/xy/operations/x180"
        pairs = r.merged["qubit_pairs"]
        assert set(pairs) == {"q0-q1", "q1-q2"}
        assert pairs["q0-q1"]["macros"]["cz"]["phase_shift_control"] == 0.11
        assert pairs["q1-q2"]["macros"]["cz"]["phase_shift_control"] == 0.22
        assert r.stats.residual_lost == []
        assert qr == [("q1", "q0"), ("q2", "q1"), ("q3", "q2")]
        assert pr == [("q1-q2", "q0-q1"), ("q2-q3", "q1-q2")]

    def test_same_lines_means_nothing_moved_and_nothing_was_inherited(self):
        old = _chip(*self.OLD)
        new = _fresh({"q0": 1, "q1": 2, "q2": 3})
        r, _, _ = _merge(old, new, {"q0": "q1", "q1": "q2", "q2": "q3"})
        assert r.stats.ports_moved == [] and r.stats.ports_inherited == []
        assert r.merged["ports"]["analog_outputs"]["con1"]["5"]["2"]["delay"] == 32

    def test_without_the_record_the_merge_still_matches_by_id(self):
        # an older client: today's behaviour, unchanged
        old = _chip(*self.OLD)
        new = _fresh({"q0": 1, "q1": 2, "q2": 3})
        r, qr, _ = _merge(old, new, None)
        assert qr == []
        assert r.merged["qubits"]["q1"]["f_01"] == 4.1e9
        assert "qubits.q3.f_01" in r.stats.residual_lost


class TestARenameOntoARemovedQubitsId:
    def test_the_removed_qubits_values_are_reported_never_carried(self):
        # q1 deleted, then q2, q3 renamed to q1, q2: the old q1 is gone -- its
        # values must not land on the new q1, which is the old q2
        old = _chip({"q1": 1, "q2": 2, "q3": 3}, {"q1": 4.1e9, "q2": 4.2e9, "q3": 4.3e9},
                    pairs=[("q1", "q2", 0.11), ("q2", "q3", 0.22)])
        new = _fresh({"q1": 2, "q2": 3}, pairs=[("q1", "q2")])
        r, qr, pr = _merge(old, new, {"q1": "q2", "q2": "q3"})
        q = r.merged["qubits"]
        assert (q["q1"]["f_01"], q["q2"]["f_01"]) == (4.2e9, 4.3e9)
        assert r.merged["qubit_pairs"]["q1-q2"]["macros"]["cz"]["phase_shift_control"] == 0.22
        lost = r.stats.residual_lost
        assert "qubits.q1_removed.f_01" in lost
        assert "qubit_pairs.q1-q2 (source).macros.cz.phase_shift_control" in lost
        assert qr == [("q2", "q1"), ("q3", "q2")]
        assert pr == [("q2-q3", "q1-q2")]

    def test_a_swap_is_two_renames(self):
        old = _chip({"q1": 1, "q2": 2}, {"q1": 4.1e9, "q2": 4.2e9})
        new = _fresh({"q2": 1, "q1": 2})
        r, qr, _ = _merge(old, new, {"q2": "q1", "q1": "q2"})
        assert r.merged["qubits"]["q2"]["f_01"] == 4.1e9
        assert r.merged["qubits"]["q1"]["f_01"] == 4.2e9
        assert r.stats.residual_lost == []
        assert qr == [("q1", "q2"), ("q2", "q1")]


class TestTheRecordIsCheckedNotTrusted:
    OLD = {"qubits": {"q1": {}, "q2": {}}}

    def test_a_real_rename_is_kept(self):
        assert source_renames({"q0": "q1", "q2": "q2"}, self.OLD, {"q0", "q2"}) == {"q1": "q0"}

    @pytest.mark.parametrize("sources", [
        {"q0": "q9"},                 # no such source qubit
        {"q0": "q1", "q5": "q1"},     # one source claimed twice
        {"q0": "q1", "q1": "q1"},     # ...also when one claim is the identity
        {"q7": "q1"},                 # the build has no q7
        {"q0": 1},                    # not an id
        ["q0", "q1"],                 # not a record
    ])
    def test_a_claim_that_cannot_be_checked_is_dropped(self, sources):
        assert source_renames(sources, self.OLD, {"q0", "q1", "q2", "q5"}) == {}

    def test_no_rename_hands_the_source_back_untouched(self):
        s, w = _chip({"q1": 1}, {"q1": 4.1e9})
        s2, w2, qr, pr = rename_source_qubits(s, w, {})
        assert s2 is s and w2 is w and qr == [] and pr == []

    def test_the_source_is_not_modified(self):
        s, w = _chip({"q1": 1, "q2": 2}, {"q1": 4.1e9, "q2": 4.2e9}, pairs=[("q1", "q2", 0.1)])
        before = json.dumps([s, w], sort_keys=True)
        rename_source_qubits(s, w, {"q1": "q0", "q2": "q1"}, *_fresh({"q0": 1, "q1": 2}))
        assert json.dumps([s, w], sort_keys=True) == before


def _write(folder: Path, state: dict, wiring: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state))
    (folder / "wiring.json").write_text(json.dumps(wiring))
    return folder


class TestRunRegenerateUsesTheRecord:
    def test_the_rebuilt_chip_on_disk_keeps_each_qubits_values(self, tmp_path, monkeypatch):
        src = _write(tmp_path / "old", *_chip({"q1": 1, "q2": 2}, {"q1": 4.1e9, "q2": 4.2e9},
                                             pairs=[("q1", "q2", 0.11)]))
        fresh = _fresh({"q0": 1, "q1": 2}, pairs=[("q0", "q1")])

        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            _write(Path(out_dir), *fresh)
            return {"ok": True, "status": "ok", "error": None, "result": {}}

        monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
        out = regenerate.run_regenerate(
            "py", src, {"qubits": ["q0", "q1"]}, tmp_path / "new",
            qubit_sources={"q0": "q1", "q1": "q2"}, scripts_enabled=False)
        assert out["ok"] is True, out
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        assert merged["qubits"]["q0"]["f_01"] == 4.1e9
        assert merged["qubits"]["q1"]["f_01"] == 4.2e9
        assert merged["qubit_pairs"]["q0-q1"]["macros"]["cz"]["phase_shift_control"] == 0.11
        m = out["merge"]
        assert m["residual_lost"] == []
        assert m["qubits_renamed"] == [{"old": "q1", "new": "q0"}, {"old": "q2", "new": "q1"}]
        assert m["pairs_renamed"] == [{"old": "q1-q2", "new": "q0-q1"}]

    def test_a_populate_edit_on_a_renamed_row_names_its_own_source_value(
            self, tmp_path, monkeypatch):
        # The wizard re-keys its hydration snapshot with the rename, so the
        # edit on q0 is diffed against q0's own source (the old q1); the
        # report's "was" is that value, and the un-edited q1 keeps the old q2's.
        old_s, old_w = _chip({"q1": 1, "q2": 2}, {"q1": 4.1e9, "q2": 4.2e9})
        old_s["qubits"]["q1"]["anharmonicity"] = -200e6
        old_s["qubits"]["q2"]["anharmonicity"] = -210e6
        src = _write(tmp_path / "old", old_s, old_w)
        fresh_s, fresh_w = _fresh({"q0": 1, "q1": 2})
        fresh_s["qubits"]["q0"]["anharmonicity"] = -190e6     # the build applied the edit
        fresh_s["qubits"]["q1"]["anharmonicity"] = -210e6

        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            _write(Path(out_dir), fresh_s, fresh_w)
            return {"ok": True, "status": "ok", "error": None, "result": {}}

        monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
        base = {"qubit": {"q0": {"anharmonicity": -200e6}, "q1": {"anharmonicity": -210e6}}}
        pop = {"qubit": {"q0": {"anharmonicity": -190e6}, "q1": {"anharmonicity": -210e6}}}
        out = regenerate.run_regenerate(
            "py", src, {"qubits": ["q0", "q1"], "populate": pop}, tmp_path / "new",
            populate_baseline=base, populate_touched=[],
            qubit_sources={"q0": "q1", "q1": "q2"}, scripts_enabled=False)
        assert out["ok"] is True, out
        m = out["merge"]
        assert m["populate_protected_paths"] == ["qubits.q0.anharmonicity"]
        assert m["populate_protected_detail"][0]["old"] == -200e6
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        assert merged["qubits"]["q0"]["anharmonicity"] == -190e6
        assert merged["qubits"]["q1"]["anharmonicity"] == -210e6
        assert merged["qubits"]["q1"]["f_01"] == 4.2e9


class TestTheBuildRoutePassesTheRecord:
    @pytest.fixture(autouse=True)
    def _all_capabilities(self, monkeypatch):
        from quam_state_manager.core import config_generator
        from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
        manifest = {
            "ok": True, "cached": False, "error": None, "versions": {},
            "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS},
        }
        monkeypatch.setattr(config_generator, "probe_capabilities",
                            lambda *a, **k: manifest)

    def test_qubit_sources_reach_run_regenerate(self, tmp_path, monkeypatch):
        from quam_state_manager.web.app import create_app
        from tests.test_web import _gen_valid_spec

        client = create_app(testing=True,
                            instance_path=str(tmp_path / "_inst")).test_client()
        got = {}

        def fake_run(py, src, spec, out, timeout=300, **kw):
            got.clear()
            got.update(kw)
            return {"ok": True, "status": "ok", "error": None, "merge": None}

        monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
        client.post("/generate/select-env", json={"python": sys.executable})
        (tmp_path / "src").mkdir()
        base = {"spec": _gen_valid_spec(), "output_path": str(tmp_path / "out"),
                "source_folder": str(tmp_path / "src")}
        resp = client.post("/regenerate/build",
                           json={**base, "qubit_sources": {"q0": "q1"}})
        assert resp.status_code == 200, resp.get_json()
        assert got["qubit_sources"] == {"q0": "q1"}
        client.post("/regenerate/build", json=base)        # an older client
        assert got["qubit_sources"] is None


def test_a_renamed_rows_fsp_change_still_raises_its_offer(tmp_path):
    # the build route asks about a changed port FSP BEFORE building; the row is
    # q0 now, its port is found through the source qubit it was (q1)
    from tests.test_regen_fsp_compensation import _chip as _fsp_chip

    state, wiring = _fsp_chip(fsp=0)
    _write(tmp_path, state, wiring)
    spec = {"qubits": ["q0"], "populate": {"qubit": {"q0": {"full_scale_power_dbm": -6}}}}
    base = {"qubit": {"q0": {"full_scale_power_dbm": 0}}}
    pend = regenerate.pending_fsp_offers(tmp_path, spec, base, [], None,
                                         qubit_sources={"q0": "q1"})
    assert len(pend) == 1 and pend[0]["fsp_new"] == -6.0
    assert pend[0]["amps"]


class TestEveryPlaceAQubitIsNamed:
    """Measured on a real 21-qubit rebuild: after the keys and pointers, a
    qubit's id was still in each channel's ``thread``, in the TWPAs' plain-name
    ``qubits`` lists, and inside the lab's per-partner pulse names."""

    def _old(self):
        s, w = _chip({"q1": 1, "q2": 2, "q3": 3},
                     {"q1": 4.1e9, "q2": 4.2e9, "q3": 4.3e9},
                     pairs=[("q2", "q1", 0.21), ("q2", "q3", 0.23)])
        for q in ("q1", "q2", "q3"):
            s["qubits"][q]["xy"]["thread"] = q
        z2 = s["qubits"]["q2"]["z"]
        z2["operations"] = {"cz_SNZ_flux_pulse_q2_q1": {"amplitude": 0.31},
                            "cz_SNZ_flux_pulse_q2_q3": {"amplitude": 0.33},
                            "cz_flattop_pulse_q1": "#./cz_SNZ_flux_pulse_q2_q1"}
        s["qubit_pairs"]["q2-q1"]["macros"]["cz"]["flux_pulse"] = (
            "#/qubits/q2/z/operations/cz_SNZ_flux_pulse_q2_q1")
        s["qubit_pairs"]["q2-q1"]["extras"] = {"q2-q1-q3": "q1 then q3", "note": "q1"}
        s["twpas"] = {"twpaA": {"qubits": ["q1", "q2", "q3"]},
                      "twpaB": {"qubits": ["#/qubits/q3"]}}
        return s, w

    def test_ids_inside_names_strings_and_lists_follow(self):
        s, w = self._old()
        ren = {"q1": "q0", "q2": "q1", "q3": "q2"}
        new = _fresh({"q0": 1, "q1": 2, "q2": 3}, pairs=[("q1", "q0"), ("q1", "q2")])
        s2, _, _, pr = rename_source_qubits(s, w, ren, *new)
        assert [s2["qubits"][q]["xy"]["thread"] for q in ("q0", "q1", "q2")] == ["q0", "q1", "q2"]
        ops = s2["qubits"]["q1"]["z"]["operations"]
        assert set(ops) == {"cz_SNZ_flux_pulse_q1_q0", "cz_SNZ_flux_pulse_q1_q2",
                            "cz_flattop_pulse_q0"}
        assert ops["cz_flattop_pulse_q0"] == "#./cz_SNZ_flux_pulse_q1_q0"
        cz = s2["qubit_pairs"]["q1-q0"]["macros"]["cz"]
        assert cz["flux_pulse"] == "#/qubits/q1/z/operations/cz_SNZ_flux_pulse_q1_q0"
        assert s2["twpas"]["twpaA"]["qubits"] == ["q0", "q1", "q2"]
        assert s2["twpas"]["twpaB"]["qubits"] == ["#/qubits/q2"]
        # extras is free-form: kept verbatim
        assert s2["qubit_pairs"]["q1-q0"]["extras"] == {"q2-q1-q3": "q1 then q3", "note": "q1"}
        assert pr == [("q2-q1", "q1-q0"), ("q2-q3", "q1-q2")]

    def test_a_merged_rebuild_carries_the_lab_pulse_onto_the_builders_name(self):
        # the builder names the renamed pair's pulse after the NEW ids; the
        # lab's calibrated one now lands on it instead of beside it
        s, w = self._old()
        ns, nw = _fresh({"q0": 1, "q1": 2, "q2": 3}, pairs=[("q1", "q0"), ("q1", "q2")])
        ns["qubits"]["q1"]["z"]["operations"] = {
            "cz_SNZ_flux_pulse_q1_q0": {"amplitude": 0.0}}
        r, _, _ = _merge((s, w), (ns, nw), {"q0": "q1", "q1": "q2", "q2": "q3"})
        ops = r.merged["qubits"]["q1"]["z"]["operations"]
        assert ops["cz_SNZ_flux_pulse_q1_q0"]["amplitude"] == 0.31
        assert "cz_SNZ_flux_pulse_q2_q1" not in ops

    def test_a_removed_partners_pulse_cannot_take_a_renamed_name(self):
        # q1 removed; q2, q3 renamed to q1, q2. q2's pulse for the removed q1
        # must not become the pulse "for q1" -- that is now another qubit
        s, w = self._old()
        ren = {"q2": "q1", "q3": "q2"}
        s2, _, _, _ = rename_source_qubits(s, w, ren, *_fresh({"q1": 2, "q2": 3}))
        ops = s2["qubits"]["q1"]["z"]["operations"]
        assert "cz_SNZ_flux_pulse_q1_q1_removed" in ops
        assert ops["cz_SNZ_flux_pulse_q1_q2"]["amplitude"] == 0.33
        assert s2["twpas"]["twpaA"]["qubits"] == ["q1_removed", "q1", "q2"]

    def test_an_id_inside_a_word_is_left_alone(self):
        s, w = _chip({"q1": 1}, {"q1": 4.1e9})
        s["qubits"]["q1"]["xy"]["operations"]["xq1_q10"] = {"amplitude": 0.5}
        s2, _, _, _ = rename_source_qubits(s, w, {"q1": "q0"})
        assert "xq1_q10" in s2["qubits"]["q0"]["xy"]["operations"]


class TestReviewFindings:
    """An adversarial review of the first cut (docs/295 §Review) reproduced each
    of these; every one was silent."""

    def test_a_lab_macro_plays_the_pulse_its_id_names_after_a_swap(self):
        # the CZ macro plays `pulse.id`, a STRING naming an operation key: a
        # swap that renamed the keys but not the id made pair q3-q2 play the
        # other pair's pulse
        s, w = _chip({"q1": 1, "q2": 2, "q3": 3}, {"q1": 4.1e9, "q2": 4.2e9, "q3": 4.3e9},
                     pairs=[("q3", "q1", 0.0), ("q3", "q2", 0.0)])
        s["qubits"]["q3"]["z"]["operations"] = {
            "cz_unipolar_pulse_q1": {"id": "cz_unipolar_pulse_q1", "amplitude": 0.11},
            "cz_unipolar_pulse_q2": {"id": "cz_unipolar_pulse_q2", "amplitude": 0.22}}
        for t in ("q1", "q2"):
            s["qubit_pairs"][f"q3-{t}"]["macros"]["cz"]["pulse_id"] = f"cz_unipolar_pulse_{t}"
        new = _fresh({"q2": 1, "q1": 2, "q3": 3}, pairs=[("q3", "q2"), ("q3", "q1")])
        s2, _, _, _ = rename_source_qubits(s, w, {"q1": "q2", "q2": "q1"}, *new)
        ops = s2["qubits"]["q3"]["z"]["operations"]
        for pid in ("q3-q1", "q3-q2"):
            pulse = s2["qubit_pairs"][pid]["macros"]["cz"]["pulse_id"]
            assert ops[pulse]["id"] == pulse
        # pair q3-q2 is the old q3-q1: it plays the old q3-q1's 0.11
        assert ops[s2["qubit_pairs"]["q3-q2"]["macros"]["cz"]["pulse_id"]]["amplitude"] == 0.11

    def test_a_new_pair_never_inherits_an_old_pair_id(self):
        # shift q1,q2,q3 -> q0,q1,q2; the old q1-q2 (now q0-q1) is deleted and
        # a NEW q1-q2 (the old q2,q3, never a pair) is added: the old id now
        # names a different pair, which must start uncalibrated
        old = _chip({"q1": 1, "q2": 2, "q3": 3}, {"q1": 4.1e9, "q2": 4.2e9, "q3": 4.3e9},
                    pairs=[("q1", "q2", 0.11)])
        new = _fresh({"q0": 1, "q1": 2, "q2": 3}, pairs=[("q1", "q2")])
        r, _, pr = _merge(old, new, {"q0": "q1", "q1": "q2", "q2": "q3"})
        assert r.merged["qubit_pairs"]["q1-q2"]["macros"]["cz"]["phase_shift_control"] == 0.0
        assert "qubit_pairs.q1-q2 (source).macros.cz.phase_shift_control" in r.stats.residual_lost
        assert pr == []

    def test_a_renamed_pair_rebuilt_reversed_is_reported_as_the_source_pair(self):
        old = _chip({"q1": 1, "q2": 2}, {"q1": 4.1e9, "q2": 4.2e9}, pairs=[("q1", "q2", 0.11)])
        new = _fresh({"q0": 1, "q1": 2}, pairs=[("q1", "q0")])
        r, _, _ = _merge(old, new, {"q0": "q1", "q1": "q2"})
        assert r.stats.pairs_reversed == [("q1-q2 (source)", "q1-q0")]
        assert r.merged["qubit_pairs"]["q1-q0"]["macros"]["cz"]["phase_shift_control"] == 0.0

    def test_an_id_with_an_underscore_is_its_own_qubit(self):
        # q1 -> q5 only; q1_b and q5_b are other qubits and keep their values
        old = _chip({"q1": 1, "q1_b": 2, "q5_b": 3}, {"q1": 4.1e9, "q1_b": 4.2e9, "q5_b": 4.3e9})
        new = _fresh({"q5": 1, "q1_b": 2, "q5_b": 3})
        r, _, _ = _merge(old, new, {"q5": "q1", "q1_b": "q1_b", "q5_b": "q5_b"})
        q = r.merged["qubits"]
        assert (q["q5"]["f_01"], q["q1_b"]["f_01"], q["q5_b"]["f_01"]) == (4.1e9, 4.2e9, 4.3e9)
        assert r.stats.residual_lost == []

    def test_a_pointer_kept_under_extras_follows_the_renamed_operation(self):
        s, w = _chip({"q1": 1, "q2": 2}, {"q1": 4.1e9, "q2": 4.2e9})
        s["qubits"]["q2"]["z"]["operations"] = {"cz_flux_q2_q1": {"amplitude": 0.3}}
        s["extras"] = {"best": "#/qubits/q2/z/operations/cz_flux_q2_q1", "note": "q2"}
        s2, _, _, _ = rename_source_qubits(s, w, {"q1": "q0", "q2": "q1"}, *_fresh({"q0": 1, "q1": 2}))
        assert s2["extras"] == {"best": "#/qubits/q1/z/operations/cz_flux_q1_q0", "note": "q2"}


def test_source_drift_compares_each_row_with_its_own_source(tmp_path):
    # the wizard's baseline is in the renamed ids; an unrelated save to the
    # source used to light up every renamed row as "changed elsewhere"
    from quam_state_manager.core import regen_populate
    from tests.test_web import _make_state, _make_wiring

    chip = _write(tmp_path / "src", _make_state(), _make_wiring())
    rec = regenerate.reconstruct_from_folder(chip)
    qs = list(rec.spec["qubits"])
    idmap = {q: f"q{i}" for i, q in enumerate(qs)}
    sources = {n: o for o, n in idmap.items()}

    def renamed(view):
        out = {}
        for g, rows in view.items():
            if g == "pairs":
                out[g] = {"-".join(idmap.get(m, m) for m in k.split("-", 1)): v
                          for k, v in rows.items()}
            elif g in ("qubit", "resonator", "flux", "pulses", "qdac"):
                out[g] = {idmap.get(k, k): v for k, v in rows.items()}
            else:
                out[g] = rows
        return out

    base = renamed(regen_populate.populate_view(rec.spec))
    spec = dict(rec.spec, qubits=[idmap[q] for q in qs])
    state = json.loads((chip / "state.json").read_text(encoding="utf-8"))
    state.setdefault("extras", {})["note"] = "an unrelated save"
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    assert regenerate.source_drift(chip, rec.source_hash, base, spec=spec,
                                   qubit_sources=sources) == []
    state["qubits"][qs[0]]["anharmonicity"] = -190e6
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    drift = regenerate.source_drift(chip, rec.source_hash, base, spec=spec,
                                    qubit_sources=sources)
    # without the record the source's qA1 never meets the wizard's q0: missed
    assert regenerate.source_drift(chip, rec.source_hash, base, spec=spec) == []
    assert [(d["group"], d["id"], d["field"], d["now"]) for d in drift] == [
        ("qubit", "q0", "anharmonicity", -190e6)]
