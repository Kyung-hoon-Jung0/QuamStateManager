"""QA diagnostics-r2-03: a failed "Validate deeply (Quam.load)" shows its reason.

/config/regenerate answers a failure with 502 (400 with no env) and an
explanatory body naming the failing path. htmx 2.x drops error bodies unless
app.js's beforeSwap allowance opts the target in, which it does for
`.config-status-host` targets only -- the Diagnostics slot was not one, so the
user saw a generic "please try again" toast. Pinned under jsdom against the
shipped template markup + the real app.js handler.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_diag_deep_validate_selfcheck():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / "diag_deep_validate_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(_ROOT), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL OK" in r.stdout, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# QA F-R: "Validate deeply (Quam.load)" answers with a VERDICT -- succeeded /
# failed, in which env, when -- not the Config Viewer's Regenerate/export
# controls and a raw-UTC "Last good". Same /config/regenerate subprocess
# (docs/56); only deep=1 changes the presentation, the viewer is byte-identical.
# ---------------------------------------------------------------------------
import json
import re

from quam_state_manager.web.app import create_app

_ENV = "/labs/conda/envs/cqt/python.exe"


def _deep_client(tmp_path, monkeypatch, result, ok=True, error=None):
    from quam_state_manager.core import config_generator
    from tests.test_diagnostics_banner_routes import _state, _wiring
    (tmp_path / "state.json").write_text(json.dumps(_state(0.3)), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    monkeypatch.setattr(config_generator, "get_selected_env", lambda *a, **kw: _ENV)
    monkeypatch.setattr(config_generator, "run_config_preview", lambda *a, **kw: {
        "ok": ok, "status": "ok" if ok else "error", "result": result,
        "returncode": 0 if ok else 1, "stdout": "", "stderr": "", "error": error})
    return client


def _ok_result(**kw):
    r = {"status": "ok", "config": {"version": 1, "elements": {}},
         "versions": {"quam": "0.6.0", "quam_builder": "0.4.0", "qm": "1.4.0"},
         "warnings": [], "qubits": ["q1"], "qubit_pairs": [],
         "chip_class": "quam_builder.x.FluxTunableQuam",
         "loaded_class": "quam_builder.x.FluxTunableQuam"}
    r.update(kw)
    return r


def _flat(resp):
    return re.sub(r"\s+", " ", resp.get_data(as_text=True))


def test_deep_success_is_a_verdict_not_the_viewer_controls(tmp_path, monkeypatch):
    c = _deep_client(tmp_path, monkeypatch, _ok_result())
    r = c.post("/config/regenerate", data={"deep": "1"})
    assert r.status_code == 200
    body = _flat(r)
    assert "Quam.load() + generate_config() succeeded" in body
    assert "diag-env-deep-ok" in body
    assert ">cqt</code>" in body                       # the env, by name
    assert "quam&nbsp;0.6.0" in body and "qm&nbsp;1.4.0" in body
    assert "<time datetime=" in body                  # local time, ISO kept
    assert "Regenerate" not in body and "config.json" not in body
    assert "Last&nbsp;good" not in body
    assert "unsaved edits were not included" not in body


def test_deep_failure_says_failed_with_the_reason(tmp_path, monkeypatch):
    c = _deep_client(tmp_path, monkeypatch, {"traceback": "Traceback: boom\n"},
                     ok=False, error="RuntimeError: Could not load QUAM machine")
    r = c.post("/config/regenerate", data={"deep": "1"})
    assert r.status_code == 502                       # the swap allowance keeps it
    body = _flat(r)
    assert "Quam.load() / generate_config() failed" in body
    assert "diag-env-deep-err" in body and ">cqt</code>" in body
    assert "Could not load QUAM machine" in body and "Traceback: boom" in body
    assert "Regenerate" not in body


def test_deep_fallback_root_is_not_a_green_tick(tmp_path, monkeypatch):
    c = _deep_client(tmp_path, monkeypatch, _ok_result(
        loaded_class="quam_builder.y.FixedFrequencyQuam",
        warnings=["loaded as fallback root FixedFrequencyQuam after: x"]))
    body = _flat(c.post("/config/regenerate", data={"deep": "1"}))
    assert "diag-env-deep-warn" in body and "diag-env-deep-ok" not in body
    assert "fallback root <code>quam_builder.y.FixedFrequencyQuam</code>" in body
    assert "1 warning" in body


def test_deep_names_unsaved_edits_it_did_not_see(tmp_path, monkeypatch):
    c = _deep_client(tmp_path, monkeypatch, _ok_result())
    c.post("/field/edit", data={
        "dot_path": "qubits.q1.resonator.operations.readout.amplitude", "value": "0.2"})
    body = _flat(c.post("/config/regenerate", data={"deep": "1"}))
    assert "unsaved edits were not included" in body


def test_the_config_viewer_path_is_unchanged(tmp_path, monkeypatch):
    c = _deep_client(tmp_path, monkeypatch, _ok_result())
    body = _flat(c.post("/config/regenerate"))
    assert "Regenerate from loaded chip" in body and "Last&nbsp;good" in body
    assert "succeeded" not in body


def test_the_button_asks_for_the_verdict():
    tpl = (_ROOT / "quam_state_manager" / "web" / "templates"
           / "_diagnostics_env.html").read_text(encoding="utf-8")
    btn = re.search(r'<button[^>]*hx-post="/config/regenerate"[^>]*>', tpl).group(0)
    assert "hx-vals='{\"deep\": \"1\"}'" in btn
    assert 'hx-disabled-elt="this"' in btn


def test_generator_reports_a_fallback_root(tmp_path, monkeypatch):
    """The subprocess side: the loaded class + how it loaded ride the result,
    instead of only stderr, so the verdict cannot hide a fallback."""
    import importlib
    import types
    from quam_state_manager.generator import run_generate_config as rgc

    class _Root:
        @classmethod
        def load(cls):
            return cls()

        def generate_config(self):
            return {"version": 1}

    class _Broken(_Root):
        @classmethod
        def load(cls):
            raise ValueError("chip class needs a newer quam")

    mods = {"labmod": types.SimpleNamespace(Quam=_Broken, Good=_Root),
            "quam_builder.architecture.superconducting.qpu.flux_tunable":
                types.SimpleNamespace(FluxTunableQuam=_Root)}

    def fake_import(name, *a, **kw):
        if name in mods:
            return mods[name]
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    (tmp_path / "state.json").write_text(json.dumps({"__class__": "labmod.Quam"}),
                                         encoding="utf-8")
    out = rgc.run_generate_config(tmp_path)
    assert out["loaded_class"].endswith("_Root")
    assert any("fallback root FluxTunableQuam" in w and "newer quam" in w
               for w in out["warnings"]), out["warnings"]
    # the chip's own class loading is a clean run: no note, its own class
    (tmp_path / "state.json").write_text(json.dumps({"__class__": "labmod.Good"}),
                                         encoding="utf-8")
    out = rgc.run_generate_config(tmp_path)
    assert out["warnings"] == [] and out["loaded_class"].endswith("_Root")
