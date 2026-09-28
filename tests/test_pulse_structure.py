"""w9/pulsegate (user decision 2026-09-28): a pulse is added, deleted, renamed
or copied ONLY on the Pulses page; every other editor may change what a pulse
holds, never which pulses exist.

Pinned here:

* the rule's definition of "a pulse" IS the Pulses page's row list
  (``core.pulse_structure.places_at`` == ``pulse_index.list_pulses``, on a
  synthetic chip with every location type and on the real chips present);
* the classification matrix -- what is structural (a pulse / an
  ``operations`` dict / pulses changing inside an object that stays) and what
  is not (a field, a re-link, a non-pulse object coming or going with its
  pulses);
* every generic door refuses with the way there (``/field/create``,
  ``/field/delete``, ``/field/edit``, ``/field/edit-batch`` atomic and
  independent, the qubit / pair inspector edits, ``cli set``), and TAKE LIVE
  through the batch door passes only when the live chip really holds it;
* the Pulses page's own doors: ``/api/pulse/delete-together`` (a verified
  set, one Ctrl+Z), ``/pulses/goto`` (the tree's link), the ``together=``
  offer, the tree payload (never built cold);
* the tree's own behaviour under jsdom (``pulse_gate_tree_selfcheck.cjs``).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import pulse_index, pulse_structure as ps
from quam_state_manager.web.app import create_app
from tests.test_pulse_locations import (CPL, PUMP, QC, SLOT, SPEC, XY2,  # noqa: F401
                                        _golden)
from tests.test_pulse_locations import _state as _loc_state

_ROOT = Path(__file__).resolve().parent.parent
X180 = "qubits.q1.xy.operations.x180"
OPS = "qubits.q1.xy.operations"
W3 = "qubit_pairs.q1-2.macros.cz_unipolar.flux_pulse_qubit"
W3P = "qubit_pairs.q1-2.macros.cz_unipolar.coupler_flux_pulse"
GATE = "qubit_pairs.q1-2.macros.cz_custom"
INNER = f"{XY2}.holder.inner"


def _state() -> dict:
    """The docs/217 location chip, plus a QDAC trigger op (a whitelisted
    place one level deeper), a top-level pulse (never a row) and a pulse in
    the skipped tops."""
    st = _loc_state()
    q2 = {"z": {"__class__": "quam_config.qdac_components.QdacBiasLine",
                "qdac_channel": 3, "opx_trigger_out": {"operations": {
                    "trigger": {"__class__": QC + "SquarePulse", "length": 16,
                                "amplitude": 0.1}}}},
          "xy": {"operations": {}}}
    st["qubits"]["q2"] = q2
    # an unclassed entry of a found operations dict is looked INSIDE: a
    # pulse-class dict there is a row (docs/217 "an unclassed dict: look inside")
    st["qubits"]["q1"]["xy2"]["operations"]["holder"] = {
        "note": "not a pulse", "inner": {"__class__": QC + "SquarePulse", "length": 8,
                                         "amplitude": 0.1}}
    st["qubits"]["q1"]["spare"] = None               # a null a whole pulse could land on
    st["top_pulse"] = {"__class__": QC + "SquarePulse", "length": 4, "amplitude": 0.1}
    st["ports"] = {"x": {"operations": {"p": {"__class__": QC + "SquarePulse"}}}}
    return st


def _rows(m) -> set:
    return {r["path"] for r in pulse_index.list_pulses(m, with_used_by=False)}


def _places(m) -> set:
    out: set = set()
    for top, node in m.items():
        out |= ps.places_at(m, top, node)[0]
    return out


# ---------------------------------------------------------------------------
# The definition is the Pulses page's own
# ---------------------------------------------------------------------------

class TestAPulseIsWhatThePulsesPageLists:
    def test_every_location_type(self):
        m = _state()
        rows = _rows(m)
        assert "qubits.q2.z.opx_trigger_out.operations.trigger" in rows
        assert INNER in rows and f"{XY2}.holder" not in rows
        assert _places(m) == rows

    @pytest.mark.parametrize("chip", sorted(_golden()["chips"]))
    def test_real_chips(self, chip):
        if not (Path(chip) / "state.json").is_file():
            pytest.skip("real chip not on this machine")
        from quam_state_manager.core.loader import QuamStore
        m = QuamStore(chip).merged
        assert _places(m) == _rows(m)

    def test_a_hypothetical_value_is_judged_where_it_would_sit(self):
        m = _state()
        rows, entries = ps.places_at(m, "qubits.q1.xy", m["qubits"]["q1"]["xy"])
        assert rows == {X180, "qubits.q1.xy.operations.x90"} == entries
        # the same dict at a place that cannot hold a pulse: nothing
        assert ps.places_at(m, "extras.copy", m["qubits"]["q1"]["xy"]) == (set(), set())
        assert ps.places_at(m, "qubits.q1.stash.0", {"__class__": QC + "SquarePulse"}) == (set(), set())


# ---------------------------------------------------------------------------
# The classification matrix
# ---------------------------------------------------------------------------

_PULSE = {"__class__": QC + "SquarePulse", "length": 40, "amplitude": 0.1}


def _kind(op, path, value=ps.ABSENT, m=None):
    ch = ps.structural_change(m or _state(), op, path, value)
    return None if ch is None else ch.kind


class TestWhatIsStructural:
    @pytest.mark.parametrize("op,path,value,kind", [
        # a pulse, created or deleted
        ("create", f"{OPS}.y90", _PULSE, "pulse"),
        ("create", f"{OPS}.alias2", "#./x180", "pulse"),
        ("create", f"{OPS}.junk", 5, "pulse"),              # any key there is a pulse
        ("create", f"{XY2}.junk", 5, "pulse"),              # ...in a found channel too
        ("delete", X180, ps.ABSENT, "pulse"),
        ("delete", f"{XY2}.number", ps.ABSENT, "pulse"),    # a broken entry is still one
        ("delete", W3, ps.ABSENT, "pulse"),                 # a gate slot
        ("delete", SPEC, ps.ABSENT, "pulse"),               # found by shape (R2)
        ("delete", SLOT, ps.ABSENT, "pulse"),
        ("create", "qubit_pairs.q1-2.macros.cz_custom.new_slot", _PULSE, "pulse"),
        ("delete", "qubits.q2.z.opx_trigger_out.operations.trigger", ps.ABSENT, "pulse"),
        # an operations dict holding pulses
        ("delete", OPS, ps.ABSENT, "operations"),
        ("create", "qubits.q2.z.operations", {"a": _PULSE}, "operations"),
        # a value write that makes / unmakes a pulse
        ("set", W3P, None, "pulse"),                          # the slot emptied
        ("set", f"{XY2}.unclassed", _PULSE, "pulse"),        # a found entry becomes one
        ("set", "qubits.q1.notes_blob", _PULSE, "pulse"),    # R2 appears in place
        ("set", f"{XY2}.x180_alias", 7, "pulse"),            # a found alias stops being one
        # a NEW non-pulse object that brings pulses (else: delete the channel,
        # create it again with one more op)
        ("create", "qubits.q1.xy3", {"operations": {"a": _PULSE}}, "carries"),
        ("create", "qubit_pairs.q1-2.macros.cz_new",
         {"__class__": "lab_pkg.gates.CZGateCustom",
          "flux_pulse_qubit": "#/qubits/q1/xy/operations/x180"}, "carries"),
        ("set", "qubits.q1.spare", {"operations": {"a": _PULSE}}, "carries"),
    ])
    def test_structural(self, op, path, value, kind):
        assert _kind(op, path, value) == kind

    @pytest.mark.parametrize("op,path,value", [
        # inside a pulse: its fields
        ("set", f"{X180}.amplitude", 0.3),
        ("create", f"{X180}.digital_marker", "ON"),
        ("delete", f"{X180}.amplitude", ps.ABSENT),
        ("set", f"{PUMP}.pump.length", 120),
        # re-links: the pulse set stays
        ("set", "qubits.q1.xy.operations.x90", "#./x180_other"),
        ("set", W3P, "#/qubits/q1/xy/operations/x180"),
        ("set", X180, "#./x90"),                            # a whitelisted op may alias
        # a non-pulse object coming or going with its pulses
        ("delete", "qubits.q1.xy2", ps.ABSENT),
        ("delete", "qubits.q1", ps.ABSENT),
        ("delete", GATE, ps.ABSENT),
        ("create", "qubit_pairs.q1-2.macros.cz_new", {"__class__": "lab_pkg.gates.CZGateCustom",
                                                       "flux_pulse_qubit": None}),
        ("create", "qubits.q1.new_block", {"k": 1}),
        ("create", "qubits.q2.xy.custom", {}),
        ("set", "qubits.q1.xy", None),                      # the channel disappears
        # an empty operations dict: no pulse comes or goes
        ("create", "qubits.q1.xy3", {"operations": {}}),
        ("delete", "qubits.q2.xy.operations", ps.ABSENT),
        # the write itself fails: never a verdict here
        ("create", X180, _PULSE),
        ("delete", f"{OPS}.nope", ps.ABSENT),
        ("create", "qubits.q9.xy.operations.a", _PULSE),
        # places no pulse can live
        ("create", "extras.p", _PULSE),
        ("create", "qubits.q1.stash.0.x", 1),
        ("set", "top_pulse.length", 5),
        ("create", "ports.x.operations.q", _PULSE),
        # an identical whole-dict write
        ("set", OPS, "SAME"),
    ])
    def test_not_structural(self, op, path, value):
        m = _state()
        if value == "SAME":
            value = copy.deepcopy(m["qubits"]["q1"]["xy"]["operations"])
        assert ps.structural_change(m, op, path, value) is None

    def test_a_whole_dict_edit_names_what_it_adds_and_removes(self):
        m = _state()
        ops = copy.deepcopy(m["qubits"]["q1"]["xy"]["operations"])
        ops["x180_renamed"] = ops.pop("x180")
        ch = ps.structural_change(m, "set", OPS, ops)
        assert ch.kind == "operations"
        assert ch.added == [f"{OPS}.x180_renamed"] and ch.removed == [X180]
        xy = copy.deepcopy(m["qubits"]["q1"]["xy"])
        del xy["operations"]["x90"]
        ch = ps.structural_change(m, "set", "qubits.q1.xy", xy)
        assert (ch.kind, ch.removed, ch.anchor) == (
            "within", ["qubits.q1.xy.operations.x90"], "qubits.q1.xy.operations.x90")

    def test_the_message_says_where(self):
        ch = ps.structural_change(_state(), "delete", X180)
        msg = ps.refusal_message(ch)
        assert msg.startswith(f"{X180} is a pulse.")
        assert "Pulses are added, removed and renamed on the Pulses page" in msg
        assert ps.goto_url(ch.anchor) == "/pulses/goto?path=qubits.q1.xy.operations.x180"
        assert ps.goto_url("a.b c", together="g.h") == "/pulses/goto?path=a.b%20c&together=g.h"


class TestJsonSame:
    @pytest.mark.parametrize("a,b,same", [
        (1, 1.0, True), (float("nan"), float("nan"), True), (True, 1, False),
        ({"a": [1, 2.0]}, {"a": [1.0, 2]}, True), ({"a": 1}, {"a": 1, "b": 2}, False),
        ("x", "x", True), (None, None, True), (None, 0, False), ([1], [1, 1], False),
    ])
    def test_table(self, a, b, same):
        assert ps.json_same(a, b) is same


def test_tree_payload_ships_only_what_the_tree_cannot_derive():
    m = _state()
    pl = ps.tree_payload(list(_rows(m)))
    assert set(pl["rows"]) == {SPEC, SLOT, INNER}   # found outside `operations`
    assert pl["rows_known"] is True and pl["gate_slots"] == list(pulse_index.GATE_SLOTS)
    assert "extras" in pl["skip_tops"] and pl["goto"] == "/pulses/goto"
    assert ps.tree_payload(None)["rows_known"] is False


# ---------------------------------------------------------------------------
# The doors
# ---------------------------------------------------------------------------

@pytest.fixture
def chip(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    c._app = app
    c._folder = folder
    return c


def _ctx(c):
    return next(iter(c._app.config["contexts"].values()))


def _store(c):
    return _ctx(c)["store"]


def _get(c, path):
    cur = _store(c).merged
    for s in path.split("."):
        cur = cur[s]
    return cur


def _refused(r):
    j = r.get_json()
    assert r.status_code == 409, r.data[:300]
    assert j["ok"] is False and j["error_kind"] == "pulse_structure", j
    assert "Pulses page" in j["error"] and j["pulses_page"].startswith("/pulses/goto?path=")
    return j


class TestTheGenericDoorsRefuse:
    def test_create(self, chip):
        n = len(_store(chip).change_log)
        j = _refused(chip.post("/field/create", data={
            "dot_path": f"{OPS}.y90", "key": "y90", "value": json.dumps(_PULSE),
            "expect_type": "dict"}))
        assert j["pulse_paths"] == [f"{OPS}.y90"]
        assert "y90" not in _get(chip, OPS) and len(_store(chip).change_log) == n
        # a field inside a pulse, and an empty channel, go
        assert chip.post("/field/create", data={
            "dot_path": f"{X180}.digital_marker", "value": "ON"}).status_code == 200
        assert chip.post("/field/create", data={
            "dot_path": "qubits.q1.xy3", "value": json.dumps({"operations": {}}),
            "expect_type": "dict"}).status_code == 200
        # ...a channel that BRINGS a pulse does not (the way round the rule)
        j = _refused(chip.post("/field/create", data={
            "dot_path": "qubits.q1.xy4", "value": json.dumps({"operations": {"a": _PULSE}}),
            "expect_type": "dict"}))
        assert "would bring the new pulse qubits.q1.xy4.operations.a" in j["error"]

    def test_delete(self, chip):
        for p in (X180, OPS, W3, SPEC):
            _refused(chip.post("/field/delete", data={"dot_path": p}))
        assert "x180" in _get(chip, OPS)
        r = chip.post("/field/delete", data={"dot_path": "qubits.q1.xy2"})
        assert r.status_code == 200, r.data[:300]

    def test_edit(self, chip):
        ops = copy.deepcopy(_get(chip, OPS))
        del ops["x90"]
        _refused(chip.post("/field/edit", data={"dot_path": OPS, "value": json.dumps(ops)}))
        # a gate's pulse slot taken out of the gate by a whole-gate edit (a
        # null typed over the slot's pointer is refused earlier: docs/121)
        gate = copy.deepcopy(_get(chip, "qubit_pairs.q1-2.macros.cz_unipolar"))
        gate["coupler_flux_pulse"] = None
        _refused(chip.post("/field/edit", data={
            "dot_path": "qubit_pairs.q1-2.macros.cz_unipolar", "value": json.dumps(gate)}))
        xy = copy.deepcopy(_get(chip, "qubits.q1.xy"))
        xy["operations"]["x270"] = copy.deepcopy(_PULSE)
        _refused(chip.post("/field/edit", data={"dot_path": "qubits.q1.xy",
                                                 "value": json.dumps(xy)}))
        # value edits inside pulses, a whole-dict edit that keeps the set,
        # and re-links stay
        ops = copy.deepcopy(_get(chip, OPS))
        ops["x180"]["amplitude"] = 0.33
        assert chip.post("/field/edit", data={"dot_path": OPS, "value": json.dumps(ops)}).status_code == 200
        assert _get(chip, f"{X180}.amplitude") == 0.33
        assert chip.post("/field/edit", data={"dot_path": f"{X180}.length",
                                               "value": "44"}).status_code == 200
        assert chip.post("/field/edit", data={
            "dot_path": W3P, "value": "#/qubits/q1/xy/operations/x180"}).status_code == 200

    def test_batch_atomic(self, chip):
        n = len(_store(chip).change_log)
        r = chip.post("/field/edit-batch", json={"updates": [
            {"dot_path": f"{X180}.length", "value": 48},
            {"dot_path": f"{OPS}.y90", "value": _PULSE, "create": True}]})
        j = _refused(r)
        assert [x["applied"] for x in j["results"]] == [False, False]
        assert j["results"][1]["error_kind"] == "pulse_structure"
        assert "another row" in j["results"][0]["error"]
        assert len(_store(chip).change_log) == n and _get(chip, f"{X180}.length") == 40

    def test_batch_a_whole_pulse_over_a_null(self, chip):
        """The batch's cheap pre-filter must not wave through a dict landing
        on a key that holds no dict yet (JSON value, or JSON text)."""
        for value in (_PULSE, json.dumps(_PULSE)):
            r = chip.post("/field/edit-batch", json={"updates": [
                {"dot_path": "qubits.q1.spare", "value": value}]})
            _refused(r)
            assert _get(chip, "qubits.q1.spare") is None

    def test_batch_rows_are_judged_in_order(self, chip):
        """A pulse whose parent an earlier row of the SAME batch creates is
        judged against what the batch has written so far -- not waved through
        because the parent did not exist yet."""
        n = len(_store(chip).change_log)
        r = chip.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.q1.newch", "value": {}, "create": True},
            {"dot_path": "qubits.q1.newch.operations", "value": {}, "create": True},
            {"dot_path": "qubits.q1.newch.operations.x", "value": _PULSE, "create": True}]})
        j = _refused(r)
        assert j["results"][2]["error_kind"] == "pulse_structure"
        assert "newch" not in _get(chip, "qubits.q1") and len(_store(chip).change_log) == n
        # delete a channel, then create it again with one more op
        xy = copy.deepcopy(_get(chip, "qubits.q1.xy"))
        xy["operations"]["y90"] = copy.deepcopy(_PULSE)
        r = chip.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.q1.xy", "delete": True},
            {"dot_path": "qubits.q1.xy", "value": xy, "create": True}]})
        _refused(r)
        assert "y90" not in _get(chip, OPS) and "x180" in _get(chip, OPS)
        # independent: that row alone is refused, the rest applies
        r = chip.post("/field/edit-batch", json={"independent": True, "updates": [
            {"dot_path": "qubits.q1.newch", "value": {"operations": {}}, "create": True},
            {"dot_path": "qubits.q1.newch.operations.x", "value": _PULSE, "create": True}]})
        j = r.get_json()
        assert j["results"][0]["applied"] is True
        assert j["results"][1]["error_kind"] == "pulse_structure"
        assert _get(chip, "qubits.q1.newch.operations") == {}

    def test_batch_independent(self, chip):
        r = chip.post("/field/edit-batch", json={"independent": True, "updates": [
            {"dot_path": f"{X180}.length", "value": 48},
            {"dot_path": X180, "delete": True}]})
        j = r.get_json()
        assert j["ok"] is False and j["results"][0]["applied"] is True
        assert j["results"][1]["error_kind"] == "pulse_structure"
        assert _get(chip, f"{X180}.length") == 48 and "x180" in _get(chip, OPS)

    def test_the_inspector_routes(self, chip):
        ops = copy.deepcopy(_get(chip, OPS))
        del ops["x90"]
        r = chip.post("/qubit/q1/edit", data={"dot_path": OPS, "value": json.dumps(ops)})
        assert r.status_code == 400 and b"renamed on the Pulses page" in r.data, r.data[:300]
        gate = copy.deepcopy(_get(chip, "qubit_pairs.q1-2.macros.cz_unipolar"))
        del gate["flux_pulse_qubit"]
        r = chip.post("/pair/q1-2/edit", data={
            "dot_path": "qubit_pairs.q1-2.macros.cz_unipolar", "value": json.dumps(gate)})
        assert r.status_code == 400 and b"renamed on the Pulses page" in r.data, r.data[:300]
        assert "x90" in _get(chip, OPS) and isinstance(_get(chip, W3), dict)

    def test_cli_set(self, tmp_path):
        from typer.testing import CliRunner

        from quam_state_manager.cli import app as cli_app
        folder = tmp_path / "c"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
        res = CliRunner().invoke(cli_app, ["set", OPS, "{}", "-f", str(folder)])
        assert res.exit_code == 1 and "Pulses page" in res.output, res.output
        res = CliRunner().invoke(cli_app, ["set", f"{X180}.length", "44", "-f", str(folder)])
        assert res.exit_code == 0, res.output


class TestTakeLiveIsNotAStructuralEdit:
    """The live-diff accept / Accept all and the sync review accept post
    ``source: "live"`` -- verified against the live chip, never trusted."""

    def _live(self, chip, mutate):
        st = json.loads((chip._folder / "state.json").read_text(encoding="utf-8"))
        mutate(st)
        p = chip._folder / "state.json"
        p.write_text(json.dumps(st), encoding="utf-8")
        future = time.time() + 100
        os.utime(p, (future, future))

    def test_a_pulse_the_live_chip_has_comes_in(self, chip):
        self._live(chip, lambda st: st["qubits"]["q1"]["xy"]["operations"].update(y90=_PULSE))
        up = {"dot_path": f"{OPS}.y90", "value": _PULSE, "create": True}
        _refused(chip.post("/field/edit-batch", json={"updates": [up]}))
        r = chip.post("/field/edit-batch", json={"updates": [up], "source": "live"})
        assert r.status_code == 200 and r.get_json()["ok"], r.data[:300]
        assert _get(chip, f"{OPS}.y90")["length"] == 40

    def test_a_value_the_live_chip_does_not_hold_is_refused(self, chip):
        self._live(chip, lambda st: st["qubits"]["q1"]["xy"]["operations"].update(y90=_PULSE))
        edited = dict(_PULSE, length=99)
        _refused(chip.post("/field/edit-batch", json={"source": "live", "updates": [
            {"dot_path": f"{OPS}.y90", "value": edited, "create": True}]}))

    def test_a_pulse_the_live_chip_dropped_goes_and_one_it_kept_does_not(self, chip):
        self._live(chip, lambda st: st["qubits"]["q1"]["xy"]["operations"].pop("x90"))
        r = chip.post("/field/edit-batch", json={"source": "live", "independent": True,
                                                  "updates": [
            {"dot_path": "qubits.q1.xy.operations.x90", "delete": True},
            {"dot_path": X180, "delete": True}]})
        j = r.get_json()
        assert j["results"][0]["applied"] is True
        assert j["results"][1]["error_kind"] == "pulse_structure"
        assert "x90" not in _get(chip, OPS) and "x180" in _get(chip, OPS)

    def test_undo_is_never_asked(self, chip):
        r = chip.post("/api/pulse/delete", data={"path": X180, "force": "1"})
        assert r.status_code == 200, r.data[:300]
        assert "x180" not in _get(chip, OPS)
        assert chip.post("/undo").status_code == 200
        assert "x180" in _get(chip, OPS)
        assert chip.post("/redo").status_code == 200
        assert "x180" not in _get(chip, OPS)


# ---------------------------------------------------------------------------
# The Pulses page's own doors
# ---------------------------------------------------------------------------

class TestDeleteTogetherIsItsOwnVerifiedDoor:
    def test_the_set_is_verified(self, chip):
        dt = lambda main, paths: chip.post("/api/pulse/delete-together",  # noqa: E731
                                           json={"path": main, "paths": paths})
        r = dt(X180, [X180])
        assert r.status_code == 400 and "two or more" in r.get_json()["error"]
        r = dt(X180, [f"{OPS}.x90", SPEC])
        assert r.status_code == 400                          # main not in the set
        r = dt(X180, [X180, "qubits.q1.anharmonicity"])
        assert r.status_code == 400 and "qubits.q1.anharmonicity" in r.get_json()["error"]
        r = dt("qubits.q1.notes_blob", ["qubits.q1.notes_blob", "qubits.q1.stash"])
        assert r.status_code == 400
        assert "x180" in _get(chip, OPS) and "anharmonicity" in _get(chip, "qubits.q1")

    def test_one_batch_one_ctrl_z(self, chip):
        st = _store(chip)
        before = json.dumps(st.merged, sort_keys=True)
        r = chip.post("/api/pulse/delete-together", json={
            "path": X180, "paths": [X180, "qubits.q1.xy.operations.x90"], "group": "new"})
        assert r.status_code == 200 and r.get_json()["ok"], r.data[:300]
        assert "x180" not in _get(chip, OPS) and "x90" not in _get(chip, OPS)
        assert len({e.group_id for e in st.change_log[-2:]}) == 1
        assert chip.post("/undo").status_code == 200
        assert json.dumps(st.merged, sort_keys=True) == before

    def test_the_chip_gate_still_holds(self, chip):
        r = chip.post("/api/pulse/delete-together", json={
            "path": X180, "paths": [X180, f"{OPS}.x90"], "expect_chip": "not-this-chip"})
        assert r.status_code == 409 and "x180" in _get(chip, OPS), r.data[:300]


class TestTheLinkLandsOnThePulse:
    def test_goto(self, chip):
        r = chip.get(f"/pulses/goto?path={X180}&json=1")
        assert r.get_json()["url"] == f"/pulses?owner=q1&channel=xy&pulse={X180}"
        assert "HX-Location" not in r.headers
        r = chip.get(f"/pulses/goto?path={X180}.amplitude")
        assert r.status_code == 302 and r.headers["Location"].endswith(f"pulse={X180}")
        r = chip.get(f"/pulses/goto?path={OPS}")
        assert r.headers["Location"].endswith("/pulses?owner=q1&channel=xy")
        r = chip.get(f"/pulses/goto?path={W3}&together={GATE}")
        assert r.headers["Location"].endswith(
            f"/pulses?owner=q1-2&channel=flux&pulse={W3}&together={GATE}")
        r = chip.get("/pulses/goto?path=qubits.q2.xy.operations")      # empty: its owner
        assert r.headers["Location"].endswith("/pulses?owner=q2&channel=xy")

    def test_the_together_offer_opens_in_the_delete_step(self, chip):
        html = chip.get(f"/pulses?owner=q1&pulse={X180}&together={OPS}.x90").get_data(as_text=True)
        assert f"/pulse/detail?path={X180}&amp;together=qubits.q1.xy.operations.x90" in html
        html = chip.get(f"/pulses?owner=q1&pulse={X180}").get_data(as_text=True)
        assert "together=" not in html.split('id="pulse-open-loader"', 1)[1][:200]
        d = chip.get(f"/pulse/detail?path={X180}&together=qubits.q1.xy.operations.x90").get_data(as_text=True)
        confirm = d.split('class="pulse-delete-confirm"', 1)[1][:60]
        assert "hidden" not in confirm.split(">", 1)[0]
        assert "/api/pulse/delete-together/offer?path=qubits.q1.xy.operations.x90" in d
        # without it the step stays closed
        d = chip.get(f"/pulse/detail?path={X180}").get_data(as_text=True)
        assert d.split('class="pulse-delete-confirm"', 1)[1].startswith(" hidden")
        r = chip.get(f"/api/pulse/delete-together/offer?path={OPS}.x90&pulse={X180}")
        assert r.status_code == 200 and b"can be deleted on its own" in r.data
        r = chip.get(f"/api/pulse/delete-together/offer?path={OPS}.gone&pulse={X180}")
        assert r.status_code == 200 and b"not on this chip" in r.data

    def test_the_tree_asks_for_the_rows_after_a_cold_render(self, chip):
        _store(chip).mutation_seq += 1                  # cold: /explorer cannot say
        html = chip.get("/explorer").get_data(as_text=True)
        assert '"rows_known": false' in html and "window._pulseGateFill()" in html
        pl = chip.get("/explorer/pulse-gate").get_json()
        assert pl["rows_known"] is True and set(pl["rows"]) == {SPEC, SLOT, INNER}

    def test_the_tree_payload_never_builds_a_cold_index(self, chip):
        def payload():
            html = chip.get("/explorer").get_data(as_text=True)
            return json.loads(html.split("window._treePulseGate = ", 1)[1].split(";\n", 1)[0])
        chip.get("/pulses")                                  # warm it
        idx = _ctx(chip)["pulse_index"]
        pl = payload()
        assert pl["rows_known"] is True and set(pl["rows"]) == {SPEC, SLOT, INNER}
        # an unexplained change: only a whole-chip walk could say -- the
        # tree does not wait for one (the write doors still check)
        cold0 = idx.stats["cold"]
        _store(chip).mutation_seq += 1
        pl = payload()
        assert pl["rows_known"] is False and pl["rows"] == []
        assert idx.stats["cold"] == cold0


# ---------------------------------------------------------------------------
# The tree (jsdom, the REAL app.js)
# ---------------------------------------------------------------------------

def test_pulse_gate_tree_selfcheck():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                      cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "pulse_gate_tree_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ALL OK" in res.stdout
