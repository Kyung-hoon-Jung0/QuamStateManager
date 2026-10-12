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


def test_api_contours_come_back_in_figure_orientation_when_transposed():
    body = request("ridge")
    body["contour"] = {"on": True}
    first = analyze(body)
    body["z"] = np.asarray(body["z"]).T
    body["x"],body["y"] = body["y"],body["x"]
    body["meta"]["x"],body["meta"]["y"] = body["meta"]["y"],body["meta"]["x"]
    second = analyze(body)
    assert not first["caps_applied"]["transposed"] and second["caps_applied"]["transposed"]
    assert second["model_axes"] == {"x":"y","y":"x"}
    a,b = first["contours"][0]["lines"][0],second["contours"][0]["lines"][0]
    assert np.allclose(np.asarray(b),np.asarray(a)[:,::-1])


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
