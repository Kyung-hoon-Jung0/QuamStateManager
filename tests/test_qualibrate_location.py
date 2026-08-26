"""Config-location picker (docs/63 §B).

Windows and Linux keep ``~/.qualibrate`` in different homes, and the common
split deployment (qualibrate inside a WSL distro, SM native Windows) puts the
config somewhere SM's default never looks. These tests pin: the SM-side
override tier (env > UI-chosen > default), the WSL distro-share bridge for
POSIX config values, the locate/use routes (read-only over the tree), and the
safe_io ReplaceFileW→os.replace fallback that WSL's P9 server requires."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import qualibrate_config as qc
from quam_state_manager.core import safe_io
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _mini_tree(base: Path, active: str = "alpha") -> Path:
    """A tiny but valid .qualibrate: root + alpha/beta overlays + one chip."""
    cfg = base / ".qualibrate"
    chip = base / "chips" / "c1"
    chip.mkdir(parents=True, exist_ok=True)
    (chip / "state.json").write_text('{"qubits": {}}', encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    _write(cfg / "config.toml", f'''
[qualibrate]
project = "{active}"
version = 5

[quam]
state_path = "{chip.as_posix()}"
version = 3
''')
    for name in ("alpha", "beta"):
        _write(cfg / "projects" / name / "config.toml",
               f'[quam]\nstate_path = "{chip.as_posix()}"\n')
    return cfg


@pytest.fixture(autouse=True)
def _clear_override():
    """The override is process-global — never leak one across tests."""
    yield
    qc.set_dir_override(None)


class TestOverridePrecedence:
    def test_env_wins_over_override(self, tmp_path, monkeypatch):
        envd = tmp_path / "envd"
        envd.mkdir()
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(envd))
        qc.set_dir_override(tmp_path / "chosen")
        assert qc._config_dir() == envd
        assert qc.config_source()["source"] == "env"

    def test_override_wins_over_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("QUALIBRATE_CONFIG_FILE", raising=False)
        monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
        qc.set_dir_override(tmp_path / "chosen")
        assert qc._config_dir() == tmp_path / "chosen"
        assert qc.config_source()["source"] == "override"

    def test_clear_returns_default(self, monkeypatch):
        monkeypatch.delenv("QUALIBRATE_CONFIG_FILE", raising=False)
        monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
        qc.set_dir_override(None)
        assert qc._config_dir() == Path.home() / ".qualibrate"
        assert qc.config_source()["source"] == "default"

    def test_listing_source_reports_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv("QUALIBRATE_CONFIG_FILE", raising=False)
        monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
        cfg = _mini_tree(tmp_path)
        qc.set_dir_override(cfg)
        assert qc.list_projects()["source"] == "sm-override"


class TestWslBridge:
    def test_wsl_root_of_forms(self):
        assert qc._wsl_root_of(
            r"\\wsl.localhost\Ubuntu\home\u\.qualibrate") == r"\\wsl.localhost\Ubuntu"
        assert qc._wsl_root_of(r"\\wsl$\Debian\home\u") == r"\\wsl$\Debian"
        assert qc._wsl_root_of("//wsl.localhost/Ubuntu/home") == "//wsl.localhost/Ubuntu"
        assert qc._wsl_root_of(Path("/home/u/.qualibrate")) is None
        assert qc._wsl_root_of(r"C:\Users\u\.qualibrate") is None

    def test_home_paths_map_to_distro_share(self):
        p = qc._to_native("/home/u/proj", "nt",
                          wsl_root=r"\\wsl.localhost\Ubuntu")
        assert str(p) == r"\\wsl.localhost\Ubuntu\home\u\proj"

    def test_mnt_prefers_direct_drive_over_share(self):
        p = qc._to_native("/mnt/d/work/x", "nt",
                          wsl_root=r"\\wsl.localhost\Ubuntu")
        assert str(p) == r"D:\work\x"

    def test_posix_host_ignores_wsl_root(self):
        p = qc._to_native("/home/u/proj", "posix",
                          wsl_root=r"\\wsl.localhost\Ubuntu")
        # as_posix: Path("/home/…") str-renders with backslashes on a
        # Windows HOST even for a posix-target mapping — compare dialect-free
        assert p.as_posix() == "/home/u/proj"

    def test_windows_values_pass_through(self):
        p = qc._to_native(r"D:\chips\one", "nt",
                          wsl_root=r"\\wsl.localhost\Ubuntu")
        assert str(p) == r"D:\chips\one"

    def test_native_path_anchors_on_the_config_dir(self, monkeypatch):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE",
                           r"\\wsl.localhost\Ubuntu\home\u\.qualibrate")
        got = str(qc.native_path("/home/u/chips"))
        if os.name == "nt":
            assert got == r"\\wsl.localhost\Ubuntu\home\u\chips"
        else:
            assert got == "/home/u/chips"


class TestSafeIoP9Fallback:
    def test_not_supported_falls_back_to_os_replace(self, tmp_path, monkeypatch):
        dst = tmp_path / "f.json"
        dst.write_text("{}", encoding="utf-8")

        class FakeNotSupported(OSError):
            winerror = 50        # ERROR_NOT_SUPPORTED — the WSL P9 answer

        def boom(_tmp, _dst):
            raise FakeNotSupported(50, "not supported")

        monkeypatch.setattr(safe_io, "_IS_WINDOWS", True)
        monkeypatch.setattr(safe_io, "_replace_file_windows", boom)
        safe_io.atomic_write_json(dst, {"v": 2})
        assert json.loads(dst.read_text(encoding="utf-8")) == {"v": 2}

    def test_other_winerrors_still_retry_then_raise(self, tmp_path, monkeypatch):
        dst = tmp_path / "f.json"
        dst.write_text("{}", encoding="utf-8")

        class FakeAccessDenied(OSError):
            winerror = 5

        calls: list[int] = []

        def boom(_tmp, _dst):
            calls.append(1)
            raise FakeAccessDenied(5, "denied")

        monkeypatch.setattr(safe_io, "_IS_WINDOWS", True)
        monkeypatch.setattr(safe_io, "_replace_file_windows", boom)
        monkeypatch.setattr(safe_io, "_WRITE_BACKOFF_S", 0.001)
        with pytest.raises(safe_io.LiveFileError):
            safe_io.atomic_write_json(dst, {"v": 2})
        assert len(calls) == safe_io._WRITE_ATTEMPTS


@pytest.fixture
def unpinned(tmp_path, monkeypatch):
    """App with NO env pin, so the override tier is what resolves. The
    default tier would read the developer's real home — every test here
    installs an override (via the routes) before touching listing routes."""
    monkeypatch.delenv("QUALIBRATE_CONFIG_FILE", raising=False)
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    inst = tmp_path / "_inst"
    app = create_app(testing=True, instance_path=str(inst))
    return {"app": app, "client": app.test_client(), "inst": inst,
            "tmp": tmp_path}


class TestLocateRoutes:
    def test_locate_previews_valid_tree(self, unpinned):
        cfg = _mini_tree(unpinned["tmp"])
        body = unpinned["client"].post(
            "/qualibrate/locate", data={"path": str(cfg)}).get_data(as_text=True)
        assert "Use this folder" in body
        assert "2 projects" in body
        assert "alpha" in body

    def test_locate_accepts_config_toml_file(self, unpinned):
        cfg = _mini_tree(unpinned["tmp"])
        body = unpinned["client"].post(
            "/qualibrate/locate",
            data={"path": str(cfg / "config.toml")}).get_data(as_text=True)
        assert "Use this folder" in body

    def test_locate_dir_without_config(self, unpinned):
        d = unpinned["tmp"] / "empty"
        d.mkdir()
        body = unpinned["client"].post(
            "/qualibrate/locate", data={"path": str(d)}).get_data(as_text=True)
        assert "holds no" in body and "Use this folder" not in body

    def test_locate_missing_path(self, unpinned):
        body = unpinned["client"].post(
            "/qualibrate/locate",
            data={"path": str(unpinned["tmp"] / "ghost")}).get_data(as_text=True)
        assert "Not found" in body

    def test_use_location_persists_and_activates(self, unpinned):
        cfg = _mini_tree(unpinned["tmp"])
        r = unpinned["client"].post("/qualibrate/use-location",
                                    data={"path": str(cfg)})
        assert r.status_code == 302          # non-htmx → redirect home
        memo = json.loads((unpinned["inst"] / "qualibrate_location.json")
                          .read_text(encoding="utf-8"))
        assert Path(memo["config_dir"]) == cfg
        assert qc.config_source()["source"] == "override"
        landing = unpinned["client"].get("/").get_data(as_text=True)
        assert "what's your project?" in landing        # project-first shell
        cards = unpinned["client"].get("/landing/projects").get_data(as_text=True)
        assert "alpha" in cards and "beta" in cards

    def test_use_location_rejected_when_env_pinned(self, tmp_path):
        # conftest's autouse env pin is still in force for this app
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        cfg = _mini_tree(tmp_path)
        body = app.test_client().post(
            "/qualibrate/use-location",
            data={"path": str(cfg)}).get_data(as_text=True)
        assert "environment variable" in body
        assert qc.config_source()["source"] == "env"
        assert not (tmp_path / "_inst" / "qualibrate_location.json").exists()

    def test_use_default_clears_the_choice(self, unpinned):
        cfg = _mini_tree(unpinned["tmp"])
        unpinned["client"].post("/qualibrate/use-location",
                                data={"path": str(cfg)})
        r = unpinned["client"].post("/qualibrate/use-default-location")
        assert r.status_code == 302
        assert not (unpinned["inst"] / "qualibrate_location.json").exists()
        assert qc.config_source()["source"] == "default"

    def test_choice_survives_restart(self, unpinned, tmp_path):
        cfg = _mini_tree(unpinned["tmp"])
        unpinned["client"].post("/qualibrate/use-location",
                                data={"path": str(cfg)})
        qc.set_dir_override(None)            # simulate process death
        app2 = create_app(testing=True, instance_path=str(unpinned["inst"]))
        assert qc.config_source()["source"] == "override"
        cards = app2.test_client().get("/landing/projects").get_data(as_text=True)
        assert "alpha" in cards

    def test_locate_never_touches_the_tree(self, unpinned):
        cfg = _mini_tree(unpinned["tmp"])

        def snap(root: Path) -> dict:
            out = {}
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    out[str(p.relative_to(root))] = (
                        p.stat().st_mtime_ns,
                        hashlib.sha256(p.read_bytes()).hexdigest())
            return out

        before = snap(cfg)
        c = unpinned["client"]
        c.post("/qualibrate/locate", data={"path": str(cfg)})
        c.post("/qualibrate/use-location", data={"path": str(cfg)})
        c.get("/")
        c.get("/landing/projects")
        c.get("/qualibrate", headers={"HX-Request": "true"})
        assert snap(cfg) == before


class TestLandingLocateBlock:
    def test_no_config_landing_shows_the_block(self, tmp_path):
        # conftest env pin → nonexistent config → the welcome branch
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        body = app.test_client().get("/").get_data(as_text=True)
        assert "landing-locate" in body
        assert "qualibrate-locate-result" in body
        assert "Welcome to QUAM State Manager" in body   # manual still there

    def test_with_config_landing_has_no_welcome_block(self, unpinned):
        """The no-config WELCOME page's lead block must not also render once
        a config resolved — that block's copy ("No config found") would be a
        lie. The project-landing gets its OWN compact strip instead (below)."""
        cfg = _mini_tree(unpinned["tmp"])
        unpinned["client"].post("/qualibrate/use-location",
                                data={"path": str(cfg)})
        body = unpinned["client"].get("/").get_data(as_text=True)
        assert "landing-locate" not in body

    def test_with_config_landing_shows_the_compact_strip(self, unpinned):
        """User feedback: the picker used to live ONLY inside /qualibrate's
        details, and felt like "a hidden menu" once a config was already
        found. A compact strip at the top of the project landing must let a
        user change the config location without navigating away."""
        cfg = _mini_tree(unpinned["tmp"])
        unpinned["client"].post("/qualibrate/use-location",
                                data={"path": str(cfg)})
        body = unpinned["client"].get("/").get_data(as_text=True)
        assert "landing-cfg-strip" in body
        assert str(cfg) in body                    # shows the resolved path
        assert "qualibrate-locate-result" in body   # the real locate form, not a stub
        assert "chosen in SM" not in body           # the source chip: "override" not "sm-override"
        assert "override" in body

    def test_qualibrate_page_shows_location_controls(self, unpinned):
        cfg = _mini_tree(unpinned["tmp"])
        unpinned["client"].post("/qualibrate/use-location",
                                data={"path": str(cfg)})
        body = unpinned["client"].get(
            "/qualibrate", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "Config location" in body
        assert "chosen in SM" in body
        assert "Reset to default location" in body


class TestNormalizeInput:
    def test_strips_quotes_and_toml_suffix(self):
        p = routes_mod._normalize_config_input('"/some/dir/config.toml"')
        assert str(p) == str(Path("/some/dir"))

    def test_expands_user(self):
        p = routes_mod._normalize_config_input("~/.qualibrate")
        assert str(p) == str(Path.home() / ".qualibrate")
