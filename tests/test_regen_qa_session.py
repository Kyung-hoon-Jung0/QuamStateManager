"""Server-side pins for the Re-generate session findings from the 2026-09
real-browser QA pass (package gen-session).

F9                 /regenerate/reconstruct names the folder it actually READ
                   when "Load different…" posts one; the loaded chip's name is
                   kept only for the default (working-copy) path.
regenerate-r2-18   an unticked scripts export writes NO build-script bundle
                   (a None scripts_dir used to mean "the legacy folder"), and
                   the outcome names the real folder + whether it lies inside
                   the output folder, so the report stops claiming "written to
                   the output folder" for a folder somewhere else.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import regenerate
from quam_state_manager.web.app import create_app
from tests.test_web import _gen_valid_spec, _make_state, _make_wiring


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    return app.test_client()


# ── F9 ────────────────────────────────────────────────────────────────────
class TestReconstructSourceName:
    def test_an_explicit_folder_is_named_after_itself(self, client, tmp_path):
        loaded = _chip(tmp_path / "loaded_chip")
        other = _chip(tmp_path / "other_chip")
        client.post("/load", data={"folder": str(loaded)})
        resp = client.post("/regenerate/reconstruct", json={"folder": str(other)})
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert body["source_folder"] == str(other)
        assert body["source_name"] == "other_chip", (
            "Load different… showed the LOADED chip's name over another "
            "chip's counts: " + repr(body["source_name"]))

    def test_the_default_path_keeps_the_loaded_chip_name(self, client, tmp_path):
        loaded = _chip(tmp_path / "loaded_chip")
        client.post("/load", data={"folder": str(loaded)})
        resp = client.post("/regenerate/reconstruct", json={})
        body = resp.get_json()
        assert resp.status_code == 200, body
        # the folder read is the working copy — its key must never be the label
        assert body["source_name"] == "loaded_chip"
        assert Path(body["source_folder"]).name != "loaded_chip"

    def test_a_generic_container_folder_names_the_chip(self, client, tmp_path):
        loaded = _chip(tmp_path / "loaded_chip")
        other = _chip(tmp_path / "LabB" / "quam_state")
        client.post("/load", data={"folder": str(loaded)})
        body = client.post("/regenerate/reconstruct",
                           json={"folder": str(other)}).get_json()
        assert body["source_name"] == "LabB"


# ── regenerate-r2-18 (core) ───────────────────────────────────────────────
def _fake_build_into(monkeypatch):
    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps({"qubits": {"q1": {}}}))
        (out_dir / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
        return {"ok": True, "status": "ok", "error": None, "result": {}}
    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)


def _old_chip(tmp_path: Path) -> Path:
    old = tmp_path / "old"
    old.mkdir()
    (old / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {}}, "active_qubit_names": []}))
    (old / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}))
    return old


class TestScriptsExport:
    def test_unticked_export_writes_no_bundle(self, tmp_path, monkeypatch):
        _fake_build_into(monkeypatch)
        old = _old_chip(tmp_path)
        new = tmp_path / "new"
        out = regenerate.run_regenerate("py", old, {"qubits": ["q1"]}, new,
                                        scripts_enabled=False)
        assert out["ok"] is True
        assert out["merge"] is not None           # the rebuild itself happened
        assert not (new / "build_scripts").exists(), \
            "an unticked export still wrote <out>/build_scripts"
        assert not (new / "state_gen_scripts").exists()
        assert out["script"] is None
        assert out["script_in_output"] is None

    def test_the_default_folder_is_named_and_inside(self, tmp_path, monkeypatch):
        _fake_build_into(monkeypatch)
        new = tmp_path / "new"
        out = regenerate.run_regenerate("py", _old_chip(tmp_path),
                                        {"qubits": ["q1"]}, new)
        assert out.get("script_error") is None, out.get("script_error")
        assert out["script"] == str(new / "build_scripts")
        assert (new / "build_scripts" / "02_build_machine.py").exists()
        assert out["script_in_output"] is True

    def test_a_folder_elsewhere_is_reported_as_elsewhere(self, tmp_path, monkeypatch):
        _fake_build_into(monkeypatch)
        new = tmp_path / "new"
        shared = tmp_path / "shared" / "state_gen_scripts"
        out = regenerate.run_regenerate("py", _old_chip(tmp_path),
                                        {"qubits": ["q1"]}, new, scripts_dir=shared)
        assert out.get("script_error") is None, out.get("script_error")
        assert out["script"] == str(shared)
        assert (shared / "02_build_machine.py").exists()
        assert out["script_in_output"] is False

    def test_a_sibling_with_the_output_as_prefix_is_not_inside(
            self, tmp_path, monkeypatch):
        # "new_scripts" starts with "new" as a STRING but is not under it.
        _fake_build_into(monkeypatch)
        out = regenerate.run_regenerate(
            "py", _old_chip(tmp_path), {"qubits": ["q1"]}, tmp_path / "new",
            scripts_dir=tmp_path / "new_scripts")
        assert out["script_in_output"] is False


# ── regenerate-r2-18 (route) ──────────────────────────────────────────────
class TestBuildRouteCarriesTheCheckbox:
    @pytest.fixture(autouse=True)
    def _all_capabilities(self, monkeypatch):
        from quam_state_manager.core import config_generator
        from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
        manifest = {
            "ok": True, "cached": False, "error": None, "versions": {},
            "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS},
        }
        monkeypatch.setattr(config_generator, "probe_capabilities",
                            lambda *a, **k: manifest)

    def test_scripts_enabled_reaches_run_regenerate(self, client, tmp_path, monkeypatch):
        got = {}

        def fake_run(py, src, spec, out, timeout=300, **kw):
            got.clear()
            got.update(kw)
            return {"ok": True, "status": "ok", "error": None, "merge": None}

        monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
        client.post("/generate/select-env", json={"python": sys.executable})
        src = tmp_path / "src"
        src.mkdir()
        base = {"spec": _gen_valid_spec(), "output_path": str(tmp_path / "out"),
                "source_folder": str(src)}
        resp = client.post("/regenerate/build", json={**base, "scripts_enabled": False})
        assert resp.status_code == 200, resp.get_json()
        assert got["scripts_enabled"] is False
        resp = client.post("/regenerate/build", json=base)     # an older client
        assert resp.status_code == 200, resp.get_json()
        assert got["scripts_enabled"] is True
        resp = client.post("/regenerate/build", json={**base, "scripts_enabled": True})
        assert got["scripts_enabled"] is True
