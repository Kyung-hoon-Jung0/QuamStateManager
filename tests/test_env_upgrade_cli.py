"""CLI output shares report evidence and snapshots write only under instance."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest
from typer.testing import CliRunner

from quam_state_manager import cli
from quam_state_manager.core import env_upgrade, env_upgrade_service, waveform_refs

runner = CliRunner()


@pytest.fixture
def command_folder(tmp_path, monkeypatch):
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text("{}", encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "_env_instance_path", lambda: tmp_path / "instance")
    return folder


def test_json_and_text_share_case_verdicts(command_folder, monkeypatch):
    report = env_upgrade.report({}, {"ok": False, "error": "probe timeout"})
    report.update(versions={}, checked_in="fixture-python", verdicts=env_upgrade.verdict_lines(report))
    calls = []

    def check(folder, python, instance, **kwargs):
        calls.append((folder, python, instance))
        return report

    monkeypatch.setattr(env_upgrade_service, "check", check)
    result = runner.invoke(cli.app, ["env", "check", "-f", str(command_folder), "--python", "fixture-python", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["verdicts"] == report["verdicts"]
    text = runner.invoke(cli.app, ["env", "check", "-f", str(command_folder), "--python", "fixture-python"])
    assert text.exit_code == 0, text.output
    assert all(line in text.stdout for line in report["verdicts"])
    assert "Not checked: run-time behaviour" in text.stdout
    assert calls[0][1] == "fixture-python"


def test_snapshot_archive_rejected_before_probe(command_folder, monkeypatch):
    monkeypatch.setattr(cli, "_env_command_context", lambda _: {"origin": "run_snapshot"})
    result = runner.invoke(cli.app, ["env", "snapshot", "-f", str(command_folder)])
    assert result.exit_code == 1
    assert "writable live state" in result.output
    assert not (command_folder.parent / "instance").exists()


def test_cli_wording_pin():
    import inspect
    import re
    text = "\n".join(inspect.getsource(function) for function in
                     (cli.env_check, cli.env_snapshot, cli._env_command_context, cli._env_instance_path))
    assert not re.search(r"safe|compatible", text, re.I)


def test_real_snapshot_and_check_regenerate_exact_multifile_input(command_folder, tmp_path, monkeypatch):
    site = tmp_path / "Lib/site-packages/official_fixture"
    site.mkdir(parents=True)
    source = '''
from dataclasses import field
from quam.core import QuamRoot, quam_dataclass
PADDING = 1
@quam_dataclass
class Base(QuamRoot):
    value: float = .1
    metadata: dict = field(default_factory=dict)
    def generate_config(self):
        sample = self.value * self.metadata.get("scale", 1)
        samples = [0.0]*PADDING + [sample]*4 + [0.0]*(16-PADDING-4)
        return {"version": 1, "elements": {"element": {"operations": {"cz_test": "pulse"}}},
                "pulses": {"pulse": {"length": 16, "waveforms": {"I": "wave"}}},
                "waveforms": {"wave": {"type": "arbitrary", "samples": samples}}}
'''
    official = site / "__init__.py"
    official.write_text(source, encoding="utf-8")
    (tmp_path / "extension_fixture.py").write_text(
        "from official_fixture import Base\nfrom quam.core import quam_dataclass\n"
        "@quam_dataclass\nclass Root(Base):\n    extra_phase: float = 0.0\n"
        "    def save(self, *args, **kwargs):\n        raise AssertionError('save was called')\n", encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(site.parent), str(tmp_path), str(root)]))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    state = {"__class__": "extension_fixture.Root", "value": .1, "extra_phase": 0.0}
    (command_folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (command_folder / "aux.json").write_text('{"metadata":{"scale":2}}', encoding="utf-8")
    before = {path.name: path.read_bytes() for path in command_folder.iterdir()}
    snapshot = runner.invoke(cli.app, ["env", "snapshot", "-f", str(command_folder)])
    assert snapshot.exit_code == 0, snapshot.output
    instance = tmp_path / "instance"
    from quam_state_manager.core.env_seen import chip_key
    references = waveform_refs.list_references(instance, chip_key(str(command_folder)))
    assert len(references) == 1
    assert set(references[0]["files"]) == {"state.json", "wiring.json", "aux.json"}
    official.write_text(source.replace("PADDING = 1", "PADDING = 4"), encoding="utf-8")
    checked = runner.invoke(cli.app, ["env", "check", "-f", str(command_folder), "--python", sys.executable, "--json"])
    assert checked.exit_code == 0, checked.output
    result = json.loads(checked.stdout)
    assert result["cases"]["6"]["rows"][0]["onset_lag"] == 3
    assert "VERDICT case 6: found" in "\n".join(result["verdicts"])
    assert all(result["cases"][str(n)]["status"] == "none_found" for n in range(1, 5))
    # The fixture's save() guard really differs from QuamRoot.save; case 5 must name only it.
    assert {row["method"] for row in result["cases"]["5"]["rows"]} == {"save"}
    assert {path.name: path.read_bytes() for path in command_folder.iterdir()} == before


def test_reference_skips_previewer_input_repair(tmp_path):
    outcome = {"ok": True, "result": {"warnings": ["loaded as fallback root"], "config": {}},
               "reference_inputs": {"state.json": "e30=", "wiring.json": "e30="}}
    assert waveform_refs.record(tmp_path, "key", "python", {}, outcome, context={}) is None
