"""Regression tests for the pre-customer Generate-Config / Config-Viewer fixes.

Covers the four red-team fixes (docs: genconfig red-team 2026-06-21):
  ③ null-anharmonicity crash  — run_build defaults a None anharmonicity so a
     zero-populate chip's generate_config() no longer crashes (float*None).
  ① wizard CR/ZZ emission     — deriveLines (JS) now emits cross_resonance /
     zz_drive pair lines; the build engine produces cr_<c>_<t> config elements
     (verified here at the run_build level, the engine the JS feeds).
  ⑤ schema/version-skew error — run_generate_config annotates a load failure
     with an actionable "pick the matching env" hint + the chip's __class__.
  ④ OPX+/Octave hidden        — covered by the template/JS change; the spec
     contract is unchanged, so no Python test (UI-only).

Pure-function tests for ⑤ run everywhere. The ③/① tests spawn the real QM
build subprocess and auto-skip when no QM-capable conda env is present (they
run against e.g. the wsl_kri env in a full local suite).
"""

import importlib.util
import json
from pathlib import Path

import pytest

from quam_state_manager.core.config_generator import (
    discover_envs,
    probe_env,
    run_config_preview,
    run_generator,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GEN_DIR = REPO_ROOT / "quam_state_manager" / "generator"


def _first_usable_qm_env():
    for env in discover_envs():
        if probe_env(env["python"])["usable"]:
            return env["python"]
    return None


def _load_run_generate_config():
    """Import the standalone run_generate_config.py as a module for unit tests."""
    spec = importlib.util.spec_from_file_location(
        "rgc_under_test", GEN_DIR / "run_generate_config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# ⑤ schema/version-skew error annotation — pure, runs everywhere
# ---------------------------------------------------------------------------

class TestSkewErrorAnnotation:
    def test_hint_leads_on_module_not_found(self):
        rgc = _load_run_generate_config()
        raw = ("RuntimeError: Could not load QUAM machine from /x. __class__ "
               "load failed with AttributeError: Attribute id is not a valid "
               "attr of ...macros[\"cr\"]; FluxTunableQuam.load() failed with "
               "ModuleNotFoundError: No module named "
               "'quam_builder.architecture.superconducting.qpu.flux_tunable'")
        out = rgc._annotate_load_error(
            raw, "quam_config.my_quam.Quam",
            {"quam": "0.5.0a3", "quam_builder": "0.3.0"})
        # Leads with the actionable hint, not the misleading ModuleNotFoundError.
        assert out.startswith("Couldn't load this chip")
        assert "quam_config.my_quam.Quam" in out          # the chip's class
        assert "quam_builder 0.3.0" in out                 # the env's version
        assert "Environment step" in out                   # the remedy
        assert raw in out                                  # detail preserved

    def test_attribute_error_triggers_hint(self):
        rgc = _load_run_generate_config()
        raw = "AttributeError: Attribute isolation is not a valid attr of ...twpas"
        out = rgc._annotate_load_error(raw, None, {"quam_builder": "0.2.0"})
        assert out.startswith("Couldn't load this chip")
        assert raw in out

    def test_unrelated_error_is_passthrough(self):
        rgc = _load_run_generate_config()
        raw = "ValueError: amplitude must be finite"
        assert rgc._annotate_load_error(raw, "X", {}) == raw

    def test_read_chip_class(self, tmp_path):
        rgc = _load_run_generate_config()
        (tmp_path / "state.json").write_text(
            json.dumps({"__class__": "quam_config.my_quam.Quam", "qubits": {}}))
        assert rgc._read_chip_class(tmp_path) == "quam_config.my_quam.Quam"

    def test_read_chip_class_missing_file(self, tmp_path):
        rgc = _load_run_generate_config()
        assert rgc._read_chip_class(tmp_path) is None

    def test_read_chip_class_non_string(self, tmp_path):
        rgc = _load_run_generate_config()
        (tmp_path / "state.json").write_text(json.dumps({"__class__": 123}))
        assert rgc._read_chip_class(tmp_path) is None


# ---------------------------------------------------------------------------
# Integration specs (mirror what the post-fix wizard emits)
# ---------------------------------------------------------------------------

def _mw_lf_2q_no_populate():
    """A flux-tunable 2q chip with EMPTY populate (the ③ crash trigger)."""
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [
            {"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2"],
        "qubit_pairs": [],
        "twpas": [],
        "lines": [
            {"element": "q1", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q1", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q1", "line": "flux", "channel": {"kind": "lf_fem"}},
            {"element": "q2", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q2", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q2", "line": "flux", "channel": {"kind": "lf_fem"}},
        ],
        "populate": {},
    }


def _mw_only_cr_3q():
    """A fixed-frequency 3q chip with cross_resonance pair lines (the ① path)."""
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [
            {"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "mw"},
                                {"slot": 3, "fem": "mw"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2", "q3"],
        "qubit_pairs": [["q1", "q2"], ["q2", "q3"]],
        "twpas": [],
        "lines": [
            {"element": "q1", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q1", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q2", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q2", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q3", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q3", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q1-q2", "line": "cross_resonance", "channel": {"kind": "mw_fem"}},
            {"element": "q2-q3", "line": "cross_resonance", "channel": {"kind": "mw_fem"}},
        ],
        "populate": {},
    }


class TestAnharmonicityDefaultIntegration:
    """③ — a zero-populate build must default anharmonicity and config cleanly."""

    def test_no_populate_build_then_generate_config_does_not_crash(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")

        out_dir = tmp_path / "quam_state"
        outcome = run_generator(usable, "build", _mw_lf_2q_no_populate(), out_dir, timeout=180)
        assert outcome["ok"], outcome.get("error")

        state = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))
        # The fix: anharmonicity is no longer None for a DragCosine-bearing qubit.
        for qid, q in state["qubits"].items():
            assert q.get("anharmonicity") is not None, f"{qid} anharmonicity still None"

        # The payoff: generate_config() succeeds instead of float*None TypeError.
        cfg = run_config_preview(usable, out_dir, timeout=180)
        assert cfg["ok"], cfg.get("error")
        assert cfg["result"]["config"]["elements"], "empty config elements"


class TestCrossResonanceGenerationIntegration:
    """① — cross_resonance pair lines build and yield cr_<c>_<t> config elements."""

    def test_cr_pairs_build_and_appear_in_config(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")

        out_dir = tmp_path / "quam_state"
        outcome = run_generator(usable, "build", _mw_only_cr_3q(), out_dir, timeout=180)
        assert outcome["ok"], outcome.get("error")
        # Fixed-frequency chip with real qubit pairs (CR was not silently dropped).
        assert outcome["result"]["quam_class"] == "FixedFrequencyQuam"
        assert outcome["result"]["qubit_pairs"], "qubit pairs were dropped"

        cfg = run_config_preview(usable, out_dir, timeout=180)
        assert cfg["ok"], cfg.get("error")
        elements = cfg["result"]["config"]["elements"]
        cr_elements = [e for e in elements if e.startswith("cr_")]
        # Customer naming convention: cr_<control>_<target>.
        assert cr_elements, f"no cr_ elements in {sorted(elements)[:20]}"


def _mw_lf_cz_chip(pair_gate, *, with_coupler):
    """A flux-tunable chip with qubit flux; coupler lines only for the tunable case.

    Mirrors what the wizard emits per the device templates: cz_fixed has NO
    coupler wiring line (run_build creates the pair), cz_tunable has one.
    """
    qubits = ["q1", "q2", "q3"]
    lines = []
    for q in qubits:
        lines += [
            {"element": q, "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": q, "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": q, "line": "flux", "channel": {"kind": "lf_fem"}},
        ]
    pairs = [["q1", "q2"], ["q2", "q3"]]
    if with_coupler:
        for a, b in pairs:
            lines.append({"element": f"{a}-{b}", "line": "coupler", "channel": {"kind": "lf_fem"}})
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "mw"}, {"slot": 3, "fem": "mw"},
            {"slot": 5, "fem": "lf"}, {"slot": 6, "fem": "lf"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": qubits, "qubit_pairs": pairs, "twpas": [],
        "lines": lines, "populate": {}, "pair_gate": pair_gate,
    }


def _cz_ops_in_config(config):
    """All (element, op) pairs whose op name looks like a CZ pulse."""
    out = []
    for ek, ev in config.get("elements", {}).items():
        for op in (ev.get("operations") or {}):
            if "cz" in op.lower():
                out.append((ek, op))
    return out


class TestCZFixedCouplerIntegration:
    """CZ fixed coupler: pairs created post-build (no coupler line), CZ on qubit z."""

    def test_pairs_created_and_cz_on_qubit_z(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")
        out_dir = tmp_path / "quam_state"
        outcome = run_generator(usable, "build", _mw_lf_cz_chip("cz_fixed", with_coupler=False),
                                out_dir, timeout=180)
        assert outcome["ok"], outcome.get("error")
        # No coupler wiring line, yet the pairs exist (run_build created them).
        assert outcome["result"]["quam_class"] == "FluxTunableQuam"
        assert len(outcome["result"]["qubit_pairs"]) == 2

        cfg = run_config_preview(usable, out_dir, timeout=180)
        assert cfg["ok"], cfg.get("error")
        cz = _cz_ops_in_config(cfg["result"]["config"])
        # CZ flux pulse lands on a qubit z element; there is NO coupler element.
        assert any(e.endswith(".z") for e, _ in cz), f"no CZ op on a qubit z line: {cz}"
        assert not any(e.startswith("coupler_") for e in cfg["result"]["config"]["elements"])


class TestCZTunableCouplerIntegration:
    """CZ tunable coupler: pairs from coupler lines, CZ on qubit z AND the coupler."""

    def test_cz_on_qubit_z_and_coupler(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")
        out_dir = tmp_path / "quam_state"
        outcome = run_generator(usable, "build", _mw_lf_cz_chip("cz_tunable", with_coupler=True),
                                out_dir, timeout=180)
        assert outcome["ok"], outcome.get("error")
        assert len(outcome["result"]["qubit_pairs"]) == 2

        cfg = run_config_preview(usable, out_dir, timeout=180)
        assert cfg["ok"], cfg.get("error")
        elements = cfg["result"]["config"]["elements"]
        cz = _cz_ops_in_config(cfg["result"]["config"])
        assert any(e.endswith(".z") for e, _ in cz), f"no CZ op on a qubit z line: {cz}"
        # The tunable coupler also carries the CZ pulse on its own element.
        assert any(e.startswith("coupler_") for e, _ in cz), f"no CZ op on a coupler: {cz}"
