"""P2 fixes for the lab-additions feature: honest reasons, wiring, round trips."""
from __future__ import annotations

from quam_state_manager.core import additions

SPEC = {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "populate": {},
        "lines": [{"line": "drive", "element": "q1"}, {"line": "drive", "element": "q2"}]}
PULSE = {"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "x_lab",
         "class": "labpkg.pulses.LabPulse", "fields": {}}


def test_unprobed_class_is_not_called_absent():
    man = {"classes": {"other.Cls": {"role": "pulse", "fields": {}}}, "missing_classes": []}
    assert additions.manifest_reason(PULSE, man) is None
    res = additions.lint_proposal(SPEC, PULSE, man)
    assert not any("not in this environment" in e for e in res["errors"]), res
    assert any("not been probed" in w for w in res["warnings"]), res


def test_no_manifest_says_not_probed():
    res = additions.lint_proposal(SPEC, PULSE, None)
    assert res["ok"], res
    assert any("not been probed" in w for w in res["warnings"]), res
    assert not any("not in this environment" in e for e in res["errors"])


def test_a_class_the_probe_could_not_find_is_absent():
    man = {"classes": {"other.Cls": {"role": "pulse", "fields": {}}},
           "missing_classes": ["labpkg.pulses.LabPulse"]}
    assert "not in this environment" in additions.manifest_reason(PULSE, man)


# -- the proposal doors trust the window proof, never the header ----------
import pytest

from quam_state_manager.web.app import create_app
from quam_state_manager.web import callers

DOORS = ["/generate/proposals/p1/accept", "/generate/proposals/p1/dismiss",
         "/generate/proposals/context"]


@pytest.fixture
def proving(tmp_path):
    a = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    a.config["PROVE_CALLERS"] = True
    return a


@pytest.mark.parametrize("path", DOORS)
def test_no_header_is_not_a_person(proving, path):
    """A plain curl (no agent header, no window cookie) is refused."""
    r = proving.test_client().post(path, json={"spec": {}})
    assert r.status_code == 403, (path, r.status_code)


@pytest.mark.parametrize("path", DOORS)
def test_an_agent_header_is_refused(proving, path):
    cl = proving.test_client()
    cl.set_cookie("sm_person", proving.config["SM_PERSON_SECRET"], domain="localhost")
    r = cl.post(path, json={"spec": {}}, headers={"X-SM-Agent": "claude"})
    assert r.status_code == 403


def test_the_window_proof_lets_a_person_through(proving):
    cl = proving.test_client()
    cl.set_cookie("sm_person", proving.config["SM_PERSON_SECRET"], domain="localhost")
    r = cl.post("/generate/proposals/context", json={"spec": {}})
    assert r.status_code == 200, r.get_json()


def test_the_trust_model_is_written_down():
    doc = callers.__doc__ or ""
    assert "LABEL, never an authority" in doc
    for ep in ("main.generate_proposal_accept", "main.generate_proposal_dismiss",
               "main.generate_proposals_context"):
        assert ep in callers.PERSON_ONLY


def test_wizard_p2fix_selfcheck():
    import shutil
    import subprocess
    from pathlib import Path
    if not shutil.which("node"):
        pytest.skip("node not installed")
    p = subprocess.run(["node", str(Path(__file__).with_name("gen_additions_p2fix_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.startswith("ok ")
