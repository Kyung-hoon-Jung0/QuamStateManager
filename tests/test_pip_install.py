"""r16 ① — pip-install viability (docs: README).

Pins the instance-path policy (repo/editable → repo ``instance/``; installed
or frozen → the per-user data dir; Flask's ``<sys.prefix>/var`` fallback must
never be reached) and, as a slow smoke, that a built wheel actually serves a
page from an installed (site-packages-like) layout with templates + static
included and a writable instance dir.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager.web import app as app_mod

_REPO = Path(__file__).resolve().parent.parent


@pytest.mark.real_instance_path        # docs/155 F7: this class IS the policy
class TestDefaultInstancePath:
    def test_repo_checkout_keeps_repo_instance(self):
        # The test run IS a repo checkout (pyproject.toml beside the package).
        assert app_mod.default_instance_path() is None

    def test_installed_layout_uses_user_dir(self, tmp_path, monkeypatch):
        # Simulate site-packages: module __file__ under a tree with NO
        # pyproject.toml above the package.
        fake_pkg = tmp_path / "site" / "quam_state_manager" / "web"
        fake_pkg.mkdir(parents=True)
        monkeypatch.setattr(app_mod, "__file__",
                            str(fake_pkg / "app.py"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
        got = app_mod.default_instance_path()
        assert got is not None
        assert "QUAM State Manager" in got
        assert Path(got).is_dir()                    # created on demand

    def test_frozen_uses_user_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
        got = app_mod.default_instance_path()
        assert got is not None and "QUAM State Manager" in got

    def test_create_app_never_lands_in_sys_prefix_var(self, tmp_path,
                                                      monkeypatch):
        # The failure this guards: Flask's instance_relative_config derived
        # <sys.prefix>/var/quam_state_manager.web-instance for installed
        # packages — unwritable under a system Python.
        fake_pkg = tmp_path / "site" / "quam_state_manager" / "web"
        fake_pkg.mkdir(parents=True)
        monkeypatch.setattr(app_mod, "__file__", str(fake_pkg / "app.py"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
        flask_app = app_mod.create_app()
        assert "var" + os.sep not in flask_app.instance_path
        assert "QUAM State Manager" in flask_app.instance_path


@pytest.mark.slow
def test_wheel_install_serves_a_page(tmp_path):
    """Build the wheel, install it into an isolated target, and serve '/'
    from OUTSIDE the repo — templates, static and generator scripts must all
    resolve from the installed layout."""
    pip = [sys.executable, "-m", "pip"]
    wheel_dir = tmp_path / "wheel"
    r = subprocess.run(pip + ["wheel", str(_REPO), "--no-deps",
                              "--no-build-isolation", "-w", str(wheel_dir)],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=600)
    if r.returncode != 0:
        pytest.skip(f"wheel build unavailable here: {r.stderr[-400:]}")
    wheels = list(wheel_dir.glob("quam_state_manager-*.whl"))
    assert wheels, r.stdout + r.stderr
    site = tmp_path / "site"
    r = subprocess.run(pip + ["install", str(wheels[0]), "--no-deps",
                              "--target", str(site)],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=600)
    assert r.returncode == 0, r.stderr[-800:]

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, sys\n"
        "from quam_state_manager.web.app import create_app, default_instance_path\n"
        "app = create_app(testing=True)\n"
        "c = app.test_client()\n"
        "r = c.get('/')\n"
        "print(json.dumps({'status': r.status_code,\n"
        "                  'has_html': b'<html' in r.data[:200].lower(),\n"
        "                  'pkg': __import__('quam_state_manager').__file__}))\n",
        encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(site)
    env["PYTHONUTF8"] = "1"
    # run from an unrelated cwd so nothing resolves via the repo by accident
    r = subprocess.run([sys.executable, str(probe)], capture_output=True,
                       text=True, encoding="utf-8", timeout=300,
                       cwd=str(tmp_path), env=env)
    assert r.returncode == 0, r.stderr[-1200:]
    import json as _json
    out = _json.loads(r.stdout.strip().splitlines()[-1])
    assert out["status"] == 200 and out["has_html"], out
    assert str(site) in out["pkg"]          # really the installed copy
