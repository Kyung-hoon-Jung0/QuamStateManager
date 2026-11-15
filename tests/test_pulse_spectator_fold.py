"""The Pulses page folds the spectator pulses of gates: the server marks them
from the gates (spectator_qubits_control), never from an operation's name."""
from __future__ import annotations

import re

from quam_state_manager.core.pulse_index import fold_spectators, spectator_marks
from tests.test_cz_gate_spectators import CHECK, CREATE, KEY, _body, app  # noqa: F401 (fixture)


def _merged():
    return {"qubits": {}, "qubit_pairs": {"q1-q2": {"macros": {
        "cz_a": {"spectator_qubits_control": {"q3": "#/qubits/q3/z/operations/cz_a_spectator_pulse_q1_q2",
                                              "q4": {"id": "cz_a_spectator_pulse_q1_q2", "length": 48}}},
        "cz_b": {"flux_pulse_qubit": "#/qubits/q1/z/operations/cz_b_pulse"}}}}}


def test_marks_come_from_the_gates_for_references_and_owned_pulses():
    m = spectator_marks(_merged())
    assert m["qubits.q3.z.operations.cz_a_spectator_pulse_q1_q2"] == {"gate": "cz_a", "pair": "q1-q2", "qubit": "q3"}
    assert m["qubits.q4.z.operations.cz_a_spectator_pulse_q1_q2"]["qubit"] == "q4"
    assert "qubit_pairs.q1-q2.macros.cz_a.spectator_qubits_control.q4" in m
    assert not any("cz_b" in k for k in m)
    # a name that looks like a spectator is not one unless a gate says so
    assert "qubits.q3.z.operations.cz_x_spectator_pulse_q1_q2" not in m


def _r(owner, ch, op):
    return {"path": f"qubits.{owner}.{ch}.operations.{op}", "owner": owner, "channel": ch}


def test_fold_puts_one_fold_row_after_the_channel_and_keeps_the_rest():
    rows = [_r("q3", "z", "const"), _r("q3", "z", "s1"), _r("q3", "z", "flux"), _r("q3", "z", "s2"), _r("q3", "xy", "x180")]
    marks = {rows[1]["path"]: {"gate": "g1", "pair": "p"}, rows[3]["path"]: {"gate": "g2", "pair": "p"}}
    out = fold_spectators(rows, marks)
    assert [r.get("path", "FOLD") for r in out] == [rows[0]["path"], rows[2]["path"], "FOLD", rows[1]["path"], rows[3]["path"], rows[4]["path"]]
    fold = out[2]
    assert fold["fold"] and fold["fold_n"] == 2 and fold["fold_key"] == "q3.z"
    assert out[3]["spectator_of"]["gate"] == "g1" and out[4]["fold_key"] == "q3.z"
    assert out[0] is rows[0] and "spectator_of" not in rows[1]
    assert fold_spectators(rows, {}) is rows


def test_the_fold_button_opens_and_closes_its_rows():
    import shutil
    import subprocess
    from pathlib import Path
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(Path(__file__).parent / "pulses_spectator_fold_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if "Cannot find module 'jsdom'" in r.stderr:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "6 assertions ok" in r.stdout, r.stdout + r.stderr


def test_the_pulses_page_folds_a_created_gate_s_spectators(app):  # noqa: F811
    chk = app.c.post(CHECK, json=_body()).get_json()
    made = app.c.post(CREATE, json={"check_token": chk["check_token"], "plan_sig": chk["plan_sig"]}).get_json()
    assert made["ok"], made
    html = app.c.get("/pulses?rows=1&per_page=500").get_data(as_text=True)
    folds = re.findall(r'data-pulse-fold-toggle="([^"]+)"[^>]*>([^<]+)<', html)
    assert ("q3.z", "1 spectator pulse of CZ gates") in folds and ("q4.z", "1 spectator pulse of CZ gates") in folds, folds
    member = re.search(r'<tr[^>]*data-pulse-path="qubits\.q3\.z\.operations\.' + KEY + r'"[^>]*>', html).group(0)
    assert 'data-pulse-fold-member="q3.z"' in member and " hidden" in member
    assert "spectator of cz_sp on q1-q2" in html
    # the calibrated pulse of the same line stays a plain row
    const = re.search(r'<tr[^>]*data-pulse-path="qubits\.q3\.z\.operations\.const"[^>]*>', html).group(0)
    assert "data-pulse-fold-member" not in const and " hidden" not in const
    # a row re-rendered in place stays inside its fold
    one = app.c.get(f"/pulse/row?path=qubits.q3.z.operations.{KEY}").get_data(as_text=True)
    assert 'data-pulse-fold-member="q3.z"' in one and "spectator of cz_sp" in one
