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


# -- a recalibrated chip keeps its additions record through Re-generate -----
def _chip_with_addition(tmp_path):
    import json
    from quam_state_manager.core import regen_spec
    state = {"qubits": {"q1": {"xy": {"operations": {
        "x_lab": {"__class__": "labpkg.pulses.LabPulse", "length": 40, "amplitude": 0.1}}}}}}
    wiring = {"wiring": {"qubits": {"q1": {}}}}
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    spec = {"qubits": ["q1"], "additions": [
        {"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "x_lab",
         "class": "labpkg.pulses.LabPulse", "fields": {"length": 40, "amplitude": 0.1},
         "env": {"python": "py", "versions": {}}},
        {"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "gone",
         "class": "labpkg.pulses.LabPulse", "fields": {}}]}
    regen_spec.write_spec_sidecar(d, spec, state, wiring)
    return d, state, wiring


def test_recalibrated_chip_keeps_its_additions(tmp_path):
    import json
    from quam_state_manager.core import regenerate
    d, state, wiring = _chip_with_addition(tmp_path)
    # a recalibration: the content hash moves, the exact sidecar no longer applies
    state["qubits"]["q1"]["xy"]["operations"]["x_lab"]["amplitude"] = 0.237
    (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
    rec = regenerate.reconstruct_from_folder(d)
    assert not rec.exact
    adds = rec.spec.get("additions") or []
    assert [a["name"] for a in adds] == ["x_lab"], adds          # an object that is gone is not carried
    assert adds[0]["fields"]["amplitude"] == 0.237               # the chip's current number, never the build's
    assert adds[0].get("env"), "the build record (the wizard's 'carried' marking) survives"


def test_unchanged_chip_still_uses_the_exact_sidecar(tmp_path):
    from quam_state_manager.core import regenerate
    d, _, _ = _chip_with_addition(tmp_path)
    rec = regenerate.reconstruct_from_folder(d)
    assert rec.exact and len(rec.spec["additions"]) == 2


def test_the_lint_route_judges_every_listed_addition_in_one_request(tmp_path):
    a = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    good = dict(PULSE, name="x_ok")
    clash = dict(PULSE, name="x_ok")            # the same name twice on one channel
    spec = dict(SPEC, additions=[good, clash])
    r = a.test_client().post("/generate/additions/lint", json={"spec": spec, "indices": [0, 1, 7]})
    body = r.get_json()
    assert r.status_code == 200 and len(body["results"]) == 3
    assert any("x_ok" in e for e in body["results"][1]["errors"]), body
    assert body["results"][2]["errors"] == ["no such addition"]
    # the single-addition form is kept
    one = a.test_client().post("/generate/additions/lint", json={"spec": SPEC, "addition": PULSE})
    assert "ok" in one.get_json()


def test_populate_mounts_the_additions_section_when_it_nears_the_view():
    """60Q: the additions section's DOM stays out of the Populate step's first
    forced layout (real Chrome, see the study note); a source pin, since jsdom
    has no IntersectionObserver (and then mounts at once, as before)."""
    from pathlib import Path
    js = (Path(__file__).parents[1] / "quam_state_manager" / "web" / "static" / "generate.js").read_text(encoding="utf-8")
    i = js.index("function enterPopulateStep")
    body = js[i:js.index("// -- step 7", i)]
    assert "mountAdditionsWhenNear(document.getElementById(\"gen-pop-additions\")" in body
    assert "window.GenAdditions.mount(\n      document.getElementById(\"gen-pop-additions\")" not in body
    f = js[js.index("function mountAdditionsWhenNear"):]
    f = f[:f.index("\n  }\n")]
    assert "IntersectionObserver" in f and "host.childNodes.length" in f
