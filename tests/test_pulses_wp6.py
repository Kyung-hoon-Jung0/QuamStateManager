"""Pins for the final Pulses browser findings in v1.3.0."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.test_lab_doors import lab, _store, _v, G, FL_C, FL_T


@pytest.mark.parametrize("field,value", [("flat_length", 40), ("padding", 8)])
@pytest.mark.parametrize("mirror", [False, True])
def test_coupled_pulses_offer_the_existing_batch(lab, field, value, mirror):
    c, fake = lab
    source = "qubits.q1.z.operations.czl_q1" if mirror else G + ".flux_pulse_qubit"
    target = G + ".flux_pulse_qubit." + field
    partner = G + ".flux_pulse_target." + field
    before = _v(c, target)
    r = c.post("/pulse/edit", data={"path": source, "dot_path": source + "." + field,
                                     "mode": "value", "value": str(value)})
    assert r.status_code == 400
    payload = json.loads(r.headers.get("X-Lab-Refusal", "{}"))
    assert payload.get("lab_follow") == [{"dot_path": target, "value": value},
                                          {"dot_path": partner, "value": value}]
    assert not _store(c).change_log and _v(c, target) == before
    r = c.post("/field/edit-batch", json={"updates": payload["lab_follow"], "group": "new"})
    assert r.status_code == 200, r.data[:300]
    assert _v(c, target) == _v(c, partner) == value
    assert len(_store(c).change_log) == 2
    assert len({e.group_id for e in _store(c).change_log}) == 1
    assert c.post("/undo").status_code == 200
    assert _v(c, target) == _v(c, partner) == before
    assert not _store(c).change_log


def test_pulses_wp6_client_selfcheck():
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(["node", str(root / "tests/pulses_wp6_selfcheck.cjs")],
                          cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "assertions passed" in proc.stdout
