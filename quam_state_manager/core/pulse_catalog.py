"""Registry of QUAM pulse classes: parameters, defaults, and derived lengths.

Single source of truth for everything the Pulses page needs to know about a
pulse class without importing the QM stack:

- which ``__class__`` strings map to which parameter schema (create form,
  detail rendering, synth dispatch),
- per-class parameter specs (kind, default, unit, whether the parameter
  participates in waveform synthesis),
- the ``inferred_length`` math for classes whose ``length`` field is stored
  as a ``"#./inferred_length"``-style runtime pointer that the JSON pointer
  resolver can never resolve (the value is a Python ``@property`` on the
  quam class, not a JSON node).

The schemas below are transcribed from the authoritative quam source the
user's calibrations run on (conda env ``LabC``, quam 0.5.0a3,
``quam/components/pulses.py``) and are pinned against it by the golden
waveform tests (``tests/test_waveform_golden.py``).

Deprecated classes (``_FlatTopGaussianPulse``, ``_CosineBipolarPulse``) are
included with ``creatable=False`` because real chip states use them; the
``DragPulse``/``ConstantReadoutPulse`` aliases resolve to their successors.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ParamSpec",
    "PulseSpec",
    "PULSE_CATALOG",
    "by_qclass",
    "resolve_qclass",
    "infer_spec",
    "infer_spec_ex",
    "unmodeled_fields",
    "chip_qclass",
    "build_template",
    "inferred_length",
    "resolve_length",
]


# ---------------------------------------------------------------------------
# Spec dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamSpec:
    """One pulse parameter as stored in the state JSON.

    kind: "float" | "int" | "bool" | "str" | "list_float" — used for form
    rendering and coercion. ``synth=False`` parameters (ids, markers,
    integration weights, thresholds) never affect the waveform shape.
    ``required=True`` parameters have no default in the quam dataclass.
    """

    name: str
    label: str
    kind: str
    default: Any = None
    unit: str = ""
    required: bool = False
    synth: bool = True
    doc: str = ""
    # Other field names QM stacks have stored this SAME parameter under
    # (e.g. quam_builder 0.4.0 renamed the GaussianFiltered* classes'
    # post_zero_padding_length to padding_length). Alias values are read
    # into this param by synthesis / inferred-length math and never count
    # as unmodeled fields.
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class PulseSpec:
    """One pulse class: identity, parameters, and length semantics.

    length_mode:
        "explicit" — ``length`` is a plain stored int.
        "inferred" — ``length`` is stored as the ``length_pointer`` self-ref
            (e.g. ``"#./inferred_length"``); :func:`inferred_length`
            re-implements the runtime property.
        "derived"  — no stored length; derived from data
            (WaveformPulse: ``len(waveform_I)``).
    iq: "always" (returns complex), "optional" (complex only when
        axis_angle is not None), "never".
    """

    key: str
    qclass: str
    label: str
    iq: str
    readout: bool
    channels: tuple[str, ...]
    params: tuple[ParamSpec, ...]
    length_mode: str = "explicit"
    length_pointer: str = "#./inferred_length"
    creatable: bool = True
    group: str = "Control"
    doc: str = ""

    def param(self, name: str) -> ParamSpec | None:
        for p in self.params:
            if p.name == name or name in p.aliases:
                return p
        return None

    @property
    def synth_param_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.synth)


# ---------------------------------------------------------------------------
# Shared param fragments
# ---------------------------------------------------------------------------

def _p(name, label, kind, default=None, unit="", required=False, synth=True,
       doc="", aliases=()):
    return ParamSpec(name, label, kind, default, unit, required, synth, doc,
                     aliases)


_LENGTH = _p("length", "Length", "int", 100, unit="ns", required=True)
_ID = _p("id", "Id", "str", None, synth=False)
_DIGITAL_MARKER = _p("digital_marker", "Digital marker", "str", None, synth=False)
_AXIS_ANGLE_OPT = _p(
    "axis_angle", "Axis angle", "float", None, unit="rad",
    doc="IQ axis angle; None targets a single channel / the I port",
)
_AMPLITUDE = _p("amplitude", "Amplitude", "float", 0.1, unit="V", required=True)

_READOUT_PARAMS = (
    _p("integration_weights", "Integration weights", "list_float",
       "#./default_integration_weights", synth=False,
       doc="Runtime-resolved by QUAM; does not affect the envelope"),
    _p("integration_weights_angle", "IW angle", "float", 0.0, unit="rad", synth=False),
    _p("threshold", "Threshold", "float", None, synth=False),
    _p("rus_exit_threshold", "RUS exit threshold", "float", None, synth=False),
)


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

_QC = "quam.components.pulses."

_SPECS: tuple[PulseSpec, ...] = (
    PulseSpec(
        key="SquarePulse", qclass=_QC + "SquarePulse", label="Square",
        iq="optional", readout=False, channels=("xy", "z", "resonator"),
        params=(_LENGTH, _AMPLITUDE, _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER),
        group="Control", doc="Constant amplitude",
    ),
    PulseSpec(
        key="SquareReadoutPulse", qclass=_QC + "SquareReadoutPulse",
        label="Square readout",
        iq="optional", readout=True, channels=("resonator",),
        params=(
            _p("length", "Length", "int", 1000, unit="ns", required=True),
            _p("amplitude", "Amplitude", "float", 0.01, unit="V", required=True),
            _AXIS_ANGLE_OPT,
            *_READOUT_PARAMS,
            _ID,
            _p("digital_marker", "Digital marker", "str", "ON", synth=False),
        ),
        group="Readout", doc="Constant readout pulse + integration weights",
    ),
    PulseSpec(
        key="GaussianPulse", qclass=_QC + "GaussianPulse", label="Gaussian",
        iq="optional", readout=False, channels=("xy", "z"),
        params=(
            _p("length", "Length", "int", 40, unit="ns", required=True),
            _AMPLITUDE,
            _p("sigma", "Sigma", "float", 8.0, unit="ns", required=True,
               doc="Std dev; generally < length/2"),
            _AXIS_ANGLE_OPT,
            _p("subtracted", "Subtracted", "bool", True,
               doc="Shift so first/last samples are 0 V"),
            _ID, _DIGITAL_MARKER,
        ),
        group="Control", doc="Gaussian envelope",
    ),
    PulseSpec(
        key="DragGaussianPulse", qclass=_QC + "DragGaussianPulse",
        label="DRAG (Gaussian)",
        iq="always", readout=False, channels=("xy",),
        params=(
            _p("length", "Length", "int", 40, unit="ns", required=True),
            _p("axis_angle", "Axis angle", "float", 0.0, unit="rad", required=True,
               doc="0 is X, π/2 is Y"),
            _AMPLITUDE,
            _p("sigma", "Sigma", "float", 8.0, unit="ns", required=True),
            _p("alpha", "DRAG α", "float", 0.0, required=True),
            _p("anharmonicity", "Anharmonicity", "float", -220e6, unit="Hz",
               required=True, doc="f_21 - f_10"),
            _p("detuning", "Detuning", "float", 0.0, unit="Hz"),
            _p("subtracted", "Subtracted", "bool", True),
            _ID, _DIGITAL_MARKER,
        ),
        group="Control", doc="Gaussian DRAG (leakage + AC-Stark compensation)",
    ),
    PulseSpec(
        key="DragCosinePulse", qclass=_QC + "DragCosinePulse",
        label="DRAG (Cosine)",
        iq="always", readout=False, channels=("xy",),
        params=(
            _p("length", "Length", "int", 40, unit="ns", required=True),
            _p("axis_angle", "Axis angle", "float", 0.0, unit="rad", required=True,
               doc="0 is X, π/2 is Y"),
            _AMPLITUDE,
            _p("alpha", "DRAG α", "float", 0.0, required=True),
            _p("anharmonicity", "Anharmonicity", "float", -220e6, unit="Hz",
               required=True, doc="f_21 - f_10"),
            _p("detuning", "Detuning", "float", 0.0, unit="Hz"),
            _ID, _DIGITAL_MARKER,
        ),
        group="Control", doc="Cosine DRAG (leakage + AC-Stark compensation)",
    ),
    PulseSpec(
        key="FlatTopGaussianPulse", qclass=_QC + "FlatTopGaussianPulse",
        label="Flat-top (Gaussian edges)",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("length", "Length", "int", 120, unit="ns", required=True,
               doc="Total; (length - flat_length) must be even"),
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("flat_length", "Flat length", "int", 100, unit="ns", required=True),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        group="Flux / Bipolar", doc="Square with Gaussian rise/fall (σ = rise/5)",
    ),
    PulseSpec(
        key="FlatTopCosinePulse", qclass=_QC + "FlatTopCosinePulse",
        label="Flat-top (Cosine edges)",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("length", "Length", "int", 120, unit="ns", required=True,
               doc="Total; (length - flat_length) must be even"),
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("flat_length", "Flat length", "int", 100, unit="ns"),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        group="Flux / Bipolar", doc="Square with cosine rise/fall",
    ),
    PulseSpec(
        key="FlatTopTanhPulse", qclass=_QC + "FlatTopTanhPulse",
        label="Flat-top (tanh edges)",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("length", "Length", "int", 120, unit="ns", required=True,
               doc="Total; (length - flat_length) must be even"),
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("flat_length", "Flat length", "int", 100, unit="ns"),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        group="Flux / Bipolar", doc="Square with tanh rise/fall (±4 span)",
    ),
    PulseSpec(
        key="FlatTopBlackmanPulse", qclass=_QC + "FlatTopBlackmanPulse",
        label="Flat-top (Blackman edges)",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("length", "Length", "int", 120, unit="ns", required=True,
               doc="Total; (length - flat_length) must be even"),
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("flat_length", "Flat length", "int", 100, unit="ns", required=True),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        group="Flux / Bipolar", doc="Square with Blackman-window rise/fall",
    ),
    PulseSpec(
        key="BlackmanIntegralPulse", qclass=_QC + "BlackmanIntegralPulse",
        label="Blackman integral ramp",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("length", "Length", "int", 100, unit="ns", required=True),
            _p("v_start", "V start", "float", 0.0, unit="V", required=True),
            _p("v_end", "V end", "float", 0.1, unit="V", required=True),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        group="Flux / Bipolar", doc="Adiabatic ramp from v_start to v_end",
    ),
    PulseSpec(
        key="ErfSquarePulse", qclass=_QC + "ErfSquarePulse",
        label="Erf square",
        iq="never", readout=False, channels=("z",),
        params=(
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("flat_length", "Flat length", "int", 100, unit="ns", required=True),
            _p("risetime_samples", "Risetime", "int", 16, unit="ns", required=True),
            _p("sample_rate", "Sample rate", "float", 1e9, unit="Hz"),
            _p("phase", "Phase", "float", 0.0, unit="cycles"),
            _p("detuning", "Detuning", "float", 0.0, unit="Hz"),
            _p("positive_polarity", "Positive polarity", "bool", True),
            _p("post_zero_padding_length", "Post zero padding", "int", 0, unit="ns"),
            _ID, _DIGITAL_MARKER,
        ),
        length_mode="inferred",
        group="Flux / Bipolar", doc="Flat top with erf edges (Quil ErfSquare)",
    ),
    PulseSpec(
        key="SNZPulse", qclass=_QC + "SNZPulse",
        label="SNZ (sudden net-zero)",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("flat_length", "Flat length", "int", 20, unit="ns", required=True,
               doc="Total of both lobes; must be even"),
            _p("t_phi_eff", "tφ (effective)", "float", 0.0, unit="ns",
               doc="Effective idle time between lobes"),
            _p("padding", "Padding", "int", 0, unit="ns"),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        length_mode="inferred",
        group="Flux / Bipolar", doc="Di Carlo bipolar flux pulse with B-samples",
    ),
    PulseSpec(
        key="GaussianFilteredSquarePulse",
        qclass=_QC + "GaussianFilteredSquarePulse",
        label="Gaussian-filtered square",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("pulse_length", "Core length", "int", 100, unit="ns", required=True),
            _p("post_zero_padding_length", "Post zero padding", "int", 0, unit="ns",
               aliases=("padding_length",),
               doc="quam_builder >=0.4 stores this as padding_length"),
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("gaussian_filter_frequency_mhz", "Filter freq", "float", 50.0,
               unit="MHz", required=True),
            _p("sample_rate", "Sample rate", "float", 1e9, unit="Hz"),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        length_mode="inferred",
        group="Flux / Bipolar", doc="Square smoothed by a 1D Gaussian filter",
    ),
    PulseSpec(
        key="GaussianFilteredSymmetricBipolarPulse",
        qclass=_QC + "GaussianFilteredSymmetricBipolarPulse",
        label="Gaussian-filtered bipolar",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("pulse_length", "Core length", "int", 100, unit="ns", required=True,
               doc="Total of both lobes; must be even"),
            _p("post_zero_padding_length", "Post zero padding", "int", 0, unit="ns",
               aliases=("padding_length",),
               doc="quam_builder >=0.4 stores this as padding_length"),
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("gaussian_filter_frequency_mhz", "Filter freq", "float", 50.0,
               unit="MHz", required=True),
            _p("sample_rate", "Sample rate", "float", 1e9, unit="Hz"),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        length_mode="inferred",
        group="Flux / Bipolar", doc="Net-zero bipolar smoothed by a Gaussian filter",
    ),
    PulseSpec(
        key="WaveformPulse", qclass=_QC + "WaveformPulse",
        label="Arbitrary waveform",
        iq="optional", readout=False, channels=("xy", "z", "resonator"),
        params=(
            _p("waveform_I", "Waveform I", "list_float", (0.0, 0.1, 0.1, 0.0),
               required=True),
            _p("waveform_Q", "Waveform Q", "list_float", None),
            _ID, _DIGITAL_MARKER,
        ),
        length_mode="derived",
        group="Control", doc="Pre-computed sample arrays (length = len(I))",
    ),
    # ---- deprecated classes still present in real chip states ----
    PulseSpec(
        key="_FlatTopGaussianPulse", qclass=_QC + "_FlatTopGaussianPulse",
        label="Flat-top Gaussian (deprecated)",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("flat_length", "Flat length", "int", 100, unit="ns", required=True),
            _p("smoothing_length", "Smoothing length", "int", 0, unit="ns",
               doc="Total rise+fall; must be even"),
            _p("post_zero_padding_length", "Post zero padding", "int", 0, unit="ns"),
            _p("sigma", "Window sigma", "float", 2.0, synth=False,
               doc="Ignored by the installed qualang_tools (no sigma kwarg)"),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        length_mode="inferred", length_pointer="#./inferred_total_length",
        creatable=False,
        group="Flux / Bipolar", doc="Deprecated; kept for existing states",
    ),
    PulseSpec(
        key="_CosineBipolarPulse", qclass=_QC + "_CosineBipolarPulse",
        label="Cosine bipolar (deprecated)",
        iq="optional", readout=False, channels=("z",),
        params=(
            _p("amplitude", "Amplitude", "float", 0.05, unit="V", required=True),
            _p("flat_length", "Flat length", "int", 100, unit="ns", required=True,
               doc="Must be even (split into + and - halves)"),
            _p("smoothing_length", "Smoothing length", "int", 0, unit="ns"),
            _p("post_zero_padding_length", "Post zero padding", "int", 0, unit="ns"),
            _AXIS_ANGLE_OPT, _ID, _DIGITAL_MARKER,
        ),
        length_mode="inferred", length_pointer="#./inferred_total_length",
        creatable=False,
        group="Flux / Bipolar", doc="Deprecated net-zero cosine bipolar",
    ),
)

PULSE_CATALOG: dict[str, PulseSpec] = {spec.key: spec for spec in _SPECS}

# Additional module homes where these classes VERIFIABLY exist with the same
# waveform semantics — transcribed from the qop37_new env (quam 0.6.0 /
# quam_builder 0.4.0) and pinned bit-identical against our committed golden
# (see docs/53_qop37_alignment.md). resolve_qclass treats these paths as
# exact matches, and chip_qclass only derives a "prefix" write when the
# target class actually lives under that prefix — a guessed path that no
# stack can import makes the whole state.json unloadable.
_QB_ARCH = "quam_builder.architecture.superconducting.components.pulses."
_QB_COMMON = "quam_builder.common.pulses."
_EXTRA_HOMES: dict[str, tuple[str, ...]] = {
    "BlackmanIntegralPulse": (_QB_ARCH,),
    "DragCosinePulse": (_QB_ARCH,),
    "DragGaussianPulse": (_QB_ARCH,),
    "ErfSquarePulse": (_QB_ARCH,),
    "FlatTopBlackmanPulse": (_QB_ARCH,),
    "FlatTopTanhPulse": (_QB_ARCH,),
    "GaussianFilteredSymmetricBipolarPulse": (_QB_ARCH,),
    "SNZPulse": (_QB_ARCH,),
    "FlatTopCosinePulse": (_QB_COMMON,),
    "FlatTopGaussianPulse": (_QB_COMMON,),
    "GaussianFilteredSquarePulse": (_QB_COMMON,),
    "GaussianPulse": (_QB_COMMON,),
}

# Deprecated aliases → successor spec (loadable, never offered for create).
# quam_builder 0.4.0's Smoothed* classes preserve the OLD centered-padding
# semantics of our deprecated _-classes bit-for-bit (golden-verified), so
# they render and synthesize through those specs.
_QCLASS_ALIASES = {
    _QC + "DragPulse": "DragGaussianPulse",
    _QC + "ConstantReadoutPulse": "SquareReadoutPulse",
    _QB_ARCH + "DragPulse": "DragGaussianPulse",
    _QB_ARCH + "SmoothedFlatTopGaussianPulse": "_FlatTopGaussianPulse",
    _QB_ARCH + "SmoothedCosineBipolarPulse": "_CosineBipolarPulse",
}

_BY_QCLASS: dict[str, PulseSpec] = {spec.qclass: spec for spec in _SPECS}
_BY_QCLASS.update({prefix + key: PULSE_CATALOG[key]
                   for key, prefixes in _EXTRA_HOMES.items()
                   for prefix in prefixes})

# Leaf-name form of the deprecated aliases — QM stacks churn module homes
# (quam.components.pulses.X → quam_builder.….pulses.X); the class *name* is
# the stable part, so the leaf step below must cover alias leaves too.
_LEAF_ALIASES: dict[str, str] = {
    qc.rsplit(".", 1)[-1]: key for qc, key in _QCLASS_ALIASES.items()
}


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def resolve_qclass(qclass: Any) -> tuple[PulseSpec | None, str | None]:
    """Resolve a ``__class__`` string (or bare key) to ``(spec, how)``.

    how:
        "exact" — a catalog path or bare key the catalog was transcribed from.
        "alias" — a deprecated full-path alias (DragPulse → DragGaussianPulse).
        "leaf"  — matched by class *name* only: the module path is not one the
            catalog knows (path-churned QM stack, fork, or an unrelated class
            that shares a name). Render it, but callers must flag it — our
            transcription is not verified against that class.
        None    — unknown.

    The leaf step is appended strictly AFTER the bare-key step: the golden
    payload test (``test_payload_layer_matches_raw``) reaches this function
    with bare keys and must keep resolving as before.
    """
    if not isinstance(qclass, str) or not qclass:
        return None, None
    spec = _BY_QCLASS.get(qclass)
    if spec is not None:
        return spec, "exact"
    alias = _QCLASS_ALIASES.get(qclass)
    if alias is not None:
        return PULSE_CATALOG[alias], "alias"
    spec = PULSE_CATALOG.get(qclass)  # bare key, e.g. "SquarePulse"
    if spec is not None:
        return spec, "exact"
    leaf = qclass.rsplit(".", 1)[-1]
    spec = PULSE_CATALOG.get(leaf)
    if spec is not None:
        return spec, "leaf"
    alias = _LEAF_ALIASES.get(leaf)
    if alias is not None:
        return PULSE_CATALOG[alias], "leaf"
    return None, None


def by_qclass(qclass: str) -> PulseSpec | None:
    """Resolve a ``__class__`` string (or bare key) to its spec, or None."""
    return resolve_qclass(qclass)[0]


def infer_spec_ex(pulse_dict: dict, *, context_slot: str | None = None
                  ) -> tuple[PulseSpec | None, str | None]:
    """``(spec, how)`` for a pulse dict from a state file.

    Adds ``how="implicit"`` over :func:`resolve_qclass`: no ``__class__``
    inside a gate flux slot ⇒ SquarePulse (quam-builder's declared default
    for ``flux_pulse_qubit``/``coupler_flux_pulse``). That is a structural
    *guess*, not a class match — callers must never style it as one (no
    leaf-caution chips, no unmodeled-field warnings).
    """
    if not isinstance(pulse_dict, dict):
        return None, None
    qclass = pulse_dict.get("__class__")
    if isinstance(qclass, str):
        return resolve_qclass(qclass)
    if context_slot in ("flux_pulse_qubit", "coupler_flux_pulse"):
        return PULSE_CATALOG["SquarePulse"], "implicit"
    return None, None


def infer_spec(pulse_dict: dict, *, context_slot: str | None = None) -> PulseSpec | None:
    """Best-effort spec for a pulse dict (see :func:`infer_spec_ex`)."""
    return infer_spec_ex(pulse_dict, context_slot=context_slot)[0]


def unmodeled_fields(spec: PulseSpec | None, body: Any) -> list[str]:
    """Body keys the catalog spec does not model, sorted.

    ``__class__`` is identity. ``length`` is stored on EVERY pulse — the
    inferred/derived-length classes deliberately omit it from ``params``
    (the file holds a ``"#./inferred_length"`` self-ref there, written by
    :func:`build_template` and ``machine.save()`` alike), so it can never
    count as unmodeled; explicit-length classes declare it as a param anyway.
    """
    if spec is None or not isinstance(body, dict):
        return []
    known = {p.name for p in spec.params} | {"__class__", "length"}
    for p in spec.params:
        known.update(p.aliases)
    return sorted(k for k in body if k not in known)


# ---------------------------------------------------------------------------
# Template building (create flow)
# ---------------------------------------------------------------------------

def _chip_pulse_classes(merged: dict) -> list[str]:
    """Every explicit pulse ``__class__`` string on the chip.

    Walks the same structural shapes as ``pulse_index.list_pulses``
    (qubit channel operations + pair-gate flux slots + pair drive channels —
    on some CR chips the only DragGaussian evidence lives on a
    ``cross_resonance`` channel). Only bodies carrying a literal ``__class__``
    contribute — implicit slots and alias strings are no evidence about the
    chip's module layout (and the row-level ``qclass`` back-fill must never be
    used here: it injects the catalog's own path for implicit slots, poisoning
    the pool).
    """
    from quam_state_manager.core.pulse_index import (
        GATE_SLOTS, PAIR_PULSE_CHANNELS, PULSE_CHANNELS)

    out: list[str] = []
    qubits = merged.get("qubits")
    if isinstance(qubits, dict):
        for qubit in qubits.values():
            if not isinstance(qubit, dict):
                continue
            for channel in PULSE_CHANNELS:
                chan = qubit.get(channel)
                ops = chan.get("operations") if isinstance(chan, dict) else None
                if not isinstance(ops, dict):
                    continue
                for body in ops.values():
                    if isinstance(body, dict) and isinstance(body.get("__class__"), str):
                        out.append(body["__class__"])
    pairs = merged.get("qubit_pairs")
    if isinstance(pairs, dict):
        for pair in pairs.values():
            if not isinstance(pair, dict):
                continue
            macros = pair.get("macros")
            if isinstance(macros, dict):
                for macro in macros.values():
                    if not isinstance(macro, dict):
                        continue
                    for slot in GATE_SLOTS:
                        body = macro.get(slot)
                        if isinstance(body, dict) and isinstance(body.get("__class__"), str):
                            out.append(body["__class__"])
            for channel in PAIR_PULSE_CHANNELS:
                chan = pair.get(channel)
                ops = chan.get("operations") if isinstance(chan, dict) else None
                if not isinstance(ops, dict):
                    continue
                for body in ops.values():
                    if isinstance(body, dict) and isinstance(body.get("__class__"), str):
                        out.append(body["__class__"])
    return out


def chip_qclass(merged: Any, spec: PulseSpec) -> tuple[str, str]:
    """The ``__class__`` string a NEW pulse of *spec* should carry on THIS chip.

    Never invent a module path the chip's stack can't load — prefer evidence
    from the chip itself over the catalog's transcription source. Returns
    ``(qclass, how)``:

        "reused"  — an existing pulse of the same class name; its exact
            string, verbatim (majority count, tie → lexicographic — a chip
            mid-migration must not flip paths between requests).
        "prefix"  — no same-class pulse, but the chip's catalog-recognized
            classed pulses share a strict-majority module prefix.
        "catalog" — no usable evidence (empty or classless chip); the
            catalog's own ``spec.qclass`` (long-standing behavior).
    """
    classes = _chip_pulse_classes(merged) if isinstance(merged, dict) else []

    same_leaf = [c for c in classes if c.rsplit(".", 1)[-1] == spec.key]
    if same_leaf:
        ranked = sorted(Counter(same_leaf).items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[0][0], "reused"

    prefixes = []
    for c in classes:
        if "." not in c:
            continue
        prefix, leaf = c.rsplit(".", 1)
        if leaf in PULSE_CATALOG:  # a custom class must not donate its prefix
            prefixes.append(prefix + ".")
    if prefixes:
        ranked = sorted(Counter(prefixes).items(), key=lambda kv: (-kv[1], kv[0]))
        candidate = ranked[0][0] + spec.key
        # Strict majority AND the candidate must be a KNOWN home of this
        # class — QM stacks scatter classes across modules (quam_builder's
        # architecture package has SNZPulse but no GaussianPulse), and a
        # guessed path that no stack defines makes Quam.load fail on the
        # whole file. No known home ⇒ the catalog path (always importable
        # somewhere) + the editable create-form field for the rest.
        if (ranked[0][1] * 2 > len(prefixes)
                and candidate in _BY_QCLASS):
            return candidate, "prefix"

    return spec.qclass, "catalog"


def build_template(spec: PulseSpec, fields: dict[str, Any], *,
                   qclass: str | None = None) -> dict[str, Any]:
    """Build the dict written into the state JSON for a new pulse.

    Only catalog-declared params are copied (whitelist, like the legacy
    ``_build_pulse_template``). ``id``/``digital_marker`` are dropped when
    None so new pulses stay minimal. Inferred-length classes get their
    canonical self-ref pointer so the file matches what ``machine.save()``
    produces. *qclass* overrides the written ``__class__`` (the create flow
    passes :func:`chip_qclass` so a new-stack chip gets its own module path,
    not the catalog's transcription source).
    """
    template: dict[str, Any] = {"__class__": qclass or spec.qclass}
    for p in spec.params:
        if p.name in ("id", "digital_marker") and fields.get(p.name) is None:
            continue
        if p.name in fields:
            template[p.name] = fields[p.name]
        elif p.required:
            template[p.name] = p.default
        # tuples (immutable catalog defaults) must land as JSON-safe lists
        if isinstance(template.get(p.name), tuple):
            template[p.name] = list(template[p.name])
    if spec.length_mode == "inferred":
        template["length"] = spec.length_pointer
    return template


# ---------------------------------------------------------------------------
# Inferred / resolved length
# ---------------------------------------------------------------------------

def _ceil4(raw: float) -> int:
    return int(math.ceil(raw / 4) * 4)


def inferred_length(spec_key: str, params: dict[str, Any]) -> int | None:
    """Re-implementation of the quam runtime ``inferred_length`` properties.

    Returns None when the inputs are missing/invalid rather than raising —
    callers surface that as "length unresolvable".
    """
    spec = PULSE_CATALOG.get(spec_key)
    if spec is None or spec.length_mode != "inferred":
        return None
    try:
        if spec_key == "ErfSquarePulse":
            return _ceil4(
                int(params["flat_length"])
                + int(params["risetime_samples"])
                + int(params.get("post_zero_padding_length", 0) or 0)
            )
        if spec_key == "SNZPulse":
            t_phi_eff = float(params.get("t_phi_eff", 0.0) or 0.0)
            if t_phi_eff < 0:
                return None
            t_phi = int(math.floor(t_phi_eff / 2.0)) * 2
            return _ceil4(
                2 * int(params.get("padding", 0) or 0)
                + int(params["flat_length"]) + 2 + t_phi
            )
        if spec_key in ("GaussianFilteredSquarePulse",
                        "GaussianFilteredSymmetricBipolarPulse"):
            pad = params.get("post_zero_padding_length")
            if pad is None:  # quam_builder >=0.4 field name
                pad = params.get("padding_length", 0)
            return _ceil4(int(params["pulse_length"]) + int(pad or 0))
        if spec_key in ("_FlatTopGaussianPulse", "_CosineBipolarPulse"):
            return _ceil4(
                int(params["flat_length"])
                + int(params.get("smoothing_length", 0) or 0)
                + int(params.get("post_zero_padding_length", 0) or 0)
            )
    except (KeyError, TypeError, ValueError):
        return None
    return None


def resolve_length(spec: PulseSpec | None, params: dict[str, Any]) -> int | None:
    """Display/synth length for a pulse dict whose pointers are resolved.

    Handles all three length modes; returns None when unresolvable (e.g. a
    raw ``#./inferred_length`` string for an unknown class).
    """
    if spec is not None and spec.length_mode == "derived":
        wf = params.get("waveform_I")
        return len(wf) if isinstance(wf, (list, tuple)) else None
    raw = params.get("length")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw)
    if spec is not None and spec.length_mode == "inferred":
        return inferred_length(spec.key, params)
    if isinstance(raw, str) and raw.startswith("#") and spec is not None:
        # explicit-length class whose state stores a pointer we couldn't
        # resolve upstream — nothing more we can do here
        return None
    return None
