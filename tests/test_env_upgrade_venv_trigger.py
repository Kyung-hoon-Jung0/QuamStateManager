"""A real package version bump in a throwaway venv fires the per-chip transition without force.

The fake distribution is installed into ``<tmp>/venv`` (never into any other environment). Version 2.0
renames a field, so the official class digest moves and case 3 must be found for a lab subclass.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from quam_state_manager.core import config_generator, env_upgrade_service

DIST = "official_fixture_pkg"
MODULE = "official_fixture"
SOURCE = '''
from quam.core import QuamRoot, quam_dataclass
@quam_dataclass
class Base(QuamRoot):
    {field}: float = 0.1
'''


def _wheel(directory: Path, version: str, field: str) -> Path:
    files = {
        f"{MODULE}/__init__.py": SOURCE.format(field=field),
        f"{DIST}-{version}.dist-info/METADATA": f"Metadata-Version: 2.1\nName: {DIST}\nVersion: {version}\n",
        f"{DIST}-{version}.dist-info/WHEEL":
            "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for name, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(text.encode())}")
    record.append(f"{DIST}-{version}.dist-info/RECORD,,")
    files[f"{DIST}-{version}.dist-info/RECORD"] = "\n".join(record) + "\n"
    path = directory / f"{DIST}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return path


def _make_venv(tmp_path: Path) -> str:
    venv = tmp_path / "venv"
    done = subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
                          capture_output=True, text=True, timeout=300)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if done.returncode != 0 or not python.is_file():
        pytest.skip("venv creation failed: " + (done.stderr or done.stdout)[-300:])
    probe = subprocess.run([str(python), "-c", "import quam"], capture_output=True, text=True, timeout=120)
    if probe.returncode != 0:
        pytest.skip("venv cannot see the base packages: " + probe.stderr[-300:])
    return str(python)


def _pip_install(python: str, wheel: Path):
    done = subprocess.run([python, "-m", "pip", "install", "--no-deps", "--no-index",
                           "--disable-pip-version-check", str(wheel)],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-600:]


def test_real_version_bump_fires_transition_without_force(tmp_path, monkeypatch):
    python = _make_venv(tmp_path)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _pip_install(python, _wheel(wheels, "1.0", "old_phase"))

    (tmp_path / "lab_fixture.py").write_text(
        "from official_fixture import Base\nfrom quam.core import quam_dataclass\n"
        "@quam_dataclass\nclass Root(Base):\n    extra: float = 0.0\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(tmp_path), str(root)]))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(
        json.dumps({"__class__": "lab_fixture.Root", "old_phase": 0.1, "extra": 0.0}), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    instance = tmp_path / "instance"

    signature_before = config_generator._env_signature(python)
    first = env_upgrade_service.check(folder, python, instance, context={"origin": "live"})
    assert first["event"]["first"] is True and not first["event"]["changed"]
    assert first["cases"]["3"]["status"] != "found"

    _pip_install(python, _wheel(wheels, "2.0", "new_phase"))
    signature_after = config_generator._env_signature(python)
    assert signature_before != signature_after  # the request-path probe gate would re-probe

    second = env_upgrade_service.check(folder, python, instance, context={"origin": "live"})
    event = second["event"]
    assert event["changed"] and event["official_changed"] and not event["first"]
    assert not any(word in (event.get("direction") or "").lower() for word in ("safe", "compatible"))
    assert any(line.startswith("VERDICT case 3: found") for line in second["verdicts"]), second["verdicts"]
