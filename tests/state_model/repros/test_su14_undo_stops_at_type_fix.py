"""SU-14: Ctrl+Z cannot walk past a saved type-fix.

Type-fix converts a numeric-looking text leaf to a number and persists a type
assignment for the field. After Save, the journal walk reaches that unit and
tries to stage the OLD text back: the assignment refuses it, the press answers
409 "Undo (staged) failed", and every later Ctrl+Z hits the same unit -- the
rest of the journal below it can no longer be walked.
Harness: TYPEFIX, SAVE, UNDO.
Contract: docs/107 (the journal step restores the historical value verbatim,
``coerce=False``, "the field's CURRENT type may differ (e.g. a later type-fix)").
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-14: the type assignment refuses the verbatim restore")
def test_ctrl_z_walks_back_over_a_type_fix(tmp_path):
    chip = Chip(tmp_path)
    path = "twpas.twpa1.spectroscopy_amplitude"
    assert chip.ctx["store"].state["twpas"]["twpa1"]["spectroscopy_amplitude"] == "0.12"
    r = chip.c.post("/type-fix/apply", json={"paths": [path]})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    assert chip.c.post("/save").status_code == 200
    r = chip.c.post("/undo")
    assert r.status_code == 200, r.get_data(as_text=True)[-300:]
    assert chip.ctx["store"].merged["twpas"]["twpa1"]["spectroscopy_amplitude"] == "0.12"
