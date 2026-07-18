"""Synthetic QUAlibrate run generator — the autofit dev/test backbone (docs/56 §2b, §6).

Emits run folders that are **indistinguishable from real QUAlibrate output** to
every SM reader (``dataset._parse_run_folder``, ``experiment_data``, ``ndview``,
``contracts`` patches-first, ``fit_targets``):

    <root>/YYYY-MM-DD/#<id>_<node_name>_<HHMMSS>/
        node.json     (metadata / data.parameters.model / data.outcomes / patches)
        data.json     (fit_results / figures / ds refs)
        ds_raw.h5     (h5netcdf-style: 1-D dimension scales + DIMENSION_LIST vars)
        ds_fit.h5
        figures.*.png (best-effort; only when matplotlib is importable)
        quam_state/   state.json + wiring.json (POST-update snapshot when patches exist)

Every field layout here is pinned against real runs captured 2026-07-17
(KRISS/KAVR/IQCC archives — see docs/56 §6.1): e.g. qubit-spec ds_raw carries
``{I,Q,IQ_abs,phase,detuning,full_freq,qubit}`` with detuning as the swept scale.

The generator owns a **SimChip** (ground truth + a deliberately-imperfect
current state) and a crude per-family "sim node": raw data is synthesized from
the truth through the sweep window the *parameters* request, and the claimed
``fit_results`` behave like a node's fitter — including being wrong on demand
via ``corrupt`` modes (docs/47's manufactured-wrong-fit methodology):

    wrong_peak   raw carries the true feature + a sidelobe; the CLAIM locks onto
                 the sidelobe with success=True  (G3 must reject)
    no_signal    the true feature lies OUTSIDE the requested window; the claim
                 invents a value off noise with a lying r²  (G2/G3 must reject)
    noisy        honest low-SNR data + honest low r²  (G2 suspects; retry with
                 more shots converges — the generator scales noise by 1/√shots)
    out_of_band  claim is physically absurd (G4 must reject)
    drift        claim sits several linewidths off the true feature (G3/G4)

Pure numpy + h5py (no xarray / matplotlib dependency; figures are best-effort).
Deterministic under ``seed``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

try:  # figures are optional — the numeric bundle is the auditor's fallback
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
except Exception:  # pragma: no cover - matplotlib genuinely absent
    _plt = None


# ---------------------------------------------------------------------------
# Ground truth + sim chip
# ---------------------------------------------------------------------------

@dataclass
class QubitTruth:
    f_res: float          # true resonator dip (Hz)
    f_01: float           # true qubit transition (Hz)
    x180_amp: float       # true pi-pulse amplitude (V)
    t1: float             # seconds
    t2_star: float        # seconds
    t2_echo: float        # seconds
    ro_opt_freq: float    # optimal readout frequency (Hz)
    iw_angle: float       # radians
    res_fwhm: float = 2e6
    q_fwhm: float = 4e6


@dataclass
class PairTruth:
    j_coupling: float     # Hz
    cz_amp: float         # V (flux-pulse amplitude at the 11<->02 resonance)
    cz_len: float         # ns (true gate length; nodes ceil to 4 ns)
    phase_amp: float      # V (conditional-phase optimal amplitude; near cz_amp)


@dataclass
class SimChip:
    """Ground truth + the chip's CURRENT (imperfect) state values.

    ``state`` is the live-file dict (loadable by QuamStore); calibration moves
    its values toward the truth. ``build_state_json`` seeds every path the sim
    nodes patch, so ``patches[].old`` always resolves.
    """

    qubits: dict[str, QubitTruth]
    pairs: dict[str, PairTruth]
    state: dict = field(default_factory=dict)
    wiring: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.state:
            self.state = self.build_state_json()
        if not self.wiring:
            self.wiring = {"network": {"host": "127.0.0.1",
                                       "cluster_name": "sim_cluster"}}

    def build_state_json(self) -> dict:
        qubits = {}
        for name, t in self.qubits.items():
            # start detuned/imperfect: calibration must actually change values
            qubits[name] = {
                "id": name,
                "f_01": t.f_01 + 3e6,
                "T1": t.t1 * 0.7,
                "T2ramsey": t.t2_star * 0.7,
                "T2echo": t.t2_echo * 0.7,
                "xy": {
                    "RF_frequency": t.f_01 + 3e6,
                    "operations": {
                        "x180": {"amplitude": t.x180_amp * 1.15, "alpha": 0.0,
                                 "length": 48},
                        "saturation": {"amplitude": 0.05, "length": 10000},
                    },
                },
                "resonator": {
                    "f_01": t.f_res + 1e6,
                    "RF_frequency": t.f_res + 1e6,
                    "operations": {
                        "readout": {"amplitude": 0.08, "length": 1500,
                                    "integration_weights_angle": 0.0,
                                    "threshold": 0.0},
                    },
                },
            }
        pairs = {}
        for pname, pt in self.pairs.items():
            control, target = _split_pair(pname)
            pairs[pname] = {
                "id": pname,
                "qubit_control": f"#/qubits/{control}",
                "qubit_target": f"#/qubits/{target}",
                "macros": {
                    "cz_unipolar": {
                        "flux_pulse_qubit": {"amplitude": pt.cz_amp * 1.1,
                                             "length": 60},
                        "phase_shift_control": 0.0,
                        "phase_shift_target": 0.0,
                    },
                },
            }
        return {
            "qubits": qubits,
            "qubit_pairs": pairs,
            "active_qubit_names": list(self.qubits),
            "active_qubit_pair_names": list(self.pairs),
        }

    # -- state access helpers (slash-free dotted paths, no pointers here) ---
    def get(self, dotted: str):
        node: Any = self.state
        for part in dotted.split("."):
            node = node[part]
        return node

    def set(self, dotted: str, value) -> None:
        parts = dotted.split(".")
        node: Any = self.state
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value

    def apply_patches(self, patches: list[dict]) -> None:
        """Apply node-style patches (``/quam/...`` JSON-pointer paths)."""
        for p in patches or []:
            dotted = patch_path_to_dotted(p["path"])
            self.set(dotted, p["value"])


def _split_pair(pair_name: str) -> tuple[str, str]:
    """'qA2-qA1' -> ('qA2','qA1'); tolerate 'qA2-A1' by prefixing."""
    a, _, b = pair_name.partition("-")
    if b and not b.startswith(a[0]):
        b = a[0] + b
    return a, b or a


def patch_path_to_dotted(path: str) -> str:
    """'/quam/qubits/qA1/f_01' -> 'qubits.qA1.f_01' (the modifier dialect)."""
    parts = [p for p in str(path).split("/") if p]
    if parts and parts[0] == "quam":
        parts = parts[1:]
    return ".".join(parts)


def make_sim_chip(qubit_names: tuple[str, ...] = ("qA1", "qA2"),
                  pair_names: tuple[str, ...] = ("qA2-qA1",),
                  seed: int = 1234) -> SimChip:
    rng = np.random.default_rng(seed)
    qubits = {}
    for i, name in enumerate(qubit_names):
        qubits[name] = QubitTruth(
            f_res=7.2e9 + i * 0.11e9 + float(rng.uniform(-2e6, 2e6)),
            f_01=5.0e9 + i * 0.13e9 + float(rng.uniform(-3e6, 3e6)),
            x180_amp=float(rng.uniform(0.22, 0.42)),
            t1=float(rng.uniform(20e-6, 80e-6)),
            t2_star=float(rng.uniform(8e-6, 30e-6)),
            t2_echo=float(rng.uniform(20e-6, 60e-6)),
            ro_opt_freq=7.2e9 + i * 0.11e9 + float(rng.uniform(-1.5e6, -0.5e6)),
            iw_angle=float(rng.uniform(-0.8, 0.8)),
        )
    pairs = {}
    for pname in pair_names:
        pairs[pname] = PairTruth(
            j_coupling=float(rng.uniform(2e6, 8e6)),
            cz_amp=float(rng.uniform(0.15, 0.3)),
            cz_len=float(rng.uniform(30, 70)),
            phase_amp=0.0,   # filled below (near cz_amp)
        )
        pairs[pname].phase_amp = pairs[pname].cz_amp * float(rng.uniform(0.98, 1.02))
    return SimChip(qubits=qubits, pairs=pairs)


# ---------------------------------------------------------------------------
# h5 writing (dimension scales — what ndview's DIMENSION_LIST deref expects)
# ---------------------------------------------------------------------------

def _write_h5(path: Path, coords: dict[str, np.ndarray],
              variables: dict[str, tuple[tuple[str, ...], np.ndarray]]) -> None:
    import h5py

    with h5py.File(path, "w") as f:
        for cname, arr in coords.items():
            if arr.dtype.kind in ("U", "O", "S"):
                dt = h5py.string_dtype(encoding="utf-8")
                ds = f.create_dataset(cname, data=np.asarray(arr, dtype=object),
                                      dtype=dt)
            else:
                ds = f.create_dataset(cname, data=arr)
            ds.make_scale(cname)
        for vname, (dims, arr) in variables.items():
            ds = f.create_dataset(vname, data=arr)
            for i, dim in enumerate(dims):
                ds.dims[i].attach_scale(f[dim])


# ---------------------------------------------------------------------------
# Signal helpers
# ---------------------------------------------------------------------------

def _lorentzian(x: np.ndarray, x0: float, fwhm: float) -> np.ndarray:
    hw = fwhm / 2.0
    return hw * hw / ((x - x0) ** 2 + hw * hw)


def _noise(rng: np.random.Generator, shape, scale: float,
           shots: float) -> np.ndarray:
    """Gaussian noise scaled by 1/sqrt(shots/base) so retries with more shots
    genuinely improve SNR (the adaptation loop's convergence lever)."""
    eff = scale / math.sqrt(max(float(shots), 1.0) / 400.0)
    return rng.normal(0.0, eff, shape)


# ---------------------------------------------------------------------------
# The per-family sim nodes
# ---------------------------------------------------------------------------
# Each generator returns:
#   coords: {name: 1-D array}                      (dimension scales)
#   variables: {name: (dims, array)}               (data vars)
#   fit_results: {target: {...}}                   (the CLAIM, corruption-aware)
#   patch_specs: [(dotted_path, new_value)]        (what the node "updates";
#                                                   old values resolved later)
#   figures: {figname: (x, per-target-y dict, claimed-x-marker per target)}

_SIDELOBE_FRAC = 0.45     # sidelobe height relative to the main feature
_SIDELOBE_OFFSET_FWHM = 6  # sidelobe distance from the truth, in FWHM units


def _spec_family(chip: SimChip, targets: list[str], params: dict, corrupt_for,
                 rng: np.random.Generator, *, kind: str):
    """Shared generator for resonator (dip) and qubit (peak) spectroscopy."""
    span = float(params.get("frequency_span_in_mhz", 60.0)) * 1e6
    step = float(params.get("frequency_step_in_mhz", span / 300 / 1e6)) * 1e6
    shots = float(params.get("num_shots", params.get("num_averages", 400)))
    # floor 8 (was 40): a coarse frequency_step must actually COARSEN the grid
    # or the undersampling scenario (docs/56 v2, LOOP_STUDY case C) can't exist
    n = max(int(round(span / step)), 8)
    detuning = np.linspace(-span / 2, span / 2, n).astype(np.int64 if span > 1e3 else float)

    is_dip = kind == "resonator"
    iq, full_freq, fits, patches, figs = [], [], {}, [], {}
    for q in targets:
        corrupt = corrupt_for(q)
        t = chip.qubits[q]
        truth = t.f_res if is_dip else t.f_01
        fwhm = t.res_fwhm if is_dip else t.q_fwhm
        center = chip.get(f"qubits.{q}.resonator.RF_frequency") if is_dip \
            else chip.get(f"qubits.{q}.xy.RF_frequency")
        freqs = center + detuning.astype(float)

        sig = np.zeros(n)
        in_window = freqs.min() <= truth <= freqs.max()
        if is_dip:
            amp = 1.0
        else:
            # qubit-line visibility is PHYSICAL, not constant (docs/56 v2
            # scenario c): contrast scales with the drive actually applied
            # (saturation amp × the node's amplitude factor) AND with how
            # well the READOUT is centered — a badly mis-calibrated resonator
            # entry kills qubit-spec SNR at any span, and only re-running the
            # resonator node (cross-node escalation, LOOP_STUDY case A)
            # restores it. State is read live, so a re-cal genuinely helps.
            try:
                sat = float(chip.get(
                    f"qubits.{q}.xy.operations.saturation.amplitude"))
            except (KeyError, TypeError, ValueError):
                sat = 0.05
            ampf = float(params.get("operation_amplitude_factor", 1.0))
            drive_v = min(1.0, max(0.0, (sat * ampf) / 0.05))
            try:
                res_rf = float(chip.get(f"qubits.{q}.resonator.RF_frequency"))
            except (KeyError, TypeError, ValueError):
                res_rf = t.f_res
            readout_v = 1.0 / (1.0 + ((res_rf - t.f_res) / t.res_fwhm) ** 2)
            amp = drive_v * readout_v
        if corrupt != "no_signal" and in_window:
            sig += amp * _lorentzian(freqs, truth, fwhm)
        # resonator G3 tolerance is deliberately wide (rotated-S21 channel) —
        # its sidelobe must sit beyond even that to be a meaningful wrong-peak
        side_pos = truth + (12.0 if is_dip else _SIDELOBE_OFFSET_FWHM) * fwhm
        if corrupt == "wrong_peak" and freqs.min() <= side_pos <= freqs.max():
            sig += amp * _SIDELOBE_FRAC * _lorentzian(freqs, side_pos, fwhm)
        base = 0.55 - 0.35 * sig if is_dip else 0.1 + 0.6 * sig
        noise_scale = 0.09 if corrupt == "noisy" else 0.015
        y = base + _noise(rng, n, noise_scale, shots)

        # ---- the sim node's CLAIM -------------------------------------
        # honest-miss physics (docs/56 v2): an UNCORRUPTED run whose window
        # missed the feature, whose grid undersampled the linewidth, or whose
        # visibility collapsed reports an honest FAILED fit (success=False,
        # low r2) — never the old magically-correct claim. The raw data stays
        # truthful (empty window / coarse-but-visible feature), which is
        # exactly what the presence probe + ladders key on.
        eff_step = span / max(n - 1, 1)
        honest_miss = corrupt is None and (
            not in_window or eff_step > fwhm * 0.6 or amp < 0.12)
        if honest_miss:
            claimed = float(freqs[int(np.argmax(y)) if not is_dip
                                  else int(np.argmin(y))])
            r2 = float(np.clip(rng.normal(0.2, 0.05), 0, 1))
            success = False
        elif corrupt == "wrong_peak":
            claimed, r2, success = side_pos, 0.90, True
        elif corrupt == "no_signal":
            # dishonest gate: invents a value off noise, reports a good r2
            claimed = float(freqs[int(np.argmax(y)) if not is_dip
                                  else int(np.argmin(y))])
            r2, success = 0.86, True
        elif corrupt == "noisy":
            claimed = truth + float(rng.normal(0, fwhm / 6))
            r2, success = 0.45, True
        elif corrupt == "out_of_band":
            claimed, r2, success = truth + 2.5e9, 0.91, True
        elif corrupt == "drift":
            # the resonator family's G3 tolerance is deliberately wide (rotated
            # S21 channel, docs/47) — its drift must clear even that
            claimed = truth + (12.0 if is_dip else 3.5) * fwhm
            r2, success = 0.88, True
        else:
            claimed = truth + float(rng.normal(0, fwhm / 40))
            r2, success = float(np.clip(rng.normal(0.96, 0.015), 0, 1)), True

        entry: dict[str, Any] = {"frequency": claimed, "fwhm": fwhm,
                                 "r2": r2, "success": success}
        if is_dip:
            fits[q] = entry
            patches += [(f"qubits.{q}.resonator.f_01", claimed),
                        (f"qubits.{q}.resonator.RF_frequency", claimed)]
        else:
            entry.update({
                "contrast": (0.02 if (corrupt == "no_signal" or honest_miss)
                             else float(np.clip(rng.normal(0.55, 0.05), 0, 1))),
                "iw_angle": t.iw_angle + float(rng.normal(0, 0.03)),
                "saturation_amp": 0.02, "x180_amp": t.x180_amp * 1.1,
            })
            fits[q] = entry
            patches += [(f"qubits.{q}.f_01", claimed),
                        (f"qubits.{q}.xy.RF_frequency", claimed)]
        iq.append(y)
        full_freq.append(freqs)
        figs[q] = (freqs, y, claimed)

    iqa = np.asarray(iq)
    phase = np.unwrap(np.angle(np.exp(1j * (iqa - iqa.mean()) * 4.0)), axis=-1)
    coords = {"qubit": np.asarray(targets, dtype=object), "detuning": detuning}
    variables = {
        "IQ_abs": (("qubit", "detuning"), iqa),
        "I": (("qubit", "detuning"), iqa / np.sqrt(2)),
        "Q": (("qubit", "detuning"), iqa / np.sqrt(2)),
        "phase": (("qubit", "detuning"), phase),
        "full_freq": (("qubit", "detuning"), np.asarray(full_freq)),
    }
    return coords, variables, fits, patches, {"amplitude": figs}


def _gen_resonator_spec(chip, targets, params, corrupt_for, rng):
    return _spec_family(chip, targets, params, corrupt_for, rng, kind="resonator")


def _gen_qubit_spec(chip, targets, params, corrupt_for, rng):
    return _spec_family(chip, targets, params, corrupt_for, rng, kind="qubit")


def _gen_power_rabi(chip, targets, params, corrupt_for, rng):
    n_amp = int(params.get("num_amps", 80))
    pulses = np.asarray(params.get("nb_of_pulses") or [1, 3, 5, 7, 9], dtype=np.int64)
    shots = float(params.get("num_shots", 400))
    apf = np.linspace(0.6, 1.4, n_amp)

    state, fits, patches, figs = [], {}, [], {}
    for q in targets:
        corrupt = corrupt_for(q)
        t = chip.qubits[q]
        cur_amp = chip.get(f"qubits.{q}.xy.operations.x180.amplitude")
        # rotation per pulse: theta = pi * (apf * cur_amp / true_amp)
        theta = np.pi * (apf[None, :] * cur_amp / t.x180_amp)
        p_e = np.sin(pulses[:, None] * theta / 2.0) ** 2
        y = p_e + _noise(rng, p_e.shape, 0.12 if corrupt == "noisy" else 0.03, shots)

        opt_apf_true = t.x180_amp / cur_amp
        if corrupt == "wrong_peak":       # locks a Rabi harmonic (×3 error)
            claimed_apf, success = opt_apf_true / 3.0, True
        elif corrupt == "out_of_band":
            claimed_apf, success = 3.0, True
        elif corrupt == "no_signal":
            claimed_apf, success = float(rng.uniform(0.7, 1.3)), True
            y = _noise(rng, p_e.shape, 0.05, shots) + 0.5
        elif corrupt == "noisy":
            claimed_apf, success = opt_apf_true * float(rng.normal(1, 0.03)), True
        else:
            claimed_apf, success = opt_apf_true * float(rng.normal(1, 0.004)), True
        claimed_amp = claimed_apf * cur_amp
        fits[q] = {"opt_amp_prefactor": claimed_apf, "opt_amp": claimed_amp,
                   "operation": "x180", "success": success}
        patches.append((f"qubits.{q}.xy.operations.x180.amplitude", claimed_amp))
        state.append(y)
        figs[q] = (apf, y.mean(axis=0), claimed_apf)

    arr = np.asarray(state)
    coords = {"qubit": np.asarray(targets, dtype=object),
              "nb_of_pulses": pulses, "amp_prefactor": apf}
    variables = {
        "I": (("qubit", "nb_of_pulses", "amp_prefactor"), arr),
        "IQ_abs": (("qubit", "nb_of_pulses", "amp_prefactor"), arr),
        "data_mean": (("qubit", "amp_prefactor"), arr.mean(axis=1)),
    }
    return coords, variables, fits, patches, {"amplitude": figs}


def _gen_ramsey(chip, targets, params, corrupt_for, rng):
    # dense sampling (20 ns step): a real ramsey resolves its detuned
    # oscillation — an undersampled fringe would be garbage data, not a fit
    # problem, and the span gate would (rightly) call it no_signal
    n_t = int(params.get("num_time_points", 400))
    shots = float(params.get("num_shots", 400))
    idle = np.linspace(16, 8000, n_t).astype(np.int64)   # ns
    signs = np.asarray([-1, 1], dtype=np.int64)
    detuning_mhz = float(params.get("frequency_detuning_in_mhz", 1.0))

    data, fits, patches, figs = [], {}, [], {}
    for q in targets:
        corrupt = corrupt_for(q)
        t = chip.qubits[q]
        f_cur = chip.get(f"qubits.{q}.f_01")
        true_offset = f_cur - t.f_01         # node convention: f_01 -= freq_offset
        tt = idle.astype(float) * 1e-9
        y = np.empty((n_t, 2))
        for k, s in enumerate(signs):
            f_osc = detuning_mhz * 1e6 * s + true_offset
            y[:, k] = 0.5 + 0.5 * np.cos(2 * np.pi * f_osc * tt) * np.exp(-tt / t.t2_star)
        y += _noise(rng, y.shape, 0.10 if corrupt == "noisy" else 0.02, shots)

        if corrupt == "out_of_band":
            claimed_off, decay, success = 4.2e8, t.t2_star, True
        elif corrupt == "drift":
            claimed_off, decay, success = true_offset + 2.5e5, t.t2_star, True
        elif corrupt == "no_signal":
            y = _noise(rng, y.shape, 0.05, shots) + 0.5
            claimed_off, decay, success = float(rng.normal(0, 1e5)), 1e-6, True
        elif corrupt == "noisy":
            claimed_off = true_offset + float(rng.normal(0, 3e4))
            decay, success = t.t2_star * float(rng.normal(1, 0.2)), True
        else:
            claimed_off = true_offset + float(rng.normal(0, 2e3))
            decay, success = t.t2_star * float(rng.normal(1, 0.05)), True
        # an honest fitter reports a big error bar on noisy data
        err_frac = 0.4 if corrupt == "noisy" else 0.08
        fits[q] = {"freq_offset": claimed_off, "decay": decay,
                   "decay_error": abs(decay) * err_frac, "success": success}
        patches += [(f"qubits.{q}.f_01", f_cur - claimed_off),
                    (f"qubits.{q}.xy.RF_frequency",
                     chip.get(f"qubits.{q}.xy.RF_frequency") - claimed_off),
                    (f"qubits.{q}.T2ramsey", decay)]
        data.append(y)
        figs[q] = (idle.astype(float), y[:, 1], None)

    arr = np.asarray(data)
    coords = {"qubit": np.asarray(targets, dtype=object),
              "idle_time": idle, "detuning_signs": signs}
    variables = {"I": (("qubit", "idle_time", "detuning_signs"), arr),
                 "Q": (("qubit", "idle_time", "detuning_signs"), arr * 0.5)}
    return coords, variables, fits, patches, {"amplitude": figs}


def _decay_family(chip, targets, params, corrupt_for, rng, *, which: str):
    """Shared generator for T1 (relaxation) and echo (T2echo)."""
    n_t = int(params.get("num_time_points", 200))
    shots = float(params.get("num_shots", 400))
    idle = np.linspace(16, 200000 if which == "t1" else 120000, n_t).astype(np.int64)

    data, fits, patches, figs = [], {}, [], {}
    for q in targets:
        corrupt = corrupt_for(q)
        t = chip.qubits[q]
        truth = t.t1 if which == "t1" else t.t2_echo
        tt = idle.astype(float) * 1e-9
        y = np.exp(-tt / truth)
        y += _noise(rng, y.shape, 0.10 if corrupt == "noisy" else 0.02, shots)

        if corrupt == "out_of_band":
            claimed, success = -5e-6, True
        elif corrupt == "no_signal":
            y = _noise(rng, y.shape, 0.05, shots) + 0.5
            claimed, success = 9e-4, True
        elif corrupt == "drift":
            claimed, success = truth * 3.0, True
        elif corrupt == "noisy":
            claimed, success = truth * float(rng.normal(1, 0.15)), True
        else:
            claimed, success = truth * float(rng.normal(1, 0.03)), True
        err = abs(claimed) * (0.35 if corrupt == "noisy" else 0.06)
        if which == "t1":
            fits[q] = {"t1": claimed, "t1_error": err, "success": success}
            patches.append((f"qubits.{q}.T1", claimed))
        else:
            fits[q] = {"T2_echo": claimed, "T2_echo_error": err, "success": success}
            patches.append((f"qubits.{q}.T2echo", claimed))
        data.append(y)
        figs[q] = (idle.astype(float), y, None)

    arr = np.asarray(data)
    coords = {"qubit": np.asarray(targets, dtype=object), "idle_time": idle}
    variables = {"I": (("qubit", "idle_time"), arr),
                 "Q": (("qubit", "idle_time"), arr * 0.4)}
    return coords, variables, fits, patches, {"amplitude": figs}


def _gen_t1(chip, targets, params, corrupt_for, rng):
    return _decay_family(chip, targets, params, corrupt_for, rng, which="t1")


def _gen_echo(chip, targets, params, corrupt_for, rng):
    return _decay_family(chip, targets, params, corrupt_for, rng, which="echo")


def _gen_readout_freq_opt(chip, targets, params, corrupt_for, rng):
    span = float(params.get("frequency_span_in_mhz", 20.0)) * 1e6
    n = int(params.get("num_points", 200))
    shots = float(params.get("num_shots", 400))
    detuning = np.linspace(-span / 2, span / 2, n).astype(np.int64)

    snr_rows, fits, patches, figs = [], {}, [], {}
    for q in targets:
        corrupt = corrupt_for(q)
        t = chip.qubits[q]
        center = chip.get(f"qubits.{q}.resonator.RF_frequency")
        freqs = center + detuning.astype(float)
        truth = t.ro_opt_freq
        width = 3e6
        sig = np.exp(-((freqs - truth) ** 2) / (2 * width ** 2)) * 4.0 + 0.5
        if corrupt == "no_signal":
            sig = np.full(n, 0.6)
        y = sig + _noise(rng, n, 0.5 if corrupt == "noisy" else 0.08, shots)

        if corrupt == "wrong_peak":
            claimed, success = truth + 6 * width, True
        elif corrupt == "out_of_band":
            claimed, success = truth + 1.8e9, True
        elif corrupt == "no_signal":
            claimed, success = float(freqs[int(np.argmax(y))]), True
        elif corrupt == "drift":
            claimed, success = truth + 3.5 * width, True
        else:
            claimed, success = truth + float(rng.normal(0, width / 30)), True
        fits[q] = {"optimal_frequency": claimed,
                   "best_snr": float(np.max(y)), "success": success}
        patches += [(f"qubits.{q}.resonator.f_01", claimed),
                    (f"qubits.{q}.resonator.RF_frequency", claimed)]
        snr_rows.append(y)
        figs[q] = (freqs, y, claimed)

    coords = {"qubit": np.asarray(targets, dtype=object), "detuning": detuning}
    variables = {"snr": (("qubit", "detuning"), np.asarray(snr_rows))}
    return coords, variables, fits, patches, {"amplitude": figs}


def _gen_iq_blobs(chip, targets, params, corrupt_for, rng):
    n_runs = int(params.get("num_shots", params.get("n_runs", 1500)))
    runs = np.arange(n_runs, dtype=np.int64)

    igs, qgs, ies, qes, fits, patches, figs = [], [], [], [], {}, [], {}
    for q in targets:
        corrupt = corrupt_for(q)
        t = chip.qubits[q]
        sep = 0.02 if corrupt != "no_signal" else 0.001
        sigma = 0.012 if corrupt == "noisy" else 0.006
        ang = t.iw_angle
        gx, gy = 0.0, 0.0
        ex, ey = sep * math.cos(ang), sep * math.sin(ang)
        ig = rng.normal(gx, sigma, n_runs); qg = rng.normal(gy, sigma, n_runs)
        ie = rng.normal(ex, sigma, n_runs); qe = rng.normal(ey, sigma, n_runs)

        # DELTA convention (the modern node): iw_angle is the correction the
        # node SUBTRACTS from the current state angle
        cur_angle = chip.get(f"qubits.{q}.resonator.operations.readout."
                             f"integration_weights_angle")
        true_delta = cur_angle - ang
        if corrupt == "out_of_band":
            claimed_delta, success = 9.7, True
        elif corrupt == "drift":
            claimed_delta, success = true_delta + 1.2, True
        else:
            claimed_delta, success = true_delta + float(rng.normal(0, 0.02)), True
        snr = sep / sigma
        fid = float(np.clip(50 + 50 * math.erf(snr / (2 * math.sqrt(2))), 50, 99.9))
        fits[q] = {"iw_angle": claimed_delta,
                   "ge_threshold": sep / 2 * math.cos(ang),
                   "rus_threshold": sep / 3 * math.cos(ang),
                   "readout_fidelity": fid,
                   "confusion_matrix": [[fid / 100, 1 - fid / 100],
                                        [1 - fid / 100, fid / 100]],
                   "success": True}
        patches.append((f"qubits.{q}.resonator.operations.readout."
                        f"integration_weights_angle", cur_angle - claimed_delta))
        igs.append(ig); qgs.append(qg); ies.append(ie); qes.append(qe)
        figs[q] = (ig, qg, None)

    coords = {"qubit": np.asarray(targets, dtype=object), "n_runs": runs}
    variables = {
        "Ig": (("qubit", "n_runs"), np.asarray(igs)),
        "Qg": (("qubit", "n_runs"), np.asarray(qgs)),
        "Ie": (("qubit", "n_runs"), np.asarray(ies)),
        "Qe": (("qubit", "n_runs"), np.asarray(qes)),
    }
    return coords, variables, fits, patches, {"iq": figs}


def _gen_cz_chevron(chip, targets, params, corrupt_for, rng):
    n_amp = int(params.get("num_amps", 41))
    n_time = int(params.get("num_time_points", 120))
    shots = float(params.get("num_shots", 400))
    amp = np.linspace(0.8, 1.2, n_amp)
    time = np.linspace(4, 200, n_time).astype(np.int64)   # ns

    sc, st, fits, patches, figs = [], [], {}, [], {}
    for p in targets:
        corrupt = corrupt_for(p)
        pt = chip.pairs[p]
        cur_amp = chip.get(f"qubit_pairs.{p}.macros.cz_unipolar.flux_pulse_qubit.amplitude")
        # detuning linear in amplitude around the resonance amp
        slope = 200e6   # Hz per unit prefactor
        delta = slope * (amp * cur_amp - pt.cz_amp) / max(cur_amp, 1e-9)
        j = pt.j_coupling
        rabi = j ** 2 / (j ** 2 + delta[:, None] ** 2)
        omega = np.sqrt(j ** 2 + delta[:, None] ** 2)
        swap = rabi * np.sin(np.pi * omega * time[None, :].astype(float) * 1e-9) ** 2
        y = swap + _noise(rng, swap.shape, 0.10 if corrupt == "noisy" else 0.02, shots)
        if corrupt == "no_signal":
            y = _noise(rng, swap.shape, 0.04, shots) + 0.1

        true_len = 1.0 / (2 * j) * 1e9   # half swap period at resonance, ns
        if corrupt == "wrong_peak":
            claimed_amp, claimed_len, success = pt.cz_amp * 1.12, true_len * 2, True
        elif corrupt == "out_of_band":
            claimed_amp, claimed_len, success = 2.4, 3.0, True
        elif corrupt == "drift":
            claimed_amp, claimed_len, success = pt.cz_amp * 1.05, true_len, True
        elif corrupt == "no_signal":
            claimed_amp, claimed_len, success = float(rng.uniform(0.15, 0.3)), 40.0, True
        else:
            claimed_amp = pt.cz_amp * float(rng.normal(1, 0.004))
            claimed_len, success = true_len * float(rng.normal(1, 0.02)), True
        fits[p] = {"J": j, "f0": slope * (1 - pt.cz_amp / cur_amp),
                   "cz_amp": claimed_amp, "cz_len": float(claimed_len),
                   "success": success}
        patches += [(f"qubit_pairs.{p}.macros.cz_unipolar.flux_pulse_qubit.amplitude",
                     claimed_amp),
                    (f"qubit_pairs.{p}.macros.cz_unipolar.flux_pulse_qubit.length",
                     int(math.ceil(claimed_len / 4.0) * 4))]
        sc.append(1 - y); st.append(y)
        figs[p] = (amp, y.max(axis=1), claimed_amp / max(cur_amp, 1e-9))

    coords = {"qubit_pair": np.asarray(targets, dtype=object),
              "amplitude": amp, "time": time}
    variables = {
        "state_control": (("qubit_pair", "amplitude", "time"), np.asarray(sc)),
        "state_target": (("qubit_pair", "amplitude", "time"), np.asarray(st)),
        "amp_full": (("qubit_pair", "amplitude"),
                     np.asarray([amp * chip.get(f"qubit_pairs.{p}.macros.cz_unipolar.flux_pulse_qubit.amplitude") for p in targets])),
    }
    return coords, variables, fits, patches, {"chevron": figs}


def _gen_cz_conditional_phase(chip, targets, params, corrupt_for, rng):
    n_amp = int(params.get("num_amps", 61))
    n_frame = int(params.get("num_frames", 10))
    shots = float(params.get("num_shots", 400))
    operation = str(params.get("operation", "cz_unipolar"))
    amp = np.linspace(0.95, 1.05, n_amp)
    frame = np.linspace(0.0, 0.9, n_frame)
    axes = np.asarray([0, 1], dtype=np.int64)

    st, fits, patches, figs = [], {}, [], {}
    for p in targets:
        corrupt = corrupt_for(p)
        pt = chip.pairs[p]
        cur_amp = chip.get(f"qubit_pairs.{p}.macros.{operation}.flux_pulse_qubit.amplitude")
        # conditional phase crosses 0.5 (units of 2pi) at amp*cur = phase_amp
        phase = 0.5 + 2.0 * (amp * cur_amp - pt.phase_amp) / max(pt.phase_amp, 1e-9)
        y = np.empty((n_amp, n_frame, 2))
        for k, ctrl in enumerate(axes):
            shift = phase * ctrl
            y[:, :, k] = 0.5 + 0.5 * np.cos(2 * np.pi * (frame[None, :] + shift[:, None]))
        y += _noise(rng, y.shape, 0.10 if corrupt == "noisy" else 0.02, shots)
        if corrupt == "no_signal":
            y = _noise(rng, y.shape, 0.04, shots) + 0.5

        opt_true = pt.phase_amp / max(cur_amp, 1e-9) * cur_amp
        if corrupt == "wrong_peak":
            claimed, success = opt_true * 1.06, True
        elif corrupt == "out_of_band":
            claimed, success = 2.2, True
        elif corrupt == "drift":
            claimed, success = opt_true * 1.03, True
        elif corrupt == "no_signal":
            claimed, success = float(rng.uniform(0.15, 0.3)), True
        else:
            claimed, success = opt_true * float(rng.normal(1, 0.003)), True
        fits[p] = {"optimal_amplitude": claimed, "success": success}
        patches.append((f"qubit_pairs.{p}.macros.{operation}.flux_pulse_qubit.amplitude",
                        claimed))
        st.append(y)
        figs[p] = (amp * cur_amp, y[:, 0, 1], claimed)

    coords = {"qubit_pair": np.asarray(targets, dtype=object),
              "amp": amp, "frame": frame, "control_axis": axes}
    variables = {
        "state_target": (("qubit_pair", "amp", "frame", "control_axis"),
                         np.asarray(st)),
    }
    return coords, variables, fits, patches, {"phase": figs}


# node_name → (generator, targets_kind)
GENERATORS: dict[str, tuple[Callable, str]] = {
    "03_resonator_spectroscopy": (_gen_resonator_spec, "qubits"),
    "08_qubit_spectroscopy": (_gen_qubit_spec, "qubits"),
    "11_power_rabi": (_gen_power_rabi, "qubits"),
    "12_ramsey": (_gen_ramsey, "qubits"),
    "25_T1": (_gen_t1, "qubits"),
    "26_echo": (_gen_echo, "qubits"),
    "15a_readout_frequency_optimization": (_gen_readout_freq_opt, "qubits"),
    "16_iq_blobs": (_gen_iq_blobs, "qubits"),
    "31_chevron_11_02": (_gen_cz_chevron, "qubit_pairs"),
    "32_cz_conditional_phase": (_gen_cz_conditional_phase, "qubit_pairs"),
}

CORRUPTION_MODES = ("wrong_peak", "no_signal", "noisy", "out_of_band", "drift")


# ---------------------------------------------------------------------------
# The run-folder writer
# ---------------------------------------------------------------------------

@dataclass
class SynthRun:
    folder: Path
    run_id: int
    node_name: str
    targets: list[str]
    fit_results: dict
    patches: list[dict]
    corrupt: str | None


def synth_run(node_name: str, chip: SimChip, targets: list[str], out_root: Path,
              run_id: int, *, params: dict | None = None,
              corrupt: Any = None, seed: int = 0,
              apply_updates: bool = True,
              when: datetime | None = None) -> SynthRun:
    """Generate one synthetic run folder + (optionally) apply its patches to
    the sim chip (mirroring the node's own ``record_state_updates``).

    ``corrupt``: a mode string applied to every target, a ``{target: mode}``
    dict (per-target corruption — the engine's retry tests need one bad qubit
    among clean ones), or None.
    ``when`` orders runs in the DatasetStore (date folder + HHMMSS + run_start);
    defaults to a deterministic time derived from ``run_id``.
    """
    if node_name not in GENERATORS:
        raise KeyError(f"no synth generator for node {node_name!r}")
    gen, kind = GENERATORS[node_name]
    params = dict(params or {})
    rng = np.random.default_rng(seed * 100003 + run_id)
    when = when or (datetime(2026, 7, 17, 8, 0, 0,
                             tzinfo=timezone.utc) + timedelta(minutes=run_id))

    if isinstance(corrupt, dict):
        corrupt_for = lambda t: corrupt.get(t)          # noqa: E731
    else:
        corrupt_for = lambda t: corrupt                 # noqa: E731
    coords, variables, fits, patch_specs, figures = gen(chip, targets, params,
                                                        corrupt_for, rng)

    date_str = when.strftime("%Y-%m-%d")
    time_str = when.strftime("%H%M%S")
    folder = Path(out_root) / date_str / f"#{run_id}_{node_name}_{time_str}"
    folder.mkdir(parents=True, exist_ok=False)

    # ---- patches: resolve old values BEFORE mutating the sim chip ---------
    patches: list[dict] = []
    if apply_updates:
        for dotted, new in patch_specs:
            target_name = dotted.split(".")[1]
            entry = fits.get(target_name) or {}
            if entry.get("success") is False:
                continue                      # nodes skip failed targets
            try:
                old = chip.get(dotted)
            except (KeyError, TypeError):
                old = None
            patches.append({"op": "replace", "path": "/quam/" + dotted.replace(".", "/"),
                            "value": new, "old": old})
        chip.apply_patches(patches)

    # ---- h5 cubes ----------------------------------------------------------
    _write_h5(folder / "ds_raw.h5", coords, variables)
    fitvars = {}
    dim0 = "qubit" if kind == "qubits" else "qubit_pair"
    for tname in ("r2", "fwhm"):
        col = [float((fits.get(t) or {}).get(tname) or 0.0) for t in targets]
        fitvars[tname] = ((dim0,), np.asarray(col))
    _write_h5(folder / "ds_fit.h5", {dim0: coords[dim0]}, fitvars)

    # ---- figures (best-effort) ---------------------------------------------
    fig_refs: dict[str, str] = {}
    if _plt is not None:
        for figname, per_target in figures.items():
            fig, ax = _plt.subplots(figsize=(6, 3.2), dpi=80)
            for t, (x, y, marker) in per_target.items():
                ax.plot(x, y, lw=0.9, label=str(t))
                if marker is not None:
                    ax.axvline(marker, color="red", ls="--", lw=0.8)
            ax.legend(fontsize=6)
            ax.set_title(f"{node_name} — {figname}", fontsize=8)
            fname = f"figures.{figname}.png"
            fig.savefig(folder / fname)
            _plt.close(fig)
            fig_refs[figname] = f"./{fname}"

    # ---- data.json ----------------------------------------------------------
    data_json = {"ds_raw": "./ds_raw.h5", "ds_fit": "./ds_fit.h5",
                 "fit_results": fits, "figures": fig_refs}
    (folder / "data.json").write_text(json.dumps(data_json), encoding="utf-8")

    # ---- node.json -----------------------------------------------------------
    model: dict[str, Any] = dict(params)
    model[kind] = list(targets)
    if node_name not in ("31_chevron_11_02",) and kind == "qubit_pairs":
        model.setdefault("operation", str(params.get("operation", "cz_unipolar")))
    outcomes = {t: ("successful" if (fits.get(t) or {}).get("success") else "failed")
                for t in targets}
    node_json = {
        "created_at": when.isoformat(),
        "metadata": {
            "description": "",
            "run_start": when.isoformat(),
            "run_end": (when + timedelta(seconds=17)).isoformat(),
            "type_of_execution": "run",
            "status": "finished",
            "name": node_name,
            "data_path": f"{date_str}/#{run_id}_{node_name}_{time_str}",
        },
        "data": {"parameters": {"model": model, "schema": {}},
                 "outcomes": outcomes,
                 "quam": "./quam_state/state.json"},
        "id": run_id,
        "parents": [],
        "patches": patches,
    }
    (folder / "node.json").write_text(json.dumps(node_json), encoding="utf-8")

    # ---- quam_state snapshot (POST-update when patches exist — the
    #      contracts.py patches-first rule) ---------------------------------
    qs = folder / "quam_state"
    qs.mkdir()
    (qs / "state.json").write_text(json.dumps(chip.state), encoding="utf-8")
    (qs / "wiring.json").write_text(json.dumps(chip.wiring), encoding="utf-8")

    return SynthRun(folder=folder, run_id=run_id, node_name=node_name,
                    targets=list(targets), fit_results=fits, patches=patches,
                    corrupt=corrupt)
