"""generate-r2-09: two builds into ONE output folder must not both succeed.

The overwrite guard (``_build_output_guard``) and the build itself are
check-then-write. Two tabs pressing Generate into the same folder a moment
apart both passed the guard, both returned "✓ Generated", and the later
build's files silently replaced the earlier chip. The routes now hold a
non-blocking per-output-folder lock across guard + build: a second build into
the same folder while one runs gets a 409 ``busy``; once the first finishes a
retry meets the overwrite guard. Generate and Re-generate share the registry.
"""

from __future__ import annotations

import sys
import threading

import pytest

from quam_state_manager.web.app import create_app


def _spec() -> dict:
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {
            "controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}],
            "opx_plus": [], "octaves": [],
        },
        "qubits": ["q1"],
        "qubit_pairs": [],
        "twpas": [],
        "lines": [{"element": "q1", "line": "drive", "channel": None}],
        "populate": {},
    }


@pytest.fixture
def app(tmp_path, monkeypatch):
    from quam_state_manager.core import config_generator
    from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
    manifest = {
        "ok": True, "cached": False, "error": None, "versions": {},
        "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS},
    }
    monkeypatch.setattr(config_generator, "probe_capabilities",
                        lambda *a, **k: manifest)
    a = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    a.test_client().post("/generate/select-env", json={"python": sys.executable})
    return a


class _BlockingBuild:
    """A run_generator / run_regenerate stand-in: the FIRST call blocks until
    released (a build in flight), then writes a chip into its out folder the
    way a real build does; later calls return at once."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.raise_on_first = False

    def _write(self, out):
        out.mkdir(parents=True, exist_ok=True)
        (out / "state.json").write_text("{}", encoding="utf-8")
        (out / "wiring.json").write_text("{}", encoding="utf-8")

    def __call__(self, out):
        self.calls.append(str(out))
        if len(self.calls) == 1:
            self.started.set()
            assert self.release.wait(10), "test never released the build"
            if self.raise_on_first:
                raise RuntimeError("build crashed")
        self._write(out)
        return {"ok": True, "status": "ok", "error": None,
                "result": {"qubits": ["q1"], "qubit_pairs": [], "warnings": []}}


@pytest.fixture
def blocking(monkeypatch):
    from quam_state_manager.core import config_generator
    from quam_state_manager.core import regenerate as regen_mod
    b = _BlockingBuild()
    monkeypatch.setattr(config_generator, "run_generator",
                        lambda py, mode, spec, out, **k: b(out))
    monkeypatch.setattr(regen_mod, "run_regenerate",
                        lambda py, src, spec, out, **k: b(out))
    return b


def _post_gen(app, out, **extra):
    return app.test_client().post("/generate/build", json={
        "spec": _spec(), "output_path": str(out), **extra})


def _post_regen(app, out, src, **extra):
    return app.test_client().post("/regenerate/build", json={
        "spec": _spec(), "output_path": str(out), "source_folder": str(src),
        **extra})


def _start(fn):
    box = {}

    def run():
        try:
            box["resp"] = fn()
        except Exception as exc:  # noqa: BLE001 — surfaced by the test
            box["exc"] = exc
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, box


class TestOneBuildPerOutputFolder:
    def test_second_generate_into_same_folder_is_refused_busy(
            self, app, blocking, tmp_path):
        out = tmp_path / "gen_out" / "r2_twin"
        t, box = _start(lambda: _post_gen(app, out))
        assert blocking.started.wait(10)
        second = _post_gen(app, out)          # tab B, while tab A builds
        assert second.status_code == 409, second.get_json()
        body = second.get_json()
        assert body["ok"] is False and body.get("busy") is True
        assert "already running" in body["error"]
        assert len(blocking.calls) == 1       # B never started a build
        blocking.release.set()
        t.join(10)
        assert box["resp"].status_code == 200 and box["resp"].get_json()["ok"]
        # B retries after A finished: the overwrite guard now asks
        retry = _post_gen(app, out)
        assert retry.status_code == 200
        j = retry.get_json()
        assert j.get("needs_confirm") and j.get("existing_chip")
        assert len(blocking.calls) == 1

    def test_case_variant_spelling_shares_the_lock(self, app, blocking, tmp_path):
        import os
        if os.name != "nt" and sys.platform != "darwin":
            pytest.skip("case-insensitive filesystems only")
        out = tmp_path / "gen_out" / "Twin"
        t, _ = _start(lambda: _post_gen(app, out))
        assert blocking.started.wait(10)
        second = _post_gen(app, tmp_path / "gen_out" / "TWIN")
        assert second.status_code == 409
        blocking.release.set()
        t.join(10)

    def test_a_different_folder_still_builds_in_parallel(
            self, app, blocking, tmp_path):
        t, box = _start(lambda: _post_gen(app, tmp_path / "a"))
        assert blocking.started.wait(10)
        other = _post_gen(app, tmp_path / "b")
        assert other.status_code == 200 and other.get_json()["ok"]
        blocking.release.set()
        t.join(10)
        assert box["resp"].status_code == 200

    def test_lock_released_when_the_build_raises(self, app, blocking, tmp_path):
        out = tmp_path / "crash"
        blocking.raise_on_first = True
        t, box = _start(lambda: _post_gen(app, out))
        assert blocking.started.wait(10)
        blocking.release.set()
        t.join(10)
        assert "exc" in box or box["resp"].status_code >= 500
        again = _post_gen(app, out)
        assert again.status_code == 200 and again.get_json()["ok"], again.get_json()

    def test_regenerate_and_generate_exclude_each_other(
            self, app, blocking, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        out = tmp_path / "regen_out"
        t, box = _start(lambda: _post_regen(app, out, src))
        assert blocking.started.wait(10)
        r2 = _post_regen(app, out, src)
        assert r2.status_code == 409 and r2.get_json().get("busy")
        g = _post_gen(app, out)
        assert g.status_code == 409 and g.get_json().get("busy")
        blocking.release.set()
        t.join(10)
        assert box["resp"].status_code == 200, box["resp"].get_json()
        assert len(blocking.calls) == 1
