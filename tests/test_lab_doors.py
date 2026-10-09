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
        # Quam.load: a required slot left null stops the WHOLE chip loading,
        # whichever macro holds it -- named the way quam names it
        for pn, pb in (contents.get("qubit_pairs") or {}).items():
            for mn, mb in ((pb or {}).get("macros") or {}).items():
                if isinstance(mb, dict) and "flux_pulse_qubit" in mb \
                        and mb["flux_pulse_qubit"] is None:
                    return {"ok": False, "load_failed": True, "macros": {},
                            "error": "TypeError: None is not allowed for required "
                                     f'attribute Quam.qubit_pairs["{pn}"].macros'
                                     f'["{mn}"].flux_pulse_qubit'}
        for mp in params["macros"]:
            try:
                mac = _walk(contents, mp)
                pair = _walk(contents, mp.rsplit(".macros.", 1)[0])

                def pulse_of(slot, role):
                    p = mac[slot]
                    if isinstance(p, str) and p.startswith("#/"):   # a linked pulse
                        p = _walk(contents, p[2:].replace("/", "."))
                    # get_pulse_name(pulse) -> <qubit>.z.play(name): the op of
                    # that id must exist on the pair's own qubit's z line
                    if isinstance(p, dict) and isinstance(p.get("id"), str):
                        q = _walk(contents, pair[role][2:].replace("/", "."))
                        if p["id"] not in q["z"]["operations"]:
                            raise KeyError(f"Operation {p['id']!r} not found in "
                                           f"channel {pair[role]}.z")
                    return p
                a = pulse_of("flux_pulse_qubit", "qubit_control")
                # CZGateTwoFlux: no target pulse = a control-only gate (its own
                # bootstrap case) -- nothing to compare
                b = (pulse_of("flux_pulse_target", "qubit_target")
                     if mac.get("flux_pulse_target") is not None else a)

                def knob_of(p, k):          # quam resolves an absolute link
                    v = p.get(k, 0)
                    if isinstance(v, str) and v.startswith("#/"):
                        v = _walk(contents, v[2:].replace("/", "."))
                    return v
                for knob in ("flat_length", "padding"):
                    va, vb = knob_of(a, knob), knob_of(b, knob)
                    if va != vb:
                        raise ValueError(f"control/target {knob} differ ({va} vs {vb})")
                # spectators: self.spectator_qubits[q].z.play(get_pulse_name(p))
                spq = mac.get("spectator_qubits") or {}
                for k, sp in (mac.get("spectator_qubits_control") or {}).items():
                    if isinstance(sp, str):
                        try:
                            body = _walk(contents, sp[2:].replace("/", "."))
                        except (KeyError, TypeError):
                            raise AttributeError("'str' object has no attribute 'id'")
                        name = body.get("id") or sp.rsplit("/", 1)[-1]
                    else:
                        name = sp.get("id") or k
                    if k in spq:
                        qb = _walk(contents, spq[k][2:].replace("/", "."))
                        if name not in qb["z"]["operations"]:
                            raise KeyError(f"Operation {name!r} not found in "
                                           f"channel {spq[k]}.z")
                res["macros"][mp] = None
            except Exception as exc:  # noqa: BLE001
                res["macros"][mp] = f"{type(exc).__name__}: {exc}"
                res["ok"] = False
                res["error"] = res["error"] or f"{mp}.apply(): {exc}"
        if params.get("config"):
            # generate_config(): a pulse field left pointing at nothing is a
            # string where quam wants a number -> a bare assert, whole chip
            res["config_ran"] = True
            res["config_error"] = FakeLab._config_error(contents)
        return res

    @staticmethod
    def _config_error(contents):
        def dangles(v):
            if isinstance(v, str) and v.startswith("#/"):
                try:
                    _walk(contents, v[2:].replace("/", "."))
                except (KeyError, TypeError):
                    return True
            elif isinstance(v, dict):
                return any(dangles(x) for x in v.values())
            return False
        for qn, qb in (contents.get("qubits") or {}).items():
            for ch, cb in qb.items():
                for on, ob in ((cb.get("operations") if isinstance(cb, dict) else None)
                               or {}).items():
                    if dangles(ob):
                        return ("AssertionError:  (at pulses.py:288: assert "
                                "isinstance(self.length, int))")
        return None


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
        # remove padding on BOTH halves and q1's mirror link to it (a delete
        # that left the link dangling is refused since docs/218)
        assert c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.q1.z.operations.czl_q1.padding", "delete": True},
            {"dot_path": pad, "delete": True},
            {"dot_path": f"{G}.flux_pulse_target.padding", "delete": True}]}).status_code == 200
        r = c.post("/field/create", data={"dot_path": pad, "key": "padding", "value": "-3"})
        assert r.status_code == 400, r.data[:300]
        assert r.get_json()["lab_refused"] is True
        assert "padding" not in _v(c, f"{G}.flux_pulse_qubit")

    def test_a_whole_new_lab_class_dict_is_drawn_by_its_class(self, lab):
        """w9/pulsegate: a whole new pulse is created on the Pulses page only
        -- the tree's ＋ is refused before the lab is asked, and the Pulses
        page's create asks the class's own code."""
        c, fake = lab
        body = {"__class__": LAB, "amplitude": 0.1, "flat_length": 3}
        r = c.post("/field/create", data={
            "dot_path": "qubits.q2.z.operations.qa_bad", "key": "qa_bad",
            "value": json.dumps(body), "expect_type": "dict"})
        assert r.status_code == 409 and r.get_json()["error_kind"] == "pulse_structure"
        assert fake.calls == [] and "qa_bad" not in _v(c, "qubits.q2.z.operations")
        form = {"target_kind": "qubit", "qubit": "q2", "channel": "z",
                "pulse_type": "NZPulse", "qclass": LAB, "amplitude": "0.1",
                "padding": "4"}
        r = c.post("/api/pulse/create", data={**form, "op_name": "qa_bad",
                                              "flat_length": "3"})
        assert r.status_code == 400 and b"must be even" in r.data, r.data[:300]
        assert "qa_bad" not in _v(c, "qubits.q2.z.operations")
        r = c.post("/api/pulse/create", data={**form, "op_name": "qa_ok",
                                              "flat_length": "4"})
        assert r.status_code == 200, r.data[:300]
        assert "qa_ok" in _v(c, "qubits.q2.z.operations")


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
        """w9/pulsegate: the create door for a pulse is the Pulses page."""
        c, fake = lab
        fake.unavailable = True
        r = c.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "q2", "channel": "z", "op_name": "qa_new",
            "pulse_type": "NZPulse", "qclass": LAB, "amplitude": "0.1",
            "flat_length": "3", "padding": "4"})
        assert r.status_code == 200, r.data[:300]
        assert ENV in json.loads(r.headers["HX-Trigger"])["sm:toast"]["message"]
        # a field re-created inside an existing pulse is not a structural
        # edit: the tree's ＋ still goes through (with the same warning)
        st = _store(c)
        del st.state["qubits"]["q2"]["z"]["operations"]["qa_new"]["padding"]
        st.structure_seq += 1
        r = c.post("/field/create", data={
            "dot_path": "qubits.q2.z.operations.qa_new.padding", "key": "padding",
            "value": "4"})
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
        # docs/218: alone, the gate may not go -- q1's czl_q1 links into it and
        # generate_config() would fail for the whole chip; together it may
        r = c.post("/field/delete", data={"dot_path": G})
        assert r.status_code == 400 and "czl_q1" in r.get_json()["error"], r.data[:300]
        # w9/pulsegate: the set holds a pulse (czl_q1) -- the tree's offer is
        # a link to the Pulses page, whose own door deletes it together
        j = r.get_json()
        assert "qubits.q1.z.operations.czl_q1" in j["lab_delete_also"]
        assert "together=" in j["lab_delete_pulses_url"]
        paths = [G, "qubits.q1.z.operations.czl_q1"]
        r = c.post("/field/edit-batch", json={"updates": [
            {"dot_path": p, "delete": True} for p in paths]})
        assert r.status_code == 409 and r.get_json()["error_kind"] == "pulse_structure"
        r = c.post("/api/pulse/delete-together", json={"path": G, "paths": paths})
        assert r.status_code == 200, r.data[:300]
        assert "czl" not in _v(c, f"qubit_pairs.{PAIR}.macros")

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


# ------------------------------------------------------- verifier 3 (by name)
def _by_name(st):
    """Give the lab gate the chip's own layout: its inline pulses carry an
    ``id`` and the gate plays that NAME on each qubit's z line
    (CZGateTwoFlux: ``qubit_control.z.play(get_pulse_name(pulse))``)."""
    mac = _walk(st.state, G)
    mac["flux_pulse_qubit"]["id"] = "czl_q1"
    mac["flux_pulse_target"]["id"] = "czl_q2t"
    st.state["qubits"]["q2"]["z"]["operations"]["czl_q2t"] = {
        "__class__": LAB, "flat_length": f"#/{FL_T.replace('.', '/')}",
        "amplitude": 0.1, "padding": 4}
    st.structure_seq += 1


OP_C = "qubits.q1.z.operations.czl_q1"
OP_T = "qubits.q2.z.operations.czl_q2t"


class TestAnOpTheGatePlaysByNameIsOnItsRoute:
    """Verifier 3 (lab-F 5Q): CZGateTwoFlux holds its pulses inline and plays
    them BY NAME on the pair's own z lines; deleting or renaming that channel
    op went through every door and every CZ node on the pair then failed."""

    def test_the_watch_records_the_name_and_who_holds_it(self, lab):
        from quam_state_manager.core import lab_watch
        c, fake = lab
        st = _store(c)
        _by_name(st)
        w = lab_watch.watch_for(st)
        assert w.named_by[OP_C] == {f"{G}.flux_pulse_qubit.id"}
        assert w.named_by[OP_T] == {f"{G}.flux_pulse_target.id"}
        assert w.macros_for(OP_C) == {G}
        assert w.macros_for("qubits.q1.z.operations") == {G}   # the container
        assert w.macros_for(f"qubit_pairs.{PAIR}.qubit_control") == {G}

    def test_every_door_that_removes_or_renames_it_asks_the_gate(self, lab):
        c, fake = lab
        _by_name(_store(c))
        # w9/pulsegate: the Json Tree is no longer a door for it at all
        r = c.post("/field/delete", data={"dot_path": OP_C})
        assert r.status_code == 409 and r.get_json()["error_kind"] == "pulse_structure"
        r = c.post("/api/pulse/delete", data={"path": OP_C, "force": "1"})
        assert r.status_code == 400 and b"not found" in r.data, r.data[:300]
        r = c.post("/api/pulse/rename", data={"path": OP_C, "new_name": "czl_renamed"})
        assert r.status_code == 400 and b"not found" in r.data, r.data[:300]
        r = c.post("/api/pulse/delete", data={"path": OP_T})
        assert r.status_code == 409 and f"{G}.flux_pulse_target.id".encode() in r.data
        r = c.post("/api/pulse/delete", data={"path": OP_T, "force": "1"})
        assert r.status_code == 400 and b"not found" in r.data, r.data[:300]
        ops = _v(c, "qubits.q1.z.operations")
        assert "czl_q1" in ops and "czl_renamed" not in ops
        assert "czl_q2t" in _v(c, "qubits.q2.z.operations")
        # an op no gate plays still renames and deletes freely
        st = _store(c)
        st.state["qubits"]["q2"]["z"]["operations"]["free"] = {"__class__": "quam.P", "amplitude": 0.1}
        st.structure_seq += 1
        assert c.post("/api/pulse/rename", data={"path": "qubits.q2.z.operations.free",
                                                 "new_name": "free2"}).status_code == 200
        assert c.post("/api/pulse/delete",
                      data={"path": "qubits.q2.z.operations.free2"}).status_code == 200

    def test_the_pulses_page_names_the_gate_that_plays_it(self, lab):
        c, fake = lab
        _by_name(_store(c))
        t = c.get(f"/pulse/detail?path={OP_C}").get_data(as_text=True)
        assert "pulse-played-by-name" in t and f"{G}.flux_pulse_qubit.id" in t
        assert "No other operation references" not in t

    def test_re_linking_the_pair_s_qubit_is_asked_with_that_qubit_in_the_contents(self, lab):
        c, fake = lab
        st = _store(c)
        _by_name(st)
        ctl = f"qubit_pairs.{PAIR}.qubit_control"
        # q3 has no op of that name: every program on the pair would fail
        r = c.post("/field/edit", data={"dot_path": ctl, "value": "#/qubits/q3"})
        assert r.status_code == 400 and "not found" in r.get_json()["error"], r.data[:300]
        # once q3 carries it, the same re-link applies -- q3 was SENT
        st.state["qubits"]["q3"]["z"]["operations"]["czl_q1"] = {"__class__": "quam.P"}
        st.structure_seq += 1
        r = c.post("/field/edit", data={"dot_path": ctl, "value": "#/qubits/q3"})
        assert r.status_code == 200, r.data[:300]
        assert "q3" in fake.gate_calls[-1]["contents"]["qubits"]


class TestACreateIntoAnEmptyLabSlotIsAsked:
    def test_refused_when_the_gate_refuses_it_and_written_when_it_applies(self, lab):
        c, fake = lab
        st = _store(c)
        _walk(st.state, G)["flux_pulse_qubit"] = None
        st.structure_seq += 1
        form = {"target_kind": "pair", "pair": PAIR, "gate": "czl",
                "slot": "flux_pulse_qubit", "pulse_type": "NZPulse", "qclass": LAB,
                "amplitude": "0.1", "padding": "4"}
        r = c.post("/api/pulse/create", data={**form, "flat_length": "40"})
        assert r.status_code == 400 and b"flat_length differ" in r.data, r.data[:400]
        assert _walk(st.state, G)["flux_pulse_qubit"] is None
        assert not any(k.startswith("czl_flux_pulse") for k in _v(c, "qubits.q1.z.operations"))
        r = c.post("/api/pulse/create", data={**form, "flat_length": "32"})
        assert r.status_code == 200, r.data[:400]
        assert isinstance(_walk(st.state, G)["flux_pulse_qubit"], str)


class TestAGateThatAlreadyFailsSaysSo:
    def test_a_note_when_it_cannot_be_checked_and_a_warning_when_the_error_moves(self, lab):
        c, fake = lab
        st = _store(c)
        _walk(st.state, f"{G}.flux_pulse_target")["flat_length"] = 50
        st.structure_seq += 1
        r = c.post("/field/edit", data={"dot_path": f"{G}.flux_pulse_qubit.amplitude",
                                        "value": "0.2"})
        assert r.status_code == 200, r.data[:300]
        w = r.get_json().get("warning") or ""
        assert "could not be checked" in w and "already fails" in w and "(32 vs 50)" in w
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 200, r.data[:300]
        w = r.get_json().get("warning") or ""
        assert "fails differently" in w and "(40 vs 50)" in w


class TestAPrunedFailureIsAskedOfTheWholeChip:
    def test_a_gate_whose_pulse_lives_in_another_pair_is_still_checked(self, lab):
        c, fake = lab
        st = _store(c)
        st.state["qubits"]["q2"]["z"]["operations"]["tp"] = {
            "__class__": LAB, "amplitude": 0.1, "padding": 4,
            "flat_length": "#/qubit_pairs/q2-q3/macros/czl/flux_pulse_qubit/flat_length"}
        _walk(st.state, G)["flux_pulse_target"] = "#/qubits/q2/z/operations/tp"
        st.structure_seq += 1
        n0 = len(fake.gate_calls)
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 400 and "flat_length differ" in r.get_json()["error"], r.data[:300]
        full = fake.gate_calls[-1]["contents"]
        assert "q2-q3" in full["qubit_pairs"] and len(fake.gate_calls) - n0 == 4


class TestTheFollowOfferIgnoresAMirrorOfTheEditedField:
    def test_a_by_name_mirror_op_does_not_repeat_the_edited_field(self, lab):
        c, fake = lab
        _by_name(_store(c))
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 400, r.data[:300]
        # czl_q1 (q1's z op, played by name) links back to FL_C: offering it
        # would write the edited field twice
        assert r.get_json()["lab_follow"] == [{"dot_path": FL_C, "value": 40},
                                              {"dot_path": FL_T, "value": 40}]


# ------------------------------------------------ verifier 4 (docs/218)
CZQ = ("quam_builder.architecture.superconducting.custom_gates."
       "flux_tunable_transmon_pair.two_qubit_gates.CZGate")


def _real_draw(monkeypatch, python_path):
    """Undo the fixture's fake: the REAL lab_waveform.draw (subprocess, warm
    worker, timeout), with the selected env set to *python_path*."""
    import importlib

    from quam_state_manager.core import config_generator, lab_waveform
    real = importlib.reload(lab_waveform).draw
    monkeypatch.setattr(lab_waveform, "draw", real)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: python_path)
    return lab_waveform


class TestALabCheckThatCannotRunSaysSo:
    """Verifier 4 (lab-F 5Q): with the env's python gone (or the worker timed
    out / crashed) a gate-breaking edit and a by-name op delete returned 200
    with no word at all -- the user thought the gate had been asked."""

    def test_a_missing_env_path_still_writes_and_says_nothing_was_checked(
            self, lab, monkeypatch):
        c, fake = lab
        _by_name(_store(c))
        _real_draw(monkeypatch, r"D:\no_such_env\python.exe")
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 200, r.data[:300]
        w = r.get_json().get("warning") or ""
        assert "could not be run" in w and "no longer exists" in w, w
        assert "NOT checked against" in w and f"your gate czl ({G})" in w, w
        assert "your class NZPulse" in w, w
        assert _v(c, FL_C) == 40
        # the by-name op delete (the Pulses page's door): written, and said
        r = c.post("/api/pulse/delete", data={"path": OP_T, "force": "1"})
        assert r.status_code == 200, r.data[:300]
        assert "NOT checked against" in json.loads(r.headers["HX-Trigger"])["sm:toast"]["message"]
        # the pair inspector's toast carries it too
        r = c.post(f"/pair/{PAIR}/edit", data={"dot_path": FL_T, "value": "42"})
        assert r.status_code == 200, r.data[:300]
        assert "NOT checked" in json.loads(r.headers["HX-Trigger"])["sm:toast"]["message"]

    def test_no_env_selected_says_so_too(self, lab, monkeypatch):
        c, fake = lab
        from quam_state_manager.core import config_generator
        monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: None)
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 200, r.data[:300]
        w = r.get_json().get("warning") or ""
        assert "no Python environment is selected" in w and "NOT checked" in w, w
        assert fake.calls == []

    def test_a_timed_out_worker_is_named_stopped_and_not_asked_twice(
            self, lab, monkeypatch, tmp_path):
        import sys

        from quam_state_manager.core import config_generator
        c, fake = lab
        stub = tmp_path / "hang.py"
        stub.write_text("import sys, time\nsys.stdin.readline()\ntime.sleep(120)\n")
        lw = _real_draw(monkeypatch, sys.executable)
        monkeypatch.setattr(lw, "TIMEOUT_S", 4)
        monkeypatch.setattr(config_generator, "_script_path", lambda name: stub)
        try:
            t0 = time.time()
            r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
            took = time.time() - t0
            assert r.status_code == 200, r.data[:300]
            w = r.get_json().get("warning") or ""
            assert "did not answer within 4 s" in w and "NOT checked" in w, w
            assert f"your gate czl ({G})" in w, w
            # one timeout, not a cold re-run and a second (gate) wait
            assert took < 7.5, took
            assert lw._WORKERS == {}          # the hung worker was killed
        finally:
            lw.shutdown_workers()


class TestAnEmptySlotElsewhereDoesNotSwitchTheCheckOff:
    """Verifier 4: a quam CZGate with flux_pulse_qubit=null makes Quam.load
    fail -- before AND after -- so every lab gate on that chip went
    unchecked ('could not be checked'), and a fill later made the chip load
    with a broken lab gate nobody named."""

    def test_the_unloadable_macro_is_dropped_and_the_gate_still_refuses(self, lab):
        c, fake = lab
        st = _store(c)
        _walk(st.state, f"qubit_pairs.{PAIR}.macros")["cz_empty"] = {
            "__class__": CZQ, "flux_pulse_qubit": None}
        st.structure_seq += 1
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 400, r.data[:300]
        assert "flat_length differ" in r.get_json()["error"]
        before, after = fake.gate_calls[-2:]
        for side in (before, after):
            assert "cz_empty" not in side["contents"]["qubit_pairs"][PAIR]["macros"]
        assert _v(c, FL_C) == 32

    def test_also_on_the_whole_chip_retry(self, lab):
        """The pruned check fails (a pulse in another pair), the full chip
        does not load (an empty slot there): both are routed around."""
        c, fake = lab
        st = _store(c)
        st.state["qubits"]["q2"]["z"]["operations"]["tp"] = {
            "__class__": LAB, "amplitude": 0.1, "padding": 4,
            "flat_length": "#/qubit_pairs/q2-q3/macros/czl/flux_pulse_qubit/flat_length"}
        _walk(st.state, G)["flux_pulse_target"] = "#/qubits/q2/z/operations/tp"
        _walk(st.state, "qubit_pairs.q2-q3.macros")["cz_empty"] = {
            "__class__": CZQ, "flux_pulse_qubit": None}
        st.structure_seq += 1
        r = c.post("/field/edit", data={"dot_path": FL_C, "value": "40"})
        assert r.status_code == 400 and "flat_length differ" in r.get_json()["error"], r.data[:300]

    def test_a_slot_fill_names_a_lab_gate_that_now_fails(self, lab):
        c, fake = lab
        st = _store(c)
        _walk(st.state, f"qubit_pairs.{PAIR}.macros")["cz_empty"] = {
            "__class__": CZQ, "flux_pulse_qubit": None}
        _walk(st.state, f"{G}.flux_pulse_target")["flat_length"] = 50   # broken
        st.structure_seq += 1
        r = c.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": PAIR, "gate": "cz_empty",
            "slot": "flux_pulse_qubit", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1"})
        assert r.status_code == 200, r.data[:400]
        msg = json.loads(r.headers["HX-Trigger"])["sm:toast"]["message"]
        assert f"Your gate czl ({G}) fails its own apply()" in msg, msg
        assert "(32 vs 50)" in msg and "cz_empty" in msg, msg
        # the pair's lab gate was ASKED although the create never touched it
        assert G in fake.gate_calls[-1]["macros"]


class TestADeleteThatBreaksGenerateConfigIsRefused:
    """Verifier 4: deleting a lab gate, or its inline pulse, left the by-name
    mirror ops' linked fields pointing at nothing -- the chip still loaded,
    but generate_config() failed for the WHOLE chip."""

    def test_the_gate_and_its_inline_pulse_are_refused_naming_the_mirrors(self, lab):
        c, fake = lab
        _by_name(_store(c))
        n0 = len(fake.gate_calls)
        r = c.post("/field/delete", data={"dot_path": G})
        assert r.status_code == 400, r.data[:300]
        # answered on the PRUNED chip (q1's link into q2-q3 is set aside, not
        # a reason to load the whole chip) -- one before/after pair
        assert len(fake.gate_calls) - n0 == 2
        assert all(p.get("config") for p in fake.gate_calls[n0:])
        assert "q2-q3" not in fake.gate_calls[-1]["contents"]["qubit_pairs"]
        j = r.get_json()
        assert "generate_config()" in j["error"] and "isinstance(self.length" in j["error"]
        assert OP_C in j["error"] and OP_T in j["error"]
        assert "played by name by" in j["error"]
        assert set(j["lab_delete_also"]) >= {OP_C, OP_T}
        assert "together=" in j["lab_delete_pulses_url"]      # w9: a link, not a batch
        # w9/pulsegate: the gate's inline pulse is a pulse -- the tree refuses
        r = c.post("/field/delete", data={"dot_path": f"{G}.flux_pulse_target"})
        assert r.status_code == 409 and r.get_json()["error_kind"] == "pulse_structure"
        r = c.post("/api/pulse/delete", data={"path": f"{G}.flux_pulse_target",
                                              "force": "1"})
        assert r.status_code == 400 and b"generate_config()" in r.data, r.data[:400]
        # a container above the chain end
        r = c.post("/field/delete", data={"dot_path": f"qubit_pairs.{PAIR}.macros"})
        assert r.status_code == 400, r.data[:300]
        assert "czl" in _v(c, f"qubit_pairs.{PAIR}.macros")
        assert "flux_pulse_target" in _v(c, G)

    def test_a_delete_above_a_lab_field_s_chain_end_is_asked(self, lab):
        """No gate, no by-name op: a lab pulse's field links into a container
        another delete removes WHOLE (a prefix of the chain end)."""
        c, fake = lab
        st = _store(c)
        st.state["qubits"]["q3"]["shared"] = {"fl": 32}
        st.state["qubits"]["q2"]["z"]["operations"]["solo"] = {
            "__class__": LAB, "amplitude": 0.1, "flat_length": "#/qubits/q3/shared/fl"}
        st.structure_seq += 1
        r = c.post("/field/delete", data={"dot_path": "qubits.q3.shared"})
        assert r.status_code == 400, r.data[:300]
        assert "flat_length of qubits.q2.z.operations.solo" in r.get_json()["error"]
        assert _v(c, "qubits.q3.shared.fl") == 32

    def test_deleted_together_it_goes(self, lab):
        c, fake = lab
        st = _store(c)
        _by_name(st)
        # an ordinary (non-lab) op linking into the gate goes in the same
        # batch: its row reaches no lab pulse, but the 'after' must lack it
        plain = "qubits.q1.xy.operations.plain"
        st.state["qubits"]["q1"]["xy"]["operations"]["plain"] = {
            "__class__": "quam.P", "amplitude": f"#/{G.replace('.', '/')}/flux_pulse_qubit/amplitude"}
        st.structure_seq += 1
        # w9/pulsegate: through the Pulses page's own door
        r = c.post("/api/pulse/delete-together",
                   json={"path": G, "paths": [G, OP_C, OP_T, plain]})
        assert r.status_code == 200, r.data[:300]
        assert "czl" not in _v(c, f"qubit_pairs.{PAIR}.macros")
        assert "plain" not in _v(c, "qubits.q1.xy.operations")

    def test_a_delete_that_still_generates_warns_naming_what_stays(self, lab):
        c, fake = lab
        st = _store(c)
        _by_name(st)
        # the mirror ops hold literals, not links: nothing dangles
        for op in (OP_C, OP_T):
            body = _walk(st.state, op)
            for k in list(body):
                if isinstance(body[k], str) and body[k].startswith("#/"):
                    body[k] = 32 if k == "flat_length" else 4 if k == "padding" else 0.1
        st.structure_seq += 1
        r = c.post("/field/delete", data={"dot_path": G})
        assert r.status_code == 200, r.data[:300]
        w = r.get_json().get("warning") or ""
        assert OP_C in w and "behind with no gate to play them" in w, w
        assert "generate_config() passes" in w, w


    def test_a_rename_that_re_points_its_referrers_leaves_nothing_dangling(self, lab):
        c, fake = lab
        st = _store(c)
        ops = st.state["qubits"]["q2"]["z"]["operations"]
        ops["base"] = {"__class__": LAB, "amplitude": 0.1, "flat_length": 32}
        ops["mirror"] = {"__class__": LAB, "amplitude": 0.1,
                         "flat_length": "#/qubits/q2/z/operations/base/flat_length"}
        st.structure_seq += 1
        n0 = len(fake.gate_calls)
        r = c.post("/api/pulse/rename", data={"path": "qubits.q2.z.operations.base",
                                              "new_name": "base2", "retarget": "1"})
        assert r.status_code == 200, r.data[:300]
        trig = r.headers.get("HX-Trigger") or ""
        assert "pointing at nothing" not in trig and "NOT checked" not in trig, trig
        assert len(fake.gate_calls) == n0          # nothing to ask
        assert _v(c, "qubits.q2.z.operations.mirror.flat_length").endswith("/base2/flat_length")
        # without re-pointing, the same rename is refused
        r = c.post("/api/pulse/rename", data={"path": "qubits.q2.z.operations.base2",
                                              "new_name": "base3", "retarget": "0"})
        assert r.status_code == 400 and b"mirror" in r.data, r.data[:400]


class TestAnOrdinaryEditReadsNoEnvSettings:
    def test_zero_settings_reads_for_a_plain_edit(self, lab, monkeypatch):
        from quam_state_manager.core import config_generator
        c, fake = lab
        n = {"reads": 0}

        def counted(inst):
            n["reads"] += 1
            return ENV
        monkeypatch.setattr(config_generator, "get_selected_env", counted)
        assert c.post("/field/edit", data={"dot_path": "qubits.q1.f_01",
                                           "value": "5.3e9"}).status_code == 200
        assert c.post("/qubit/q1/edit", data={"dot_path": "qubits.q1.f_01",
                                              "value": "5.31e9"}).status_code == 200
        assert c.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.q2.f_01", "value": 4.8e9}]}).status_code == 200
        assert n["reads"] == 0
        # the counter is live: a lab edit reads it
        c.post("/field/edit", data={"dot_path": f"{G}.flux_pulse_qubit.amplitude",
                                    "value": "0.2"})
        assert n["reads"] >= 1


class TestASpectatorPulseIsOnTheRoute:
    """Verifier 4 (admitted open issue): CZGateTwoFlux plays
    ``spectator_qubits[q].z.play(get_pulse_name(spectator_qubits_control[q]))``
    -- the spectator's op was on no route, so deleting it went through."""

    def _spect(self, c, control):
        st = _store(c)
        mac = _walk(st.state, G)
        mac["spectator_qubits"] = {"q3": "#/qubits/q3"}
        mac["spectator_qubits_control"] = {"q3": control}
        st.state["qubits"]["q3"]["z"]["operations"]["park"] = {
            "__class__": "quam.P", "amplitude": 0.01}
        st.structure_seq += 1
        return st

    def test_a_pointed_spectator_pulse(self, lab):
        from quam_state_manager.core import lab_watch
        c, fake = lab
        st = self._spect(c, "#/qubits/q3/z/operations/park")
        w = lab_watch.watch_for(st)
        assert w.macros_for("qubits.q3.z.operations.park") == {G}
        assert w.macros_for(f"{G}.spectator_qubits.q3") == {G}
        r = c.post("/api/pulse/delete", data={"path": "qubits.q3.z.operations.park",
                                              "force": "1"})
        assert r.status_code == 400 and b"no attribute" in r.data, r.data[:300]
        assert "q3" in fake.gate_calls[-1]["contents"]["qubits"]

    def test_an_inline_spectator_pulse_played_by_its_id(self, lab):
        from quam_state_manager.core import lab_watch
        c, fake = lab
        st = self._spect(c, {"__class__": "quam.P", "amplitude": 0.01, "id": "park"})
        w = lab_watch.watch_for(st)
        assert w.named_by["qubits.q3.z.operations.park"] == {
            f"{G}.spectator_qubits_control.q3.id"}
        r = c.post("/api/pulse/delete", data={"path": "qubits.q3.z.operations.park",
                                              "force": "1"})
        assert r.status_code == 400 and b"not found" in r.data, r.data[:300]
        r = c.post("/api/pulse/rename", data={"path": "qubits.q3.z.operations.park",
                                              "new_name": "park2"})
        assert r.status_code == 400, r.data[:300]
        # re-linking the spectator to a qubit without that op is refused too
        r = c.post("/field/edit", data={"dot_path": f"{G}.spectator_qubits.q3",
                                        "value": "#/qubits/q2"})
        assert r.status_code == 400 and "not found" in r.get_json()["error"], r.data[:300]
        assert "park" in _v(c, "qubits.q3.z.operations")
