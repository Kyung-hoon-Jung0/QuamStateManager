"""Shared parameter matrix for the waveform golden tests.

Single source of truth consumed by BOTH sides of the parity check:

- ``generator/run_waveform_golden.py`` (run in the user's ``LabC`` QM-stack
  env) instantiates the real quam classes with these params and dumps
  ``calculate_waveform()`` output to ``tests/golden/waveform_golden.json``;
- ``tests/test_waveform_golden.py`` (run in ``qm_mng``) synthesizes the
  same cases with :mod:`quam_state_manager.core.waveform_synth` and
  compares against the committed golden file.

Regenerate the golden file (from WSL):

    <qm-env>/python \
        quam_state_manager/generator/run_waveform_golden.py \
        --out 'D:\\work\\state-manager\\tests\\golden'

Each case: ``{"id": str, "key": <catalog key>, "params": {...}}``. Params
are exactly the quam constructor kwargs (length included for explicit-length
classes; omitted for inferred-length classes — the dump script reads the
``inferred_length`` runtime property instead). Cases whose construction
*should raise* carry ``"raises": true`` and both sides assert the rejection.
"""

from __future__ import annotations

import math

PI = math.pi


def _c(case_id, key, raises=False, **params):
    return {"id": case_id, "key": key, "params": params, "raises": raises}


CASES = [
    # ---- SquarePulse (constant) ----
    _c("square_basic", "SquarePulse", length=100, amplitude=0.1),
    _c("square_axis0", "SquarePulse", length=100, amplitude=0.1, axis_angle=0.0),
    _c("square_axis", "SquarePulse", length=60, amplitude=-0.25, axis_angle=PI / 2),
    _c("square_len1", "SquarePulse", length=1, amplitude=0.5),

    # ---- SquareReadoutPulse (constant, readout) ----
    _c("sq_readout_basic", "SquareReadoutPulse", length=1000, amplitude=0.01),
    _c("sq_readout_axis", "SquareReadoutPulse", length=512, amplitude=0.042,
       axis_angle=1.234),

    # ---- GaussianPulse ----
    _c("gauss_basic", "GaussianPulse", length=40, amplitude=0.1, sigma=8.0),
    _c("gauss_unsub", "GaussianPulse", length=40, amplitude=0.1, sigma=8.0,
       subtracted=False),
    _c("gauss_axis", "GaussianPulse", length=41, amplitude=-0.2, sigma=10.0,
       axis_angle=0.7),
    _c("gauss_len2", "GaussianPulse", length=2, amplitude=0.1, sigma=1.0),
    _c("gauss_wide_sigma", "GaussianPulse", length=20, amplitude=0.3, sigma=100.0),

    # ---- DragGaussianPulse (IQ) ----
    _c("draggauss_basic", "DragGaussianPulse", length=40, axis_angle=0.0,
       amplitude=0.1, sigma=8.0, alpha=-0.5, anharmonicity=-220e6),
    _c("draggauss_axis", "DragGaussianPulse", length=40, axis_angle=PI / 2,
       amplitude=0.1, sigma=8.0, alpha=-0.5, anharmonicity=-220e6),
    _c("draggauss_detuned", "DragGaussianPulse", length=48, axis_angle=1.1,
       amplitude=0.319, sigma=9.6, alpha=-0.34, anharmonicity=-200e6,
       detuning=3e6),
    _c("draggauss_unsub", "DragGaussianPulse", length=40, axis_angle=0.0,
       amplitude=0.1, sigma=8.0, alpha=0.0, anharmonicity=-220e6,
       subtracted=False),
    _c("draggauss_alpha0_det", "DragGaussianPulse", length=32, axis_angle=0.0,
       amplitude=0.2, sigma=6.0, alpha=0.0, anharmonicity=-180e6, detuning=5e6),
    _c("draggauss_anh_eq_det", "DragGaussianPulse", length=40, axis_angle=0.0,
       amplitude=0.1, sigma=8.0, alpha=-0.5, anharmonicity=1e6, detuning=1e6,
       raises=True),

    # ---- DragCosinePulse (IQ) ----
    _c("dragcos_basic", "DragCosinePulse", length=48, axis_angle=0.0,
       amplitude=0.319, alpha=-0.34, anharmonicity=-200e6),
    _c("dragcos_axis", "DragCosinePulse", length=48, axis_angle=PI / 2,
       amplitude=0.319, alpha=-0.34, anharmonicity=-200e6),
    _c("dragcos_detuned", "DragCosinePulse", length=40, axis_angle=0.5,
       amplitude=0.16, alpha=-0.2, anharmonicity=-220e6, detuning=2e6),
    _c("dragcos_odd_len", "DragCosinePulse", length=41, axis_angle=0.0,
       amplitude=0.1, alpha=0.0, anharmonicity=-220e6),
    _c("dragcos_anh_eq_det", "DragCosinePulse", length=40, axis_angle=0.0,
       amplitude=0.1, alpha=-0.3, anharmonicity=0.0, raises=True),

    # ---- FlatTop family ----
    _c("ftgauss_basic", "FlatTopGaussianPulse", length=120, amplitude=0.05,
       flat_length=100),
    _c("ftgauss_axis", "FlatTopGaussianPulse", length=120, amplitude=0.05,
       flat_length=100, axis_angle=PI / 4),
    _c("ftgauss_rf0", "FlatTopGaussianPulse", length=100, amplitude=0.05,
       flat_length=100),
    _c("ftgauss_odd", "FlatTopGaussianPulse", length=121, amplitude=0.05,
       flat_length=100, raises=True),
    _c("ftcos_basic", "FlatTopCosinePulse", length=120, amplitude=0.05,
       flat_length=100),
    _c("ftcos_flat0", "FlatTopCosinePulse", length=40, amplitude=0.05,
       flat_length=0),
    _c("fttanh_basic", "FlatTopTanhPulse", length=120, amplitude=0.05,
       flat_length=100),
    _c("fttanh_flat0", "FlatTopTanhPulse", length=40, amplitude=-0.07,
       flat_length=0),
    _c("ftblack_basic", "FlatTopBlackmanPulse", length=120, amplitude=0.05,
       flat_length=100),
    _c("ftblack_axis", "FlatTopBlackmanPulse", length=60, amplitude=0.1,
       flat_length=20, axis_angle=2.0),

    # ---- BlackmanIntegralPulse ----
    _c("blackint_basic", "BlackmanIntegralPulse", length=100, v_start=0.0,
       v_end=0.1),
    _c("blackint_down", "BlackmanIntegralPulse", length=64, v_start=0.2,
       v_end=-0.1),
    _c("blackint_axis", "BlackmanIntegralPulse", length=32, v_start=0.0,
       v_end=0.05, axis_angle=0.3),

    # ---- ErfSquarePulse (inferred length) ----
    _c("erf_basic", "ErfSquarePulse", amplitude=0.05, flat_length=100,
       risetime_samples=16),
    _c("erf_padded", "ErfSquarePulse", amplitude=0.05, flat_length=100,
       risetime_samples=16, post_zero_padding_length=10),
    _c("erf_neg_pol", "ErfSquarePulse", amplitude=0.05, flat_length=50,
       risetime_samples=8, positive_polarity=False),
    _c("erf_phase_det", "ErfSquarePulse", amplitude=0.05, flat_length=40,
       risetime_samples=8, phase=0.25, detuning=10e6),
    _c("erf_rise1", "ErfSquarePulse", amplitude=0.1, flat_length=10,
       risetime_samples=1),
    _c("erf_rise0", "ErfSquarePulse", amplitude=0.1, flat_length=10,
       risetime_samples=0, raises=True),

    # ---- SNZPulse (inferred length) ----
    _c("snz_basic", "SNZPulse", amplitude=0.05, flat_length=20),
    _c("snz_tphi_frac", "SNZPulse", amplitude=0.05, flat_length=20,
       t_phi_eff=3.5),
    _c("snz_tphi_even", "SNZPulse", amplitude=0.05, flat_length=20,
       t_phi_eff=2.0, padding=3),
    _c("snz_tphi1", "SNZPulse", amplitude=-0.08, flat_length=12, t_phi_eff=1.0),
    _c("snz_axis", "SNZPulse", amplitude=0.05, flat_length=20, t_phi_eff=0.0,
       axis_angle=PI / 2),
    _c("snz_odd_flat", "SNZPulse", amplitude=0.05, flat_length=21, raises=True),

    # ---- GaussianFiltered (inferred length) ----
    _c("gfsq_basic", "GaussianFilteredSquarePulse", pulse_length=100,
       amplitude=0.05, gaussian_filter_frequency_mhz=50.0),
    _c("gfsq_padded", "GaussianFilteredSquarePulse", pulse_length=100,
       amplitude=0.05, gaussian_filter_frequency_mhz=20.0,
       post_zero_padding_length=60),
    _c("gfsq_neg_amp", "GaussianFilteredSquarePulse", pulse_length=48,
       amplitude=-0.1, gaussian_filter_frequency_mhz=80.0,
       post_zero_padding_length=16),
    _c("gfbip_basic", "GaussianFilteredSymmetricBipolarPulse", pulse_length=100,
       amplitude=0.05, gaussian_filter_frequency_mhz=50.0,
       post_zero_padding_length=20),
    _c("gfbip_odd", "GaussianFilteredSymmetricBipolarPulse", pulse_length=101,
       amplitude=0.05, gaussian_filter_frequency_mhz=50.0, raises=True),

    # ---- WaveformPulse (derived length) ----
    _c("wfp_real", "WaveformPulse", waveform_I=[0.0, 0.1, 0.2, 0.1, 0.0]),
    _c("wfp_iq", "WaveformPulse", waveform_I=[0.0, 0.1, 0.2, 0.1],
       waveform_Q=[0.0, -0.05, 0.0, 0.05]),

    # ---- deprecated classes (present in real chip states) ----
    _c("ftgauss_dep_basic", "_FlatTopGaussianPulse", amplitude=0.05,
       flat_length=100, smoothing_length=20),
    _c("ftgauss_dep_pad", "_FlatTopGaussianPulse", amplitude=0.05,
       flat_length=100, smoothing_length=20, post_zero_padding_length=8,
       sigma=3.0),
    _c("cosbip_basic", "_CosineBipolarPulse", amplitude=0.05, flat_length=100,
       smoothing_length=20),
    _c("cosbip_sm0", "_CosineBipolarPulse", amplitude=0.05, flat_length=100,
       smoothing_length=0),
    _c("cosbip_sm1", "_CosineBipolarPulse", amplitude=0.05, flat_length=100,
       smoothing_length=1),
    _c("cosbip_sm2", "_CosineBipolarPulse", amplitude=0.05, flat_length=100,
       smoothing_length=2),
    _c("cosbip_sm3", "_CosineBipolarPulse", amplitude=0.05, flat_length=100,
       smoothing_length=3),
    _c("cosbip_sm5", "_CosineBipolarPulse", amplitude=0.05, flat_length=96,
       smoothing_length=5, post_zero_padding_length=3),
    _c("cosbip_odd_flat", "_CosineBipolarPulse", amplitude=0.05, flat_length=99,
       smoothing_length=4, raises=True),
]


def case_by_id(case_id: str) -> dict:
    for case in CASES:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)
