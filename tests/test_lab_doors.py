"""Every editing door asks the LAB's own code -- pulse class AND gate class.

2026-09-27 verifier (out_verify_pulsecreate2): after /field/edit, -batch and
/pulse/edit were checked,

* the pair / qubit inspector forms (/pair/<n>/edit, /qubit/<n>/edit) and the
  Json Tree's ＋ (/field/create, incl. a whole new lab-class dict) still wrote
  values the class refuses;
* an env that cannot import the class refused EVERY edit ("could not import");
* the lab's GATE class was never asked: CZGateTwoFlux.apply() ->
  assert_lines_compatible() needs control and target flat_length equal, and the
  branch's own journey left 82 vs 74 -- every CZ node on that pair then failed;
* two coupled edits in flight were each checked against the OLD state;
* a tray ✕ of one half of a coupled pair restored a refused combination
  silently; a lab field re-linked to a container / nowhere landed.

The class's code is replaced here by a fake ``lab_waveform.draw`` that
answers like the real worker: pulse items by their fields, gate items
(``@macro:<root>``) by comparing the two pulses' flat_length inside the
contents it is sent -- so these pins also check WHAT the gate check is sent.
"""

from __future__ import annotations

import copy
import json
import threading
import time

import pytest

from quam_state_manager.web.app import create_app
from tests.test_pulses_routes import _make_wiring

LAB = "mylab.g.NZPulse"
GATE = "mylab.g.CZTwo"
PAIR = "q1-q2"
G = f"qubit_pairs.{PAIR}.macros.czl"
FL_C = f"{G}.flux_pulse_qubit.flat_length"
FL_T = f"{G}.flux_pulse_target.flat_length"
ENV = r"C:\lab\env\python.exe"


def _state() -> dict:
    nz = {"__class__": LAB, "amplitude": 0.15, "flat_length": 32, "padding": 4}
    return {
        "__class__": "mylab.root.Quam",
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.2e9,
                   "z": {"operations": {
                       "czl_q1": {"__class__": LAB,
                                  "amplitude": f"#/{G.replace('.', '/')}/flux_pulse_qubit/amplitude",
                                  "flat_length": f"#/{G.replace('.', '/')}/flux_pulse_qubit/flat_length",
                                  "padding": f"#/{G.replace('.', '/')}/flux_pulse_qubit/padding"},
                       "other_pair_pulse": "#/qubit_pairs/q2-q3/macros/czl/flux_pulse_qubit"}},
                   "xy": {"operations": {}}},
            "q2": {"id": "q2", "f_01": 4.9e9, "z": {"operations": {}},
                   "xy": {"operations": {}}},
            "q3": {"id": "q3", "f_01": 4.7e9, "z": {"operations": {}},
                   "xy": {"operations": {}}},
        },
        "qubit_pairs": {
            PAIR: {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                   "macros": {"czl": {"__class__": GATE,
                                      "flux_pulse_qubit": copy.deepcopy(nz),
                                      "flux_pulse_target": copy.deepcopy(nz)}}},
            "q2-q3": {"qubit_control": "#/qubits/q2", "qubit_target": "#/qubits/q3",
                      "macros": {"czl": {"__class__": GATE,
                                         "flux_pulse_qubit": copy.deepcopy(nz),
                                         "flux_pulse_target": copy.deepcopy(nz)}}},
        },
    }


def _walk(d, dotted):
    for seg in dotted.split("."):
        d = d[seg]
    return d


class FakeLab:
    """The worker's answers: a pulse refuses an odd flat_length, a negative
    padding, and (flat_length=26 with filter=40); a gate refuses unequal
    control/target flat_length or padding."""

    def __init__(self):
        self.calls = []
        self.gate_calls = []
        self.unavailable = False
        self.delay = 0.0

    def draw(self, py, items, spawn=True):
        self.calls.append((py, items))
        out = []
        for qclass, params in items:
            if self.unavailable:
                out.append({"ok": False, "reason": "class-unavailable",
                            "error": f"could not import {qclass}: ModuleNotFoundError"})
                continue
            if qclass.startswith("@macro:"):
                self.gate_calls.append(params)
                out.append(self._gate(params))
                continue
            if self.delay:
                time.sleep(self.delay)
            fl, pad = params.get("flat_length"), params.get("padding", 0)
            if isinstance(fl, int) and fl % 2:
                out.append({"ok": False, "error": f"ValueError: flat_length={fl} must be even"})
            elif isinstance(pad, (int, float)) and pad < 0:
                out.append({"ok": False, "error": "ValueError: padding must be non-negative"})
            elif fl == 26 and params.get("gaussian_filter_frequency_mhz") == 40:
                out.append({"ok": False, "error": "ValueError: half the flat length is below 6*sigma"})
            else:
                out.append({"ok": True, "i": [0.0], "q": None})
        return out

    @staticmethod
    def _gate(params):
        contents = params["contents"]
        res = {"ok": True, "macros": {}, "error": None}
        for mp in params["macros"]:
            try:
                mac = _walk(contents, mp)
                a, b = mac["flux_pulse_qubit"], mac["flux_pulse_target"]

                def knob_of(p, k):          # quam resolves an absolute link
                    v = p.get(k, 0)
                    if isinstance(v, str) and v.startswith("#/"):
                        v = _walk(contents, v[2:].replace("/", "."))
                    return v
                for knob in ("flat_length", "padding"):
                    va, vb = knob_of(a, knob), knob_of(b, knob)
                    if va != vb:
                        raise ValueError(f"control/target {knob} differ ({va} vs {vb})")
                res["macros"][mp] = None
            except Exception as exc:  # noqa: BLE001
                res["macros"][mp] = f"{type(exc).__name__}: {exc}"
                res["ok"] = False
                res["error"] = res["error"] or f"{mp}.apply(): {exc}"
        return res


@pytest.fixture
def lab(tmp_path, monkeypatch):
    from quam_state_manager.core import config_generator, lab_waveform, pulse_catalog
    pulse_catalog.apply_env_overlay(None)
    pulse_catalog.apply_chip_classes({LAB: {
        "importable": True, "canonical": LAB,
        "bases": ["quam.components.pulses.Pulse"],
        "fields": {"amplitude": {"type": {"base": "float"}, "has_default": False},
                   "flat_length": {"type": {"base": "int"}, "has_default": False},
                   "padding": {"type": {"base": "int"}, "has_default": True}}}})
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    c._app = app
    fake = FakeLab()
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: ENV)
    monkeypatch.setattr(lab_waveform, "draw", fake.draw)
    yield c, fake
    pulse_catalog.apply_chip_classes(None)


def _store(c):
    return next(iter(c._app.config["contexts"].values()))["store"]


def _v(c, path):
    return _walk(_store(c).merged, path)


# ------------------------------------------------------------------ MAJOR 1
class TestTheInspectorFormsAreDoorsToo:
    def test_the_pair_inspector_refuses_what_the_class_refuses(self, lab):
        c, fake = lab
        n = len(_store(c).change_log)
        r = c.post(f"/pair/{PAIR}/edit", data={"dot_path": FL_C, "value": "9"})
        assert r.status_code == 400, r.data[:300]
        assert b"must be even" in r.data and b"nothing was written" in r.data
        assert _v(c, FL_C) == 32 and len(_store(c).change_log) == n

    def test_the_qubit_inspector_refuses_through_a_pointer(self, lab):
        c, fake = lab
        # the qubit's op field is a pointer INTO the gate's pulse: the write
        # lands there, and the class is asked about the value it would hold
        r = c.post("/qubit/q1/edit", data={
            "dot_path": "qubits.q1.z.operations.czl_q1.padding", "value": "-3"})
        assert r.status_code == 400, r.data[:300]
        assert b"non-negative" in r.data
        assert _v(c, f"{G}.flux_pulse_qubit.padding") == 4

    def test_the_inspector_writes_what_the_class_and_gate_accept(self, lab):
        c, fake = lab
        r = c.post(f"/pair/{PAIR}/edit", data={
            "dot_path": f"{G}.flux_pulse_qubit.amplitude", "value": "0.2"})
        assert r.status_code == 200, r.data[:300]
        assert _v(c, f"{G}.flux_pulse_qubit.amplitude") == 0.2


class TestTheTreePlusIsADoorToo:
    def test_a_recreated_field_the_class_refuses_is_not_created(self, lab):
        c, fake = lab
        pad = f"{G}.flux_pulse_qubit.padding"
        # remove padding on BOTH halves (one alone would break the gate)
        assert c.post("/field/edit-batch", json={"updates": [
            {"dot_path": pad, "delete": True},
            {"dot_path": f"{G}.flux_pulse_target.padding", "delete": True}]}).status_code == 200
        r = c.post("/field/create", data={"dot_path": pad, "key": "padding", "value": "-3"})
        assert r.status_code == 400, r.data[:300]
        assert r.get_json()["lab_refused"] is True
        assert "padding" not in _v(c, f"{G}.flux_pulse_qubit")

    def test_a_whole_new_lab_class_dict_is_drawn_by_its_class(self, lab):
        c, fake = lab
        body = {"__class__": LAB, "amplitude": 0.1, "flat_length": 3}
        r = c.post("/field/create", data={
            "dot_path": "qubits.q2.z.operations.qa_bad", "key": "qa_bad",
            "value": json.dumps(body), "expect_type": "dict"})
        assert r.status_code == 400, r.data[:300]
        assert "must be even" in r.get_json()["error"]
        assert "qa_bad" not in _v(c, "qubits.q2.z.operations")
        body["flat_length"] = 4
        r = c.post("/field/create", data={
            "dot_path": "qubits.q2.z.operations.qa_ok", "key": "qa_ok",
            "value": json.dumps(body), "expect_type": "dict"})
        assert r.status_code == 200, r.data[:300]


# ------------------------------------------------------------------ MAJOR 2
class TestAnEnvThatCannotImportTheClassNeverBlocks:
    def test_a_valid_and_an_invalid_edit_both_go_through_with_a_warning(self, lab):
        c, fake = lab
        fake.unavailable = True
        r = c.post("/field/edit", data={"dot_path": f"{G}.flux_pulse_qubit.amplitude",
                                        "value": "0.25"})
        assert r.status_code == 200, r.data[:300]
        w = r.get_json().get("warning") or ""
        assert ENV in w and "cannot import" in w and "skipped" in w
        # even a value the class WOULD refuse: the check could not run
        r = c.post(f"/pair/{PAIR}/edit", data={"dot_path": FL_C, "value": "9"})
        assert r.status_code == 200, r.data[:300]
        trig = json.loads(r.headers["HX-Trigger"])
        assert ENV in trig["sm:toast"]["message"]

    def test_a_pulse_in_no_gate_warns_too(self, lab):
        """The warning comes from the PULSE draw itself, not only the gate."""
        c, fake = lab
        st = _store(c)
        st.state["qubits"]["q2"]["z"]["operations"]["solo"] = {
            "__class__": LAB, "amplitude": 0.1, "flat_length": 32}
        st.structure_seq += 1
        fake.unavailable = True
        r = c.post("/field/edit", data={
            "dot_path": "qubits.q2.z.operations.solo.flat_length", "value": "9"})
        assert r.status_code == 200, r.data[:300]
        assert ENV in r.get_json()["warning"]
        assert fake.gate_calls == [] and all(
            not q.startswith("@macro:") for _py, items in fake.calls for q, _p in items)

    def test_a_create_goes_through_with_a_warning(self, lab):
        c, fake = lab
        fake.unavailable = True
        r = c.post("/field/create", data={
            "dot_path": "qubits.q2.z.operations.qa_new", "key": "qa_new",
            "value": json.dumps({"__class__": LAB, "amplitude": 0.1, "flat_length": 3}),
            "expect_type": "dict"})
        assert r.status_code == 200, r.data[:300]
        assert ENV in r.get_json()["warning"]


# ------------------------------------------------------------------ MAJOR 3
class TestTheLabGateIsAskedToo:
    def test_one_half_of_a_coupled_pair_is_refused_naming_the_other(self, lab):
        c, fake = lab
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 400, r.data[:300]
        j = r.get_json()
        assert "apply()" in j["error"] and "flat_length differ" in j["error"]
        assert FL_T in j["error"]            # names the field that must follow
        assert j["lab_follow"] == [{"dot_path": FL_C, "value": 40},
                                   {"dot_path": FL_T, "value": 40}]
        assert _v(c, FL_C) == 32

    def test_the_gate_check_is_sent_the_edit_and_only_the_members_it_needs(self, lab):
        c, fake = lab
        c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        before, after = fake.gate_calls[-2:]
        assert before["macros"] == after["macros"] == [G]
        assert _walk(before["contents"], FL_C) == 32
        assert _walk(after["contents"], FL_C) == 40
        # the pair and its two qubits; NOT q3 / q2-q3 (q1 links into q2-q3,
        # but a qubit's links into other gates are not followed)
        assert sorted(after["contents"]["qubits"]) == ["q1", "q2"]
        assert sorted(after["contents"]["qubit_pairs"]) == [PAIR]

    def test_the_offered_batch_sets_both_and_passes(self, lab):
        c, fake = lab
        follow = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"}).get_json()["lab_follow"]
        r = c.post("/field/edit-batch", json={"updates": follow, "group": "new"})
        assert r.status_code == 200, r.data[:300]
        assert _v(c, FL_C) == 40 and _v(c, FL_T) == 40
        # one group: one Ctrl+Z
        log = _store(c).change_log
        assert log[-1].group_id == log[-2].group_id and log[-1].group_id

    def test_the_pulses_page_and_the_pair_inspector_ask_the_gate(self, lab):
        c, fake = lab
        r = c.post(f"/pair/{PAIR}/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 400 and b"flat_length differ" in r.data
        op = f"{G}.flux_pulse_qubit"
        r = c.post("/pulse/edit", data={"path": op, "dot_path": f"{op}.flat_length",
                                        "mode": "value", "value": "40"})
        assert r.status_code == 400 and b"flat_length differ" in r.data
        assert _v(c, FL_C) == 32

    def test_deleting_one_half_s_field_is_refused_but_the_gate_itself_may_go(self, lab):
        c, fake = lab
        r = c.post("/field/delete", data={"dot_path": f"{G}.flux_pulse_qubit.padding"})
        assert r.status_code == 400 and "padding differ" in r.get_json()["error"]
        r = c.post("/field/delete", data={"dot_path": G})
        assert r.status_code == 200, r.data[:300]

    def test_a_gate_already_broken_never_blocks_an_edit(self, lab):
        c, fake = lab
        st = _store(c)
        _walk(st.state, f"{G}.flux_pulse_target")["flat_length"] = 50
        st.structure_seq += 1
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 200, r.data[:300]

    def test_an_unavailable_gate_check_warns_and_writes(self, lab, monkeypatch):
        c, fake = lab
        orig = fake._gate
        monkeypatch.setattr(fake, "_gate", lambda p: {
            "ok": False, "reason": "class-unavailable", "error": "could not import mylab.root.Quam"})
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 200, r.data[:300]
        assert ENV in r.get_json()["warning"]
        assert orig


# ------------------------------------------------------------------ minors
class TestCheckThenWriteIsSerialized:
    def test_two_coupled_edits_in_flight_cannot_both_land(self, lab):
        """flat_length=26 alone is fine, filter=40 alone is fine, together the
        class refuses. Without the hold both were checked against the OLD
        pulse and both landed."""
        c, fake = lab
        op = "qubits.q2.z.operations.solo"
        st = _store(c)
        st.state["qubits"]["q2"]["z"]["operations"]["solo"] = {
            "__class__": LAB, "amplitude": 0.1, "flat_length": 32,
            "gaussian_filter_frequency_mhz": 80}
        st.structure_seq += 1
        fake.delay = 0.4
        out = {}

        def edit(key, path, value):
            cl = c._app.test_client()
            out[key] = cl.post("/field/edit", data={"dot_path": path, "value": value}).status_code

        t1 = threading.Thread(target=edit, args=("a", f"{op}.flat_length", "26"))
        t2 = threading.Thread(target=edit, args=("b", f"{op}.gaussian_filter_frequency_mhz", "40"))
        t1.start()
        time.sleep(0.1)
        t2.start()
        t1.join(10)
        t2.join(10)
        assert sorted(out.values()) == [200, 400], out
        body = st.state["qubits"]["q2"]["z"]["operations"]["solo"]
        assert not (body["flat_length"] == 26 and body["gaussian_filter_frequency_mhz"] == 40)


class TestAPartialRevertSaysWhatItRestores:
    def test_a_tray_discard_of_one_half_warns_and_names_the_other(self, lab):
        c, fake = lab
        assert c.post("/field/edit-batch", json={"updates": [
            {"dot_path": FL_C, "value": 40}, {"dot_path": FL_T, "value": 40}]}).status_code == 200
        log = _store(c).change_log
        idx = max(i for i, e in enumerate(log) if e.dot_path == FL_C)
        r = c.post("/discard", data={"index": str(idx), "expect_path": FL_C})
        assert r.status_code == 200
        trig = json.loads(r.headers["HX-Trigger"])
        msg = trig["sm:toast"]["message"]
        assert "flat_length differ" in msg and f"Discard {FL_T}" in msg
        assert _v(c, FL_C) == 32               # the discard itself happened

    def test_a_discard_of_an_ordinary_edit_says_nothing(self, lab):
        c, fake = lab
        c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5.3e9"})
        r = c.post("/discard", data={"index": str(len(_store(c).change_log) - 1),
                                     "expect_path": "qubits.q1.f_01"})
        assert "sm:toast" not in json.loads(r.headers["HX-Trigger"])
        assert fake.calls == []


class TestALabFieldCannotBeRelinkedToNothingOrAContainer:
    @pytest.mark.parametrize("ptr, why", [("#/qubits/q1/xy", "is a dict"),
                                          ("#/qubits/q1/nonexistent", "does not resolve")])
    def test_refused(self, lab, ptr, why):
        c, fake = lab
        r = c.post("/field/edit", data={"dot_path": FL_T, "value": ptr})
        assert r.status_code == 400, r.data[:300]
        assert why in r.get_json()["error"]
        assert _v(c, FL_T) == 32

    def test_a_relink_to_a_leaf_is_drawn_and_written(self, lab):
        c, fake = lab
        c.post("/field/create", data={"dot_path": "qubits.q1.shared_fl", "key": "shared_fl",
                                      "value": "32"})
        r = c.post("/field/edit", data={"dot_path": FL_T, "value": "#/qubits/q1/shared_fl"})
        assert r.status_code == 200, r.data[:300]


# ------------------------------------------------------------ lab_watch pins
class TestLabWatchContainerChain:
    """A lab field pointing at a LIST: a write to one element changes it."""

    def test_an_element_write_under_a_container_chain_is_seen(self):
        from quam_state_manager.core import lab_watch
        merged = {
            "shared": {"weights": [[1.0, 0.0], [0.0, 1.0]]},
            "qubits": {"q1": {"resonator": {"operations": {"ro": {
                "__class__": LAB, "integration_weights": "#/shared/weights"}}}}},
        }
        w = lab_watch.build(merged, lab_test=lambda c: c == LAB,
                            macro_test=lambda c: False)
        op = "qubits.q1.resonator.operations.ro"
        assert w.affected("shared.weights.1.0") == [(op, "integration_weights", ("1", "0"))]
        assert w.affected("shared.weights") == [(op, "integration_weights", ())]
        assert w.affected("shared.other") == []

    def test_gate_pulses_named_pointed_and_inline_are_tracked(self):
        from quam_state_manager.core import lab_watch
        merged = {
            "qubits": {"q1": {"z": {"operations": {"cz_a": {"__class__": "quam.P", "amplitude": 0.1}}}},
                       "q2": {"z": {"operations": {"cz_b": {"__class__": "quam.P", "amplitude": 0.2}}}}},
            "qubit_pairs": {"p": {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                                  "macros": {"g": {"__class__": GATE,
                                                   "flux_pulse_qubit": "cz_a",
                                                   "flux_pulse_target": "#/qubits/q2/z/operations/cz_b",
                                                   "coupler_flux_pulse": {"__class__": "quam.P", "amplitude": 0.3}}}}},
        }
        w = lab_watch.build(merged, lab_test=lambda c: False,
                            macro_test=lambda c: c == GATE)
        mp = "qubit_pairs.p.macros.g"
        assert w.macros[mp]["ops"] == {"qubits.q1.z.operations.cz_a",
                                       "qubits.q2.z.operations.cz_b",
                                       f"{mp}.coupler_flux_pulse"}
        assert w.macros_for("qubits.q1.z.operations.cz_a.amplitude") == {mp}
        assert w.macros_for(f"{mp}.flux_pulse_qubit") == {mp}       # re-route
        assert w.macros_for("qubits.q1.f_01") == set()
        assert w.lab_ops == set()                                   # nothing drawn


# --------------------------------------------------------------- the worker
class TestTheWorker:
    def test_an_import_failure_is_class_unavailable(self):
        from quam_state_manager.generator import run_pulse_waveform as rpw
        rec = rpw._draw_one("no_such_lab_pkg.mod.Pulse", {}, 100)
        assert rec["ok"] is False and rec["reason"] == "class-unavailable"
        rec = rpw._check_macros("no_such_lab_pkg.root.Quam", {"contents": {}, "macros": []})
        assert rec["reason"] == "class-unavailable"

    def test_the_gate_check_runs_each_macro_s_own_apply(self, tmp_path, monkeypatch):
        pytest.importorskip("qm.qua")
        pkg = tmp_path / "fakelab_gate"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "root.py").write_text(
            "class Gate:\n"
            "    def __init__(self, d): self.d = d\n"
            "    def apply(self):\n"
            "        a, b = self.d['a'], self.d['b']\n"
            "        if a != b: raise ValueError(f'differ ({a} vs {b})')\n"
            "class Pair:\n"
            "    def __init__(self, d): self.macros = {k: Gate(v) for k, v in d['macros'].items()}\n"
            "class Quam:\n"
            "    @classmethod\n"
            "    def load(cls, contents):\n"
            "        m = cls(); m.qubit_pairs = {k: Pair(v) for k, v in contents['qubit_pairs'].items()}\n"
            "        return m\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        from quam_state_manager.generator import run_pulse_waveform as rpw
        contents = {"qubit_pairs": {"p": {"macros": {"ok": {"a": 1, "b": 1},
                                                     "bad": {"a": 1, "b": 2}}}}}
        out = rpw.draw([{"qclass": rpw.MACRO_PREFIX + "fakelab_gate.root.Quam",
                         "params": {"contents": contents,
                                    "macros": ["qubit_pairs.p.macros.ok",
                                               "qubit_pairs.p.macros.bad"]}}])["items"][0]
        assert out["macros"]["qubit_pairs.p.macros.ok"] is None
        assert "differ (1 vs 2)" in out["macros"]["qubit_pairs.p.macros.bad"]
        assert out["ok"] is False and "macros.bad.apply()" in out["error"]

    def test_the_prefix_is_one_string_on_both_sides(self):
        from quam_state_manager.core import lab_waveform
        from quam_state_manager.generator import run_pulse_waveform as rpw
        assert lab_waveform.MACRO_PREFIX == rpw.MACRO_PREFIX

    def test_class_unavailable_is_never_cached(self, monkeypatch):
        from quam_state_manager.core import lab_waveform
        runs = []

        def run(py, items):
            runs.append(items)
            return {"ok": True, "error": None, "sources": {},
                    "items": [{"ok": False, "reason": "class-unavailable",
                               "error": "could not import"} for _ in items]}
        monkeypatch.setattr(lab_waveform, "_run", run)
        monkeypatch.setattr(lab_waveform, "_env_sig", lambda py: ("sig",))
        a = lab_waveform.draw("py-x.exe", [("lab.X", {"k": 1})])[0]
        b = lab_waveform.draw("py-x.exe", [("lab.X", {"k": 1})])[0]
        assert a["reason"] == b["reason"] == "class-unavailable"
        assert len(runs) == 2          # asked again, not served from RAM
