"""Numerical metrics, exact inputs, matched-state regeneration and degradation."""
from __future__ import annotations

import ast
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from quam_state_manager.core import waveform_refs as refs


def config(samples, *, name="cz_test"):
    return {"elements": {"element": {"operations": {name: "pulse"}}},
            "pulses": {"pulse": {"length": len(samples), "waveforms": {"I": "wave"}}},
            "waveforms": {"wave": {"type": "arbitrary", "samples": samples}}}


def test_metrics_numerical_examples():
    result = refs.metrics([0, 2, 0], [0, 0, 0, 2, 0])
    assert result == {"onset_lag": 2, "onset_reason": "", "lag_unit": "samples", "length_difference": 2,
                      "relative_deviation": 1.0}
    zero = refs.metrics([0, 0], [0, 0])
    assert zero["onset_lag"] is None and zero["onset_reason"] == "all-zero waveform"
    assert refs.metrics([2, 4], [1, 5])["relative_deviation"] == .25
    assert refs.metrics([0], [0])["relative_deviation"] == 0
    assert refs.metrics([0], [1])["relative_deviation"] is None
    assert refs.metrics([], [1])["onset_lag"] is None
    assert refs.metrics([0, 2], [2])["onset_lag"] == -1


def test_equality_labels_and_counts():
    before = refs.traces(config([0, 1, 1e-8]))
    assert refs.compare_traces(before, before)["rows"][0]["label"] == "bit-identical"
    close = refs.traces(config([0, 1, 1.01e-8]))
    assert refs.compare_traces(before, close)["rows"][0]["label"] == "numerically equal"
    different = refs.traces(config([0, 2, 0]))
    result = refs.compare_traces(before, different)
    assert result["rows"][0]["label"] == "differs"
    assert result["message"] == "1 differ \u00b7 0 identical \u00b7 0 not comparable"
    assert refs.compare_traces(before, {})["message"] == "0 differ \u00b7 0 identical \u00b7 1 not comparable"


def test_constant_value_and_missing_operations():
    cfg = config([1])
    cfg["waveforms"]["wave"] = {"type": "constant", "sample": .2}
    before = refs.traces(cfg)
    cfg["waveforms"]["wave"]["sample"] = .3
    assert refs.compare_traces(before, refs.traces(cfg))["rows"][0]["label"] == "differs"


def test_two_qubit_first_and_name_filter():
    before = refs.traces(config([1], name="single"))
    before.update(refs.traces(config([1], name="cz_test")))
    assert refs.compare_traces(before, before)["rows"][0]["operation"] == "cz_test"
    assert len(refs.compare_traces(before, before, "single")["rows"]) == 1


def outcome():
    return {"ok": True, "result": {"config": config([0, 1]), "versions": {"quam": "0.6"}},
            "reference_inputs": {"state.json": base64.b64encode(b'{"value":1}').decode(),
                                 "wiring.json": base64.b64encode(b'{}').decode()}}


@pytest.mark.parametrize("context,unsaved", [({"origin": "dataset_archive"}, False),
    ({"readonly": True}, False), ({"origin": "run_snapshot"}, False), ({}, True)])
def test_reference_archive_and_unsaved_gate(tmp_path, context, unsaved):
    assert refs.record(tmp_path, "key", "python", {}, outcome(), context=context, unsaved=unsaved) is None
    assert not list(tmp_path.iterdir())


def test_reference_inputs_are_shared_and_schema_format_is_independent(tmp_path):
    first = refs.record(tmp_path, "key", "python", {"official_digest": "a", "schema_format": 4},
                        outcome(), context={})
    second = refs.record(tmp_path, "key", "python", {"official_digest": "b", "schema_format": 5},
                         outcome(), context={})
    assert len(list((tmp_path / "waveform_refs/inputs").glob("*.json"))) == 2
    assert refs.load(tmp_path, first)["format"] == refs.WAVEFORM_REF_FORMAT == 1
    assert refs.load(tmp_path, second)["basis"] == refs.load(tmp_path, first)["basis"]


def test_computation_only_recording_retains_the_earlier_samples(tmp_path):
    original = outcome()
    body = {"official_digest": "same", "source_fingerprint": "before"}
    first = refs.record(tmp_path, "key", "python", body, original, context={})
    original["result"]["config"] = config([0, 0, 1])
    body["source_fingerprint"] = "after"
    second = refs.record(tmp_path, "key", "python", body, original, context={})
    assert first != second
    old, new = refs.load(tmp_path, first), refs.load(tmp_path, second)
    assert old["identity"] == new["identity"]
    assert refs.compare_traces(old["traces"], new["traces"])["rows"][0]["onset_lag"] == 1


def test_corrupt_reference_and_missing_reference_abstain(tmp_path):
    key = "a" * 64
    assert refs.compare(tmp_path, key, sys.executable)["status"] == "not_checked"
    directory = tmp_path / "waveform_refs"
    directory.mkdir(exist_ok=True)
    (directory / f"{key}.json").write_text("{", encoding="utf-8")
    assert "could not check" in refs.compare(tmp_path, key, sys.executable)["message"]


def test_comparison_passes_strict_flag_and_uses_stored_state(tmp_path, monkeypatch):
    original = outcome()
    key = refs.record(tmp_path, "key", "python", {}, original, context={})
    calls = []

    def preview(python, folder, **kwargs):
        calls.append(kwargs)
        assert json.loads((folder / "state.json").read_text())["value"] == 1
        return original

    monkeypatch.setattr(refs, "preview", preview)
    result = refs.compare(tmp_path, key, "python")
    assert calls == [{"strict": True}]
    assert result["reference_state_hash"] == result["current_state_hash"]


def test_no_save_calls_in_comparison_or_previewer():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "quam_state_manager/core/waveform_refs.py",
                 root / "quam_state_manager/generator/run_generate_config.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == "save"]


def test_strict_previewer_never_uses_retry(tmp_path, monkeypatch):
    from quam_state_manager.generator import run_generate_config as previewer
    import quam.core

    (tmp_path / "state.json").write_text('{"__class__":"quam.core.QuamRoot"}', encoding="utf-8")

    def bad_load(*args, **kwargs):
        raise AttributeError("unexpected key")

    def forbidden(*args, **kwargs):
        pytest.fail("strict comparison used the retry shim")

    monkeypatch.setattr(quam.core.QuamRoot, "load", bad_load)
    monkeypatch.setattr(previewer, "_retry_without_unknown_empty_keys", forbidden)
    with pytest.raises(AttributeError, match="unexpected key"):
        previewer._load_machine(tmp_path, strict=True)


def test_real_computation_only_fault_has_known_lag_on_matched_state(tmp_path, monkeypatch):
    site = tmp_path / "Lib/site-packages/official_fixture"
    site.mkdir(parents=True)
    source = ("import numpy as np\nfrom quam.core import quam_dataclass\n"
              "from quam.components.pulses import Pulse\nPAD = 1\n"
              "@quam_dataclass\nclass Parent(Pulse):\n    amplitude: float = .1\n"
              "    def waveform_function(self):\n"
              "        return np.array([0.0]*PAD + [self.amplitude]*4 + [0.0]*(self.length-PAD-4))\n")
    (site / "__init__.py").write_text(source, encoding="utf-8")
    extension = ("from official_fixture import Parent\nfrom dataclasses import field\n"
        "from typing import Dict\nfrom quam.core import QuamRoot, QuamComponent, quam_dataclass\n"
        "from quam.components.channels import SingleChannel\n"
        "@quam_dataclass\nclass Child(Parent):\n    extra_phase: float = 0.0\n"
        "@quam_dataclass\nclass Qubit(QuamComponent):\n    xy: SingleChannel\n"
        "@quam_dataclass\nclass Root(QuamRoot):\n    qubits: Dict[str, Qubit] = field(default_factory=dict)\n"
        "    def save(self, *args, **kwargs):\n        raise AssertionError('previewer called save')\n")
    (tmp_path / "extension_fixture.py").write_text(extension, encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(site.parent), str(tmp_path), str(root)]))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    folder = tmp_path / "state"
    folder.mkdir()
    state = {"__class__": "extension_fixture.Root", "qubits": {"q0": {
        "__class__": "extension_fixture.Qubit", "xy": {
            "__class__": "quam.components.channels.SingleChannel", "id": "q0",
            "opx_output": ["controller", 1], "operations": {"cz_test": {
                "__class__": "extension_fixture.Child", "length": 16, "amplitude": .1,
                "extra_phase": 0.0}}}}}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    original = refs.preview(sys.executable, folder)
    assert original["ok"], original.get("error")
    key = refs.record(tmp_path / "instance", "key", sys.executable, {"official_digest": "a"},
                      original, context={"path": str(folder)})
    # The live state also changes; comparison must still read the stored value .1.
    state["qubits"]["q0"]["xy"]["operations"]["cz_test"]["amplitude"] = .3
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (site / "__init__.py").write_text(source.replace("PAD = 1", "PAD = 4"), encoding="utf-8")
    driver = ("import json,sys\nfrom pathlib import Path\n"
              "from quam_state_manager.core.waveform_refs import compare\n"
              "from quam_state_manager.core.env_upgrade import verdict_lines\n"
              "r=compare(sys.argv[1],sys.argv[2],sys.executable)\n"
              "Path(sys.argv[3]).write_text(json.dumps(r))\n"
              "print('\\n'.join(verdict_lines({'cases':{'6':r}})))\n")
    result_file = tmp_path / "comparison.json"
    process = subprocess.run([sys.executable, "-c", driver, str(tmp_path / "instance"), key,
        str(result_file)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert process.returncode == 0, process.stderr
    compared = json.loads(result_file.read_text())
    assert "VERDICT case 6: found" in process.stdout
    assert compared["rows"][0]["onset_lag"] == 3
    assert compared["rows"][0]["relative_deviation"] == pytest.approx(1.0)
    assert compared["reference_state_hash"] == compared["current_state_hash"]


def test_preview_forwards_strict_to_the_previewer(tmp_path, monkeypatch):
    """A comparison run must reach run_config_preview with strict=True (no empty-key retry)."""
    from quam_state_manager.core import config_generator, waveform_refs
    (tmp_path / "state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
    seen = []
    monkeypatch.setattr(config_generator, "run_config_preview",
                        lambda python, folder, **kw: seen.append(kw) or {"ok": False})
    waveform_refs.preview("python", tmp_path, strict=True)
    waveform_refs.preview("python", tmp_path)
    assert seen[0].get("strict") is True
    assert "strict" not in seen[1]
