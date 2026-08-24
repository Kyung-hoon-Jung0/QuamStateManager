"""Emit the customer-idiom generator for a chip that mixes QDAC, LF-FEM and BOTH.

The customer's own builder decides per qubit id between a QDAC-biased class and
a flux-tunable one — one `if`, two branches::

    # quam_config/build_quam_qdac.py:93
    qubit_class = QdacBiasedFixedFrequencyTransmon if is_qdac_biased else machine.qubit_type

A **bias-tee** qubit is both: the QDAC-II holds its DC operating point while an
LF-FEM port plays flux pulses on top of it. That shape has no branch to land in,
and the two guards on either side of it are exact complements — a flux line on a
QDAC qubit raises, a ``qt`` trigger line on a non-QDAC qubit raises — so the
qubit is unreachable from both directions. Worse, the bias assignment after the
line loop is unconditional on ``z``, so on a qubit that DID get a flux line the
``FluxLine`` is silently overwritten by the ``QdacBiasLine``: no exception, a
chip that builds, and a flux line that quietly stopped existing.

This module emits two files, in the lab's OWN idiom, for them to run:

``build_quam_qdac_lf.py``
    the combined builder — a three-way class pick, the bias attached to a
    SIBLING field on a bias-tee qubit, and the trigger attached to the bias line
    rather than to ``z``.
``generate_qdac_LF_combined.py``
    the top-level script — the three qubit sets, the declared trigger cabling,
    and **every** flux line explicitly pinned.

That last point is not tidiness. In the lab's `generate_quam.py` the coupler
flux lines are cabled by allocation ORDER, with only the last few pinned. Adding
one qubit flux line shifts every unpinned coupler one port along — no exception,
no warning, and nothing visibly wrong until a CZ misbehaves on hardware. A
bias-tee chip adds exactly such a line. So the emitted script pins every flux
line from the allocation this spec was built with, the same doctrine
``script_emitter._qdac_pins`` already applies to trigger ports.

**What the lab must still add themselves** (SM cannot, and does not, write into
their tree): the ``QdacBiasedFluxTunableTransmon`` subclass and the widened
``Quam.qubits`` Union. `SNIPPET` below is that text, and both emitted files
refuse to run without it rather than half-building a chip.

Pure string generation. Nothing here imports quam.
"""

from __future__ import annotations

from pprint import pformat
from typing import Any

from quam_state_manager.core import qdac

__all__ = ["SNIPPET", "BUILDER_FILENAME", "GENERATOR_FILENAME",
           "emit_files", "wanted"]

BUILDER_FILENAME = "build_quam_qdac_lf.py"
GENERATOR_FILENAME = "generate_qdac_LF_combined.py"

#: The irreducible lab-side change. Verified by a real ``Quam.load()``: without
#: it a qubit carrying both components fails with
#: ``Unexpected attribute 'qdac_bias' in Quam.qubits["q1"]``.
SNIPPET = '''\
# ---- add to quam_config/qdac_components.py ---------------------------------
from quam_builder.architecture.superconducting.qubit import FluxTunableTransmon


@quam_dataclass
class QdacBiasedFluxTunableTransmon(FluxTunableTransmon):
    """Bias tee: the QDAC-II holds the DC operating point, an LF-FEM port plays
    pulses on top. `z` stays the FluxLine; the bias is a SIBLING field."""

    qdac_bias: QdacBiasLine = None


# ---- and widen the Union in quam_config/my_quam.py --------------------------
#   qubits: Dict[str, Union[FluxTunableTransmon,
#                           QdacBiasedFixedFrequencyTransmon,
#                           QdacBiasedFluxTunableTransmon]] = field(...)
'''


def wanted(spec: Any) -> bool:
    """Does this spec declare a qubit that needs the combined builder?"""
    return bool(qdac.spec_bias_tee_qubits(spec))


# ---------------------------------------------------------------------------
# The shared preamble both emitted files carry
# ---------------------------------------------------------------------------

_IMPORT_GATE = '''\
try:
    from quam_config.qdac_components import QdacBiasedFluxTunableTransmon
except ImportError as exc:  # pragma: no cover - the whole point is to stop here
    raise ImportError(
        "This chip has bias-tee qubits (QDAC-II DC bias AND an LF-FEM flux "
        "line on the same qubit), which needs a transmon class that can hold "
        "both.\\n\\n"
        "Add QdacBiasedFluxTunableTransmon to quam_config/qdac_components.py "
        "and widen my_quam.Quam.qubits' Union with it — see the SNIPPET in "
        "this bundle's README.\\n\\n"
        "Stopping before anything is written: a chip whose bias silently did "
        "not attach looks exactly like a working one."
    ) from exc
'''


def _builder_source(stamp: str) -> str:
    """``build_quam_qdac_lf.py`` — a transcription of the lab's own
    ``build_quam_qdac._add_transmons_with_qdac`` with the divergences named.

    Transcribed rather than wrapped on purpose. Delegating would mean removing
    the ``qt`` entries from ``machine.wiring`` so the stock pass does not raise
    on them, and ``machine.wiring`` is a QuamDict whose nested writes do not
    reliably stick — a mutation hazard traded for a hundred lines that the lab
    can read side by side with the file they already have.
    """
    return f'''\
"""Combined builder: QDAC-only, LF-FEM-only, and BOTH on one qubit (bias tee).

Generated by QUAM State Manager ({stamp}). Drop this into your quam_config/
package beside build_quam_qdac.py and call it from
{GENERATOR_FILENAME}.

This is a transcription of build_quam_qdac._add_transmons_with_qdac with four
named divergences. Read them against your own file:

  D1  the class pick is THREE-way, not two (build_quam_qdac.py:93)
  D2  the flux-line guard raises only for QDAC-ONLY qubits, so a bias-tee
      qubit falls through to add_transmon_flux_component (:107-113)
  D3  the qt-line guard raises only for qubits with no QDAC bias at all
      (:119-124) - today :108 and :120 are exact complements, which is why a
      qubit needing both is unreachable from either side
  D4  the bias lands on a field chosen by mode (:129-131). The original
      assigns `transmon.z` unconditionally, so on a qubit that already got a
      FluxLine it OVERWRITES it - silently, with no exception. That single
      line is why a bias tee cannot be built by the original.

Everything else - octaves, mixers, ports, pairs, TWPAs, pulses, the shareable
trigger ports, the cabling validation - is imported from your existing modules
and runs unchanged.
"""

from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from quam.components import Channel, pulses
from quam.components.channels import DigitalOutputChannel
from quam_builder.builder.qop_connectivity.build_quam_wiring import (  # noqa: F401
    add_name_and_ip,
)
from quam_builder.builder.superconducting.add_transmon_drive_component import (
    add_transmon_drive_component,
)
from quam_builder.builder.superconducting.add_transmon_flux_component import (
    add_transmon_flux_component,
)
from quam_builder.builder.superconducting.add_transmon_pair_component import (
    add_transmon_pair_cross_resonance_component,
    add_transmon_pair_tunable_coupler_component,
    add_transmon_pair_zz_drive_component,
)
from quam_builder.builder.superconducting.add_transmon_resonator_component import (
    add_transmon_resonator_component,
)
from qualang_tools.wirer.connectivity.wiring_spec import WiringLineType

# Reused from your own tree, unchanged. If any of these move, this file should
# fail loudly at import rather than drift into a private copy.
from quam_config.build_quam_qdac import (
    _add_pulses,
    _mark_trigger_ports_shareable,
    _set_default_grid_location,
    _validate_trigger_cabling,
)
from quam_config.qdac_components import QdacBiasLine, QdacBiasedFixedFrequencyTransmon
from quam_config.qdac_trigger_connectivity import QDAC_TRIGGER_LINE_TYPE

{_IMPORT_GATE}

def _attach_bias_trigger(transmon, bias_attr, wiring_path, ports, trigger_port):
    """The lab's _attach_qdac_trigger, with the target field made explicit.

    The original hardcodes `transmon.z`. On a bias-tee qubit `z` is the PULSE
    line and declares neither `opx_trigger_out` nor `trigger_port`, so both
    writes would be dropped at save() without raising - quam serialises
    declared dataclass fields only.
    """
    if "digital_output" not in ports:
        raise ValueError(
            f"QDAC trigger line for {{transmon.name}} has no digital_output port: {{ports}}"
        )
    bias = getattr(transmon, bias_attr)
    bias.opx_trigger_out = Channel(
        id=f"{{transmon.name}}_qdac_trigger",
        digital_outputs={{
            "trigger": DigitalOutputChannel(
                opx_output=f"{{wiring_path}}/digital_output",
                delay=0,
                buffer=0,
                shareable=True,
            )
        }},
        operations={{"trigger": pulses.Pulse(length=100, digital_marker="ON")}},
    )
    bias.trigger_port = trigger_port


def _validate_combined(qdac_only, bias_tee, qubit_trigger_ports, trigger_cabling):
    """The lab's check, widened, plus the three it does not make."""
    both = {{**qdac_only, **bias_tee}}
    _validate_trigger_cabling(both, qubit_trigger_ports, trigger_cabling)

    overlap = set(qdac_only) & set(bias_tee)
    if overlap:
        raise ValueError(
            f"qubit(s) {{sorted(overlap)}} are declared BOTH QDAC-only and "
            "bias-tee. A qubit is one or the other: QDAC-only means the QDAC "
            "REPLACES its z, bias-tee means it sits beside it."
        )

    # The reverse direction of the lab's own check. Without it a stray id in
    # QDAC_QUBIT_TRIGGER_PORTS injects a qt wiring entry for a qubit that has
    # no QDAC bias, and the failure surfaces a whole phase later.
    stray = set(qubit_trigger_ports) - set(both)
    if stray:
        raise ValueError(
            f"QDAC_QUBIT_TRIGGER_PORTS names {{sorted(stray)}}, which are not "
            "QDAC-biased. Remove them, or give them a channel."
        )

    seen: Dict[int, str] = {{}}
    for qid, channel in sorted(both.items()):
        if channel in seen:
            raise ValueError(
                f"QDAC channel {{channel}} is claimed by both {{seen[channel]!r}} "
                f"and {{qid!r}} - one physical channel biases one qubit."
            )
        seen[channel] = qid


def _add_transmons_combined(machine, qdac_only, bias_tee, qubit_trigger_ports):
    channels = {{**qdac_only, **bias_tee}}
    for element_type, wiring_by_element in machine.wiring.items():
        if element_type == "qubits":
            machine.active_qubit_names = []
            number_of_qubits = len(wiring_by_element.items())
            qubit_number = 0
            for qubit_id, wiring_by_line_type in wiring_by_element.items():
                # D1: three-way, not two.
                if qubit_id in bias_tee:
                    mode = "bias_tee"
                elif qubit_id in qdac_only:
                    mode = "qdac"
                else:
                    mode = "opx"
                qubit_class = {{
                    "bias_tee": QdacBiasedFluxTunableTransmon,
                    "qdac": QdacBiasedFixedFrequencyTransmon,
                }}.get(mode, machine.qubit_type)
                transmon = qubit_class(id=qubit_id)
                machine.qubits[qubit_id] = transmon
                machine.qubits[qubit_id].grid_location = _set_default_grid_location(
                    qubit_number, number_of_qubits
                )
                qubit_number += 1
                qdac_trigger_wiring = None
                for line_type, ports in wiring_by_line_type.items():
                    wiring_path = f"#/wiring/{{element_type}}/{{qubit_id}}/{{line_type}}"
                    if line_type == WiringLineType.RESONATOR.value:
                        add_transmon_resonator_component(transmon, wiring_path, ports)
                    elif line_type == WiringLineType.DRIVE.value:
                        add_transmon_drive_component(transmon, wiring_path, ports)
                    elif line_type == WiringLineType.FLUX.value:
                        # D2: only a QDAC-ONLY qubit may not have one.
                        if mode == "qdac":
                            raise ValueError(
                                f"Qubit {{qubit_id}} is QDAC-biased (its QDAC channel "
                                "REPLACES z) and must not also have an OPX 'flux' "
                                "line. If it really is wired through a bias tee, "
                                "move it to QDAC_BIAS_TEE_CHANNELS."
                            )
                        add_transmon_flux_component(transmon, wiring_path, ports)
                    elif line_type == QDAC_TRIGGER_LINE_TYPE:
                        # D3: only a qubit with NO QDAC bias may not have one.
                        if mode == "opx":
                            raise ValueError(
                                f"Qubit {{qubit_id}} has a QDAC trigger "
                                f"('{{QDAC_TRIGGER_LINE_TYPE}}') line but no QDAC "
                                "channel in either mapping."
                            )
                        # Deferred: the loop may reach the trigger before the
                        # bias line it arms exists.
                        qdac_trigger_wiring = (wiring_path, ports)
                    else:
                        raise ValueError(f"Unknown line type: {{line_type}}")

                if mode != "opx":
                    # D4: the field is chosen by mode. `z` for a QDAC-only
                    # qubit (where the bias IS z); the sibling for a bias tee
                    # (where z is the pulse line and must survive).
                    bias_attr = "z" if mode == "qdac" else "qdac_bias"
                    setattr(transmon, bias_attr,
                            QdacBiasLine(channel=channels[qubit_id]))
                    if qdac_trigger_wiring is not None:
                        _attach_bias_trigger(
                            transmon, bias_attr, *qdac_trigger_wiring,
                            qubit_trigger_ports[qubit_id],
                        )
                machine.active_qubit_names.append(transmon.name)

        elif element_type == "qubit_pairs":
            machine.active_qubit_pair_names = []
            for qubit_pair_id, wiring_by_line_type in wiring_by_element.items():
                qc, qt = qubit_pair_id.split("-")
                qt = f"q{{qt}}"
                transmon_pair = machine.qubit_pair_type(
                    id=qubit_pair_id,
                    qubit_control=f"#/qubits/{{qc}}",
                    qubit_target=f"#/qubits/{{qt}}",
                )
                for line_type, ports in wiring_by_line_type.items():
                    wiring_path = f"#/wiring/{{element_type}}/{{qubit_pair_id}}/{{line_type}}"
                    if line_type == WiringLineType.COUPLER.value:
                        add_transmon_pair_tunable_coupler_component(
                            transmon_pair, wiring_path, ports)
                    elif line_type == WiringLineType.CROSS_RESONANCE.value:
                        add_transmon_pair_cross_resonance_component(
                            transmon_pair, wiring_path, ports)
                    elif line_type == WiringLineType.ZZ_DRIVE.value:
                        add_transmon_pair_zz_drive_component(
                            transmon_pair, wiring_path, ports)
                    else:
                        raise ValueError(f"Unknown line type: {{line_type}}")
                    machine.qubit_pairs[transmon_pair.name] = transmon_pair
                    machine.active_qubit_pair_names.append(transmon_pair.name)

        elif element_type == "twpas":
            from quam_builder.architecture.superconducting.components.twpa import TWPA
            from quam_builder.builder.superconducting.add_twpa_component import (
                add_twpa_isolation_component,
                add_twpa_pump_component,
            )

            number_of_twpas = len(wiring_by_element.items())
            twpa_number = 0
            for twpa_id, wiring_by_line_type in wiring_by_element.items():
                twpa = TWPA(id=twpa_id)
                machine.twpas[twpa_id] = twpa
                machine.twpas[twpa_id].grid_location = _set_default_grid_location(
                    twpa_number, number_of_twpas)
                twpa_number += 1
                for line_type, ports in wiring_by_line_type.items():
                    wiring_path = f"#/wiring/{{element_type}}/{{twpa_id}}/{{line_type}}"
                    if line_type == WiringLineType.TWPA_PUMP.value:
                        add_twpa_pump_component(twpa, wiring_path, ports)
                    elif line_type == WiringLineType.TWPA_ISOLATION.value:
                        add_twpa_isolation_component(twpa, wiring_path, ports)
                    else:
                        raise ValueError(f"Unknown line type: {{line_type}}")


def build_quam_qdac_lf(
    machine,
    qdac_only_channels: Dict[str, int],
    bias_tee_channels: Dict[str, int],
    qubit_trigger_ports: Dict[str, str],
    trigger_cabling: Dict[str, Tuple[int, int, int]],
    calibration_db_path: Optional[Union[Path, str]] = None,
):
    """Build a chip whose qubits may be QDAC-only, LF-FEM-only, or both.

    Args:
        qdac_only_channels: qubit id -> QDAC-II channel. The QDAC REPLACES z;
            these qubits have no OPX flux line.
        bias_tee_channels: qubit id -> QDAC-II channel. The QDAC holds the DC
            operating point on a SIBLING field and z stays an OPX flux line
            that plays pulses. Must be disjoint from qdac_only_channels.
        qubit_trigger_ports: qubit id -> ext input ("ext1".."ext4").
        trigger_cabling: ext input -> the (controller, slot, port) digital
            output physically cabled to it. Qubits sharing an ext share it.
    """
    from quam_builder.builder.superconducting.build_quam import (
        add_external_mixers,
        add_octaves,
        add_ports,
    )

    _validate_combined(qdac_only_channels, bias_tee_channels,
                       qubit_trigger_ports, trigger_cabling)
    add_octaves(machine, calibration_db_path=calibration_db_path)
    add_external_mixers(machine)
    add_ports(machine)
    _mark_trigger_ports_shareable(machine, trigger_cabling)
    _add_transmons_combined(machine, qdac_only_channels, bias_tee_channels,
                            qubit_trigger_ports)
    # Reused unchanged: it hides a QdacBiasLine z from add_default_transmon_pulses.
    # A bias-tee qubit's z is a real FluxLine, so it correctly falls through and
    # DOES get its z.operations["const"].
    _add_pulses(machine)

    machine.save()
    return machine
'''


# ---------------------------------------------------------------------------
# The top-level script
# ---------------------------------------------------------------------------

#: Wiring line kind -> (the allocation key it lands under, whether it is a pair
#: line). Only the kinds this generator emits; anything else in the spec is
#: reported as unsupported rather than silently dropped.
_LINE_KINDS = {
    "resonator": ("rr", False),
    "drive": ("xy", False),
    "flux": ("z", False),
    "coupler": ("c", True),
}


def _resonator_groups(spec: dict, allocation: dict) -> list:
    """``[(group, [qubit, ...], (con, slot, out_port, in_port))]`` per feedline.

    Readout is multiplexed: every qubit on one feedline shares a single MW-FEM
    in/out pair, so they are added as ONE `add_resonator_line` call — adding
    them one at a time would ask the allocator for one port each.
    """
    groups: dict = {}
    for line in (spec.get("lines") or []):
        if not isinstance(line, dict) or line.get("line") != "resonator":
            continue
        groups.setdefault(str(line.get("group") or "feedline"), []).append(
            str(line.get("element")))
    out = []
    for name in sorted(groups):
        members = groups[name]
        pin = None
        for q in members:
            chans = (allocation.get(q) or {}).get("rr") or []
            con = slot = op = ip = None
            for ch in chans:
                if not all(isinstance(ch.get(k), int) for k in ("con", "slot", "port")):
                    continue
                con, slot = ch["con"], ch["slot"]
                if ch.get("io_type") == "input":
                    ip = ch["port"]
                else:
                    op = ch["port"]
            if con is not None and op is not None:
                pin = (con, slot, op, ip)
                break
        out.append((name, members, pin))
    return out


def _alloc_keys(element: str) -> list:
    """Every spelling the allocation might key this element under.

    A pair is written ``q1-q2`` in a spec and keyed ``q1-2`` in an allocation —
    the target drops its leading ``q`` (``run_build._quam_pair_id``). Looking
    the element up verbatim therefore MISSES every coupler, and the miss is
    silent: no pin is emitted, the coupler is never added to the connectivity,
    and the chip comes out with no qubit pairs at all while the generator
    reports success. Measured, on a two-pair chip.
    """
    keys = [element]
    if "-" in element:
        control, _, target = element.partition("-")
        short = f"{control}-{target.lstrip('qQ')}"
        long = f"{control}-q{target.lstrip('qQ')}"
        for k in (short, long):
            if k not in keys:
                keys.append(k)
    return keys


def _resolve_alloc(allocation: dict, element: str, key: str) -> tuple | None:
    """``(con, slot, port)`` for one element's line, trying every spelling."""
    for name in _alloc_keys(element):
        for ch in ((allocation.get(name) or {}).get(key) or []):
            if all(isinstance(ch.get(k), int) for k in ("con", "slot", "port")):
                return ch["con"], ch["slot"], ch["port"]
    return None


def _flux_pins(spec: dict, allocation: dict) -> dict:
    """``{element: (kind, con, slot, port)}`` for every flux/coupler line.

    Resolved at EMIT time, from the allocation this spec was actually built
    with. The lab's own generate_quam.py pins only the last few coupler lines
    and lets the rest fall out of allocation ORDER — so adding one qubit flux
    line (which is exactly what a bias-tee qubit does) shifts every unpinned
    coupler one port along. No exception, no warning, and nothing visibly wrong
    until a CZ misbehaves. Pinning all of them removes the hazard rather than
    documenting it.
    """
    out: dict = {}
    for line in (spec.get("lines") or []):
        if not isinstance(line, dict):
            continue
        kind = line.get("line")
        if kind not in ("flux", "coupler"):
            continue
        element = str(line.get("element"))
        found = _resolve_alloc(allocation, element,
                               {"flux": "z", "coupler": "c"}[kind])
        if found:
            out[element] = (kind, *found)
    return out


def _cabling(spec: dict, allocation: dict) -> tuple[dict, dict]:
    """``(ext -> (con, slot, port), qubit -> ext)``.

    Grouped by the qubit's declared ``trigger_port``, so qubits armed on one
    ext input come out on ONE cable — the shape the bench actually has.

    A spec ``trigger_pin`` wins: it records how the bench is cabled TODAY. With
    none, the allocation this spec was built with supplies the port — the same
    order ``script_emitter._qdac_pins`` uses, and for the same reason. Only a
    qubit with neither is left without a cable, and then the emitted file says
    so in a comment instead of writing a table that raises on first use.
    """
    cabling: dict = {}
    per_qubit: dict = {}
    biased = qdac.spec_biased_qubits(spec)
    for qid in sorted(biased, key=lambda q: (len(q), q)):
        fields = biased[qid]
        ext = fields.get("trigger_port") or f"ext_{qid}"
        per_qubit[qid] = ext
        if ext in cabling:
            continue                      # a cable is shared; first one wins
        pin = fields.get("trigger_pin")
        if isinstance(pin, dict) and all(
                isinstance(pin.get(k), int) for k in ("con", "slot", "port")):
            cabling[ext] = (pin["con"], pin["slot"], pin["port"])
            continue
        for ch in ((allocation.get(qid) or {}).get("qt") or []):
            if all(isinstance(ch.get(k), int) for k in ("con", "slot", "port")):
                cabling[ext] = (ch["con"], ch["slot"], ch["port"])
                break
    return cabling, per_qubit


def _generator_source(spec: dict, allocation: dict, chip: str, stamp: str) -> str:
    tee = qdac.spec_bias_tee_qubits(spec)
    biased = qdac.spec_biased_qubits(spec)
    qdac_only = {q: f.get("channel") for q, f in biased.items() if q not in tee}
    tee_ch = {q: f.get("channel") for q, f in tee.items()}
    cabling, per_qubit = _cabling(spec, allocation)
    pins = _flux_pins(spec, allocation)
    feedlines = _resonator_groups(spec, allocation)
    drive: dict = {}
    for line in (spec.get("lines") or []):
        if not isinstance(line, dict) or line.get("line") != "drive":
            continue
        element = str(line.get("element"))
        for ch in ((allocation.get(element) or {}).get("xy") or []):
            if all(isinstance(ch.get(k), int) for k in ("con", "slot", "port")):
                drive[element] = (ch["con"], ch["slot"], ch["port"])
                break
    qubits = [str(q) for q in (spec.get("qubits") or [])]
    net = spec.get("network") or {}
    inst = spec.get("qdac") or {}

    missing = sorted((set(per_qubit.values()) - set(cabling)),
                     key=str)
    lines = []
    w = lines.append
    w('"""%s — QDAC-II + LF-FEM combined generation.' % chip)
    w("")
    w("Generated by QUAM State Manager (%s), from the same spec the wizard" % stamp)
    w("built this chip with. Run it from your quam_config package:")
    w("")
    w("    python %s" % GENERATOR_FILENAME)
    w("")
    w("Three kinds of qubit, and the third is why this file exists:")
    w("")
    w("  QDAC_ONLY_CHANNELS   the QDAC REPLACES z. No OPX flux line.")
    w("  QDAC_BIAS_TEE_CHANNELS  the QDAC holds the DC operating point on a")
    w("                       SIBLING field while z stays an OPX flux line")
    w("                       that plays pulses. Needs")
    w("                       QdacBiasedFluxTunableTransmon (see the README).")
    w("  everything else      an ordinary flux-tunable qubit.")
    w("")
    w("EVERY flux and coupler line below is explicitly pinned. Do not replace")
    w("them with unpinned add_*_lines calls: those cable by allocation ORDER,")
    w("so adding one line silently moves every later one to a different port.")
    w('"""')
    w("")
    w("import os")
    w("import sys")
    w("from pathlib import Path")
    w("")
    w("# Where the chip is written. Set BEFORE quam is imported: Quam.load() and")
    w("# machine.save() both resolve it from the environment, and the lab's own")
    w("# generate_quam.py inherits it from the qualibrate config instead — this")
    w("# script is meant to run unattended, so it states it.")
    w("OUT_DIR = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else (")
    w('    Path(__file__).resolve().parent.parent / "quam_state")')
    w("OUT_DIR.mkdir(parents=True, exist_ok=True)")
    w('os.environ["QUAM_STATE_PATH"] = str(OUT_DIR)')
    w("")
    w("from qualang_tools.wirer import Connectivity, Instruments, allocate_wiring")
    w("from qualang_tools.wirer.wirer.channel_specs import (  # noqa: F401")
    w("    lf_fem_spec, mw_fem_spec, opx_spec, octave_spec,")
    w(")")
    w("")
    w("from quam_config import Quam")
    w("from quam_config.build_quam_qdac_lf import build_quam_qdac_lf")
    w("from quam_config.build_quam_wiring_qdac import build_quam_wiring_qdac")
    w("from quam_config.qdac_components import QdacInstrument")
    w("")
    w(_IMPORT_GATE)
    w("# ======================== EDIT: the three qubit sets ==================")
    w("QUBITS = %s" % pformat(qubits, indent=4, width=88))
    w("")
    w("# qubit id -> QDAC-II channel. The QDAC REPLACES z on these.")
    w("QDAC_ONLY_CHANNELS = %s" % pformat(qdac_only, indent=4, width=88, sort_dicts=True))
    w("")
    w("# qubit id -> QDAC-II channel. BOTH: QDAC DC bias + an LF-FEM z line.")
    w("QDAC_BIAS_TEE_CHANNELS = %s" % pformat(tee_ch, indent=4, width=88, sort_dicts=True))
    w("")
    w("# ==================== EDIT: the physical trigger cabling ==============")
    w("# One OPX digital output feeds one QDAC ext input and arms every channel")
    w("# on it, so several qubits sharing a cable is normal wiring.")
    w("QDAC_TRIGGER_CABLING = %s"
      % pformat({k: list(v) for k, v in sorted(cabling.items())},
                indent=4, width=88, sort_dicts=True))
    w("QDAC_QUBIT_TRIGGER_PORTS = %s"
      % pformat(per_qubit, indent=4, width=88, sort_dicts=True))
    if missing:
        w("")
        w("# NOTE: no cable was resolved for %s." % ", ".join(missing))
        w("#   Those qubits declared no trigger port, or none was allocated.")
        w("#   Fill in a (controller, slot, port) above before running.")
    w("")
    w("QDAC_TRIGGER_CABLING = {k: tuple(v) for k, v in QDAC_TRIGGER_CABLING.items()}")
    w("")
    w("# ============================ EDIT: instrument ========================")
    w("HOST_IP = %r" % (net.get("host") or ""))
    w("CLUSTER_NAME = %r" % (net.get("cluster_name") or ""))
    w("QDAC_IP = %r" % (inst.get("ip_address") or ""))
    w("QDAC_PORT = %r" % (inst.get("port", 5025)))
    w("")
    w("# ================= EDIT: pinned flux + coupler cabling ================")
    w("# element -> (kind, controller, slot, port). Transcribed from the")
    w("# allocation this chip was generated with; edit to match your fridge.")
    w("FLUX_PINS = %s" % pformat({k: list(v) for k, v in sorted(pins.items())},
                                 indent=4, width=88, sort_dicts=True))
    w("")
    w("# Every coupler this chip declares. Checked against FLUX_PINS below, so")
    w("# a pin that failed to resolve is a raise rather than a missing pair.")
    w("PAIR_COUPLERS = %s" % pformat(
        sorted(str(ln.get("element")) for ln in (spec.get("lines") or [])
               if isinstance(ln, dict) and ln.get("line") == "coupler"),
        indent=4, width=88))
    w("")
    w("# ===================== EDIT: readout + drive cabling ==================")
    w("# One multiplexed feedline per group: every qubit on it shares ONE")
    w("# MW-FEM in/out pair, so they go in a single add_resonator_line call.")
    w("# (group, [qubit, ...], (con, slot, out_port, in_port))")
    w("FEEDLINES = %s" % pformat(
        [[g, m, (list(p) if p else None)] for g, m, p in feedlines],
        indent=4, width=88))
    w("")
    w("# qubit -> (con, slot, out_port) for its XY drive.")
    w("DRIVE_PINS = %s" % pformat({k: list(v) for k, v in sorted(drive.items())},
                                  indent=4, width=88, sort_dicts=True))
    w("")
    w("# =============================== run ==================================")
    w("instruments = Instruments()")
    for ctrl in ((spec.get("instruments") or {}).get("controllers") or []):
        con = ctrl.get("con")
        for fem in (ctrl.get("fems") or []):
            slot, kind = fem.get("slot"), fem.get("fem")
            if con is None or slot is None:
                continue
            w('instruments.add_%s_fem(controller=%r, slots=[%r])'
              % ("mw" if kind == "mw" else "lf", con, slot))
    w("")
    w("connectivity = Connectivity()")
    w("")
    w("")
    w("def _idx(qubit_id):")
    w('    """"q13" -> 13. The wirer indexes qubits by number, not by id —')
    w("    the same convention generate_quam.py uses.\"\"\"")
    w('    return int(str(qubit_id).lstrip("qQ"))')
    w("")
    w("")
    w("# Readout: one multiplexed feedline per group.")
    w("for _grp, _members, _pin in FEEDLINES:")
    w("    if _pin is None:")
    w("        raise ValueError(")
    w('            f"feedline {_grp} has no pinned MW-FEM ports. Fill FEEDLINES "')
    w('            "in before running."')
    w("        )")
    w("    connectivity.add_resonator_line(")
    w("        qubits=[_idx(_m) for _m in _members],")
    w("        constraints=mw_fem_spec(con=_pin[0], slot=_pin[1],")
    w("                                out_port=_pin[2], in_port=_pin[3]),")
    w("    )")
    w("")
    w("# XY drive: one port per qubit, pinned. NOT add_qubit_drive_lines(qubits=")
    w("# QUBITS) — that allocates by index order, which need not match the fridge.")
    w("for _q, _pin in sorted(DRIVE_PINS.items()):")
    w("    connectivity.add_qubit_drive_lines(")
    w("        qubits=[_idx(_q)],")
    w("        constraints=mw_fem_spec(con=_pin[0], slot=_pin[1], out_port=_pin[2]),")
    w("    )")
    w("")
    w("# Flux lines: QDAC-ONLY qubits get none; bias-tee qubits DO. That single")
    w("# distinction is the whole difference from the QDAC-only generator.")
    w("for _q in QUBITS:")
    w("    if _q in QDAC_ONLY_CHANNELS:")
    w("        continue")
    w("    _pin = FLUX_PINS.get(_q)")
    w("    if _pin is None:")
    w("        raise ValueError(")
    w('            f"{_q} needs an OPX flux line but has no FLUX_PINS entry. "')
    w('            "Add one rather than letting the allocator choose: an "')
    w('            "unpinned line moves every later coupler to a different "')
    w('            "port, with no error and nothing visibly wrong."')
    w("        )")
    w("    connectivity.add_qubit_flux_lines(")
    w("        qubits=[_idx(_q)],")
    w("        constraints=lf_fem_spec(con=_pin[1], out_slot=_pin[2], out_port=_pin[3]),")
    w("    )")
    w("")
    w("# Coupler flux lines. The check below is the point: this loop iterates")
    w("# FLUX_PINS, so a coupler MISSING from it is simply never added — no")
    w("# error, no pair in the finished chip, and the script still reports")
    w("# success. That happened once, from a pair-id spelling mismatch.")
    w("_pinned_pairs = {_e for _e, _p in FLUX_PINS.items() if _p[0] == \"coupler\"}")
    w("_missing_pairs = [_p for _p in PAIR_COUPLERS if _p not in _pinned_pairs]")
    w("if _missing_pairs:")
    w("    raise ValueError(")
    w('        f"coupler flux line(s) {_missing_pairs} have no FLUX_PINS entry. "')
    w('        "Add them: this loop only builds what FLUX_PINS names, so a gap "')
    w('        "here produces a chip with no qubit pairs and no complaint."')
    w("    )")
    w("for _el, _pin in sorted(FLUX_PINS.items()):")
    w('    if _pin[0] != "coupler":')
    w("        continue")
    w('    _c, _t = _el.split("-", 1)')
    w("    connectivity.add_qubit_pair_flux_lines(")
    w("        qubit_pairs=[(_idx(_c), _idx(_t))],")
    w("        constraints=lf_fem_spec(con=_pin[1], out_slot=_pin[2], out_port=_pin[3]),")
    w("    )")
    w("")
    w("# NOTE: the QDAC trigger ('qt') line is deliberately NOT added to the")
    w("# connectivity. quam_builder's create_wiring has a whitelist of line")
    w("# types and raises on 'qt'; build_quam_wiring_qdac injects those entries")
    w("# after create_wiring has run, from QDAC_TRIGGER_CABLING.")
    w("allocate_wiring(connectivity, instruments)")
    w("")
    w("machine = Quam()")
    w("build_quam_wiring_qdac(")
    w("    connectivity, HOST_IP, CLUSTER_NAME, machine,")
    w("    qubit_trigger_ports=QDAC_QUBIT_TRIGGER_PORTS,")
    w("    trigger_cabling=QDAC_TRIGGER_CABLING,")
    w(")")
    w("")
    w("machine = Quam.load()")
    w("machine.qdac = QdacInstrument(")
    w('    id="qdac", communication_type="Ethernet",')
    w("    ip_address=QDAC_IP, port=QDAC_PORT,")
    w(")")
    w("build_quam_qdac_lf(")
    w("    machine,")
    w("    qdac_only_channels=QDAC_ONLY_CHANNELS,")
    w("    bias_tee_channels=QDAC_BIAS_TEE_CHANNELS,")
    w("    qubit_trigger_ports=QDAC_QUBIT_TRIGGER_PORTS,")
    w("    trigger_cabling=QDAC_TRIGGER_CABLING,")
    w(")")
    w("")
    w("# ---- load-back check -------------------------------------------------")
    w("# A build that reports success is not evidence. If the Union in")
    w("# my_quam.py was not widened, the build still succeeds and save()")
    w("# overwrites state.json - and the failure only appears in the NEXT")
    w("# process, after the good file is gone. So check it here, now.")
    w("_reloaded = Quam.load()")
    w("for _q in QDAC_BIAS_TEE_CHANNELS:")
    w("    _qb = _reloaded.qubits[_q]")
    w('    assert type(_qb).__name__ == "QdacBiasedFluxTunableTransmon", (')
    w('        f"{_q} came back as {type(_qb).__name__}: widen the Union in "')
    w('        "my_quam.Quam.qubits with QdacBiasedFluxTunableTransmon."')
    w("    )")
    w('    assert _qb.z is not None and getattr(_qb.z, "opx_output", None) is not None, (')
    w('        f"{_q} lost its OPX flux line")')
    w('    assert getattr(_qb, "qdac_bias", None) is not None, (')
    w('        f"{_q} lost its QDAC DC bias")')
    w('print(f"OK: {len(_reloaded.qubits)} qubits, "')
    w('      f"{len(QDAC_BIAS_TEE_CHANNELS)} through a bias tee, reloaded clean")')
    w("")
    return "\n".join(lines)


def emit_files(spec: Any, allocation: Any, chip: str, stamp: str) -> dict:
    """``{filename: source}`` — empty unless the spec declares a bias-tee qubit.

    Two files, both meant for the lab's own ``quam_config`` package. They are
    NOT part of the quam_builder-idiom bundle beside them: that one reproduces
    the chip from the stock stack, this one reproduces it through the lab's own
    builder, which is the path their calibration nodes actually load.
    """
    if not wanted(spec):
        return {}
    return {
        BUILDER_FILENAME: _builder_source(stamp),
        GENERATOR_FILENAME: _generator_source(
            spec if isinstance(spec, dict) else {},
            allocation if isinstance(allocation, dict) else {},
            chip, stamp),
    }
