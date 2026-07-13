"""Standalone QUAM configuration generator.

This script is executed by a subprocess inside a *user-selected conda env*
that has the Quantum Machines stack (``qualang_tools``, ``quam_builder``,
``quam``) installed. It is NEVER imported by ``quam_state_manager`` — it may
import only the QM libraries and the Python standard library.

Driven by ``quam_state_manager.core.config_generator``. Two modes:

  --mode allocate : Instruments + Connectivity + allocate_wiring. Reports the
                    per-element port assignment. Writes no QUAM files.
  --mode build    : allocate, then build_quam_wiring + build_quam (+ populate)
                    and write state.json / wiring.json into --out.

Both modes always write a ``_result.json`` envelope next to the spec file
(status, error, traceback, library versions, and the mode-specific payload)
— the single thing the parent process reads. It is written beside the spec,
never into ``--out``: QUAM's loader reads every ``.json`` in a folder, so a
stray file there would corrupt the generated ``state.json``. The script
never raises to the OS — failures are captured into ``_result.json`` with
``status == "error"``.

Usage::

    python run_build.py --mode allocate --spec spec.json --out work_dir
    python run_build.py --mode build    --spec spec.json --out quam_state_dir
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

# This script is run as `python <path>/run_build.py` by an external conda
# interpreter. CPython prepends the script's directory to sys.path[0] — UNLESS
# PYTHONSAFEPATH / -P (3.11+) suppresses it. Insert defensively so the sibling
# import works in every launch mode, including the frozen bundle
# (<_MEIPASS>/quam_state_manager/generator/).
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from _script_common import library_versions as _library_versions  # noqa: E402
# The capability catalog is the single source of truth for what the QM stack
# exposes; it lives beside this script (same env). Importing it here couples the
# detector (probe_capabilities) and this consumer so they can't drift silently —
# if the catalog module goes missing the build fails loudly, not subtly.
from probe_capabilities import CATALOG_IDS as _CAPABILITY_IDS  # noqa: E402,F401

RESULT_FILENAME = "_result.json"


# ---------------------------------------------------------------------------
# Spec helpers
# ---------------------------------------------------------------------------

def _norm_index(qubit_id):
    """Normalise a spec qubit id to the index QubitReference expects.

    ``"q1"`` -> ``1``; ``"qA1"`` -> ``"A1"``; ``1`` -> ``1``; ``"A1"`` -> ``"A1"``.
    QM's ``QubitReference(index)`` renders as ``f"q{index}"``, so the leading
    ``q`` must be stripped to avoid a doubled ``qq1``.
    """
    s = str(qubit_id)
    if s[:1] in ("q", "Q"):
        s = s[1:]
    return int(s) if s.isdigit() else s


def _parse_pair(pair_id):
    """Split a spec qubit-pair id into ``(control_index, target_index)``.

    ``"q1-q2"`` -> ``(1, 2)``; ``"qA1-qB2"`` -> ``("A1", "B2")``. Splits on the
    first ``-`` so multi-character qubit labels survive.
    """
    parts = str(pair_id).split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"invalid qubit-pair id: {pair_id!r}")
    return _norm_index(parts[0]), _norm_index(parts[1])


def _make_constraint(channel: dict | None):
    """Translate a spec ``channel`` dict into a qualang_tools channel spec.

    ``None`` / missing => unconstrained (the allocator picks the port). Any
    subset of fields may be given; unspecified fields stay free.
    """
    if not channel:
        return None

    from qualang_tools.wirer.wirer.channel_specs import (
        mw_fem_spec,
        lf_fem_spec,
        opx_spec,
        octave_spec,
    )

    kind = channel.get("kind")
    if kind == "mw_fem":
        return mw_fem_spec(
            con=channel.get("con"),
            slot=channel.get("slot"),
            in_port=channel.get("in_port"),
            out_port=channel.get("out_port"),
        )
    if kind == "lf_fem":
        return lf_fem_spec(
            con=channel.get("con"),
            in_slot=channel.get("in_slot", channel.get("slot")),
            in_port=channel.get("in_port"),
            out_slot=channel.get("out_slot", channel.get("slot")),
            out_port=channel.get("out_port"),
        )
    if kind == "opx":
        return opx_spec(
            con=channel.get("con"),
            in_port=channel.get("in_port"),
            out_port=channel.get("out_port"),
        )
    if kind == "octave":
        return octave_spec(
            index=channel.get("index"),
            rf_in=channel.get("rf_in"),
            rf_out=channel.get("rf_out"),
        )
    raise ValueError(f"unknown channel kind: {kind!r}")


# ---------------------------------------------------------------------------
# Instruments / Connectivity
# ---------------------------------------------------------------------------

def build_instruments(spec: dict):
    """Build a qualang_tools ``Instruments`` from the spec's hardware list."""
    from qualang_tools.wirer import Instruments

    instruments = Instruments()
    hw = spec.get("instruments", {})

    for ctrl in hw.get("controllers", []):
        con = ctrl["con"]
        mw_slots = sorted(f["slot"] for f in ctrl.get("fems", []) if f.get("fem") == "mw")
        lf_slots = sorted(f["slot"] for f in ctrl.get("fems", []) if f.get("fem") == "lf")
        if mw_slots:
            instruments.add_mw_fem(controller=con, slots=mw_slots)
        if lf_slots:
            instruments.add_lf_fem(controller=con, slots=lf_slots)

    for opx in hw.get("opx_plus", []):
        instruments.add_opx_plus(opx["con"] if isinstance(opx, dict) else opx)

    for octave in hw.get("octaves", []):
        instruments.add_octave(octave["index"] if isinstance(octave, dict) else octave)

    return instruments


def build_connectivity(spec: dict):
    """Build a qualang_tools ``Connectivity`` from the spec's ``lines``.

    Returns ``(connectivity, warnings)``. Line types handled: qubit lines
    (``resonator`` / ``drive`` / ``flux``) and qubit-pair lines (``coupler`` /
    ``cross_resonance`` / ``zz_drive``). ``resonator`` entries sharing a
    ``group`` collapse into one shared (multiplexed) line.

    ``twpa_pump`` / ``twpa_isolation`` lines build natively when the env's
    ``quam_builder`` exposes ``Connectivity.add_twpa_lines`` (modern versions —
    one pump line per TWPA seeds pump + pump_ on the MW port; an isolation line
    maps to ``isolation_constraints``). Only pre-TWPA builders (0.2.0, where
    ``WiringLineType.TWPA_PUMP``'s ``"p"`` collided with ``PLUNGER_GATE``) skip
    them with a warning — upgrade ``quam_builder`` there to build TWPAs.
    """
    from qualang_tools.wirer import Connectivity

    connectivity = Connectivity()
    warnings: list = []
    lines = spec.get("lines", [])

    # Resonator lines — group multiplexed qubits onto one shared line.
    resonator_groups: dict = {}
    for line in lines:
        if line.get("line") != "resonator":
            continue
        # No explicit group => the qubit has its own dedicated feedline.
        group = line.get("group", f"__solo__{line['element']}")
        resonator_groups.setdefault(group, []).append(line)

    for items in resonator_groups.values():
        qubits = [_norm_index(it["element"]) for it in items]
        constraint = _make_constraint(items[0].get("channel"))
        connectivity.add_resonator_line(qubits=qubits, constraints=constraint)

    # TWPA lines — build natively when the installed quam_builder supports it
    # (modern versions expose Connectivity.add_twpa_lines; the pump constraint
    # seeds pump + pump_ on one MW port). Older builders (0.2.0) had no TWPA
    # category — WiringLineType.TWPA_PUMP's 'p' collided with PLUNGER_GATE — so
    # there we skip with a clear warning instead of crashing. One pump line per
    # TWPA; an optional isolation line maps to isolation_constraints.
    twpa_pumps: dict = {}
    twpa_iso: dict = {}
    for ln in lines:
        if ln.get("line") == "twpa_pump":
            twpa_pumps[ln["element"]] = ln.get("channel")
        elif ln.get("line") == "twpa_isolation":
            twpa_iso[ln["element"]] = ln.get("channel")
    twpa_elems = sorted(set(twpa_pumps) | set(twpa_iso))
    if twpa_elems and not hasattr(connectivity, "add_twpa_lines"):
        warnings.append(
            f"TWPA lines skipped ({', '.join(twpa_elems)}): the installed "
            "quam_builder has no add_twpa_lines (pre-TWPA wiring registry). "
            "Upgrade quam_builder in the selected env to build TWPAs."
        )
    elif twpa_elems:
        for tid in twpa_elems:
            # qualang_tools renders the element as f"twpa{id}" — a spec id
            # that already says "twpa1"/"twpaA" would double-prefix to
            # "twpatwpa1" in state+wiring keys. Strip the redundant prefix.
            tid_norm = tid[4:] if (tid.lower().startswith("twpa")
                                   and len(tid) > 4) else tid
            kwargs = {"twpas": [tid_norm]}
            if twpa_pumps.get(tid) is not None:
                kwargs["pump_constraints"] = _make_constraint(twpa_pumps[tid])
            if twpa_iso.get(tid) is not None:
                kwargs["isolation_constraints"] = _make_constraint(twpa_iso[tid])
            connectivity.add_twpa_lines(**kwargs)

    for line in lines:
        line_type = line.get("line")
        if line_type == "drive":
            connectivity.add_qubit_drive_lines(
                qubits=_norm_index(line["element"]),
                constraints=_make_constraint(line.get("channel")),
            )
        elif line_type == "flux":
            connectivity.add_qubit_flux_lines(
                qubits=_norm_index(line["element"]),
                constraints=_make_constraint(line.get("channel")),
            )
        elif line_type == "coupler":
            control, target = _parse_pair(line["element"])
            connectivity.add_qubit_pair_flux_lines(
                qubit_pairs=[(control, target)],
                constraints=_make_constraint(line.get("channel")),
            )
        elif line_type == "cross_resonance":
            control, target = _parse_pair(line["element"])
            connectivity.add_qubit_pair_cross_resonance_lines(
                qubit_pairs=[(control, target)],
                constraints=_make_constraint(line.get("channel")),
            )
        elif line_type == "zz_drive":
            control, target = _parse_pair(line["element"])
            connectivity.add_qubit_pair_zz_drive_lines(
                qubit_pairs=[(control, target)],
                constraints=_make_constraint(line.get("channel")),
            )

    return connectivity, warnings


def read_allocation(connectivity) -> dict:
    """Read the per-element channel assignment back out of a Connectivity.

    Returns ``{element_id: {line_type: [channel, ...]}}`` where each channel
    is a plain dict the parent process / UI can render.
    """
    allocation: dict = {}
    for element_id, element in connectivity.elements.items():
        lines: dict = {}
        for line_type, channels in element.channels.items():
            key = getattr(line_type, "value", str(line_type))
            lines[key] = [
                {
                    "instrument_id": getattr(ch, "instrument_id", None),
                    "con": getattr(ch, "con", None),
                    "slot": getattr(ch, "slot", None),
                    "port": getattr(ch, "port", None),
                    "io_type": getattr(ch, "io_type", None),
                    "signal_type": getattr(ch, "signal_type", None),
                }
                for ch in channels
            ]
        allocation[str(element_id)] = lines
    return allocation


# ---------------------------------------------------------------------------
# Populate — apply physics values onto the built machine
# ---------------------------------------------------------------------------
# Every group and field in spec["populate"] is optional; absent values keep
# build_quam's defaults. Groups are implemented across phases D2-D6.

def _band_for(freq):
    """MW-FEM Nyquist band (1/2/3) for an up/down-converter frequency in Hz."""
    if not isinstance(freq, (int, float)):
        return None
    if 50e6 <= freq < 5.5e9:
        return 1
    if 4.5e9 <= freq < 7.5e9:
        return 2
    if 6.5e9 <= freq <= 10.5e9:
        return 3
    return None


# OPX1000 LF-FEM analog outputs need a delay to align with MW-FEM outputs
# (the MW-FEM stage adds 141 ns of processing for bands 1 + 3, and 161 ns
# for band 2). We assign per-port at build time from the paired qubit's xy
# band; researchers can override later via the qubit / pair detail page.
_BAND_TO_DELAY_NS = {1: 141, 2: 161, 3: 141}


def _delay_for_band(band):
    """LF-FEM delay (ns) for an MW-FEM band, or ``None`` if unknown."""
    return _BAND_TO_DELAY_NS.get(band)


def _apply_lf_delay(lf_port, band):
    """Set ``lf_port.delay`` based on the MW-FEM ``band`` it should align with.

    Safe no-op if *lf_port* is None, doesn't carry a ``delay`` attribute, or
    *band* is not one of {1, 2, 3}.
    """
    if lf_port is None or not hasattr(lf_port, "delay"):
        return
    ns = _delay_for_band(band)
    if ns is None:
        return
    try:
        lf_port.delay = ns
    except (ValueError, TypeError):
        # QUAM may raise on reference-assignment paths; silently skip.
        pass


def _split_port_pointer(ptr):
    """Parse a wiring port reference into its path segments.

    Returns the segments between ``#/`` and the end, or ``None`` if the
    input is not a valid absolute pointer.

    >>> _split_port_pointer("#/ports/mw_outputs/con1/1/1")
    ['ports', 'mw_outputs', 'con1', '1', '1']
    """
    if not isinstance(ptr, str) or not ptr.startswith("#/"):
        return None
    segs = ptr[2:].split("/")
    return segs if all(segs) else None


def _walk_state(state, segs):
    """Resolve a list of path segments against a state dict.

    Returns the dict at ``state[segs[0]][segs[1]]...[segs[-1]]`` or
    ``None`` if any segment is missing or a non-dict is encountered
    before the last segment.
    """
    cur = state
    for s in segs:
        if not isinstance(cur, dict) or s not in cur:
            return None
        cur = cur[s]
    return cur


def _link_input_downconverters_to_outputs(state_path, wiring_path):
    """Rewrite each MW input port's ``downconverter_frequency`` so it is a
    JSON pointer to its paired MW output port's ``upconverter_frequency``.

    Why: hardware-wise the readout output and input on a given MW-FEM port
    share a single physical local oscillator.  When the wizard subprocess
    persists them as two independent floats, the constraint is implicit
    and silently drifts the moment anyone edits one side.  Encoding the
    input as the pointer ``#/ports/mw_outputs/<con>/<slot>/<port>/upconverter_frequency``
    makes the constraint explicit and lets the existing pointer resolver
    keep the two in lockstep across all future reads/edits.

    Idempotent.  No-op when the wiring has no readout channels, when the
    output has no ``upconverter_frequency`` set, or when the input is
    already a pointer.  ``band`` is intentionally left as a literal —
    matches the example-9q-rack encoding already in production.
    """
    state_path = Path(state_path)
    wiring_path = Path(wiring_path)
    if not state_path.exists() or not wiring_path.exists():
        return

    with open(state_path, "r", encoding="utf-8") as fh:
        state = json.load(fh)
    with open(wiring_path, "r", encoding="utf-8") as fh:
        wiring = json.load(fh)

    qubits = (wiring.get("wiring") or {}).get("qubits") or {}
    if not qubits:
        return

    # Build a set of (input_segs, output_segs) for every readout channel.
    # Using a set dedups the common case where many qubits share a feedline.
    pairs: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for qname, qdata in qubits.items():
        rr = (qdata or {}).get("rr") or {}
        out_segs = _split_port_pointer(rr.get("opx_output"))
        in_segs = _split_port_pointer(rr.get("opx_input"))
        if not out_segs or not in_segs:
            continue
        pairs.add((tuple(in_segs), tuple(out_segs)))

    changed = False
    for in_segs_t, out_segs_t in pairs:
        in_segs = list(in_segs_t)
        out_segs = list(out_segs_t)

        out_port = _walk_state(state, out_segs)
        if not isinstance(out_port, dict):
            continue
        up = out_port.get("upconverter_frequency")
        # If the output has no concrete upconverter we can't build a link;
        # also skip if it's already a pointer (no real source value here).
        if up is None or isinstance(up, str):
            continue

        in_port = _walk_state(state, in_segs)
        if not isinstance(in_port, dict):
            continue

        existing = in_port.get("downconverter_frequency")
        pointer = "#/" + "/".join(out_segs) + "/upconverter_frequency"
        if existing == pointer:
            continue  # already linked

        in_port["downconverter_frequency"] = pointer
        changed = True

    if not changed:
        return

    # Atomic write so a partial state.json never lands on disk if the
    # process is killed mid-write.  os.replace is atomic on every OS the
    # generator runs on (Linux/macOS/Windows).
    tmp = state_path.with_suffix(state_path.suffix + ".lo-link.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=4)
    os.replace(tmp, state_path)


def _set_port_lo(port, lo, band):
    if port is None:
        return
    # An input port's downconverter_frequency / band are usually QUAM
    # references to the coupled output port — assigning the output value
    # propagates automatically. QUAM raises ValueError on assigning over a
    # reference, so those attributes are simply skipped.
    for attr in ("upconverter_frequency", "downconverter_frequency"):
        if hasattr(port, attr):
            try:
                setattr(port, attr, lo)
            except ValueError:
                pass
    if band is not None and hasattr(port, "band"):
        try:
            port.band = band
        except ValueError:
            pass


def _set_channel_lo(channel, lo):
    """Set a channel's up/down-converter LO and the matching MW-FEM band.

    MW-FEM channels carry the LO on their ports; IQ channels carry it on the
    channel's ``LO_frequency``.
    """
    band = _band_for(lo)
    out = getattr(channel, "opx_output", None)
    if out is not None and hasattr(out, "upconverter_frequency"):
        _set_port_lo(out, lo, band)
        _set_port_lo(getattr(channel, "opx_input", None), lo, band)
    elif hasattr(channel, "LO_frequency"):
        channel.LO_frequency = lo


def _operation(channel, name):
    """Return a named operation/pulse on a channel, or None."""
    ops = getattr(channel, "operations", None)
    if ops is not None and name in ops:
        return ops[name]
    return None


def _apply_resonator(resonator, vals):
    if "RF_freq" in vals:
        resonator.f_01 = vals["RF_freq"]
        resonator.RF_frequency = vals["RF_freq"]
    if "LO_frequency" in vals:
        _set_channel_lo(resonator, vals["LO_frequency"])
    if "depletion_time" in vals:
        resonator.depletion_time = vals["depletion_time"]
    if "time_of_flight" in vals:
        resonator.time_of_flight = vals["time_of_flight"]
    # Readout MW-FEM port full-scale power. Multiplexed feedlines share a
    # single port, so writing the same value across the feedline group from
    # the UI lands on the same MWFEMAnalogOutputPort here.
    if "full_scale_power_dbm" in vals:
        out = getattr(resonator, "opx_output", None)
        if out is not None and hasattr(out, "full_scale_power_dbm"):
            out.full_scale_power_dbm = vals["full_scale_power_dbm"]
    readout = _operation(resonator, "readout")
    if readout is not None:
        if "readout_length" in vals:
            readout.length = vals["readout_length"]
        if "readout_amplitude" in vals:
            readout.amplitude = vals["readout_amplitude"]


def _apply_qubit(qubit, vals):
    if "RF_freq" in vals:
        qubit.f_01 = vals["RF_freq"]
        if getattr(qubit, "xy", None) is not None:
            qubit.xy.RF_frequency = vals["RF_freq"]
    if "anharmonicity" in vals:
        qubit.anharmonicity = vals["anharmonicity"]
    if "grid_location" in vals:
        qubit.grid_location = vals["grid_location"]

    xy = getattr(qubit, "xy", None)
    if xy is not None:
        if "LO_frequency" in vals:
            _set_channel_lo(xy, vals["LO_frequency"])
        if "full_scale_power_dbm" in vals:
            out = getattr(xy, "opx_output", None)
            if out is not None and hasattr(out, "full_scale_power_dbm"):
                out.full_scale_power_dbm = vals["full_scale_power_dbm"]

    # Align the LF-FEM z output with the MW-FEM xy band so flux pulses arrive
    # at the chip simultaneously with the MW drive. Editable per-port from the
    # qubit detail page after generation. See docs/31_lf_fem_delay.md.
    z = getattr(qubit, "z", None)
    if z is not None:
        z_port = getattr(z, "opx_output", None)
        xy_port = getattr(xy, "opx_output", None) if xy is not None else None
        band = getattr(xy_port, "band", None)
        _apply_lf_delay(z_port, band)


def _apply_flux(flux_line, vals):
    for attr in ("independent_offset", "joint_offset", "min_offset",
                 "arbitrary_offset", "flux_point", "settle_time"):
        if attr in vals:
            setattr(flux_line, attr, vals[attr])
    out = getattr(flux_line, "opx_output", None)
    if out is not None:
        for attr in ("output_mode", "upsampling_mode"):
            if attr in vals and hasattr(out, attr):
                setattr(out, attr, vals[attr])


def _apply_pulses(machine, vals):
    """Add the single-qubit DragCosine gate set and tune the saturation pulse.

    ``vals`` is now per-qubit: ``{qid: {x180_length, x180_amplitude, drag_alpha,
    drag_detuning, saturation_length, saturation_amplitude}}``. Real device
    states show per-qubit calibration (e.g. LabA' eight distinct DRAG α
    values across nine qubits), so the wizard stores per-qubit values; an
    empty per-qubit override falls back to QUAM defaults
    (x180_length=40, x180_amplitude=0.1, alpha=0, detuning=0). The wizard's
    "Set all →" row writes the same value to every qubit, which is the new
    way to express a global default.

    build_quam only adds a `saturation` pulse — the x180/x90/y… gates come
    from add_DragCosine_pulses (as in QM's own populate_quam_*.py). They are
    added unconditionally so every generated config is usable.
    """
    # add_DragCosine_pulses lived in `.pulses` in older quam_builder versions
    # and moved into `.add_default_pulses` later. Try both so the generator
    # works across the versions teams have pinned.
    try:
        from quam_builder.builder.superconducting.pulses import add_DragCosine_pulses
    except ModuleNotFoundError:
        from quam_builder.builder.superconducting.add_default_pulses import (
            add_DragCosine_pulses,
        )

    qubits = getattr(machine, "qubits", None) or {}
    for qid, qubit in qubits.items():
        if getattr(qubit, "xy", None) is None:
            continue
        q_vals = (vals or {}).get(qid) or {}
        # add_DragCosine_pulses references the qubit's `anharmonicity` attribute
        # (`#.../anharmonicity`). build_quam leaves it None and the populate step
        # is OPTIONAL, so a chip generated without an anharmonicity value would
        # make generate_config() crash the moment it builds the DRAG Q-component
        # (`derivative * (alpha / anharmonicity)` → `float * None` TypeError).
        # Default it to a standard transmon value so the zero-populate path still
        # yields a usable config; any value already set by _apply_qubit (from the
        # populate block) is preserved, and the user can edit it afterwards.
        if getattr(qubit, "anharmonicity", None) is None:
            qubit.anharmonicity = -200e6
        add_DragCosine_pulses(
            qubit,
            amplitude=q_vals.get("x180_amplitude", 0.1),
            length=q_vals.get("x180_length", 40),
            anharmonicity=qubit.get_reference() + "/anharmonicity",
            alpha=q_vals.get("drag_alpha", 0.0),
            detuning=q_vals.get("drag_detuning", 0),
        )
        saturation = _operation(qubit.xy, "saturation")
        if saturation is not None:
            if "saturation_length" in q_vals:
                saturation.length = q_vals["saturation_length"]
            if "saturation_amplitude" in q_vals:
                saturation.amplitude = q_vals["saturation_amplitude"]


def _quam_pair_id(spec_pair_id):
    """Map a spec pair id ('q1-q2') to QUAM's qubit_pair key ('q1-2')."""
    control, target = _parse_pair(spec_pair_id)
    return "q" + str(control) + "-" + str(target)


def _make_cz_gate(cz_gate_cls, pulse_id, moving):
    """Construct a CZGate with ``moving_qubit`` only when the class accepts it.

    Older quam_builder versions exposed CZGate(flux_pulse_qubit=..., moving_qubit=...);
    newer ones drop ``moving_qubit`` and instead read it off the pair object
    (which we already set via ``pair.moving_qubit = moving``).
    """
    import inspect

    if "moving_qubit" in inspect.signature(cz_gate_cls.__init__).parameters:
        return cz_gate_cls(flux_pulse_qubit=pulse_id, moving_qubit=moving)
    return cz_gate_cls(flux_pulse_qubit=pulse_id)


def _apply_pairs(machine, pairs_vals):
    """Add the requested gate macro to each qubit pair and tune the coupler.

    build_quam leaves `qubit_pair.macros` empty. The flux pulse goes on the
    *moving* qubit's z line — `moving_qubit` (``control`` or ``target``,
    user-settable, default ``control``) picks which one — and the gate
    macro references the pulse by name.

    Spec dispatch via ``gate_type`` (default ``cz_unipolar`` for backwards
    compat):

    - ``cz_unipolar``: DC flux pulse via :class:`CZGate`. Fields:
      ``cz_amplitude``, ``cz_interaction_duration``.
    - ``cz_parametric``: AC-flux modulated CZ via :class:`ParametricCZGate`.
      Fields: ``cz_amplitude``, ``cz_interaction_duration``, and
      ``cz_modulation_frequency`` (Hz, default 0). Requires quam_builder
      with ParametricCZGate exported.
    """
    from quam.components.pulses import SquarePulse
    from quam_builder.architecture.superconducting.custom_gates.flux_tunable_transmon_pair.two_qubit_gates import (
        CZGate,
    )

    qpairs = getattr(machine, "qubit_pairs", None) or {}
    for spec_id, vals in pairs_vals.items():
        quam_id = _quam_pair_id(spec_id)
        if quam_id not in qpairs:
            continue
        pair = qpairs[quam_id]
        duration = vals.get("cz_interaction_duration", 100)
        amplitude = vals.get("cz_amplitude", 0.1)
        gate_type = vals.get("gate_type", "cz_unipolar")

        moving = vals.get("moving_qubit")
        if moving not in ("control", "target"):
            moving = "control"
        pair.moving_qubit = moving
        moving_q = getattr(
            pair, "qubit_control" if moving == "control" else "qubit_target", None
        )

        if moving_q is not None and getattr(moving_q, "z", None) is not None:
            pulse_id = "cz_" + quam_id.replace("-", "_") + "_pulse"
            moving_q.z.operations[pulse_id] = SquarePulse(
                length=duration, amplitude=amplitude
            )
            if gate_type == "cz_parametric":
                # Lazy import so installations without the upgraded
                # quam_builder still load the rest of the generator.
                try:
                    from quam_builder.architecture.superconducting.custom_gates.flux_tunable_transmon_pair.two_qubit_gates import (
                        ParametricCZGate,
                    )
                except ImportError:
                    print(
                        f"WARNING: pair {quam_id}: gate_type='cz_parametric' requested "
                        "but ParametricCZGate is not available in this quam_builder "
                        "install — falling back to cz_unipolar.",
                        file=sys.stderr,
                    )
                    pair.macros["cz_unipolar"] = _make_cz_gate(CZGate, pulse_id, moving)
                else:
                    mod_freq = vals.get("cz_modulation_frequency", 0.0)
                    pair.macros["cz_parametric"] = ParametricCZGate(
                        flux_pulse_qubit=pulse_id,
                        modulation_frequency=float(mod_freq),
                    )
            else:
                pair.macros["cz_unipolar"] = _make_cz_gate(CZGate, pulse_id, moving)

        coupler = getattr(pair, "coupler", None)
        if coupler is not None:
            for attr in ("decouple_offset", "interaction_offset",
                         "flux_point", "settle_time"):
                key = "coupler_" + attr
                if key in vals:
                    setattr(coupler, attr, vals[key])

            # Align the LF-FEM coupler delay with the moving qubit's xy band:
            # during a 2Q gate the coupler plays at the same time as the
            # moving qubit's MW drive, so they must share the same delay.
            moving_xy_port = (
                getattr(getattr(moving_q, "xy", None), "opx_output", None)
                if moving_q is not None else None
            )
            coupler_band = getattr(moving_xy_port, "band", None)
            _apply_lf_delay(getattr(coupler, "opx_output", None), coupler_band)


def apply_populate(machine, populate, handle_pairs=True):
    """Apply spec['populate'] physics values onto a built QUAM machine.

    When *handle_pairs* is False the caller owns pair-gate creation (the wizard
    path drives it from ``spec['pair_gate']`` via :func:`_finalize_pair_gates`),
    so the legacy ``populate['pairs']`` path is skipped to avoid double-seeding.
    """
    qubits = getattr(machine, "qubits", None) or {}

    for qid, vals in (populate.get("resonator") or {}).items():
        if qid in qubits and getattr(qubits[qid], "resonator", None) is not None:
            _apply_resonator(qubits[qid].resonator, vals)

    for qid, vals in (populate.get("qubit") or {}).items():
        if qid in qubits:
            _apply_qubit(qubits[qid], vals)

    for qid, vals in (populate.get("flux") or {}).items():
        if qid in qubits and getattr(qubits[qid], "z", None) is not None:
            _apply_flux(qubits[qid].z, vals)

    _apply_pulses(machine, populate.get("pulses") or {})

    if handle_pairs:
        pairs = populate.get("pairs") or {}
        if pairs:
            _apply_pairs(machine, pairs)


# ---------------------------------------------------------------------------
# Two-qubit gate finalization (wizard pair_gate)
# ---------------------------------------------------------------------------
# The wizard chooses ONE 2Q-gate family for the chip via spec["pair_gate"]:
#   "cr"         — cross-resonance: build_quam builds the cr channel (+ a default
#                  square drive op) from the cross_resonance wiring lines;
#                  _seed_cr_gate then adds the flattop drive op, target-side cancel
#                  tones, the inferred-IF reference, and the CRGate macro.
#   "cz_tunable" — CZ on a tunable coupler: pairs exist from the coupler wiring
#                  lines (add_qubit_pair_flux_lines); seed a CZ on the moving
#                  qubit z line AND a matching pulse on the coupler line.
#   "cz_fixed"   — CZ on a fixed coupler: there is NO coupler wiring line, so the
#                  pair does not exist after build_quam; create the
#                  FluxTunableTransmonPair and seed a CZ on the moving qubit z
#                  line only (coupler_flux_pulse=None).
# Recipe transcribed from the customer's quam_config/pair_gates.py (unipolar
# seed variant); calibrate amplitude/duration later.

def _norm_pair_qubits(spec_pair):
    """``["q1","q2"]`` -> ``("q1", "q2", "q1-2")`` (control, target, QUAM pair id)."""
    if not (isinstance(spec_pair, (list, tuple)) and len(spec_pair) == 2):
        return None
    control, target = str(spec_pair[0]), str(spec_pair[1])
    if not control or not target:
        return None
    return control, target, _quam_pair_id(f"{control}-{target}")


# Which CZ flux-pulse variants the wizard can seed (transcribed from the
# customer's quam_config/pair_gates.py ``add_cz``). ``unipolar`` is the default;
# the other four are opt-in via the wizard's ``cz_variant`` selector.
# KEEP IN SYNC with ``config_generator.CZ_VARIANTS`` (the in-process validator) —
# they can't share one symbol across the subprocess boundary (this module runs
# stdlib-only in a foreign QM env). ``TestCzVariantAllowlistInSync`` pins them equal.
_CZ_VARIANTS = ("unipolar", "flattop", "bipolar", "SNZ", "flattop_erf")


# Module homes QM stacks have shipped pulse classes in, tried in order.
# quam <=0.5.x carried everything in quam.components.pulses; quam 0.6.0 /
# quam_builder 0.4.0 moved SNZPulse / ErfSquarePulse /
# GaussianFilteredSymmetricBipolarPulse into the quam_builder architecture
# package and GaussianFilteredSquarePulse / FlatTop duplicates into
# quam_builder.common.pulses. Verified against the qop37_new env (waveform
# formulas bit-identical across homes — see docs/53_qop37_alignment.md).
_PULSE_HOMES = (
    "quam.components.pulses",
    "quam_builder.architecture.superconducting.components.pulses",
    "quam_builder.common.pulses",
)


def _pulse_class(name):
    """Resolve a pulse class by name across every known module home."""
    import importlib

    for mod_name in _PULSE_HOMES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        cls = getattr(mod, name, None)
        if cls is not None:
            return cls
    return None


def _cz_variant_pulses(variant, *, amplitude, duration, smoothing, padding):
    """Build the (qubit_pulse, coupler_pulse, link_attrs) for a CZ *variant*.

    ``coupler_pulse`` is None when the variant carries the whole gate on the qubit
    z line (SNZ / flattop_erf) — safe on fixed OR tunable couplers. ``link_attrs``
    are the shape fields to mirror from the qubit pulse onto the coupler pulse by
    JSON reference (single source of truth). Returns None if the variant needs a
    pulse class missing from EVERY home in this env (caller falls back).
    """
    if variant == "unipolar":
        square = _pulse_class("SquarePulse")
        if square is None:
            return None
        return (square(length=duration, amplitude=amplitude),
                square(length=duration, amplitude=amplitude),
                ("length", "amplitude"))

    if variant == "flattop":
        cls = _pulse_class("_FlatTopGaussianPulse")
        if cls is None:
            return None
        mk = lambda: cls(amplitude=amplitude, flat_length=duration,
                         smoothing_length=smoothing, post_zero_padding_length=padding)
        return (mk(), mk(),
                ("amplitude", "flat_length", "smoothing_length", "post_zero_padding_length"))

    if variant == "bipolar":
        bcls = _pulse_class("_CosineBipolarPulse")
        fcls = _pulse_class("_FlatTopGaussianPulse")
        if bcls is None or fcls is None:
            return None
        # qubit gets the bipolar shape; the coupler mirrors a flat-top (pair_gates).
        return (bcls(amplitude=amplitude, flat_length=duration,
                     smoothing_length=smoothing, post_zero_padding_length=padding),
                fcls(amplitude=amplitude, flat_length=duration,
                     smoothing_length=smoothing, post_zero_padding_length=padding),
                ("amplitude", "flat_length", "smoothing_length", "post_zero_padding_length"))

    if variant == "SNZ":
        cls = _pulse_class("SNZPulse")
        if cls is None:
            return None
        return (cls(amplitude=amplitude, flat_length=duration, t_phi_eff=0.0,
                    padding=padding), None, ())

    if variant == "flattop_erf":
        cls = _pulse_class("ErfSquarePulse")
        if cls is None:
            return None
        return (cls(amplitude=amplitude, flat_length=duration, risetime_samples=16,
                    post_zero_padding_length=padding), None, ())

    return None


def _seed_cz_variant(pair, *, variant="unipolar", amplitude=0.1, duration=100,
                     moving_override=None, smoothing=20, padding=20, vals=None):
    """Seed a CZ macro of *variant* onto *pair* (pair_gates.py ``add_cz`` recipe).

    Flux pulse on the moving (higher-frequency) qubit's z line; on a tunable
    coupler, mirror a matching pulse (with its OWN independent seed) onto the
    coupler line and point the macro's ``coupler_flux_pulse`` at it — the qubit
    and coupler flux pulses are SEPARATE calibration knobs and must not be hard-
    linked into one. The macro key + op names carry the variant and BOTH qubit
    names so the Config Viewer's pair resolver can attribute them. Overwrites the
    same macro key so populate overrides win on a re-run. ``vals``
    (``populate['pairs'][id]``) supplies optional coupler tuning
    (``coupler_interaction_offset`` etc.).

    Unknown / unavailable variants fall back to ``unipolar`` with a warning.
    Returns a warning string if it degraded, else None.
    """
    import inspect

    from quam_builder.architecture.superconducting.custom_gates.flux_tunable_transmon_pair.two_qubit_gates import (  # noqa: E501
        CZGate,
    )

    vals = vals or {}
    qc = getattr(pair, "qubit_control", None)
    qt = getattr(pair, "qubit_target", None)
    if qc is None or qt is None:
        return None

    moving = moving_override
    if moving not in ("control", "target"):
        fc = getattr(qc, "f_01", None) or 0
        ft = getattr(qt, "f_01", None) or 0
        moving = "control" if fc >= ft else "target"
    pair.moving_qubit = moving
    moving_q = qc if moving == "control" else qt
    if getattr(moving_q, "z", None) is None:
        return None

    warning = None
    if variant not in _CZ_VARIANTS:
        warning = (f"CZ variant {variant!r} is not recognized — seeded 'unipolar' "
                   "instead.")
        variant = "unipolar"
    built = _cz_variant_pulses(variant, amplitude=amplitude, duration=duration,
                               smoothing=smoothing, padding=padding)
    if built is None:
        warning = (f"CZ variant {variant!r} needs a pulse class missing from this "
                   "quam_builder install — falling back to 'unipolar'.")
        variant = "unipolar"
        built = _cz_variant_pulses("unipolar", amplitude=amplitude, duration=duration,
                                   smoothing=smoothing, padding=padding)
    qubit_pulse, coupler_pulse, _link_attrs = built

    cn = getattr(qc, "name", None) or getattr(qc, "id", "c")
    tn = getattr(qt, "name", None) or getattr(qt, "id", "t")
    base = f"{cn}_{tn}"
    z = moving_q.z

    # Add the real pulse to the z / coupler operations, and point the macro at
    # them by JSON reference. (Passing op-name strings or bare pulse objects to
    # CZGate either fails to reload — coupler_flux_pulse must be a dict/ref — or
    # never surfaces the pulse on the coupler element. References do both.)
    sig = inspect.signature(CZGate.__init__).parameters
    kwargs = {}

    zop = f"cz_{variant}_flux_pulse_{base}"
    z.operations[zop] = qubit_pulse
    zop_ref = z.get_reference() + f"/operations/{zop}"
    if "flux_pulse_qubit" in sig:
        kwargs["flux_pulse_qubit"] = zop_ref

    if "coupler_flux_pulse" in sig:
        coupler = getattr(pair, "coupler", None)
        if coupler is not None and hasattr(coupler, "operations") and coupler_pulse is not None:
            cop = f"cz_{variant}_coupler_pulse_{base}"
            # Independent coupler pulse (its own literal seed) — the macro points
            # at this op directly. The qubit and coupler flux pulses are separate
            # knobs; hard-linking the coupler op to the qubit op would collapse the
            # two and make the coupler un-tunable.
            coupler.operations[cop] = coupler_pulse
            kwargs["coupler_flux_pulse"] = coupler.get_reference() + f"/operations/{cop}"
            # Coupler bias tuning from populate (decouple / interaction offset, …).
            for attr in ("decouple_offset", "interaction_offset",
                         "flux_point", "settle_time"):
                key = "coupler_" + attr
                if key in vals:
                    try:
                        setattr(coupler, attr, vals[key])
                    except Exception:  # noqa: BLE001 — never fatal
                        pass
        else:
            kwargs["coupler_flux_pulse"] = None

    pair.macros[f"cz_{variant}"] = CZGate(**kwargs)
    return warning


# --- Cross-resonance (CR) 2Q gate -----------------------------------------

def _import_cr_gate():
    """Import the CRGate macro class, version-robustly across quam_builder
    layouts (it lives in the fixed-transmon-pair tree on the customer's
    add-cr-cz-macros build; some installs expose it under flux_tunable). Returns
    the class or None if this install has no CRGate (e.g. upstream quam 0.5.x,
    where CR is only the channel)."""
    candidates = (
        "quam_builder.architecture.superconducting.custom_gates."
        "fixed_transmon_pair.two_qubit_gates",
        "quam_builder.architecture.superconducting.custom_gates."
        "flux_tunable_transmon_pair.two_qubit_gates",
    )
    for mod_name in candidates:
        try:
            mod = __import__(mod_name, fromlist=["CRGate"])
        except Exception:  # noqa: BLE001 — version-robust: try the next path
            continue
        cls = getattr(mod, "CRGate", None)
        if cls is not None:
            return cls
    return None


def _make_cr_gate(cr_gate_cls, vals):
    """Construct a CRGate, passing only the params the installed class accepts."""
    import inspect

    sig = inspect.signature(cr_gate_cls.__init__).parameters
    kwargs = {}
    for key in ("qc_correction_phase", "qt_correction_phase"):
        if key in sig and key in vals:
            kwargs[key] = vals[key]
    return cr_gate_cls(**kwargs)


def _num(v):
    """Return ``v`` if it is a real finite number, else None (rejects bool, str,
    None, and unresolved JSON-pointer strings)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    return None


def _target_lo(qubit):
    """The target qubit's xy LO frequency for a CR drive — the port's literal
    ``upconverter_frequency`` (mirrors the customer ``add_cr``), falling back to a
    numeric ``xy.LO_frequency``. None when neither resolves to a number."""
    xy = getattr(qubit, "xy", None)
    if xy is None:
        return None
    port = getattr(xy, "opx_output", None)
    lo = _num(getattr(port, "upconverter_frequency", None)) if port is not None else None
    if lo is None:
        lo = _num(getattr(xy, "LO_frequency", None))
    return lo


def _seed_cr_gate(pair, *, vals=None):
    """Seed a full cross-resonance 2Q gate onto *pair* (the customer's
    pair_gates.py ``add_cr`` recipe, adapted to our wiring-built CR channel).

    ``build_quam`` already creates ``pair.cross_resonance`` (a CrossResonanceMW
    with a default ``square`` drive op + the CR gate-level params: drive/cancel
    amplitude-scaling & phase, qc/qt correction phase) from the cross_resonance
    wiring line. This adds what a *usable* CR gate still needs:

      * a ``flattop`` drive op alongside ``square`` (build_quam seeds only square);
      * target-side cancel tones (``cr_square_<pair>`` / ``cr_flattop_<pair>`` on
        the target qubit's xy, length-linked by reference to the cr drive ops;
        used by ``cr_type="direct+cancel*"``);
      * the inferred-IF reference so the drive lands at the target frequency;
      * the ``cr`` gate macro (CRGate) so ``pair.apply("cr", ...)`` dispatches;
      * any populate-supplied frequency / scaling / correction-phase overrides.

    Unlike the customer's fixed-coupler layout, our CR channel owns its allocated
    MW port (from the cross_resonance wiring line), so NO 2nd-upconverter surgery
    on the control xy port is needed — and the control-xy intermediate_frequency
    foot-gun does not apply here.

    Returns a warning string if it had to degrade (no CRGate class), else None.
    """
    vals = vals or {}
    cr = getattr(pair, "cross_resonance", None)
    if cr is None:
        return None  # not a CR-wired pair; nothing to seed

    SquarePulse = _pulse_class("SquarePulse")
    FlatTopGaussianPulse = _pulse_class("FlatTopGaussianPulse")
    if SquarePulse is None or FlatTopGaussianPulse is None:
        # same failure surface as the old direct import
        raise ImportError("SquarePulse/FlatTopGaussianPulse not found in any "
                          "known pulse module home of this env")

    pid = str(getattr(pair, "id", None) or getattr(pair, "name", "") or "")
    drive_amp = vals.get("cr_drive_amplitude", 1.0)
    cancel_amp = vals.get("cr_cancel_amplitude", 0.1)
    sq_len = vals.get("cr_square_length", 100)
    ft_len = vals.get("cr_flattop_length", 16)
    ft_flat = vals.get("cr_flattop_flat_length", 12)

    ops = getattr(cr, "operations", None)
    if ops is not None:
        # square: build_quam seeds one; normalize to the customer seed (full-scale
        # drive, explicit axis_angle). flattop: add it (build_quam omits it).
        ops["square"] = SquarePulse(length=sq_len, amplitude=drive_amp, axis_angle=0.0)
        ops["flattop"] = FlatTopGaussianPulse(
            length=ft_len, amplitude=drive_amp, axis_angle=0.0, flat_length=ft_flat
        )

    warns = []

    # Channel-level CR gate params (defaults already 1.0 / 0.0; honor overrides).
    for attr, key in (
        ("drive_amplitude_scaling", "cr_drive_amplitude_scaling"),
        ("drive_phase", "cr_drive_phase"),
        ("cancel_amplitude_scaling", "cr_cancel_amplitude_scaling"),
        ("cancel_phase", "cr_cancel_phase"),
        ("qc_correction_phase", "qc_correction_phase"),
        ("qt_correction_phase", "qt_correction_phase"),
        ("target_qubit_LO_frequency", "target_qubit_LO_frequency"),
        ("target_qubit_IF_frequency", "target_qubit_IF_frequency"),
    ):
        if key in vals and hasattr(cr, attr):
            try:
                setattr(cr, attr, vals[key])
            except Exception:  # noqa: BLE001
                pass

    # The CR drive must play at the TARGET qubit's frequency: the channel's
    # inferred_intermediate_frequency = target_LO + target_IF - LO. Those two
    # target frequencies come from populate; when absent, derive them from the
    # target qubit (its xy LO + f_01), mirroring the customer add_cr. Pin the
    # inferred-IF reference ONLY when both resolve to NUMBERS — else the quam
    # property raises (None + None), the unresolved "#./..." string ships into the
    # config, and qm.open_qm rejects it ("Not a valid number"). When the target
    # frequency is unknown (e.g. a zero-populate build), leave intermediate_frequency
    # as None and warn — the config stays valid; calibrate the CR frequency later.
    target = getattr(pair, "qubit_target", None)
    if _num(getattr(cr, "target_qubit_LO_frequency", None)) is None:
        tgt_lo = _target_lo(target)
        if tgt_lo is not None:
            try:
                cr.target_qubit_LO_frequency = tgt_lo
            except Exception:  # noqa: BLE001
                pass
    if _num(getattr(cr, "target_qubit_IF_frequency", None)) is None:
        tgt_lo = _num(getattr(cr, "target_qubit_LO_frequency", None))
        tgt_f01 = _num(getattr(target, "f_01", None))
        if tgt_lo is not None and tgt_f01 is not None:
            try:
                cr.target_qubit_IF_frequency = tgt_f01 - tgt_lo
            except Exception:  # noqa: BLE001
                pass
    have_freqs = (_num(getattr(cr, "target_qubit_LO_frequency", None)) is not None
                  and _num(getattr(cr, "target_qubit_IF_frequency", None)) is not None)
    if have_freqs:
        try:
            if getattr(cr, "intermediate_frequency", None) is None:
                cr.intermediate_frequency = "#./inferred_intermediate_frequency"
        except Exception:  # noqa: BLE001
            pass
    else:
        warns.append(
            f"pair {pid}: CR target frequency unknown — populate the qubit "
            "frequencies (or set target_qubit_LO/IF_frequency) so the cross-"
            "resonance drive lands on the target; left intermediate_frequency "
            "unset to keep the generated config valid. Calibrate before use.")

    # Target-side cancel tones, length-linked to the cr drive ops.
    target = getattr(pair, "qubit_target", None)
    txy = getattr(target, "xy", None)
    if txy is not None and getattr(txy, "operations", None) is not None:
        try:
            cref = cr.get_reference()
        except Exception:  # noqa: BLE001
            cref = None
        sq = SquarePulse(length=sq_len, amplitude=cancel_amp, axis_angle=0.0)
        ft = FlatTopGaussianPulse(length=ft_len, amplitude=cancel_amp,
                                  axis_angle=0.0, flat_length=ft_flat)
        if cref:
            sq.length = cref + "/operations/square/length"
            ft.length = cref + "/operations/flattop/length"
            ft.flat_length = cref + "/operations/flattop/flat_length"
        txy.operations[f"cr_square_{pid}"] = sq
        txy.operations[f"cr_flattop_{pid}"] = ft

    # The cr gate macro (version-robust import; degrade gracefully if absent).
    cr_gate_cls = _import_cr_gate()
    if cr_gate_cls is None:
        warns.append(
            f"pair {pid}: CRGate macro class not found in this quam_builder "
            "install — seeded the CR drive/cancel pulses + channel params but "
            "not the 'cr' macro.")
    else:
        macros = getattr(pair, "macros", None)
        if macros is not None:
            macros["cr"] = _make_cr_gate(cr_gate_cls, vals)
    return "; ".join(warns) if warns else None


def _finalize_pair_gates(machine, spec, pair_gate):
    """Add the wizard's chosen 2Q-gate macros (CR / CZ fixed / CZ tunable).

    ``cz_fixed`` creates the otherwise-missing pairs first (no coupler wiring
    line). ``cr`` / ``cz_tunable`` pairs already exist from their wiring lines.
    ``populate['pairs'][<id>]`` overrides per-pair seed params (cz_amplitude /
    cz_interaction_duration / cz_variant / moving_qubit; cr_* drive/cancel/phase).
    Returns a list of warning strings (empty when every seed was exact).
    """
    warnings: list = []
    qubits = getattr(machine, "qubits", None) or {}
    qpairs = getattr(machine, "qubit_pairs", None)
    if qpairs is None:
        return warnings
    populate_pairs = (spec.get("populate") or {}).get("pairs") or {}
    # populate.pairs may be keyed by the wizard's "control-target" spec id
    # ("q1-q2") or already by the QUAM pair id ("q1-2"); the qpairs keys below are
    # QUAM pair ids, so normalize both forms onto the QUAM id (else wizard-entered
    # per-pair overrides silently never reach the seed).
    norm_pairs = dict(populate_pairs)
    for _k, _v in populate_pairs.items():
        try:
            norm_pairs.setdefault(_quam_pair_id(_k), _v)
        except Exception:  # noqa: BLE001 — a malformed key just isn't normalized
            pass

    if pair_gate == "cz_fixed":
        try:
            from quam_builder.architecture.superconducting.qubit_pair.flux_tunable_transmon_pair import (  # noqa: E501
                FluxTunableTransmonPair,
            )
        except Exception:  # noqa: BLE001 — version-robust: skip if the class moved
            FluxTunableTransmonPair = None
        for spec_pair in spec.get("qubit_pairs") or []:
            parsed = _norm_pair_qubits(spec_pair)
            if not parsed:
                continue
            control, target, quam_id = parsed
            if quam_id in qpairs:
                continue
            qc = qubits.get(control)
            qt = qubits.get(target)
            if qc is None or qt is None or FluxTunableTransmonPair is None:
                continue
            qpairs[quam_id] = FluxTunableTransmonPair(
                id=quam_id,
                qubit_control=qc.get_reference(),
                qubit_target=qt.get_reference(),
            )

    for quam_id, pair in list(qpairs.items()):
        vals = norm_pairs.get(quam_id) or {}
        if pair_gate == "cr":
            w = _seed_cr_gate(pair, vals=vals)
        else:
            w = _seed_cz_variant(
                pair,
                # "" is the wizard's "use default" sentinel == unipolar.
                variant=vals.get("cz_variant") or "unipolar",
                amplitude=vals.get("cz_amplitude", 0.1),
                duration=vals.get("cz_interaction_duration", 100),
                moving_override=vals.get("moving_qubit"),
                vals=vals,
            )
        if w:
            warnings.append(w)

    # Warn on populate.pairs keys that match no built pair (typos / removed pairs)
    # so a silently-dropped per-pair override is visible in _result.json.
    qpair_ids = set(qpairs.keys())
    for k in populate_pairs:
        norm = k
        try:
            norm = _quam_pair_id(k)
        except Exception:  # noqa: BLE001
            pass
        if k not in qpair_ids and norm not in qpair_ids:
            warnings.append(
                f"populate.pairs['{k}']: no matching qubit pair was built — the "
                "per-pair overrides under this key were ignored.")
    return warnings


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def run_allocate(spec: dict) -> dict:
    """Dry run: allocate channels and return the assignment. Writes no files."""
    from qualang_tools.wirer import allocate_wiring

    instruments = build_instruments(spec)
    connectivity, warnings = build_connectivity(spec)
    allocate_wiring(connectivity, instruments)
    return {"allocation": read_allocation(connectivity), "warnings": warnings}


def run_build(spec: dict, out_dir: Path) -> dict:
    """Full build: allocate, build wiring + QUAM, apply populate, write files.

    Allocates channels, builds wiring + QUAM (qubit lines and qubit-pair
    coupler / cross-resonance / ZZ lines; OPX1000 / OPX+ / Octave), applies
    the spec's ``populate`` physics values, and writes ``state.json`` +
    ``wiring.json`` into ``out_dir``. TWPA lines are skipped — see
    ``build_connectivity``.

    The build runs inside a PRIVATE, EMPTY temp directory — never directly in
    ``out_dir``. The intermediate ``quam_cls.load()`` recursively ingests EVERY
    ``*.json`` under ``QUAM_STATE_PATH`` and merges them, so if ``out_dir`` already
    holds other quam states / an experiment archive the reload would pull in
    foreign chips and crash (e.g. a newer-QOP chip whose flux port carries an
    attribute this env's quam lacks, like ``exponential_dc_gain``). We build in
    isolation, then copy only ``state.json`` + ``wiring.json`` into ``out_dir`` —
    so the destination may be any folder, empty or not.
    """
    import inspect
    import shutil
    import tempfile

    from qualang_tools.wirer import allocate_wiring
    from quam_builder.builder.qop_connectivity import build_quam_wiring
    from quam_builder.builder.superconducting import build_quam
    from quam_builder.architecture.superconducting.qpu import (
        FluxTunableQuam,
        FixedFrequencyQuam,
    )

    instruments = build_instruments(spec)
    connectivity, warnings = build_connectivity(spec)
    allocate_wiring(connectivity, instruments)

    net = spec.get("network", {})
    host = net.get("host") or "0.0.0.0"
    cluster = net.get("cluster_name") or "Cluster"
    port = net.get("port")

    lines = spec.get("lines", [])
    flux_tunable = any(ln.get("line") in ("flux", "coupler") for ln in lines)
    quam_cls = FluxTunableQuam if flux_tunable else FixedFrequencyQuam

    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "state.json"
    wiring_path = out_dir / "wiring.json"

    # Build in an ISOLATED empty dir; QUAM_STATE_PATH (pointed at out_dir by
    # main()) is redirected here so the recursive quam load/save can never touch
    # out_dir's existing contents. Auto-removed on exit (success or exception).
    with tempfile.TemporaryDirectory(prefix="quam_build_") as _bd:
        build_dir = Path(_bd)
        os.environ["QUAM_STATE_PATH"] = str(build_dir)

        # 1. Wiring + ports container, saved into the isolated build dir.
        machine = quam_cls()
        # Older quam_builder versions accept an explicit ``path`` kwarg; newer
        # ones rely on QUAM_STATE_PATH. Detect and call accordingly so we stay
        # compatible with both — either way the destination is the build dir.
        wiring_kwargs = {"port": port}
        if "path" in inspect.signature(build_quam_wiring).parameters:
            wiring_kwargs["path"] = str(build_dir)
        build_quam_wiring(connectivity, host, cluster, machine, **wiring_kwargs)

        # 2. Reload (so wiring strings become QUAM references) and build the
        #    transmons / pulses; build_quam saves state.json itself.
        machine = quam_cls.load()
        build_quam(machine)

        # 3. Apply the populate physics values, then add the chosen 2Q-gate
        #    macros, then save. When the wizard set spec["pair_gate"],
        #    _finalize_pair_gates owns pair creation/seeding, so apply_populate
        #    skips the legacy path.
        pair_gate = (spec.get("pair_gate") or "").lower()
        apply_populate(machine, spec.get("populate") or {},
                       handle_pairs=(pair_gate == ""))
        if pair_gate in ("cz_fixed", "cz_tunable", "cr"):
            warnings.extend(_finalize_pair_gates(machine, spec, pair_gate))
        machine.save()

        # 4. Copy ONLY the two artefacts into the user's destination. Anything
        #    else quam wrote stays in the throwaway build dir.
        for name, dst in (("state.json", state_path), ("wiring.json", wiring_path)):
            src = build_dir / name
            if src.exists():
                shutil.copy2(src, dst)

    # 5. Lock the readout LO constraint in state.json (on the final copies): each
    #    MW input port's downconverter_frequency must follow its paired MW output
    #    port's upconverter_frequency (shared physical LO). QUAM sometimes
    #    serializes them as two independent floats which then drift if either is
    #    edited; encode the constraint explicitly via the JSON-pointer mechanism.
    try:
        _link_input_downconverters_to_outputs(state_path, wiring_path)
    except Exception:  # noqa: BLE001 - defensive: build success must not
        # depend on this post-fixup.  The traceback shows up in
        # _result.json's "warnings" only if we propagate, so we swallow
        # here and just print to stderr.
        sys.stderr.write(
            "warning: _link_input_downconverters_to_outputs failed\n"
            + traceback.format_exc()
        )

    return {
        "files": {
            "state": str(state_path) if state_path.exists() else None,
            "wiring": str(wiring_path) if wiring_path.exists() else None,
        },
        "quam_class": quam_cls.__name__,
        "qubits": sorted(str(q) for q in getattr(machine, "qubits", {}) or {}),
        "qubit_pairs": sorted(str(p) for p in getattr(machine, "qubit_pairs", {}) or {}),
        "allocation": read_allocation(connectivity),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="QUAM configuration generator")
    parser.add_argument("--mode", required=True, choices=["allocate", "build"])
    parser.add_argument("--spec", required=True, help="path to the spec JSON")
    parser.add_argument("--out", required=True, help="output / working directory")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # _result.json is written next to the spec (a private work dir), never
    # into --out: QUAM's loader reads *every* .json in a folder, so a stray
    # file in the output dir corrupts the state.json it just built.
    result_dir = Path(args.spec).resolve().parent
    result_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "status": "error",
        "mode": args.mode,
        "versions": {},
        "warnings": [],
        "error": None,
        "traceback": None,
    }

    try:
        result["versions"] = _library_versions()

        with open(args.spec, "r", encoding="utf-8") as fh:
            spec = json.load(fh)

        if args.mode == "allocate":
            result.update(run_allocate(spec))
        else:
            # The output dir is the explicit destination from the UI's Output
            # step. QUAM keys file saving off QUAM_STATE_PATH, so we point it here
            # — never inheriting the user's ambient value. (run_build then
            # redirects it to a private temp build dir for the recursive-load-safe
            # build, and copies the two artefacts back into out_dir.)
            os.environ["QUAM_STATE_PATH"] = str(out_dir)
            result.update(run_build(spec, out_dir))

        result["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 - this is the top-level guard
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()

    result_path = result_dir / RESULT_FILENAME
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    # Echo to stdout so the parent process can also see it in logs.
    print(json.dumps({"status": result["status"], "result_file": str(result_path)}))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
