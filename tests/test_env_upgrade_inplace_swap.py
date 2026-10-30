"""Fault injection: the same env path, packages swapped in place, no force flag.

A copy that keeps the site-packages folder's modification time (or a venv
whose packages live in its base env) used to leave the env signature
unchanged, so every cache answered with the old versions and the upgrade was
never noticed until a manual Re-probe."""
import json
import os
import sys

from quam_state_manager.core import config_generator
from quam_state_manager.web.app import create_app
from quam_state_manager.web import routes


def _venv(tmp_path, name="venv_up"):
    env = tmp_path / name
    (env / "Scripts").mkdir(parents=True)
    (env / "Lib" / "site-packages").mkdir(parents=True)
    python = env / "Scripts" / "python.exe"
    python.write_bytes(b"")
    return env, python


def _swap_keeping_times(site, old, new):
    stat = site.stat()
    (site / old).rename(site / new)
    os.utime(site, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_in_place_version_swap_changes_the_signature(tmp_path):
    env, python = _venv(tmp_path)
    site = env / "Lib" / "site-packages"
    (site / "quam_builder-0.4.0.dist-info").mkdir()
    before = config_generator._env_signature(str(python))
    _swap_keeping_times(site, "quam_builder-0.4.0.dist-info", "quam_builder-0.6.0.dist-info")
    assert site.stat().st_mtime_ns == int(before.split("|")[1])
    assert config_generator._env_signature(str(python)) != before


def test_base_env_packages_of_a_system_site_packages_venv_count(tmp_path):
    base, base_python = _venv(tmp_path, "base")
    base_python.rename(base / "python.exe")
    base_site = base / "Lib" / "site-packages"
    (base_site / "quam_builder-0.4.0.dist-info").mkdir()
    env, python = _venv(tmp_path)
    (env / "pyvenv.cfg").write_text(f"home = {base}\ninclude-system-site-packages = true\n", encoding="utf-8")
    before = config_generator._env_signature(str(python))
    _swap_keeping_times(base_site, "quam_builder-0.4.0.dist-info", "quam_builder-0.6.0.dist-info")
    assert config_generator._env_signature(str(python)) != before


def test_showing_diagnostics_rechecks_after_an_in_place_swap(tmp_path, monkeypatch):
    env, python = _venv(tmp_path)
    site = env / "Lib" / "site-packages"
    (site / "quam_builder-0.4.0.dist-info").mkdir()
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    ctx = app.config["contexts"][app.config["active_context"]]
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: str(python))
    queued = []
    monkeypatch.setattr(routes, "_maybe_check_env_upgrade", lambda c, inst, **kw: queued.append(c))
    # the check that ran before the swap
    ctx["env_upgrade_sig_tried"] = (config_generator.interpreter_key(str(python)),
                                    config_generator._env_signature(str(python)))
    client.get("/diagnostics/env-card")
    assert queued == []                      # nothing moved: no re-check
    _swap_keeping_times(site, "quam_builder-0.4.0.dist-info", "quam_builder-0.6.0.dist-info")
    client.get("/diagnostics/env-card")
    assert queued and queued[0] is ctx       # the upgrade is noticed without Re-probe
