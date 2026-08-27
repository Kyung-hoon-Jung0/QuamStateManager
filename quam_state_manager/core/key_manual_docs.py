"""Curated key documentation from the OFFICIAL QM docs (2026-08-27).

The quam / quam_builder classes document their own fields (probe_state_schema
parses those docstrings), but the PORT classes carry no docstrings at all —
what `band`, `full_scale_power_dbm`, `lo_mode`, `sampling_rate` … mean is
written in the QM documentation, so this module transcribes those sections:
a faithful summary, the allowed values the docs state, and the exact file +
anchor in the docs repo (``D:\\work\\documentation-website``, read-only) so a
reader can check the source. Nothing here is invented: every entry names the
page it came from, and a key the docs do not describe is simply absent.

``applies``: which state.json places the key is meant for — matched by the
node's class name (a substring) or by a dot-path suffix — so `delay` on a
digital output port and `delay` on an analog output port can be told apart.
"""
from __future__ import annotations

DOCS_ROOT = r"D:\work\documentation-website\docs\docs\docs"

_FEMS = "Guides/opx1000_fems.md"
_CONFIG = "Introduction/config.md"
_FEATURES = "Guides/features.md"
_FILTERS = "Guides/output_filter.md"
_DEMOD = "Guides/demod.md"

# Every entry: key, applies (class-name substrings / path suffixes), summary,
# allowed (list of {"value", "meaning"} or None), default (as the docs state,
# or None), unit, docs (file#anchor), quote (a short verbatim line), since.
DOC_ENTRIES: list[dict] = [
    # ── MW-FEM analog output ports ─────────────────────────────────────
    {
        "key": "band",
        "applies": ["MWFEMAnalogOutputPort", "MWFEMAnalogInputPort"],
        "summary": "The frequency band the MW-FEM port operates in; pulses played from a port are limited to its band. Coupled ports (Out1&In1, Out2&Out3, Out4&Out5, Out6&Out7, Out8&In2) must share a band, or be in bands 1 and 3.",
        "allowed": [{"value": 1, "meaning": "50 MHz – 5.5 GHz"},
                    {"value": 2, "meaning": "4.5 GHz – 7.5 GHz"},
                    {"value": 3, "meaning": "6.5 GHz – 10.5 GHz"}],
        "default": None, "unit": None,
        "docs": f"{_FEMS}#bands",
        "quote": "`band` is a port parameter, and pulses played from a certain port are limited to the chosen band.",
    },
    {
        "key": "upconverter_frequency",
        "applies": ["MWFEMAnalogOutputPort"],
        "summary": "The digital upconverter (LO) frequency of the MW-FEM output port, in Hz, inside the port's band. Alternative to `upconverters` when one upconverter is enough. The settable range per band: band 1 0–5.5 GHz, band 2 4.5–7.5 GHz, band 3 6.5–11 GHz; values outside the band's specified range will not meet the performance specification.",
        "allowed": None, "default": None, "unit": "Hz",
        "docs": f"{_FEMS}#upconverters-and-downconverters",
        "quote": "Each analog output port must define either an `upconverter_frequency` field with a frequency in the port's band, or a `upconverters` field, with up to 2 upconverters per port.",
    },
    {
        "key": "upconverters",
        "applies": ["MWFEMAnalogOutputPort"],
        "summary": "Up to two digital upconverters per MW-FEM output port, each with its own `frequency` (Hz): `{1: {'frequency': 5e9}, 2: {'frequency': 6e9}}`. Two carriers give two ~800 MHz sub-bands within one band. An element picks one with its `upconverter` field (default 1).",
        "allowed": None, "default": None, "unit": "Hz per entry",
        "docs": f"{_FEMS}#upconverters-and-downconverters",
        "quote": "Since each output port is equipped with two Digital Upconverters (DUCs), it is possible to have multiple carriers within a band",
    },
    {
        "key": "full_scale_power_dbm",
        "applies": ["MWFEMAnalogOutputPort"],
        "summary": "The power delivered to a 50 Ω load when the waveform is at full scale (±1), in dBm. Settable from −11 to 18 dBm in 1 dB steps from QOP 3.7 (16 dBm upper limit in earlier QOP 3.x; above 16 dBm is not guaranteed across the frequency range). Amplitude is linear in voltage: 10 dBm with amplitude 0.1 gives 100 mV, 0.2 gives 200 mV. For best SNR and SFDR keep it between 1 and 10 dBm and maximise the waveform amplitude instead.",
        "allowed": [{"value": "-11 … 18", "meaning": "dBm, 1 dB granularity (QOP ≥ 3.7); ≤ 16 dBm on earlier QOP 3.x"}],
        "default": -11, "default_docs": f"{_CONFIG}#controllers", "unit": "dBm",
        "docs": f"{_FEMS}#output-power",
        "quote": "This will set the power delivered to a 50 Ω load when the waveform is set to full scale (`{-1, 1}`).",
        "since": "QOP 3.7 for the 18 dBm upper limit",
    },
    # ── MW-FEM analog input ports ──────────────────────────────────────
    {
        "key": "downconverter_frequency",
        "applies": ["MWFEMAnalogInputPort"],
        "summary": "The downconversion LO frequency of the MW-FEM input port, in Hz, inside the port's band. If both inputs are used, their downconverter frequencies must differ by at least 10 MHz. Intermediate frequencies of magnitude ≤ 5 MHz cannot be measured.",
        "allowed": None, "default": None, "unit": "Hz",
        "docs": f"{_FEMS}#upconverters-and-downconverters",
        "quote": "Each analog input port must define a `downconverter_frequency` field with a frequency in the port's band.",
    },
    {
        "key": "lo_mode",
        "applies": ["MWFEMAnalogInputPort"],
        "summary": "Whether the MW-FEM input's downconversion LO is gated around measurements or kept on. `auto` enables it before a measurement and disables it when none is active (including between programs) — in this mode the ADC trace shows a short 5 MHz ringing transient that is an artifact, not an output. `always_on` keeps it enabled whenever the QM is open (the pre-3.7 behaviour).",
        "allowed": [{"value": "auto", "meaning": "LO on only around measurements (default)"},
                    {"value": "always_on", "meaning": "LO on whenever the QM is open"}],
        "default": "auto", "unit": None,
        "docs": f"{_FEMS}#upconverters-and-downconverters",
        "quote": "Starting from QOP 3.7, MW-FEM analog inputs also support the `lo_mode` field",
        "since": "QOP 3.7",
    },
    # ── LF-FEM analog output ports ─────────────────────────────────────
    {
        "key": "sampling_rate",
        "applies": ["LFFEMAnalogOutputPort", "LFFEMAnalogInputPort"],
        "summary": "The LF-FEM pulse processor rate for this port. At 1e9 (default) samples are generated at 1 GSa/s and upsampled to the 2 GSa/s DAC (see `upsampling_mode`); an element on such a port is limited to 500 MHz and waveforms to 1e9. At 2e9 samples go straight to the DAC at 2 GSa/s and the element consumes double the number of cores. An input at 1e9 produces a 1 GSa/s ADC stream (demodulation limited to 500 MHz).",
        "allowed": [{"value": 1e9, "meaning": "1 GSa/s processing, upsampled to the DAC (default)"},
                    {"value": 2e9, "meaning": "2 GSa/s straight to the DAC; double the cores"}],
        "default": 1e9, "unit": "Sa/s",
        "docs": f"{_FEMS}#sampling-rate",
        "quote": "The DACs and ADCs of the LF-FEM always operate at 2 GSa/s. The Pulse Processor Unit (PPU) can be set to operate at 1 GSa/s, or 2 GSa/s",
    },
    {
        "key": "upsampling_mode",
        "applies": ["LFFEMAnalogOutputPort"],
        "summary": "How a 1 GSa/s LF-FEM output is upsampled to the 2 GSa/s DAC. `mw` passes the samples through a 14-tap Dolph-Chebyshev filter (clean MW signals; recommended when the intermediate frequency exceeds 100 MHz). `pulse` doubles the samples (0-order hold; clean step response; recommended when there is no intermediate frequency). Only meaningful with `sampling_rate` 1e9.",
        "allowed": [{"value": "mw", "meaning": "14-tap Dolph-Chebyshev upsampling (default)"},
                    {"value": "pulse", "meaning": "sample doubling — clean step response"}],
        "default": "mw", "default_docs": f"{_CONFIG}#controllers", "unit": None,
        "docs": f"{_FEMS}#sampling-rate",
        "quote": "This is controlled by an additional field `upsampling_mode`",
    },
    {
        "key": "output_mode",
        "applies": ["LFFEMAnalogOutputPort"],
        "summary": "The LF-FEM analog output range. `direct`: −0.5 V to 0.5 V, optimised for modulated signals (high SFDR), 35 Ω output impedance (spec given for a 50 Ω load), DC–750 MHz. `amplified`: −2.5 V to 2.5 V, 50 Ω matched, optimised step response, DC–330 MHz — it does not amplify the waveform values, it only allows higher amplitudes. There is a 0.9 ns path-delay difference between the two modes.",
        "allowed": [{"value": "direct", "meaning": "±0.5 V, DC–750 MHz (default)"},
                    {"value": "amplified", "meaning": "±2.5 V, DC–330 MHz, 50 Ω matched"}],
        "default": "direct", "default_docs": f"{_CONFIG}#controllers", "unit": None,
        "docs": f"{_FEMS}#output-mode",
        "quote": "This mode does not amplify your waveform values; it merely allows higher amplitudes to be set.",
    },
    # ── analog output ports, all controllers ───────────────────────────
    {
        "key": "offset",
        "applies": ["AnalogOutputPort", "OPXPlusAnalogOutputPort", "LFFEMAnalogOutputPort"],
        "summary": "The analog output port's idle value: the DC voltage applied to the port between jobs (on OPX+: the initial DC voltage when the QM is opened).",
        "allowed": None, "default": None, "unit": "V",
        "docs": f"{_CONFIG}#controllers",
        "quote": "`offset` defines the channel's **idle value**: the DC voltage applied to the port between jobs.",
    },
    {
        "key": "offset",
        "applies": ["AnalogInputPort", "OPXPlusAnalogInputPort", "LFFEMAnalogInputPort"],
        "summary": "DC offset on the analog input port, in V (the controller examples configure inputs with `offset`; the docs do not describe it further).",
        "allowed": None, "default": None, "unit": "V",
        "docs": f"{_CONFIG}#controllers",
        "quote": "'analog_inputs': {1: {'offset': 0.0, ...}}",
    },
    {
        "key": "delay",
        "applies": ["AnalogOutputPort", "OPXPlusAnalogOutputPort", "LFFEMAnalogOutputPort", "MWFEMAnalogOutputPort"],
        "summary": "A delay set on the analog output port, in ns (OPX+: QOP ≥ 2; the config example pairs a 20 mV offset with a 71 ns delay).",
        "allowed": None, "default": None, "unit": "ns",
        "docs": f"{_CONFIG}#controllers",
        "quote": "We can set an `'offset'`, a `'filter'`, `'delay'` to the port in units of ns.",
    },
    {
        "key": "filter",
        "applies": ["OPXPlusAnalogOutputPort", "LFFEMAnalogOutputPort"],
        "summary": "Output filters on the analog port (feedforward FIR taps and feedback / exponential IIR compensation) that pre-distort the waveform to compensate the line's response. Applied after crosstalk and before the DC offset.",
        "allowed": None, "default": None, "unit": None,
        "docs": f"{_FILTERS}#overview-of-the-filters-operation",
        "quote": "For more information on the `filter` capabilities, please refer to the Guide on output filters.",
    },
    {
        "key": "crosstalk",
        "applies": ["OPXPlusAnalogOutputPort", "LFFEMAnalogOutputPort"],
        "summary": "Crosstalk correction terms between ports of the same controller (OPX+) or LF-FEM: `{other_port: factor}` makes what this port plays also come out of `other_port` scaled by `factor`, applied before the filters and DC offsets. A self term scales the port itself (defaults to 1).",
        "allowed": None, "default": None, "unit": "amplitude factor",
        "docs": f"{_FEATURES}#crosstalk-correction-matrix",
        "quote": "Adding a crosstalk term will cause everything coming out from one port also come out from another port, with the given amplitude factor.",
    },
    {
        "key": "gain_db",
        "applies": ["LFFEMAnalogInputPort", "OPXPlusAnalogInputPort"],
        "summary": "Analog input gain in dB (the LF-FEM controller example sets `gain_db: -3`).",
        "allowed": None, "default": None, "unit": "dB",
        "docs": f"{_CONFIG}#controllers",
        "quote": "'analog_inputs': {1: {'offset': 0.0, 'sampling_rate': 2e9, 'gain_db': -3}}",
    },
    # ── channels / elements ────────────────────────────────────────────
    {
        "key": "intermediate_frequency",
        "applies": ["Channel", "IQChannel", "MWChannel", "SingleChannel", "InOutIQChannel", "InOutMWChannel"],
        "summary": "The frequency, in Hz, the element's waveform samples are modulated with (added to the LO / upconverter). Zero gives a DC pulse.",
        "allowed": None, "default": None, "unit": "Hz",
        "docs": f"{_CONFIG}#elements",
        "quote": "The `'intermediate_frequency'` key defines the frequency at which the waveform samples ... will be modulated with. We can get a DC pulse by setting this frequency to zero.",
    },
    {
        "key": "time_of_flight",
        "applies": ["InOutIQChannel", "InOutMWChannel", "InOutSingleChannel", "ReadoutResonator"],
        "summary": "Measurement timing parameter (ns) of a measurement element, defined with `smearing`; the demodulation guide's timing section explains it.",
        "allowed": None, "default": None, "unit": "ns",
        "docs": f"{_DEMOD}#timing-of-the-measurement-operation",
        "quote": "The `time_of_flight` and `smearing` keys are parameters related to the timing of the signal",
    },
    {
        "key": "smearing",
        "applies": ["InOutIQChannel", "InOutMWChannel", "InOutSingleChannel", "ReadoutResonator"],
        "summary": "Measurement timing parameter (ns) of a measurement element, defined with `time_of_flight`; the demodulation guide's timing section explains it.",
        "allowed": None, "default": None, "unit": "ns",
        "docs": f"{_DEMOD}#timing-of-the-measurement-operation",
        "quote": "The `time_of_flight` and `smearing` keys are parameters related to the timing of the signal",
    },
    {
        "key": "sticky",
        "applies": ["Channel"],
        "summary": "Sticky element: the last analog value played at the end of a pulse is held until the next pulse begins (useful where switching a bias voltage adds noise); a playing pulse's values are ADDED to the held value, and the element ramps back to its DC offset at the program's end over `duration` ns. Config form `{'analog': True, 'duration': 50}`; from QOP 2.2 the digital marker can be sticky too.",
        "allowed": None, "default": None, "unit": "duration in ns",
        "docs": f"{_FEATURES}#sticky-element",
        "quote": "When an element is defined as *sticky*, the last analog value played at the end of a pulse will be held until the next pulse begins.",
    },
    {
        "key": "core",
        "applies": ["Channel"],
        "summary": "Pin the element to a named pulse-processor core so elements that are never played together can share one core (playing one after the other without gaps), allowing more elements per program.",
        "allowed": None, "default": None, "unit": None,
        "docs": f"{_FEATURES}#sharing-cores",
        "quote": "Two (or more) elements that are never played together can share the same core",
    },
    # ── digital ───────────────────────────────────────────────────────
]


def entries_for(class_name: str | None, key: str) -> list[dict]:
    """Doc entries for *key* on a node whose class leaf name is *class_name*
    (substring match on the ``applies`` list); with no class, every entry
    for the key (the caller then shows all of them, labelled)."""
    hits = [e for e in DOC_ENTRIES if e["key"] == key]
    if class_name:
        # a KNOWN class gets only entries written for it -- never a neighbour's
        # (FluxLine.output_mode is not the LF-FEM port's output_mode)
        return [e for e in hits if any(a in class_name for a in e["applies"])]
    return hits


def all_keys() -> list[str]:
    return sorted({e["key"] for e in DOC_ENTRIES})
