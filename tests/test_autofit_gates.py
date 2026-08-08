"""Autofit deterministic gates — the per-family accuracy ledger (docs/56 §6.2).

The docs/47 methodology, CI-enforced: manufacture wrong-fit runs per family ×
corruption mode and require the gate pipeline to reject them; require clean
runs to pass. The EXPECTATION MATRIX below is the honest published coverage —
``fail`` = hard reject, ``not_pass`` = fail-or-suspect (never accepted as-is),
``pass_allowed`` = a DOCUMENTED v1 blind spot (deliberately uncovered without a
node-faithful re-fit / history trend / LLM — see docs/56 §2c). Tightening a
cell is progress; silently downgrading one is a regression this file blocks.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core.autofit import families, gates, synth


def _mk_chip():
    return synth.make_sim_chip(("qA1", "qA2"), ("qA2-qA1",), seed=7)


def _evaluate(sr, chip, run=None):
    """Gate a SynthRun the way the engine will: current = post-run sim state,
    pre-update = patches-first old values."""
    fam = families.family_for(sr.node_name)
    assert fam is not None, f"family_for missed {sr.node_name}"
    patched_old = {synth.patch_path_to_dotted(p["path"]): p["old"]
                   for p in sr.patches}

    def current_value_of(path):
        return chip.get(path)

    def pre_update_value_of(path):
        if path in patched_old:
            return patched_old[path]
        return chip.get(path)

    run_obj = run if run is not None else {
        "fit_results": sr.fit_results,
        "outcomes": {t: "successful" for t in sr.targets},
        "parameters": {"operation": "cz_unipolar"},
        "folder_path": sr.folder,
    }
    return gates.evaluate_run(run_obj, fam, sr.targets,
                              current_value_of=current_value_of,
                              pre_update_value_of=pre_update_value_of)


# --------------------------------------------------------------------------
# THE LEDGER — family × corruption expectations
# --------------------------------------------------------------------------
# fail          gate pipeline must HARD-fail the target
# not_pass      fail or suspect (never verdict == "pass")
# pass_allowed  documented v1 blind spot (value plausible; needs refit/LLM/history)
# n/a           the synth generator has no such corruption branch for the family

LEDGER = {
    "03_resonator_spectroscopy": {
        "wrong_peak": "fail", "no_signal": "fail", "noisy": "not_pass",
        "out_of_band": "fail", "drift": "fail",
    },
    "05_resonator_spectroscopy_vs_power": {
        "wrong_peak": "pass_allowed",   # +3 MHz inside the jump limit — vision's
        "no_signal": "not_pass",        # domain (docs/47 A2). Empty window is
        "noisy": "pass_allowed",        # caught by the node's own power split
        "out_of_band": "fail",          # being absent (corpus: 7/7 rejects)
        "drift": "not_pass",
    },
    "06_resonator_spectroscopy_vs_flux": {
        "wrong_peak": "pass_allowed",   # a vertex read off a noise ridge stays
        # DELIBERATE WIDENING (docs/78 §27, never silent). This cell was
        # `fail` under the SHARED spectral floor of 50 — which the corpus
        # proved rejects 17 of 160 accepted targets in this family, because
        # span mode reduces a 2-D map to one row and an arc's power is spread
        # across all of them (accepted runs bottom out at a ratio of 6). The
        # floor is now 4.5 and no longer catches a manufactured empty window.
        # What still does: ridge_amp_snr / coverage / r2, which ARE this
        # family's presence check, plus the swept-window claim gate (§26).
        "no_signal": "pass_allowed",
        "noisy": "not_pass",            # ridge_amp_snr / coverage / r2 collapse
        "out_of_band": "fail",
        "drift": "pass_allowed",        # needs G5 history (pinned separately)
    },
    "07_resonator_spectroscopy_vs_coupler_flux": {
        "wrong_peak": "pass_allowed",
        "no_signal": "fail",
        "noisy": "pass_allowed",        # the corpus has NO rejected side for
        "out_of_band": "fail",          # this family — no metric to calibrate
        "drift": "pass_allowed",
    },
    "08_qubit_spectroscopy": {
        "wrong_peak": "fail", "no_signal": "fail",
        # DELIBERATE WIDENING (docs/78 §15, never silent): this cell was
        # `not_pass` under `r2 >= 0.75`, which the corpus proved rejects 12/34
        # fits the node itself ACCEPTED (real accepted r² runs down to 0.452).
        # The synthetic noisy claim lands within fwhm/6 of truth — a GOOD value
        # with an ugly fit — and its peak SNR clears the corpus-derived floor of
        # 5.0. Rejecting it would be a production false-reject, not a catch.
        "noisy": "pass_allowed",
        "out_of_band": "fail", "drift": "fail",
    },
    "08b_qubit_spectroscopy_vs_power": {
        "wrong_peak": "pass_allowed",   # the real-archive #575 class: a
        "no_signal": "pass_allowed",    # self-consistent noise fit. THE case
        "noisy": "pass_allowed",        # that mandates the vision round —
        "out_of_band": "fail",          # deterministic gates provably can't.
        "drift": "pass_allowed",
    },
    "09_qubit_spectroscopy_vs_flux": {
        "wrong_peak": "pass_allowed",   # corpus: 0/17 node-rejects were caught
        # DELIBERATE WIDENING (docs/78 §27, never silent) and the most costly
        # one in this ledger. The shared spectral floor of 50 rejected 122 of
        # 185 accepted targets here — two thirds of the good work, including a
        # panel carrying an unmistakable bright parabolic arc that the node's
        # own fit follows (ratio 13). Accepted runs bottom out at 4, so the
        # floor is 3.0 and a manufactured empty window now passes.
        #
        # BE CLEAR ABOUT WHAT THIS COSTS: this family declares NO metric gates,
        # so the spectral check was its only deterministic presence guard and
        # it no longer is. What remains is the plausibility band, G5 history,
        # and the vision round — which makes the judge load-bearing here in a
        # way it is not for 06 (whose ridge metrics do the job). Closing it
        # properly needs a presence discriminator this family's fit output does
        # not currently report; that is a docs/78 §22.4 item, not a number.
        "no_signal": "pass_allowed",
        # the same widening reaches `noisy`: G3 was the only gate refusing it
        # (G1/G2/G4 all read ok), so a lower floor lets it through too
        "noisy": "pass_allowed",
        "out_of_band": "fail",
        "drift": "not_pass",            # 600 MHz > the 500 MHz jump limit
    },
    "10_qubit_spectroscopy_vs_coupler_flux": {
        "wrong_peak": "pass_allowed",
        "no_signal": "fail",            # num_crossings == 0 IS the node's own
        "noisy": "not_pass",            # verdict (corpus: 14/14 rejects caught)
        "out_of_band": "fail",
        "drift": "n/a",
    },
    "11_power_rabi": {
        # TIGHTENED + RE-BASED (docs/78 §15). The old `fail` came from a hard
        # prefactor band [0.5, 2.0] the corpus proved false-rejecting (4/55
        # node-accepted fits sat outside it during bring-up). Detection moved to
        # the node's own `multipulse_fit_quality`, which flags rather than hard-
        # fails — so a locked harmonic is now a SUSPECT, and `out_of_band` means
        # an amplitude the port cannot play, not merely a large prefactor.
        "wrong_peak": "not_pass",
        "no_signal": "fail",         # span check: flat trace
        "noisy": "not_pass",         # was pass_allowed — the quality metric
        "out_of_band": "fail",       # now exists and lands on the reject side
        "drift": "n/a",
    },
    "12_ramsey": {
        "wrong_peak": "n/a",
        "no_signal": "fail",
        "noisy": "not_pass",         # honest 40% error bar trips the ratio gate
        "out_of_band": "fail",
        "drift": "pass_allowed",     # 250 kHz offset drift needs history/LLM
    },
    "25_T1": {
        "wrong_peak": "n/a",
        "no_signal": "fail",
        "noisy": "not_pass",
        "out_of_band": "fail",
        "drift": "not_pass",         # ×3 jump vs pre-run state (rel-jump gate)
    },
    "26_echo": {
        "wrong_peak": "n/a",
        "no_signal": "fail",
        "noisy": "not_pass",
        "out_of_band": "fail",
        "drift": "not_pass",
    },
    "15a_readout_frequency_optimization": {
        "wrong_peak": "fail",
        "no_signal": "fail",
        "noisy": "pass_allowed",     # value ≈ truth; no r2 in this fit shape
        "out_of_band": "fail",
        "drift": "fail",
    },
    "16_iq_blobs": {
        "wrong_peak": "n/a",
        "no_signal": "not_pass",     # unseparable blobs ⇒ fidelity floor
        "noisy": "pass_allowed",
        "out_of_band": "fail",
        "drift": "pass_allowed",     # a wrong angle is invisible w/o a re-fit
    },
    "31_chevron_11_02": {
        "wrong_peak": "not_pass",    # cz_len ↔ 1/(2J) internal inconsistency
        "no_signal": "fail",
        "noisy": "pass_allowed",
        "out_of_band": "fail",
        "drift": "pass_allowed",     # small amp drift within plausibility
    },
    "32_cz_conditional_phase": {
        "wrong_peak": "pass_allowed",  # 6% amp error needs error-amp refit/LLM
        "no_signal": "fail",
        "noisy": "pass_allowed",
        "out_of_band": "fail",
        "drift": "pass_allowed",
    },
}


def _targets_for(node_name):
    _, kind = synth.GENERATORS[node_name]
    return (["qA2-qA1"] if kind == "qubit_pairs" else ["qA1"]), kind


class TestFamilyMatching:
    @pytest.mark.parametrize("node_name", list(synth.GENERATORS))
    def test_every_synth_node_resolves_a_family(self, node_name):
        assert families.family_for(node_name) is not None

    def test_graph_prefixed_and_alias_names_resolve(self):
        assert families.family_for("1Q_08_qubit_spectroscopy_new").key == \
            "qubit_spectroscopy"
        assert families.family_for("2Q_19_chevron_1102").key == "chevron_11_02"
        assert families.family_for(
            "2Q_20b_cz_conditional_phase_error_amp").key == "cz_conditional_phase"


class TestCleanRunsPass:
    @pytest.mark.parametrize("node_name", list(LEDGER))
    @pytest.mark.parametrize("seed", [11, 12, 13])
    def test_clean_run_passes(self, tmp_path, node_name, seed):
        chip = _mk_chip()
        targets, _ = _targets_for(node_name)
        sr = synth.synth_run(node_name, chip, targets, tmp_path, 700 + seed,
                             seed=seed)
        verdicts = _evaluate(sr, chip)
        for t, v in verdicts.items():
            assert v.verdict == "pass", (node_name, t, v.as_dict())


class TestCorruptionLedger:
    @pytest.mark.parametrize("node_name,mode,expected", [
        (n, m, e) for n, row in LEDGER.items() for m, e in row.items()
        if e != "n/a"
    ])
    def test_ledger_cell(self, tmp_path, node_name, mode, expected):
        chip = _mk_chip()
        targets, _ = _targets_for(node_name)
        sr = synth.synth_run(node_name, chip, targets, tmp_path, 800,
                             seed=21, corrupt=mode)
        verdicts = _evaluate(sr, chip)
        v = verdicts[targets[0]]
        if expected == "fail":
            assert v.verdict == "fail", (node_name, mode, v.as_dict())
        elif expected == "not_pass":
            assert v.verdict in ("fail", "suspect"), (node_name, mode, v.as_dict())
        elif expected == "pass_allowed":
            pass  # documented blind spot — no assertion on the verdict
        else:  # pragma: no cover
            raise AssertionError(expected)

    def test_node_failed_splits_by_raw_data_presence(self, tmp_path):
        """v2 (docs/56, LOOP_STUDY #194 class): a node-declared failure is no
        longer opaque — clean raw with a visible peak reclassifies to
        feature_present_fit_failed (retryable: refine the step), a suppressed
        window to no_signal (retryable: the widen/drive/seed ladder), and only
        unavailable data stays node_failed (defer)."""
        chip = _mk_chip()
        # (a) feature clearly present in the raw data, fit failed
        sr = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"], tmp_path,
                             900, seed=31)
        run = {"fit_results": {"qA1": dict(sr.fit_results["qA1"],
                                           success=False)},
               "outcomes": {"qA1": "failed"}, "parameters": {},
               "folder_path": sr.folder}
        v = _evaluate(sr, chip, run=run)["qA1"]
        assert v.verdict == "fail"
        assert v.failure_mode == "feature_present_fit_failed"
        assert v.feature_present is True
        # (b) window genuinely empty (no_signal corruption suppresses the
        # feature), fit failed → the no_feature ladder
        sr2 = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"],
                              tmp_path / "b", 902, seed=31,
                              corrupt="no_signal")
        run2 = {"fit_results": {"qA1": dict(sr2.fit_results["qA1"],
                                            success=False)},
                "outcomes": {"qA1": "failed"}, "parameters": {},
                "folder_path": sr2.folder}
        v2 = _evaluate(sr2, chip, run=run2)["qA1"]
        assert v2.verdict == "fail"
        assert v2.failure_mode == "no_signal"
        assert v2.feature_present is False
        # (c) raw data unavailable → stays the opaque node_failed (defer)
        sr3 = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"],
                              tmp_path / "c", 903, seed=31)
        (sr3.folder / "ds_raw.h5").unlink()
        run3 = {"fit_results": {"qA1": dict(sr3.fit_results["qA1"],
                                            success=False)},
                "outcomes": {"qA1": "failed"}, "parameters": {},
                "folder_path": sr3.folder}
        v3 = _evaluate(sr3, chip, run=run3)["qA1"]
        assert v3.verdict == "fail" and v3.failure_mode == "node_failed"

    def test_missing_ds_raw_is_never_silently_accepted(self, tmp_path):
        chip = _mk_chip()
        sr = synth.synth_run("08_qubit_spectroscopy", chip, ["qA1"], tmp_path,
                             901, seed=32)
        (sr.folder / "ds_raw.h5").unlink()
        v = _evaluate(sr, chip)["qA1"]
        assert v.verdict == "suspect"
        assert v.failure_mode == "unverifiable"


class TestUpdateResolution:
    def test_ramsey_subtract_and_chevron_ceil4(self, tmp_path):
        chip = _mk_chip()
        pre_f01 = chip.get("qubits.qA1.f_01")
        fam = families.family_for("12_ramsey")
        rows = families.resolve_updates(
            fam, "qA1", {"freq_offset": 2.5e5, "decay": 2e-5}, {},
            lambda p: {"qubits.qA1.f_01": pre_f01,
                       "qubits.qA1.xy.RF_frequency": pre_f01,
                       "qubits.qA1.T2ramsey": 1e-5}[p])
        by_path = {r["path"]: r for r in rows}
        assert by_path["qubits.qA1.f_01"]["value"] == pre_f01 - 2.5e5
        assert by_path["qubits.qA1.T2ramsey"]["value"] == 2e-5

        fam = families.family_for("31_chevron_11_02")
        rows = families.resolve_updates(
            fam, "qA2-qA1", {"cz_amp": 0.21, "cz_len": 46.3}, {},
            lambda p: 0.2)
        by_label = {r["label"]: r for r in rows}
        assert by_label["CZ length (ceil 4 ns)"]["value"] == 48

    def test_operation_placeholder_skips_when_missing(self):
        fam = families.family_for("32_cz_conditional_phase")
        rows = families.resolve_updates(fam, "qA2-qA1",
                                        {"optimal_amplitude": 0.2}, {},
                                        lambda p: 0.2)
        assert rows == []          # no run.parameters.operation ⇒ never guess
        rows = families.resolve_updates(fam, "qA2-qA1",
                                        {"optimal_amplitude": 0.2},
                                        {"operation": "cz_unipolar"},
                                        lambda p: 0.2)
        assert rows and "cz_unipolar" in rows[0]["path"]

    def test_pointer_current_blocks_subtract(self):
        fam = families.family_for("12_ramsey")
        rows = families.resolve_updates(
            fam, "qA1", {"freq_offset": 1e5}, {},
            lambda p: "#/qubits/qA1/f_01")   # pointer string current value
        assert all(r["op"] != "subtract_from_current" for r in rows)


def _resolve_template(template: str, reference: str) -> str:
    """Fill ``{operation}`` in an autofit path from the SAME segment of the
    fit-target path. Returns the template unchanged when the shapes differ, so
    a genuine path divergence still fails the assertion."""
    if "{operation}" not in template:
        return template
    tparts, rparts = template.split("."), reference.split(".")
    if len(tparts) != len(rparts):
        return template
    return ".".join(r if t == "{operation}" else t
                    for t, r in zip(tparts, rparts))


class TestFitTargetsParity:
    def test_shared_paths_agree_with_fit_target_map(self):
        """Where FIT_TARGET_MAP covers a (family, fit_key), the autofit registry
        must write the SAME path — fit_targets stays the single source of truth
        for the UI, and the two must never drift (docs/56 §4)."""
        from quam_state_manager.core.fit_targets import FIT_TARGET_MAP
        pairs = {
            ("1Q_08_qubit_spectroscopy", "frequency", "qubit_spectroscopy"),
            ("1Q_11_power_rabi", "opt_amp", "power_rabi"),
            ("1Q_15a_readout_frequency_optimization", "optimal_frequency",
             "readout_frequency_optimization"),
            ("2Q_20b_cz_conditional_phase_error_amp", "optimal_amplitude",
             "cz_conditional_phase"),
        }
        for ft_prefix, fit_key, fam_key in pairs:
            ft_path = FIT_TARGET_MAP[ft_prefix][fit_key]["path"]
            fam = families.FAMILIES[fam_key]
            # `{operation}` is run-derived (docs/78 D-14): FIT_TARGET_MAP names
            # the canonical operation literally, autofit fills it from the run's
            # own parameters. The invariant is that they agree once resolved —
            # comparing the raw templates would forbid the run-derived form.
            fam_paths = [_resolve_template(u.path, ft_path)
                         for u in fam.updates if u.fit_key == fit_key]
            assert ft_path in fam_paths, (ft_prefix, fit_key, fam_paths)

    def test_iq_blobs_is_verify_only(self):
        """The fitted iw_angle's sign convention is node-version-dependent
        (assign vs subtract-delta) — autofit must NEVER auto-write it
        (design-review physics #9). Gate-only family."""
        assert families.FAMILIES["iq_blobs"].updates == []
