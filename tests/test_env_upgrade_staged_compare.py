"""After a reviewed removal is staged, the matched-state waveform compare runs on
the reference inputs with exactly that reviewed plan applied, and the waveform
case shows one sentence."""
import base64
import copy
import json
import sys

import pytest

from quam_state_manager.core import env_seen, env_upgrade, state_env_baseline, waveform_refs
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

CONFIG = {"elements": {"q1": {"operations": {"x": "x_pulse"}}},
          "pulses": {"x_pulse": {"length": 4, "waveforms": {"I": "w"}}},
          "waveforms": {"w": {"type": "arbitrary", "samples": [0.0, 0.1, 0.2, 0.1]}}}


def _schema(version, with_gone):
    fields = {"keep": {"type": {"base": "int"}, "has_default": True,
                       "defining_class": "official.Pair", "default": 0}}
    if with_gone:
        fields["gone"] = {"type": {"base": "int"}, "has_default": True,
                          "defining_class": "official.Pair", "default": 0}
    return {"schema_format": 4, "versions": {"quam_builder": version}, "official_digest": version,
            "classes": {"official.Pair": {"importable": True, "origin": "official", "bases": [],
                                          "signatures": {}, "fields": fields}}}


@pytest.fixture
def flow(tmp_path, monkeypatch):
    state = {"pairs": {"p1": {"__class__": "official.Pair", "keep": 1, "gone": 5},
                       "p2": {"__class__": "official.Pair", "keep": 2, "gone": 6}}}
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    ctx = app.config["contexts"][app.config["active_context"]]
    ctx["store"] = QuamStore.from_dicts(copy.deepcopy(state), {})
    ctx["modifier"] = Modifier(ctx["store"])
    inst = app.instance_path
    key = env_seen.chip_key(ctx["path"])
    old_key = state_env_baseline.record_baseline(inst, _schema("0.4.0", True), python_path=sys.executable)
    old_body = state_env_baseline.load_baseline(inst, old_key)
    new_key = state_env_baseline.record_baseline(inst, _schema("0.6.0", False), python_path=sys.executable)
    body = state_env_baseline.load_baseline(inst, new_key)
    env_seen.observe(inst, key, sys.executable, old_body, context=ctx)
    # 1. a reference recorded before the change, from the state that still has the field
    raw = json.dumps(state).encode("utf-8")
    outcome = {"ok": True, "result": {"config": CONFIG, "versions": {"quam_builder": "0.4.0"}},
               "reference_inputs": {"state.json": base64.b64encode(raw).decode("ascii"),
                                    "wiring.json": base64.b64encode(b"{}").decode("ascii")}}
    assert waveform_refs.record(inst, key, sys.executable, old_body, outcome, context={"path": str(folder)})
    # 2. the upgrade
    event = env_seen.observe(inst, key, sys.executable, body, context=ctx)
    ctx["env_upgrade_env"] = sys.executable
    ctx["env_upgrade_body"] = body
    ctx["env_upgrade_report"] = env_upgrade.report(state, _schema("0.6.0", False), old_body, event=event)
    previews = []

    def fake_preview(python, folder, **kwargs):
        loaded = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        previews.append(kwargs)
        if any("gone" in pair for pair in loaded["pairs"].values()):
            return {"ok": False, "error": "Unexpected attribute 'gone'"}
        return {"ok": True, "result": {"config": CONFIG}}

    monkeypatch.setattr(routes.config_generator, "run_config_preview", fake_preview)
    return client, ctx, previews


def _card_case6(client):
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    return html, html.split("Changes output", 1)[1].split("FYI", 1)[0]


def test_staged_reviewed_removal_runs_the_compare_on_the_reference_inputs(flow):
    client, ctx, previews = flow
    rows = ctx["env_upgrade_report"]["cases"]["3"]["rows"]
    assert [(r["field"], len(r["paths"])) for r in rows] == [("gone", 2)]
    payload = {"selections": [{"class": "official.Pair", "field": "gone", "remove": True, "confirmed": True}]}
    tested = client.post("/env-upgrade/rename-test", json=payload)
    assert tested.status_code == 200, tested.get_json()
    payload["token"] = tested.get_json()["token"]
    applied = client.post("/env-upgrade/rename-apply", json=payload)
    assert applied.status_code == 200, applied.get_json()
    assert "gone" not in ctx["store"].state["pairs"]["p1"]
    html, case6 = _card_case6(client)
    assert "No reference from before this change" not in case6
    assert "compare runs once the load-stopping items" not in case6
    assert "All 1 waveforms are bit-identical to the reference" in case6
    assert "Compared on the reference inputs with your 1 reviewed removal applied." in case6


def test_reference_that_still_cannot_load_says_why(flow, monkeypatch):
    client, ctx, _ = flow
    payload = {"selections": [{"class": "official.Pair", "field": "gone", "remove": True, "confirmed": True}]}
    payload["token"] = client.post("/env-upgrade/rename-test", json=payload).get_json()["token"]
    monkeypatch.setattr(routes.config_generator, "run_config_preview",
                        lambda *a, **k: {"ok": False, "error": "lab class missing"})
    # the stage itself only needs the load test token; the compare's load now fails
    assert client.post("/env-upgrade/rename-apply", json=payload).status_code == 200
    _, case6 = _card_case6(client)
    assert "lab class missing" in case6 and "load-stopping" in case6
    assert "No reference from before this change" not in case6
    assert case6.count("A reference from before the change") == 0
