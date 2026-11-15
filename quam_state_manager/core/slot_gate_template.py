"""Text-only slot gate templates; never import, compile or run a class file.

Future builders use control_pulse, target_pulse and coupler_pulse naming
roles. Shared spectator operations belong to qubits, never to pairs.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass

from quam_state_manager.core.cz_gate_build import length_rule
from quam_state_manager.core.pulse_class_template import check_class_name

TEMPLATE_VERSION = 1
HEADER_MARK = "# Made by QUAM State Manager slot gate class template"
REGION_BEGIN = "    # >>> SM slot template region >>>"
REGION_END = "    # <<< SM slot template region end <<<"
SLOTS = ("control", "target", "coupler")
PRESETS = {
    "fixed_qubit_tunable_coupler": frozenset({"coupler"}),
    "tunable_qubit_fixed_coupler": frozenset({"control", "target"}),
    "tunable_qubit_tunable_coupler": frozenset(SLOTS),
}

#: amp() scale limit for a spectator level: |level / unit| must stay strictly below this.
#: Source: the open range (-2, 2) for amp() was confirmed by the user (2026-11-14);
#: a commented-out line in the docs source quotes the fixed-point overflow: "Indicates an overflow error in
#: `* amp()`, for values outside -8 to 8 - 2^-28" (documentation-website
#: docs/docs/docs/Guides/error.md line 174, starting with %, not published).
AMP_SCALE_LIMIT = 2.0

#: Shortest pulse length in ns; equal to the edge the shared ``length_rule`` enforces (pinned by a test).
MIN_LENGTH_NS = 16
#: Qubit z operation that spectator levels scale; one name for the pure check and the class.
SPECTATOR_UNIT_OP = "spectator_unit"

KEYS_PROBLEM = "Spectator keys differ: qubits={qubits}, levels={levels}, lengths={lengths}."
OWN_PROBLEM = "Spectator {name!r} is the pair control or target qubit."
LENGTH_PROBLEM = ("Spectator {name!r} length {value!r} must be a finite whole multiple of 4 ns, at least "
                  + str(MIN_LENGTH_NS) + " ns.")
UNIT_PROBLEM = "Spectator {name!r}: the unit operation " + repr(SPECTATOR_UNIT_OP) + " is missing on that qubit."
TYPE_PROBLEM = "Spectator {what} must be {kind}; received {value!r}."
SLOT_PROBLEM = "The {slot} slot accepts {kinds}; received {value!r}."


@dataclass
class SlotSpec:
    class_name: str
    slots: frozenset[str]
    spectators: bool = False

    def __post_init__(self):
        if isinstance(self.slots, (set, frozenset, tuple, list)) and all(s in SLOTS for s in self.slots) and len(set(self.slots)) == len(self.slots):
            self.slots = frozenset(self.slots)


@dataclass
class Parsed:
    ok: bool
    why: str | None = None
    version: int | None = None
    spec: SlotSpec | None = None

    @property
    def editable(self):
        return self.ok

    @property
    def reason(self):
        return self.why


def validate(spec: SlotSpec) -> list[str]:
    problems = []
    try:
        check_class_name(spec.class_name)
    except ValueError as exc:
        problems.append(str(exc))
    if not isinstance(spec.slots, (set, frozenset, tuple, list)) or not spec.slots:
        problems.append("Select at least one slot.")
    elif any(slot not in SLOTS for slot in spec.slots) or len(set(spec.slots)) != len(spec.slots):
        problems.append("Slots must be a subset of control, target and coupler without duplicates.")
    if type(spec.spectators) is not bool:
        problems.append("Spectators must be a boolean.")
    if spec.class_name in {"QubitPairMacro", "Pulse", "Union", "SPECTATOR_UNIT_OP"}:
        problems.append("Class name conflicts with a template import.")
    return problems


def level_scale_problem(level, unit) -> str | None:
    for name, value in (("level", level), ("unit", unit)):
        try:
            valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            return f"Spectator {name} must be a finite number, not a boolean."
    if unit == 0:
        return "Spectator unit amplitude must be nonzero."
    try:
        ratio = level / unit
    except OverflowError:
        return "Spectator level scale is not finite."
    if not math.isfinite(ratio) or not abs(ratio) < AMP_SCALE_LIMIT:
        return f"Spectator level scale must have magnitude strictly below {AMP_SCALE_LIMIT}."
    return None


def reachable_level(unit) -> float:
    """Return the excluded boundary in V for a valid finite unit amplitude."""
    return AMP_SCALE_LIMIT * abs(unit)


def spectator_length_problem(length_ns) -> str | None:
    """Use the shared pulse length rule, while requiring a numeric value."""
    if isinstance(length_ns, bool) or not isinstance(length_ns, (int, float)):
        return "Spectator length must be a number of ns."
    return length_rule({"length": length_ns})


def qubit_name(ref):
    """Turn a reference string such as "#/qubits/<name>" or a bare name into the name; None if not a string."""
    if not isinstance(ref, str) or not ref.strip():
        return None
    return ref.strip().rstrip("/").rsplit("/", 1)[-1]


def spectator_rows_problem(qubits, levels, lengths, control=None, target=None, unit_amplitudes=None):
    """Check plain spectator rows as the state holds them; return None or one sentence, never raise.

    qubits maps key -> reference string or qubit name; levels and lengths map key -> number;
    control and target are reference strings or names; unit_amplitudes maps key -> the unit
    operation amplitude (None or a missing key means the unit operation is missing).
    """
    try:
        for what, value in (("qubits", qubits), ("levels", levels), ("lengths", lengths)):
            if not isinstance(value, dict):
                return TYPE_PROBLEM.format(what=what, kind="a mapping", value=value)
        if unit_amplitudes is None:
            unit_amplitudes = {}
        if not isinstance(unit_amplitudes, dict):
            return TYPE_PROBLEM.format(what="unit amplitudes", kind="a mapping", value=unit_amplitudes)
        if set(qubits) != set(levels) or set(qubits) != set(lengths):
            return KEYS_PROBLEM.format(qubits=sorted(map(str, qubits)), levels=sorted(map(str, levels)),
                                       lengths=sorted(map(str, lengths)))
        own = set()
        for role, ref in (("control", control), ("target", target)):
            if ref is None:
                continue
            if qubit_name(ref) is None:
                return TYPE_PROBLEM.format(what=role, kind="a reference string or name", value=ref)
            own.add(qubit_name(ref))
        for name, ref in qubits.items():
            qname = qubit_name(ref)
            if qname is None:
                return TYPE_PROBLEM.format(what=f"{name!r} qubit", kind="a reference string or name", value=ref)
            if qname in own:
                return OWN_PROBLEM.format(name=name)
            value = lengths[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value % 4:
                return LENGTH_PROBLEM.format(name=name, value=value)
            problem = spectator_length_problem(value)
            if problem or value < MIN_LENGTH_NS:
                return LENGTH_PROBLEM.format(name=name, value=value)
            unit = unit_amplitudes.get(name)
            if unit is None:
                return UNIT_PROBLEM.format(name=name)
            problem = level_scale_problem(levels[name], unit)
            if problem:
                return f"Spectator {name!r} level {levels[name]!r}: {problem}"
        return None
    except Exception as exc:  # never raise on any input shape
        return f"Spectator rows could not be read: {type(exc).__name__}."


def _channel(slot):
    return "pair.coupler" if slot == "coupler" else f"pair.qubit_{slot}.z"


def _region(spec):
    slots = tuple(s for s in SLOTS if s in spec.slots)
    lines = [f"    # slots = {slots!r}", f"    # spectators = {spec.spectators!r}",
             '    """Control and target slots accept operation-name strings.',
             '    The coupler slot accepts operation-name strings or inline Pulse objects.',
             '    """']
    lines += [f"    {s}_flux_pulse: Union[Pulse, str]" for s in slots]
    lines += ["    phase_shift_control: float = 0.0", "    phase_shift_target: float = 0.0",
              "    duration: int = None", "    extras: dict = field(default_factory=dict)",
              "    fidelity: dict = field(default_factory=dict)"]
    if spec.spectators:
        lines += [f"    spectator_{key}: dict = field(default_factory=dict)"
                  for key in ("qubits", "levels", "lengths")]
    for s in slots:
        lines += ["", "    @property", f"    def {s}_flux_pulse_label(self):",
                  "        pair = self.qubit_pair", f"        pulse = self.{s}_flux_pulse",
                  "        if isinstance(pulse, str):",
                  f"            pulse = {_channel(s)}.operations[pulse]",
                  "        if pulse.id is not None:", "            return pulse.id",
                  "        return pulse.parent.get_attr_name(pulse)"]
    args = "".join(f"amplitude_scale_{s}=None, " for s in slots)
    lines += ["", f"    def apply(self, *, {args}duration=None,",
              "              phase_shift_control=None, phase_shift_target=None, **kwargs):",
              '        """Slot duration is in clock cycles; spectator lengths are in ns."""',
              "        pair = self.qubit_pair",
              "        channels = [ch.name for q in (pair.qubit_control, pair.qubit_target)",
              "                    for ch in q.channels.values()]"]
    if "coupler" in slots:
        lines += ["        channels.append(pair.coupler.name)"]
    if spec.spectators:
        lines += ["        channels.extend(q.z.name for q in self.spectator_qubits.values())"]
    for s in slots:
        kinds = "operation-name strings or inline Pulse objects" if s == "coupler" else "operation-name strings"
        types = "(str, Pulse)" if s == "coupler" else "str"
        lines += [f"        if not isinstance(self.{s}_flux_pulse, {types}):",
                  f"            raise ValueError({SLOT_PROBLEM!r}.format(slot={s!r}, kinds={kinds!r}, value=self.{s}_flux_pulse))"]
    if spec.spectators:
        lines += ["        qubits, levels, lengths = self.spectator_qubits, self.spectator_levels, self.spectator_lengths",
                  "        if set(qubits) != set(levels) or set(qubits) != set(lengths):",
                  f"            raise ValueError({KEYS_PROBLEM!r}.format(qubits=sorted(qubits), levels=sorted(levels), lengths=sorted(lengths)))",
                  "        for name, qubit in qubits.items():",
                  "            if qubit is pair.qubit_control or qubit is pair.qubit_target:",
                  f"                raise ValueError({OWN_PROBLEM!r}.format(name=name))",
                  "            value = lengths[name]",
                  "            if isinstance(value, bool) or not isinstance(value, (int, float)) or value % 4"
                  f" or value < {MIN_LENGTH_NS}:",
                  f"                raise ValueError({LENGTH_PROBLEM!r}.format(name=name, value=value))"]
    lines += ["        align(*channels)"]
    for s in slots:
        flag = ", validate=False" if s == "coupler" else ""
        lines += [f"        {_channel(s)}.play(self.{s}_flux_pulse_label{flag},",
                  f"            amplitude_scale=amplitude_scale_{s},",
                  "            duration=self.duration if duration is None else duration)"]
    if spec.spectators:
        lines += ["        for name, qubit in self.spectator_qubits.items():",
                  "            level = self.spectator_levels[name]",
                  "            length_ns = int(self.spectator_lengths[name])",
                  "            unit = qubit.z.operations[SPECTATOR_UNIT_OP].amplitude",
                  "            play(SPECTATOR_UNIT_OP * amp(level / unit), qubit.z.name,",
                  "                 duration=length_ns // 4)"]
    lines += ["        align(*channels)"]
    for s in ("control", "target"):
        lines += [f"        if phase_shift_{s} is not None:",
                  f"            pair.qubit_{s}.xy.frame_rotation_2pi(phase_shift_{s})",
                  f"        elif abs(self.phase_shift_{s}) > 1e-6:",
                  f"            pair.qubit_{s}.xy.frame_rotation_2pi(self.phase_shift_{s})"]
    lines += ["        align(*channels)"]
    return "\n".join(lines) + "\n"


def render(spec: SlotSpec) -> str:
    problems = validate(spec)
    if problems:
        raise ValueError(" ".join(problems))
    return (f"{HEADER_MARK}, version {TEMPLATE_VERSION}.\n"
            "from dataclasses import field\nfrom typing import Union\n"
            "from qm.qua import align, amp, play\nfrom quam.core import quam_dataclass\n"
            "from quam.components.macro import QubitPairMacro\n"
            "from quam.components.pulses import Pulse\n\n"
            + (f'SPECTATOR_UNIT_OP = "{SPECTATOR_UNIT_OP}"\n\n' if spec.spectators else "")
            + f"@quam_dataclass\nclass {spec.class_name}(QubitPairMacro):\n"
            + REGION_BEGIN + "\n" + _region(spec) + REGION_END + "\n")


def parse(text: str) -> Parsed:
    """Read only canonical template text, without executing it; never raises."""
    version = None
    try:
        match = re.fullmatch(re.escape(HEADER_MARK) + r", version (\d+)\.", text.split("\n", 1)[0])
        version = int(match[1]) if match else None
        if version != TEMPLATE_VERSION:
            raise ValueError("Missing or unsupported template version.")
        lines = text.split("\n")
        if lines.count(REGION_BEGIN) != 1 or lines.count(REGION_END) != 1:
            raise ValueError("Missing or duplicated template markers.")
        start, end = lines.index(REGION_BEGIN), lines.index(REGION_END)
        if end <= start:
            raise ValueError("Template markers are out of order.")
        name = re.fullmatch(r"class (\w+)\(QubitPairMacro\):", lines[start - 1])[1]
        slots = ast.literal_eval(lines[start + 1].removeprefix("    # slots = "))
        spectators = ast.literal_eval(lines[start + 2].removeprefix("    # spectators = "))
        spec = SlotSpec(name, frozenset(slots), spectators)
        if render(spec) != text:
            raise ValueError("Template text does not re-render identically.")
        return Parsed(True, version=version, spec=spec)
    except Exception as exc:
        return Parsed(False, str(exc), version)
