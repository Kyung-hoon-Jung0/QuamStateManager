"""Re-generate starts in the env the chip was built in (walk finding G3).

A chip built with quam_builder 0.6.0 and rebuilt in an env with 0.4.0 gains
different gate variants and then loads only in that env. The reconstruct now
names the build env (recorded by SM's own builds, else matched by the chip's
``__package_versions__`` against cached env probes) and warns on a mismatch.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import build_env, config_generator
from quam_state_manager.web.app import create_app
from tests.test_web import _make_state, _make_wiring


def _chip(folder: Path, versions=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    st = _make_state()
    if versions:
        st["__package_versions__"] = versions
    (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    return folder


V6 = {"quam": "0.6.0", "quam_builder": "0.6.0"}
V4 = {"quam": "0.6.0", "quam_builder": "0.4.0"}


class TestChoose:
    def test_a_version_stamp_picks_the_env_with_those_versions(self):
        probes = {"A": {"usable": True, "versions": V4},
                  "B": {"usable": True, "versions": V6}}
        out = build_env.choose([], {"__package_versions__": V6}, "A", probes,
                               lambda py: True)
        assert out["python"] == "B" and out["source"] == "versions"
        assert out["level"] == "warn" and "B" in out["message"]

    def test_the_recorded_env_wins(self, tmp_path):
        build_env.record(tmp_path, "C", V6)
        probes = {"B": {"usable": True, "versions": V6}}
        out = build_env.choose([tmp_path], {"__package_versions__": V6}, "C",
                               probes, lambda py: True)
        assert out["python"] == "C" and out["source"] == "recorded"
        assert out["level"] == "info"

    def test_an_unknown_recorded_path_is_never_used(self, tmp_path):
        build_env.record(tmp_path, "EVIL", V6)
        out = build_env.choose([tmp_path], {}, "A", {}, lambda py: py != "EVIL")
        assert out["python"] is None

    def test_no_matching_env_warns_on_the_selected_one(self):
        probes = {"A": {"usable": True, "versions": V4}}
        out = build_env.choose([], {"__package_versions__": V6}, "A", probes,
                               lambda py: True)
        assert out["python"] is None and out["level"] == "warn"
        assert "quam_builder 0.4.0" in out["message"]
        assert "quam_builder 0.6.0" in out["message"]


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst"))


class TestReconstructRoute:
    def test_the_reconstruct_names_the_build_env(self, app, tmp_path, monkeypatch):
        inst = Path(app.instance_path)
        inst.mkdir(parents=True, exist_ok=True)
        chip = _chip(tmp_path / "chip", V6)
        monkeypatch.setattr(config_generator, "get_selected_env", lambda i: "py4")
        monkeypatch.setattr(config_generator, "probe_capabilities",
                            lambda *a, **k: {"ok": False})
        monkeypatch.setattr(config_generator, "_load_probe_cache", lambda i: {
            "py4": {"sig": "x", "result": {"usable": True, "versions": V4}},
            "py6": {"sig": "y", "result": {"usable": True, "versions": V6}}})
        monkeypatch.setattr(config_generator, "known_interpreter",
                            lambda py, i, also=(): py in ("py4", "py6"))
        c = app.test_client()
        body = c.post("/regenerate/reconstruct", json={"folder": str(chip)}).get_json()
        assert body["ok"], body
        be = body["built_env"]
        assert be["python"] == "py6" and be["level"] == "warn", be

    def test_a_posted_known_env_is_the_build_env(self, app, monkeypatch):
        from quam_state_manager.web import routes
        monkeypatch.setattr(config_generator, "get_selected_env", lambda i: "py4")
        monkeypatch.setattr(config_generator, "known_interpreter",
                            lambda py, i, also=(): py == "py6")
        with app.test_request_context():
            assert routes._build_python({"python": "py6"}) == "py6"
            assert routes._build_python({"python": "other"}) == "py4"
            assert routes._build_python({}) == "py4"


def test_the_regen_page_shows_the_build_env_first():
    root = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
    html = (root / "templates" / "_regenerate.html").read_text(encoding="utf-8")
    assert "builtEnv: res.built_env" in html
    assert "unshift(be.message)" in html
    js = (root / "static" / "generate.js").read_text(encoding="utf-8")
    assert "state.regenBuiltEnv = (o.builtEnv && o.builtEnv.python) || null;" in js
    assert "python: state.env || null,   // G3" in js
