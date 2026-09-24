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
F8 (generate)      one build per output folder at a time: /generate/build and
                   /regenerate/build refuse (409, busy) a second build into a
                   folder a build is still writing, never queue it, and
                   release the folder when the build ends (a crash included).
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


# ── F8 (generate): one build per output folder at a time ──────────────────
class TestOneBuildPerFolder:
    """Two builds into one folder both passed the empty-folder guard (check-
    then-act) and both wrote it: a double press, or two SM windows. The
    second must be refused while the first runs — never queued behind it."""

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

    @staticmethod
    def _held_build(monkeypatch, target, attr):
        """Replace target.attr with a build whose FIRST call blocks until
        released (later calls return at once)."""
        import threading
        entered, release = threading.Event(), threading.Event()
        calls = []

        def fake(*a, **kw):
            calls.append(a)
            if len(calls) == 1:
                entered.set()
                assert release.wait(20), "the held build was never released"
            return {"ok": True, "status": "ok", "error": None,
                    "result": {"qubits": [], "qubit_pairs": []}, "merge": None}
        monkeypatch.setattr(target, attr, fake)
        return entered, release, calls

    def _race(self, client, url, first, second, entered, release):
        import threading
        box = {}

        def run():
            box["first"] = client.post(url, json=first)
        t = threading.Thread(target=run)
        t.start()
        try:
            assert entered.wait(20), "the first build never started"
            box["second"] = client.post(url, json=second)
        finally:
            release.set()
            t.join(20)
        return box["first"], box["second"]

    def test_generate_build_refuses_a_second_build_into_the_same_folder(
            self, client, tmp_path, monkeypatch):
        from quam_state_manager.core import config_generator
        entered, release, calls = self._held_build(
            monkeypatch, config_generator, "run_generator")
        client.post("/generate/select-env", json={"python": sys.executable})
        out = str(tmp_path / "out")
        body = {"spec": _gen_valid_spec(), "output_path": out}
        # the second spelling differs only by a trailing separator
        r1, r2 = self._race(client, "/generate/build", body,
                            {**body, "output_path": out + "\\" if sys.platform == "win32"
                             else out + "/"}, entered, release)
        assert r1.status_code == 200, r1.get_json()
        assert r2.status_code == 409, r2.get_json()
        assert r2.get_json().get("busy") is True
        assert len(calls) == 1, "the second build ran the generator too"
        # released after the first finished: the folder can be built again
        again = client.post("/generate/build", json=body)
        assert again.status_code == 200, again.get_json()
        assert len(calls) == 2

    def test_another_folder_is_not_held_up(self, client, tmp_path, monkeypatch):
        from quam_state_manager.core import config_generator
        entered, release, calls = self._held_build(
            monkeypatch, config_generator, "run_generator")
        client.post("/generate/select-env", json={"python": sys.executable})
        spec = _gen_valid_spec()
        r1, r2 = self._race(client, "/generate/build",
                            {"spec": spec, "output_path": str(tmp_path / "a")},
                            {"spec": spec, "output_path": str(tmp_path / "b")},
                            entered, release)
        assert r1.status_code == 200 and r2.status_code == 200, r2.get_json()
        assert len(calls) == 2

    def test_a_crashed_build_releases_the_folder(self, client, tmp_path, monkeypatch):
        from quam_state_manager.core import config_generator

        def boom(*a, **kw):
            raise RuntimeError("generator crashed")
        monkeypatch.setattr(config_generator, "run_generator", boom)
        client.application.config["PROPAGATE_EXCEPTIONS"] = False
        client.post("/generate/select-env", json={"python": sys.executable})
        body = {"spec": _gen_valid_spec(), "output_path": str(tmp_path / "out")}
        assert client.post("/generate/build", json=body).status_code == 500
        ok_build = {"ok": True, "status": "ok", "error": None, "result": {}}
        monkeypatch.setattr(config_generator, "run_generator", lambda *a, **k: ok_build)
        assert client.post("/generate/build", json=body).status_code == 200, \
            "a crashed build left its folder claimed"

    def test_regenerate_build_refuses_a_second_build_into_the_same_folder(
            self, client, tmp_path, monkeypatch):
        entered, release, calls = self._held_build(
            monkeypatch, regenerate, "run_regenerate")
        client.post("/generate/select-env", json={"python": sys.executable})
        src = tmp_path / "src"
        src.mkdir()
        body = {"spec": _gen_valid_spec(), "output_path": str(tmp_path / "out"),
                "source_folder": str(src)}
        r1, r2 = self._race(client, "/regenerate/build", body, body, entered, release)
        assert r1.status_code == 200, r1.get_json()
        assert r2.status_code == 409, r2.get_json()
        assert len(calls) == 1
