import json
import os
import subprocess
import sys

import numpy as np
import pytest

from quam_state_manager.core.mapfit.api import analyze, canonical_json, request_hash
from tests.mapfit_synthetic import sample

LABELS = {"erroramp":("amp_prefactor","nb_of_pulses"),"ridge":("flux","frequency"),
          "chevron":("duration","detuning"),"landscape":("a_scan","b_over_a"),
          "extremum":("position","index")}


def request(model):
    z,x,y,meta,truth = sample(model)
    lx,ly = LABELS[model]
    return {"z":z,"x":x,"y":y,"meta":{**meta,"x":{"name":lx},"y":{"name":ly}},
            "smooth":{"sigma_x":0,"sigma_y":0},"fit":{"model":"auto"}}


@pytest.mark.parametrize("model",list(LABELS))
def test_api_caps_override_and_wrong_old_verdict(model):
    body = request(model)
    body["smooth"] = {"sigma_x":100,"sigma_y":100,"override_cap":True}
    out = analyze(body)
    assert "cap overridden" in out["fit"]["verdict_line"]
    assert out["caps_applied"]["override"]
    body["smooth"] = {"sigma_x":0,"sigma_y":0}
    body["meta"].update(patch={"old":.9,"value":.95},current_old=.8)
    out = analyze(body)
    assert "display-only" in out["fit"]["verdict_line"]
    assert out["fit"]["apply_allowed"] is False


@pytest.mark.parametrize("model",list(LABELS))
def test_api_transposed_axes_keep_model_and_values(model):
    body = request(model)
    first = analyze(body)
    body["z"] = body["z"].T
    body["x"],body["y"] = body["y"],body["x"]
    body["meta"]["x"],body["meta"]["y"] = body["meta"]["y"],body["meta"]["x"]
    if "phase" in body["meta"]:
        body["meta"]["phase"] = body["meta"]["phase"].T
    second = analyze(body)
    assert first["classification"]["type"] == second["classification"]["type"]
    assert second["fit"]["verdict_line"] == "VERDICT: ok"
    if model in ("erroramp","ridge","chevron"):
        assert first["fit"]["values"] == second["fit"]["values"]
    else:
        assert second["fit"]["values"]["x"] == pytest.approx(first["fit"]["values"]["y"],abs=.5)


@pytest.mark.parametrize("model",list(LABELS))
def test_api_turns_with_degree_label(model):
    body = request(model)
    body["z"] = np.asarray(body["z"])%1
    body["meta"]["signal_units"] = "deg"
    if "phase" in body["meta"]:
        body["meta"]["phase"] = body["meta"]["phase"]/360
        body["meta"]["circular"] = 1.
    out = analyze(body)
    assert out["classification"]["circular"] == 1.
    assert out["fit"]["verdict_line"].startswith("VERDICT: ")


def test_api_full_request_cross_process_hash_and_numbers(tmp_path):
    body = request("landscape")
    body["smooth"] = {"sigma_x":1,"sigma_y":1}
    body["contour"] = {"on":True,"mode":"custom","level":.5}
    expected = analyze(body)
    path = tmp_path/"request.json"
    path.write_text(canonical_json(body),encoding="utf-8")
    code = "import json,sys; from quam_state_manager.core.mapfit.api import analyze,canonical_json; print(canonical_json(analyze(json.load(open(sys.argv[1])))))"
    outputs = []
    for seed in ("3","71"):
        run = subprocess.run([sys.executable,"-c",code,str(path)],capture_output=True,text=True,
                             env={**os.environ,"PYTHONHASHSEED":seed},timeout=60)
        assert run.returncode==0,run.stderr
        outputs.append(json.loads(run.stdout))
    assert outputs[0] == outputs[1] == expected
    assert request_hash(body) == request_hash(json.loads(canonical_json(body)))
    altered = dict(body,figure_cache_key="new revision")
    assert request_hash(body)!=request_hash(altered)
    assert len(expected["request_hash"])==64


def test_api_count_lock_model_menu_and_nan_json():
    body = request("erroramp")
    body["smooth"] = {"sigma_x":2,"sigma_y":3}
    out = analyze(body)
    assert out["caps_applied"]["sigma_y"]==0
    assert out["fit"]["verdict"]=="ok"
    assert len(out["caps_applied"]["row_sigmas"])==16
    body["fit"] = {"model":"ridge"}
    with pytest.raises(ValueError,match="not available"):
        analyze(body)
    assert canonical_json({"nan":np.nan,"array":np.array([1,np.inf])}) == '{"array":[1.0,null],"nan":null}'
    with pytest.raises(ValueError):
        analyze({"z":[[1,2],[3,4]],"x":[1,2],"y":[1,2]})
