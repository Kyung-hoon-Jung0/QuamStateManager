"""A qubit renamed by Re-generate keeps its history (docs/296): the record a
rebuild writes, the era of a saved state, and translation between eras."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import regenerate, rename_lineage
from quam_state_manager.core.regen_merge import (
    rebuilt_pairs, rename_plan, rename_source_qubits, source_renames)
from quam_state_manager.core.rename_lineage import Lineage, Step


def _chip(qubits: dict, pairs=(), extras=None):
    """``qubits`` ``{id: z port}``; ``pairs`` ``[(control, target)]``, members
    referenced through wiring (the modern two-hop form)."""
    qs, wq = {}, {}
    for q, p in qubits.items():
        qs[q] = {"id": q, "f_01": 4.0e9 + p * 1e8,
                 "z": {"opx_output": f"#/wiring/qubits/{q}/z/opx_output"},
                 "xy": {"operations": {"x180": {"amplitude": 0.1 * p},
                                       "x90": f"#/qubits/{q}/xy/operations/x180"}}}
        wq[q] = {"z": {"opx_output": f"#/ports/analog_outputs/con1/5/{p}"}}
    qp, wp = {}, {}
    for c, t in pairs:
        pid = f"{c}-{t}"
        qp[pid] = {"id": pid,
                   "qubit_control": f"#/wiring/qubit_pairs/{pid}/control_qubit",
                   "qubit_target": f"#/wiring/qubit_pairs/{pid}/target_qubit",
                   "macros": {"cz": {"flux_pulse_control": f"#/qubits/{c}/z/operations/cz_flux_pulse_{c}_{t}"}}}
        qs[c]["z"].setdefault("operations", {})[f"cz_flux_pulse_{c}_{t}"] = {"amplitude": 0.2}
        wp[pid] = {"control_qubit": f"#/qubits/{c}", "target_qubit": f"#/qubits/{t}"}
    state = {"qubits": qs, "qubit_pairs": qp, "active_qubit_names": list(qubits)}
    if extras is not None:
        state["extras"] = extras
    return state, {"wiring": {"qubits": wq, "qubit_pairs": wp}, "network": {}}


def _record(old, new, sources):
    (os_, ow), (ns, nw) = old, new
    ren = source_renames(sources, os_, ns["qubits"])
    tok, pmap, ids = rename_plan(os_, ow, ren, ns, nw)
    return rename_lineage.new_record(
        renames=ren, tokens=tok, pairs=pmap, source_qubits=ids,
        source_pairs=list(os_.get("qubit_pairs") or {}),
        qubits_after=list(ns["qubits"]), pairs_after=rebuilt_pairs(ns, nw))


SHIFT_OLD = ({"q1": 1, "q2": 2, "q3": 3}, [("q1", "q2"), ("q2", "q3")])
SHIFT_NEW = ({"q0": 1, "q1": 2, "q2": 3}, [("q0", "q1"), ("q1", "q2")])
SHIFT = {"q0": "q1", "q1": "q2", "q2": "q3"}


@pytest.fixture
def shift():
    old, new = _chip(*SHIFT_OLD), _chip(*SHIFT_NEW)
    return old, new, _record(old, new, SHIFT)


class TestTheRecord:
    def test_a_record_is_valid_json_and_names_the_renames(self, shift):
        rec = shift[2]
        assert rename_lineage.valid(json.loads(json.dumps(rec)))
        assert rec["qubits"] == {"q1": "q0", "q2": "q1", "q3": "q2"}
        assert rec["pairs"] == {"q1-q2": "q0-q1", "q2-q3": "q1-q2"}

    def test_a_states_era_is_the_ids_of_its_records(self, shift):
        rec = shift[2]
        state = rename_lineage.with_chain({"qubits": {}}, [rec])
        assert rename_lineage.era(state) == (rec["id"],)
        assert rename_lineage.era({"qubits": {}}) == ()

    def test_a_broken_entry_ends_the_chain_without_raising(self, shift):
        rec = shift[2]
        state = {"extras": {"qubit_renames": [rec, {"id": 3}, rec]}}
        assert rename_lineage.era(state) == (rec["id"],)
        assert rename_lineage.era({"extras": {"qubit_renames": "x"}}) == ()
        assert rename_lineage.era({"extras": []}) == ()

    @pytest.mark.parametrize("state,schemas,ok", [
        ({"extras": {}}, None, True),
        ({"__class__": "a.Quam"}, {"a.Quam": ["qubits", "extras"]}, True),
        ({"__class__": "a.Quam"}, {"a.Quam": ["qubits"]}, False),
        ({"__class__": "a.Quam"}, None, False),
    ])
    def test_the_record_is_written_only_where_the_root_class_keeps_extras(self, state, schemas, ok):
        assert rename_lineage.can_mark(state, schemas) is ok


class TestOneStep:
    def test_the_state_translation_is_the_rebuilds_own_rule(self, shift):
        old, new, rec = shift
        ren = source_renames(SHIFT, old[0], new[0]["qubits"])
        want = rename_source_qubits(old[0], old[1], ren, new[0], new[1])[:2]
        assert Step(rec).fwd_state(*old) == want

    def test_a_path_after_the_shift_reads_its_own_qubit_before_it(self, shift):
        s = Step(shift[2])
        assert s.back_path("qubits.q0.f_01") == "qubits.q1.f_01"
        assert s.back_path("qubits.q1.f_01") == "qubits.q2.f_01"
        assert s.back_path("wiring.qubits.q2.z.opx_output") == "wiring.qubits.q3.z.opx_output"
        assert s.back_path("qubit_pairs.q0-q1.macros.cz.flux_pulse_control") == \
            "qubit_pairs.q1-q2.macros.cz.flux_pulse_control"
        assert s.back_path("qubits.q0.z.operations.cz_flux_pulse_q0_q1.amplitude") == \
            "qubits.q1.z.operations.cz_flux_pulse_q1_q2.amplitude"

    def test_forward_is_the_inverse_on_every_rebuilt_path(self, shift):
        s = Step(shift[2])
        for path in ("qubits.q0.f_01", "qubits.q2.xy.operations.x180.amplitude",
                     "qubit_pairs.q1-q2.id", "wiring.qubit_pairs.q0-q1.control_qubit",
                     "qubits.q1.z.operations.cz_flux_pulse_q1_q2.amplitude"):
            assert s.fwd_path(s.back_path(path)) == path

    def test_extras_below_the_root_stay_verbatim(self, shift):
        s = Step(shift[2])
        assert s.back_path("extras.q0.note") == "extras.q0.note"
        assert s.back_path("qubits.q0.extras.q1") == "qubits.q1.extras.q1"

    def test_a_new_qubit_under_a_renamed_away_id_has_no_history_before(self):
        # q1 -> q0, and a brand-new qubit is added as q1
        old = _chip({"q1": 1, "q2": 2})
        new = _chip({"q0": 1, "q1": 7, "q2": 2})
        s = Step(_record(old, new, {"q0": "q1", "q2": "q2"}))
        assert s.back_path("qubits.q0.f_01") == "qubits.q1.f_01"
        assert s.back_path("qubits.q1.f_01") is None
        assert s.back_path("qubits.q2.f_01") == "qubits.q2.f_01"

    def test_a_swap_reads_each_qubits_own_values(self):
        old = _chip({"q1": 1, "q2": 2}, [("q1", "q2")])
        new = _chip({"q1": 2, "q2": 1}, [("q2", "q1")])
        s = Step(_record(old, new, {"q1": "q2", "q2": "q1"}))
        assert s.back_path("qubits.q1.f_01") == "qubits.q2.f_01"
        assert s.back_path("qubits.q2.f_01") == "qubits.q1.f_01"
        assert s.back_path("qubit_pairs.q2-q1.id") == "qubit_pairs.q1-q2.id"

    def test_a_pair_the_source_did_not_have_has_no_history_before(self):
        # the source's q1-q2 is renamed away; the rebuild adds a NEW q1-q2
        old = _chip({"q1": 1, "q2": 2, "q3": 3}, [("q1", "q2")])
        new = _chip({"q0": 1, "q1": 2, "q2": 3}, [("q0", "q1"), ("q1", "q2")])
        s = Step(_record(old, new, SHIFT))
        assert s.back_path("qubit_pairs.q0-q1.id") == "qubit_pairs.q1-q2.id"
        assert s.back_path("qubit_pairs.q1-q2.id") is None

    def test_an_older_state_with_a_qubit_the_source_lacked_cannot_collide(self, shift):
        # a snapshot from before the source: it still had a q0, removed since
        old_q0 = _chip({"q0": 9, "q1": 1, "q2": 2, "q3": 3}, [("q0", "q1"), ("q1", "q2"), ("q2", "q3")])
        state, wiring = Step(shift[2]).fwd_state(*old_q0)
        assert state["qubits"]["q0"]["f_01"] == old_q0[0]["qubits"]["q1"]["f_01"]
        assert "q0_removed" in state["qubits"] or "q0_stale" in state["qubits"]
        # the old q0-q1 is not the rebuilt q0-q1 (old q1-q2)
        assert state["qubit_pairs"]["q0-q1"]["id"] == "q0-q1"
        assert state["qubit_pairs"]["q0-q1"]["qubit_control"].endswith("/control_qubit")
        assert wiring["wiring"]["qubit_pairs"]["q0-q1"]["control_qubit"] == "#/qubits/q0"
        assert wiring["wiring"]["qubit_pairs"]["q0-q1"]["target_qubit"] == "#/qubits/q1"
        assert len(state["qubit_pairs"]) == 3


class TestBetweenEras:
    def test_two_renames_in_a_row_read_back_to_the_first_name(self, shift):
        old, new, r1 = shift
        newer = _chip({"qa": 1, "qb": 2, "qc": 3}, [("qa", "qb"), ("qb", "qc")])
        r2 = _record(new, newer, {"qa": "q0", "qb": "q1", "qc": "q2"})
        lin = Lineage([r1, r2])
        e0, e1, e2 = (), (r1["id"],), (r1["id"], r2["id"])
        assert lin.path("qubits.qa.f_01", e2, e0) == "qubits.q1.f_01"
        assert lin.path("qubits.q1.f_01", e0, e2) == "qubits.qa.f_01"
        assert lin.path("qubits.qb.f_01", e2, e1) == "qubits.q1.f_01"
        assert lin.qubit("qc", e2, e0) == "q3"
        assert lin.pair("qa-qb", e2, e0) == "q1-q2"
        assert lin.label(e0, e2)[1]["qubits"] == {"q0": "qa", "q1": "qb", "q2": "qc"}

    def test_an_unknown_record_translates_to_nothing(self, shift):
        lin = Lineage([shift[2]])
        assert lin.path("qubits.q0.f_01", ("nope",), ()) is None
        assert lin.path("qubits.q0.f_01", (), ()) == "qubits.q0.f_01"

    def test_a_state_moves_forward_and_carries_the_chain(self, shift):
        old, _new, rec = shift
        lin = Lineage([rec])
        state, _w = lin.forward_state(old[0], old[1], (rec["id"],))
        assert rename_lineage.era(state) == (rec["id"],)
        assert state["qubits"]["q0"]["f_01"] == old[0]["qubits"]["q1"]["f_01"]
        # never backward
        assert lin.forward_state(state, _w, ()) is None


class TestTheChipRegistry:
    def test_a_record_is_remembered_once(self, tmp_path, shift):
        rec = shift[2]
        assert rename_lineage.remember(tmp_path, [rec]) == [rec]
        mtime = (tmp_path / rename_lineage.REGISTRY_NAME).stat().st_mtime_ns
        assert rename_lineage.remember(tmp_path, [rec]) == [rec]
        assert (tmp_path / rename_lineage.REGISTRY_NAME).stat().st_mtime_ns == mtime

    def test_a_state_restored_to_before_the_rename_still_reads_the_newer_era(self, tmp_path, shift):
        rec = shift[2]
        rename_lineage.remember(tmp_path, [rec])
        lin = rename_lineage.lineage_for(tmp_path, {"qubits": {}})
        assert lin.path("qubits.q1.f_01", (), (rec["id"],)) == "qubits.q0.f_01"


def _write(folder: Path, state, wiring):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state))
    (folder / "wiring.json").write_text(json.dumps(wiring))
    return folder


class TestTheRebuildWritesTheRecord:
    def _run(self, tmp_path, monkeypatch, extras, sources):
        old = _chip(*SHIFT_OLD, extras=extras)
        src = _write(tmp_path / "old", *old)
        fresh = _chip(*SHIFT_NEW)

        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            _write(Path(out_dir), *fresh)
            return {"ok": True, "status": "ok", "error": None, "result": {}}

        monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
        out = regenerate.run_regenerate("py", src, {"qubits": list(SHIFT_NEW[0])}, tmp_path / "new",
                                        qubit_sources=sources, scripts_enabled=False)
        assert out["ok"] is True, out
        return out, json.loads((tmp_path / "new" / "state.json").read_text())

    def test_a_renamed_rebuild_carries_its_record(self, tmp_path, monkeypatch):
        out, merged = self._run(tmp_path, monkeypatch, {"chip_name": "c"}, SHIFT)
        chain = rename_lineage.records(merged)
        assert [r["id"] for r in chain] == [out["merge"]["rename_record"]]
        assert chain[0]["qubits"] == {"q1": "q0", "q2": "q1", "q3": "q2"}
        assert merged["extras"]["chip_name"] == "c"
        assert out["merge"]["rename_unmarked"] is False
        # the record re-expresses the source exactly as the rebuild did
        assert merged["qubits"]["q0"]["f_01"] == 4.1e9

    def test_a_rebuild_without_a_rename_adds_nothing(self, tmp_path, monkeypatch):
        old = _chip(*SHIFT_OLD, extras={"chip_name": "c"})
        src = _write(tmp_path / "old", *old)

        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            _write(Path(out_dir), *_chip(*SHIFT_OLD))
            return {"ok": True, "status": "ok", "error": None, "result": {}}

        monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
        out = regenerate.run_regenerate("py", src, {"qubits": list(SHIFT_OLD[0])}, tmp_path / "new",
                                        scripts_enabled=False)
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        assert "qubit_renames" not in merged["extras"]
        assert out["merge"]["rename_record"] is None

    def test_a_second_rename_appends_to_the_chain(self, tmp_path, monkeypatch):
        first = _record(_chip(*SHIFT_OLD), _chip(*SHIFT_NEW), SHIFT)
        old = _chip(*SHIFT_OLD, extras={"qubit_renames": [first]})
        src = _write(tmp_path / "old", *old)
        fresh = _chip(*SHIFT_NEW)

        def fake_build(python_path, mode, spec, out_dir, timeout=300):
            _write(Path(out_dir), *fresh)
            return {"ok": True, "status": "ok", "error": None, "result": {}}

        monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
        out = regenerate.run_regenerate("py", src, {"qubits": list(SHIFT_NEW[0])}, tmp_path / "new",
                                        qubit_sources=SHIFT, scripts_enabled=False)
        merged = json.loads((tmp_path / "new" / "state.json").read_text())
        assert rename_lineage.era(merged) == (first["id"], out["merge"]["rename_record"])

    def test_a_root_without_extras_is_reported_not_marked(self, tmp_path, monkeypatch):
        out, merged = self._run(tmp_path, monkeypatch, None, SHIFT)
        assert "extras" not in merged
        assert out["merge"]["rename_unmarked"] is True
        assert out["merge"]["rename_record"] is None


def test_a_pair_new_in_the_rebuild_has_no_name_before_it():
    old = _chip({"q1": 1, "q2": 2, "q3": 3}, [("q1", "q2")])
    new = _chip({"q0": 1, "q1": 2, "q2": 3}, [("q0", "q1"), ("q1", "q2")])
    s = Step(_record(old, new, SHIFT))
    assert "q1-q2" in s.after_p and "q1-q2" not in s.inv_p
    assert s.back_pair("q1-q2") is None
    old2 = _chip({"q1": 1, "q2": 2, "q5": 5, "q6": 6}, [("q1", "q2")])
    new2 = _chip({"q0": 1, "q1": 2, "q5": 5, "q6": 6}, [("q0", "q1"), ("q5", "q6")])
    s2 = Step(_record(old2, new2, {"q0": "q1", "q1": "q2", "q5": "q5", "q6": "q6"}))
    assert s2.back_pair("q5-q6") is None, "a pair the source did not have has no history"


def test_an_unreadable_registry_is_never_rewritten_from_nothing(tmp_path, shift):
    rec = shift[2]
    reg = tmp_path / rename_lineage.REGISTRY_NAME
    reg.write_text("{torn", encoding="utf-8")
    got = rename_lineage.remember(tmp_path, [rec])
    assert [r["id"] for r in got] == [rec["id"]], "the caller still gets the record it holds"
    assert reg.read_text(encoding="utf-8") == "{torn", "the file it could not read is left as it was"
