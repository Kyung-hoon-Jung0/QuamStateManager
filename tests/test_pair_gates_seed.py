"""Integration tests for the 2Q-gate seeding the Generate-Config build performs
(``run_build._seed_cr_gate`` / ``_seed_cz_variant``).

These build real chips through the generator subprocess in a discovered QM-stack
conda env and assert the resulting ``state.json`` carries a *complete* 2Q gate —
the CR drive + cancel pulses + CRGate macro, and the chosen CZ flux-pulse variant.
They auto-skip when no QM-capable env is present (same gating as
``test_generate_config_fixes``); CR-macro assertions additionally tolerate an
upstream quam_builder with no CRGate class (channel seeded, macro absent).
"""

import json

import pytest

from quam_state_manager.core.config_generator import (
    discover_envs,
    probe_env,
    run_generator,
)


def _first_usable_qm_env():
    for env in discover_envs():
        if probe_env(env["python"])["usable"]:
            return env["python"]
    return None


# --- spec builders ---------------------------------------------------------

def _cr_3q_spec():
    """Fixed-frequency 3q chip with cross_resonance pair lines → CR gate."""
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "mw"}, {"slot": 3, "fem": "mw"}]}],
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
        "pair_gate": "cr",
    }


def _cz_tunable_spec(populate_pairs=None):
    """Flux-tunable 3q chip with coupler lines → CZ tunable-coupler gate."""
    qubits = ["q1", "q2", "q3"]
    lines = []
    for q in qubits:
        lines += [
            {"element": q, "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": q, "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": q, "line": "flux", "channel": {"kind": "lf_fem"}},
        ]
    pairs = [["q1", "q2"], ["q2", "q3"]]
    for a, b in pairs:
        lines.append({"element": f"{a}-{b}", "line": "coupler", "channel": {"kind": "lf_fem"}})
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "mw"}, {"slot": 3, "fem": "mw"},
            {"slot": 5, "fem": "lf"}, {"slot": 6, "fem": "lf"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": qubits, "qubit_pairs": pairs, "twpas": [],
        "lines": lines, "pair_gate": "cz_tunable",
        "populate": {"pairs": populate_pairs or {}},
    }


# --- output-folder isolation ------------------------------------------------

class TestBuildOutputFolderIsolation:
    """A build must succeed even when the output folder already holds OTHER quam
    json (an experiment archive). The intermediate ``quam_cls.load()`` recursively
    ingests every ``*.json`` under ``QUAM_STATE_PATH``; pointing that at the output
    folder used to merge foreign chips and crash (the customer's
    ``exponential_dc_gain is not a valid attr`` error). The build now runs in an
    isolated temp dir and copies only the two artefacts out.
    """

    def test_build_ignores_foreign_json_in_output_folder(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")

        out_dir = tmp_path / "out"
        # Pre-seed a NESTED foreign chip whose state.json carries an attr no quam
        # class has. A recursive merge-load of out_dir would choke on it — exactly
        # how the customer's exponential_dc_gain crash happened.
        archive = out_dir / "old_experiment" / "quam_state"
        archive.mkdir(parents=True)
        (archive / "state.json").write_text(json.dumps({
            "__class__": "quam_builder.architecture.superconducting.qpu."
                         "fixed_frequency_quam.FixedFrequencyQuam",
            "qubits": {},
            "TOTALLY_NOT_A_VALID_QUAM_ATTR_zzz": 123,
        }), encoding="utf-8")
        (archive / "wiring.json").write_text(
            json.dumps({"network": {}, "wiring": {}}), encoding="utf-8")

        outcome = run_generator(usable, "build", _cr_3q_spec(), out_dir, timeout=180)
        # Pre-fix this raised AttributeError during the recursive load.
        assert outcome["ok"], outcome.get("error")

        state_text = (out_dir / "state.json").read_text(encoding="utf-8")
        state = json.loads(state_text)
        assert sorted(state["qubits"].keys()) == ["q1", "q2", "q3"]
        # the fresh chip won, the foreign bogus attr never leaked in
        assert "TOTALLY_NOT_A_VALID_QUAM_ATTR_zzz" not in state_text
        # the seeded archive is left untouched
        assert (archive / "state.json").exists()


# --- CR ---------------------------------------------------------------------

class TestCrossResonanceSeed:
    """CR pairs get a full gate: square+flattop drive, cancel tones, CRGate macro."""

    def test_cr_full_gate_seeded(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")

        out_dir = tmp_path / "quam_state"
        outcome = run_generator(usable, "build", _cr_3q_spec(), out_dir, timeout=180)
        assert outcome["ok"], outcome.get("error")
        assert outcome["result"]["quam_class"] == "FixedFrequencyQuam"

        state = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))
        pairs = state["qubit_pairs"]
        assert pairs, "no qubit pairs were built"

        for pid, pair in pairs.items():
            cr = pair.get("cross_resonance")
            assert cr is not None, f"{pid}: no cross_resonance channel"

            # Both drive ops present (build_quam seeds only `square`; we add `flattop`).
            ops = cr.get("operations") or {}
            assert "square" in ops and "flattop" in ops, f"{pid}: CR ops {list(ops)}"

            # Zero-populate: the target frequency is unknown, so the CR drive IF is
            # left None — NOT a dangling "#./inferred_intermediate_frequency" string
            # (which qm.open_qm rejects as "Not a valid number"). The populated case
            # (IF resolves to a number) is covered by
            # test_cr_intermediate_frequency_resolves_when_populated below.
            assert cr.get("intermediate_frequency") is None, (
                f"{pid}: zero-populate CR IF should be None, got "
                f"{cr.get('intermediate_frequency')!r}")

            # Target-side cancel tones, length-linked back into the cr drive ops.
            tgt = pair["qubit_target"].lstrip("#/").split("/")[-1]
            txy_ops = state["qubits"][tgt]["xy"]["operations"]
            sq = txy_ops.get(f"cr_square_{pid}")
            ft = txy_ops.get(f"cr_flattop_{pid}")
            assert sq is not None and ft is not None, f"{pid}: missing cancel tones"
            assert isinstance(sq["length"], str) and sq["length"].endswith(
                "/operations/square/length"), "cancel-tone length not ref-linked"

            # The cr macro (when the install ships CRGate — the customer build does).
            macros = pair.get("macros") or {}
            warns = " ".join(outcome["result"].get("warnings") or [])
            if "CRGate macro class not found" not in warns:
                assert "cr" in macros, f"{pid}: no 'cr' macro and no degrade warning"
                assert macros["cr"]["__class__"].endswith("CRGate")

        # A zero-populate CR build can't establish the target frequency, so it must
        # warn instead of silently shipping a config with a dangling CR IF.
        allw = " ".join(outcome["result"].get("warnings") or [])
        assert "CR target frequency unknown" in allw, (
            "zero-populate CR build should warn about the unknown target "
            f"frequency: {outcome['result'].get('warnings')}")

    def test_cr_appears_in_generated_config(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")
        from quam_state_manager.core.config_generator import run_config_preview

        out_dir = tmp_path / "quam_state"
        assert run_generator(usable, "build", _cr_3q_spec(), out_dir, timeout=180)["ok"]
        cfg = run_config_preview(usable, out_dir, timeout=180)
        assert cfg["ok"], cfg.get("error")
        els = cfg["result"]["config"]["elements"]
        cr_els = [e for e in els if e.startswith("cr_")]
        assert cr_els, f"no cr_ elements: {sorted(els)[:20]}"
        # Each CR element exposes BOTH drive ops (the gap this fills).
        for e in cr_els:
            ops = els[e].get("operations") or {}
            assert "square" in ops and "flattop" in ops, f"{e} ops {list(ops)}"


# --- CZ variants ------------------------------------------------------------

class TestCZVariantSeed:
    """The default stays unipolar; a populate cz_variant selects another shape."""

    def test_default_is_unipolar(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")
        out_dir = tmp_path / "quam_state"
        assert run_generator(usable, "build", _cz_tunable_spec(), out_dir, timeout=180)["ok"]
        state = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))
        macro_keys = set()
        for pair in state["qubit_pairs"].values():
            macro_keys |= set((pair.get("macros") or {}).keys())
        assert "cz_unipolar" in macro_keys, f"default not unipolar: {macro_keys}"

    def test_opt_in_snz_variant(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")
        out_dir = tmp_path / "quam_state"
        spec = _cz_tunable_spec(populate_pairs={"q1-2": {"cz_variant": "SNZ"}})
        assert run_generator(usable, "build", spec, out_dir, timeout=180)["ok"]
        state = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))
        p12 = state["qubit_pairs"]["q1-2"]
        macros = p12.get("macros") or {}
        # The opt-in variant produced a cz_SNZ macro (or degraded to unipolar with a
        # warning if SNZPulse is absent — but the customer build ships it).
        assert "cz_SNZ" in macros or "cz_unipolar" in macros, f"q1-2 macros {list(macros)}"


class TestCzVariantAllowlistInSync:
    """The seedable-CZ-variant set is declared twice and must not drift.

    ``run_build._CZ_VARIANTS`` (the seeder, stdlib-only so it can run in a foreign
    QM env) and ``config_generator.CZ_VARIANTS`` (the validator, imported in-process)
    can't share one symbol across the subprocess boundary. If they drift, the
    validator could pass a variant the seeder silently coerces to ``unipolar``
    (run_build.py: ``variant if variant in _CZ_VARIANTS else "unipolar"``), or reject
    a variant the seeder actually supports. This test pins them equal. Parses the
    run_build literal via AST so it never imports the heavy QM stack.
    """

    def test_allowlists_are_equal(self):
        import ast
        from pathlib import Path

        from quam_state_manager.core.config_generator import CZ_VARIANTS

        run_build_src = (
            Path(__file__).resolve().parent.parent
            / "quam_state_manager" / "generator" / "run_build.py"
        ).read_text(encoding="utf-8")

        seeder_variants = None
        for node in ast.walk(ast.parse(run_build_src)):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_CZ_VARIANTS" for t in node.targets
            ):
                seeder_variants = set(ast.literal_eval(node.value))
                break

        assert seeder_variants is not None, "run_build._CZ_VARIANTS literal not found"
        assert seeder_variants == set(CZ_VARIANTS), (
            "CZ-variant allowlists drifted: run_build._CZ_VARIANTS="
            f"{sorted(seeder_variants)} vs config_generator.CZ_VARIANTS="
            f"{sorted(CZ_VARIANTS)}"
        )


# --- CR frequency derivation (P0: a populated CR config must be qm-openable) -

def _cr_2q_populated_spec():
    """Fixed-frequency CR 2q chip WITH qubit frequencies populated, so the CR
    drive can derive its target frequency and the inferred-IF reference resolves."""
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "mw"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "twpas": [],
        "lines": [
            {"element": "q1", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q1", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q2", "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": "q2", "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": "q1-q2", "line": "cross_resonance", "channel": {"kind": "mw_fem"}},
        ],
        "pair_gate": "cr",
        "populate": {
            "qubit": {
                "q1": {"RF_freq": 4.9e9, "LO_frequency": 4.8e9, "anharmonicity": -2.0e8},
                "q2": {"RF_freq": 5.1e9, "LO_frequency": 5.0e9, "anharmonicity": -2.0e8},
            },
            "resonator": {
                "q1": {"RF_freq": 7.1e9, "LO_frequency": 7.0e9},
                "q2": {"RF_freq": 7.3e9, "LO_frequency": 7.2e9},
            },
        },
    }


class TestCrFrequencyDerivation:
    """P0 guard: a populated CR build must yield a NUMERIC intermediate_frequency
    in the generated config (not a dangling string that qm.open_qm rejects)."""

    def test_cr_intermediate_frequency_resolves_when_populated(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")
        from quam_state_manager.core.config_generator import run_config_preview

        out_dir = tmp_path / "quam_state"
        outcome = run_generator(usable, "build", _cr_2q_populated_spec(), out_dir, timeout=180)
        assert outcome["ok"], outcome.get("error")
        # Target freqs were derived from the populated target qubit, so no warning.
        warns = " ".join(outcome["result"].get("warnings") or [])
        assert "CR target frequency unknown" not in warns, outcome["result"].get("warnings")

        # The seeded channel carries the derived target LO/IF + the inferred ref.
        state = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))
        cr = state["qubit_pairs"]["q1-2"]["cross_resonance"]
        assert isinstance(cr.get("target_qubit_LO_frequency"), (int, float))
        assert isinstance(cr.get("target_qubit_IF_frequency"), (int, float))

        # …and generate_config() renders a NUMERIC IF (the dangling-string bug).
        cfg = run_config_preview(usable, out_dir, timeout=180)
        assert cfg["ok"], cfg.get("error")
        els = cfg["result"]["config"]["elements"]
        cr_els = [e for e in els if e.startswith("cr_")]
        assert cr_els, f"no cr_ elements: {sorted(els)[:20]}"
        for e in cr_els:
            ifreq = els[e].get("intermediate_frequency")
            assert isinstance(ifreq, (int, float)), (
                f"{e}.intermediate_frequency must be numeric, got {ifreq!r}")


# --- CZ tunable-coupler seeding (P2: independent coupler knob + coupler offset) -

def _cz_tunable_coupler_spec(populate_pairs=None):
    qubits = ["q1", "q2"]
    lines = []
    for q in qubits:
        lines += [
            {"element": q, "line": "resonator", "group": "f", "channel": {"kind": "mw_fem"}},
            {"element": q, "line": "drive", "channel": {"kind": "mw_fem"}},
            {"element": q, "line": "flux", "channel": {"kind": "lf_fem"}},
        ]
    lines.append({"element": "q1-q2", "line": "coupler", "channel": {"kind": "lf_fem"}})
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "mw"},
            {"slot": 5, "fem": "lf"}, {"slot": 6, "fem": "lf"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": qubits, "qubit_pairs": [["q1", "q2"]], "twpas": [],
        "lines": lines, "pair_gate": "cz_tunable",
        "populate": {"pairs": populate_pairs or {}},
    }


class TestCZTunableCouplerSeed:
    def test_coupler_pulse_independent_and_offset_applied(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")
        out_dir = tmp_path / "quam_state"
        spec = _cz_tunable_coupler_spec(populate_pairs={
            "q1-q2": {"cz_variant": "flattop", "cz_amplitude": 0.18,
                      "coupler_interaction_offset": 0.05}})
        assert run_generator(usable, "build", spec, out_dir, timeout=180)["ok"]
        state = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))
        pair = state["qubit_pairs"]["q1-2"]
        # The wizard "coupler off" knob reached the build (was silently dropped).
        assert pair["coupler"]["interaction_offset"] == 0.05
        # The coupler pulse holds its OWN numeric seed — NOT a hard ref into the
        # qubit z op (which would collapse the two knobs into one).
        cops = pair["coupler"].get("operations") or {}
        cz_cop = [v for k, v in cops.items() if "cz_" in k]
        assert cz_cop, f"no CZ coupler op: {list(cops)}"
        for op in cz_cop:
            amp = op.get("amplitude")
            assert isinstance(amp, (int, float)), (
                f"coupler CZ amplitude must be an independent number, got {amp!r}")


class TestOrphanPopulateKeyWarning:
    def test_orphan_pair_key_warns(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")
        out_dir = tmp_path / "quam_state"
        # A populate.pairs key that matches no real pair must not vanish silently.
        spec = _cz_tunable_coupler_spec(populate_pairs={
            "q9-q8": {"cz_amplitude": 0.3}})
        outcome = run_generator(usable, "build", spec, out_dir, timeout=180)
        assert outcome["ok"], outcome.get("error")
        warns = " ".join(outcome["result"].get("warnings") or [])
        assert "q9-q8" in warns and "no matching qubit pair" in warns, (
            f"orphan populate.pairs key should warn: {outcome['result'].get('warnings')}")
