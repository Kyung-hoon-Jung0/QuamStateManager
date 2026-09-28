"""w8 (docs/218 open issue): the Pulses page's delete refusal offers the same
"Delete together with ..." as the Json Tree.

Before: deleting a lab gate's inline pulse (or an op the gate plays BY NAME)
from the Pulses page was refused -- rightly, it would break generate_config()
or the gate's own apply() -- with a toast saying "delete them together", on a
page that had no batch delete. The user had to go to the Json Tree.

Now the refusal lands in the delete step (HX-Retarget #pulse-delete-result)
and lists EXACTLY what must go with the pulse (``routes._lab_delete_also``,
one function for every door); one press deletes that set in ONE
/field/edit-batch that the same lab check asks again as a whole, and one
Ctrl+Z restores all. The worker (a fake that answers like the real one) is
the fixture of tests/test_lab_doors.py.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import types
from html import unescape
from pathlib import Path

import pytest

from tests.test_lab_doors import (FakeLab, G, GATE, OP_C, OP_T, _by_name,  # noqa: F401
                                  _store, _v, _walk, lab)

_ROOT = Path(__file__).resolve().parent.parent
SLOT_T = f"{G}.flux_pulse_target"
SLOT_C = f"{G}.flux_pulse_qubit"


def _together(r) -> list[str]:
    m = re.search(r"data-together='([^']*)'", r.get_data(as_text=True))
    assert m, r.get_data(as_text=True)[:600]
    return json.loads(unescape(m.group(1)))


def _refused(c, path):
    r = c.post("/api/pulse/delete", data={"path": path, "force": "1"})
    assert r.status_code == 400, r.data[:400]
    assert r.headers.get("HX-Retarget") == "#pulse-delete-result"
    assert r.headers.get("HX-Reswap") == "innerHTML"
    assert b"pulse-delete-refused" in r.data
    return r


def _required_manifest(monkeypatch, c):
    """The env's own schema for the gate class: flux_pulse_qubit has no
    default (quam_builder's CZGate), flux_pulse_target is Optional."""
    st = _store(c)
    man = {"classes": {GATE: {"importable": True, "fields": {
        "flux_pulse_qubit": {"type": {"base": "any"}, "has_default": False,
                             "optional": False},
        "flux_pulse_target": {"type": {"base": "any"}, "has_default": True,
                              "optional": True}}}}}
    pol = getattr(st, "type_policy", None)
    if pol is not None and hasattr(pol, "manifest"):
        monkeypatch.setattr(pol, "manifest", man, raising=False)
    else:
        monkeypatch.setattr(st, "type_policy", types.SimpleNamespace(manifest=man),
                            raising=False)


class TestTheRefusalNamesWhatGoesTogether:
    def test_the_gate_s_inline_pulse_goes_with_its_by_name_mirror_op(self, lab):
        c, fake = lab
        _by_name(_store(c))
        n = len(_store(c).change_log)
        r = _refused(c, SLOT_T)
        assert _together(r) == [SLOT_T, OP_T]
        body = r.get_data(as_text=True)
        assert "generate_config()" in body and "Delete together with 1 op" in body
        assert "played by name by" in body          # the op's role, named
        assert len(_store(c).change_log) == n        # nothing written
        assert "flux_pulse_target" in _v(c, G)

    def test_an_op_the_gate_plays_by_name_goes_with_the_gate_field_holding_the_name(self, lab):
        c, fake = lab
        _by_name(_store(c))
        r = _refused(c, OP_T)
        assert _together(r) == [OP_T, SLOT_T]
        body = r.get_data(as_text=True)
        assert "not found" in body                   # the gate's own apply()
        assert "Delete together with 1 gate field" in body
        assert "czl_q2t" in _v(c, "qubits.q2.z.operations")

    def test_a_required_gate_field_brings_the_whole_gate(self, lab, monkeypatch):
        """The control op's name is held by flux_pulse_qubit, which the env's
        own schema says the gate cannot exist without: the gate goes, and
        with it its other by-name op."""
        c, fake = lab
        _by_name(_store(c))
        _required_manifest(monkeypatch, c)
        r = _refused(c, OP_C)
        assert _together(r) == [OP_C, G, OP_T]
        assert "Delete together with 1 gate and 1 op" in r.get_data(as_text=True)

    def test_the_gate_s_required_inline_pulse_itself_brings_the_gate(self, lab, monkeypatch):
        """Deleting the control pulse held IN the gate: the gate cannot exist
        without it -- the offer is the gate and both by-name ops, and the
        pulse the user pressed on goes inside it (folded, never deleted
        twice: a batch deleting the gate and then its field would fail)."""
        c, fake = lab
        _by_name(_store(c))
        _required_manifest(monkeypatch, c)
        r = _refused(c, SLOT_C)
        assert _together(r) == [SLOT_C, G, OP_C, OP_T]
        r = c.post("/field/edit-batch", json={"updates": [
            {"dot_path": p, "delete": True} for p in _together(r)], "group": "new"})
        assert r.status_code == 200, r.data[:300]
        assert "czl" not in _v(c, "qubit_pairs.q1-q2.macros")

    def test_without_an_env_schema_the_field_is_offered_and_the_check_judges(self, lab):
        c, fake = lab
        _by_name(_store(c))
        r = _refused(c, OP_C)
        assert _together(r) == [OP_C, SLOT_C]
        # the batch still breaks the gate: refused as a whole, nothing written
        r = c.post("/field/edit-batch", json={"updates": [
            {"dot_path": p, "delete": True} for p in (OP_C, SLOT_C)], "group": "new"})
        assert r.status_code == 400 and r.get_json()["lab_refused"] is True, r.data[:300]
        assert "czl_q1" in _v(c, "qubits.q1.z.operations") and "flux_pulse_qubit" in _v(c, G)

    def test_the_json_tree_names_the_same_set(self, lab):
        """One function for every door: /field/delete's lab_delete_also is the
        Pulses page's list without the pulse itself."""
        c, fake = lab
        _by_name(_store(c))
        j = c.post("/field/delete", data={"dot_path": OP_T}).get_json()
        assert j["lab_refused"] is True and j["lab_delete_also"] == [SLOT_T]
        # the tree's button said "1 op" for a gate field: the server names it
        assert j["lab_delete_label"] == "Delete together with 1 gate field"
        j = c.post("/field/delete", data={"dot_path": SLOT_T}).get_json()
        assert j["lab_delete_also"] == [OP_T]
        assert j["lab_delete_label"] == "Delete together with 1 op"


class TestTheOfferGoesThroughAsOneBatch:
    def test_one_batch_one_ctrl_z_restores_all(self, lab):
        c, fake = lab
        st = _store(c)
        _by_name(st)
        before = json.dumps(st.merged, sort_keys=True)
        together = _together(_refused(c, SLOT_T))
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": p, "delete": True} for p in together],
            "group": "new"})
        assert r.status_code == 200 and r.get_json()["ok"], r.data[:400]
        assert "flux_pulse_target" not in _v(c, G)
        assert "czl_q2t" not in _v(c, "qubits.q2.z.operations")
        gids = {e.group_id for e in st.change_log[-2:]}
        assert len(gids) == 1 and None not in gids   # ONE Ctrl+Z group
        r = c.post("/undo")
        assert r.status_code == 200, r.data[:300]
        assert json.dumps(st.merged, sort_keys=True) == before

    def test_the_whole_gate_batch_applies_and_undoes(self, lab, monkeypatch):
        c, fake = lab
        st = _store(c)
        _by_name(st)
        _required_manifest(monkeypatch, c)
        before = json.dumps(st.merged, sort_keys=True)
        together = _together(_refused(c, OP_C))
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": p, "delete": True} for p in together],
            "group": "new"})
        assert r.status_code == 200, r.data[:400]
        assert "czl" not in _v(c, "qubit_pairs.q1-q2.macros")
        assert c.post("/undo").status_code == 200
        assert json.dumps(st.merged, sort_keys=True) == before

    def test_a_batch_that_still_breaks_the_chip_names_what_else_must_go(self, lab):
        """Only the op, without the gate field: refused as a whole, and the
        refusal names the field -- the offer widens instead of dead-ending."""
        c, fake = lab
        _by_name(_store(c))
        r = c.post("/field/edit-batch", json={"updates": [
            {"dot_path": OP_T, "delete": True}], "group": "new"})
        j = r.get_json()
        assert r.status_code == 400 and j["lab_refused"] is True
        assert j["lab_delete_also"] == [SLOT_T]

    def test_a_worker_that_cannot_run_never_blocks_and_says_unchecked(self, lab, monkeypatch):
        c, fake = lab
        _by_name(_store(c))
        together = _together(_refused(c, SLOT_T))
        from quam_state_manager.core import config_generator
        monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: None)
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": p, "delete": True} for p in together],
            "group": "new"})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"], r.data[:300]
        assert "NOT checked" in j["warning"] and "written unchecked" in j["warning"]
        assert "flux_pulse_target" not in _v(c, G)

    def test_a_worker_that_crashes_never_blocks_and_says_unchecked(self, lab, monkeypatch):
        c, fake = lab
        _by_name(_store(c))
        together = _together(_refused(c, SLOT_T))
        from quam_state_manager.core import lab_waveform

        def boom(*a, **k):
            raise TimeoutError("the lab worker did not answer in 90 s")
        monkeypatch.setattr(lab_waveform, "draw", boom)
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": p, "delete": True} for p in together],
            "group": "new"})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"], r.data[:300]
        assert "NOT checked" in j["warning"] and "did not answer" in j["warning"]


class TestOrdinaryDeletesAreUnchanged:
    def test_a_free_op_deletes_alone_with_no_offer(self, lab):
        c, fake = lab
        st = _store(c)
        st.state["qubits"]["q2"]["z"]["operations"]["free"] = {
            "__class__": "quam.P", "amplitude": 0.1}
        st.structure_seq += 1
        r = c.post("/api/pulse/delete", data={"path": "qubits.q2.z.operations.free",
                                              "force": "1"})
        assert r.status_code == 200, r.data[:300]
        assert "HX-Retarget" not in r.headers
        assert b"Deleted free" in r.data and b"pulse-delete-refused" not in r.data
        assert "pulses-changed" in r.headers.get("HX-Trigger", "")

    def test_the_unforced_by_name_answer_is_still_the_409(self, lab):
        c, fake = lab
        _by_name(_store(c))
        r = c.post("/api/pulse/delete", data={"path": OP_T})
        assert r.status_code == 409 and b"flux_pulse_target.id" in r.data
        assert "HX-Retarget" not in r.headers

    def test_the_detail_carries_the_refusal_slot(self, lab):
        c, fake = lab
        _by_name(_store(c))
        t = c.get(f"/pulse/detail?path={OP_T}").get_data(as_text=True)
        confirm = t.split('class="pulse-delete-confirm"', 1)[1].split("</div>\n\n", 1)[0]
        assert 'id="pulse-delete-result"' in confirm


def test_pulses_delete_together_selfcheck():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                      cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "pulses_delete_together_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok - a Ctrl+Z pressed while the batch is checked waits for it" in res.stdout
