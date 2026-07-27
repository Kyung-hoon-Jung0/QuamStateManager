"""Generate-wizard supercritical feedback batch (docs/60):

1. board qubit DELETE is recoverable (snapshot undo — cjs selfcheck driven here);
2. every CZ variant seeds BY DEFAULT ("all"), pre-filled from the standard
   defaults (validate_spec / capabilities / builtin-preset surface);
3. the wizard's Preview-config step exposes the 2Q-gate pulse gallery
   (config_view helpers + the /generate/preview-pulses[/-waveform] routes).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import capabilities, config_view, gen_presets
from quam_state_manager.core.config_generator import validate_spec
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "wiring_undo_selfcheck.cjs"


# ── 1. board delete-undo (jsdom behavioral check) ──────────────────────────

@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_wiring_undo_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


# ── 2. all-CZ-gates-by-default surfaces ────────────────────────────────────

def _spec_with_variant(variant):
    return {"populate": {"pairs": {"q1-q2": {"cz_variant": variant}}}}


class TestCzAllVariants:
    def test_validate_spec_accepts_all_and_blank(self):
        for v in ("all", "", None):
            errs = [e for e in validate_spec(_spec_with_variant(v))
                    if "cz_variant" in e]
            assert errs == [], f"variant {v!r} must be accepted: {errs}"

    def test_validate_spec_still_rejects_unknown(self):
        errs = [e for e in validate_spec(_spec_with_variant("bogus"))
                if "cz_variant" in e]
        assert errs, "unknown variant must still error"

    def test_capabilities_all_requires_no_optional_shapes(self):
        """blank / "all" adds NO shape-class requirements — run_build seeds
        every AVAILABLE variant and skips missing shapes with a warning, so
        the build can never hard-fail on an optional pulse class."""
        base = capabilities.required_capabilities(_spec_with_variant(None))
        for v in ("all", ""):
            req = capabilities.required_capabilities(_spec_with_variant(v))
            assert req == base, f"variant {v!r} must add nothing: {req - base}"
        # an EXPLICIT single variant still requires its shape class
        req = capabilities.required_capabilities(_spec_with_variant("SNZ"))
        assert "pulse.cz_snz" in req

    def test_builtin_preset_carries_the_seed_values(self):
        pairs = gen_presets.builtin_standard()["sections"]["pairs"]["defaults"]
        assert pairs["cz_interaction_duration"] == 100
        assert pairs["cz_amplitude"] == 0.1
        # ZZ drive seeds mirror run_build's own defaults (never invented)
        assert pairs["zz_drive_amplitude"] == 1.0
        assert pairs["zz_flattop_length"] == 100
        assert pairs["zz_flattop_flat_length"] == 84

    def test_run_build_source_seeds_all_variants_by_default(self):
        """Source pin (the QM-env integration lives in test_pair_gates_seed):
        blank == "all" loops every variant with fallback=False (skip, never
        collapse onto unipolar N times)."""
        src = (_ROOT / "quam_state_manager" / "generator" / "run_build.py").read_text(
            encoding="utf-8")
        assert 'vals.get("cz_variant") or "all"' in src
        assert 'fallback=(req != "all")' in src


# ── 3. preview-pulses gallery ──────────────────────────────────────────────

def _synthetic_config():
    return {
        "elements": {
            "q1.z": {"operations": {
                "cz_unipolar_flux_pulse_q1_q2": "p_flux",
                "const": "p_other",              # plain flux op — not a gate
            }},
            "coupler_q1_q2": {"operations": {
                "cz_unipolar_coupler_pulse_q1_q2": "p_coupler",
            }},
            "q2.xy": {"operations": {"x180": "p_x180"}},
        },
        "pulses": {
            "p_flux": {"length": 4, "waveforms": {"single": "w_flux"}},
            "p_coupler": {"length": 4, "waveforms": {"single": "w_coupler"}},
            "p_x180": {"length": 4, "waveforms": {"I": "w_i", "Q": "w_q"}},
            "p_other": {"length": 4, "waveforms": {"single": "w_flux"}},
        },
        "waveforms": {
            "w_flux": {"type": "constant", "sample": 0.1},
            "w_coupler": {"type": "constant", "sample": 0.05},
            "w_i": {"type": "constant", "sample": 0.2},
            "w_q": {"type": "constant", "sample": 0.0},
        },
    }


class TestPreviewPulseGallery:
    def test_all_pair_gate_operations_enumerates_both_namings(self):
        ops = config_view.all_pair_gate_operations(_synthetic_config())
        keys = {(o["element"], o["op_name"]) for o in ops}
        assert ("q1.z", "cz_unipolar_flux_pulse_q1_q2") in keys
        assert ("coupler_q1_q2", "cz_unipolar_coupler_pulse_q1_q2") in keys
        # non-gate ops excluded
        assert not any(o["op_name"] in ("const", "x180") for o in ops)

    def test_waveform_for_element_op_resolves_traces(self):
        wf = config_view.waveform_for_element_op(
            _synthetic_config(), "q1.z", "cz_unipolar_flux_pulse_q1_q2")
        assert wf is not None and wf["pulse"] == "p_flux"
        tr = wf["traces"][0]
        assert tr["label"] == "single" and len(tr["x"]) == len(tr["y"]) == 4
        assert config_view.waveform_for_element_op(
            _synthetic_config(), "q1.z", "nope") is None

    def test_routes_serve_the_stashed_preview(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
        c = app.test_client()
        folder = tmp_path / "built_chip"
        folder.mkdir()
        routes_mod._stash_preview_seed(folder, "h", _synthetic_config(), {})

        r = c.get("/generate/preview-pulses", query_string={"path": str(folder)})
        assert r.status_code == 200
        ops = r.get_json()["ops"]
        assert any(o["op_name"] == "cz_unipolar_flux_pulse_q1_q2" for o in ops)

        r = c.get("/generate/preview-pulse-waveform", query_string={
            "path": str(folder), "element": "coupler_q1_q2",
            "op": "cz_unipolar_coupler_pulse_q1_q2"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] and body["pulse"] == "p_coupler"
        assert body["traces"][0]["y"] == [0.05] * 4

        # cold seed → clean 409, wrong op → 404
        r = c.get("/generate/preview-pulses", query_string={"path": str(tmp_path / "never")})
        assert r.status_code == 409
        r = c.get("/generate/preview-pulse-waveform", query_string={
            "path": str(folder), "element": "q1.z", "op": "nope"})
        assert r.status_code == 404
