"""Text-only slot gate templates; never import, compile or run a class file.

Future builders use control_pulse, target_pulse and coupler_pulse naming
roles. Shared spectator operations belong to qubits, never to pairs.
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field

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
#: Pulse length step in ns; equal to the step the shared ``length_rule`` enforces (pinned by a test).
LENGTH_STEP_NS = 4
#: Longest wait in ns: 2**24 clock cycles of LENGTH_STEP_NS ns each.
MAX_WAIT_NS = 2 ** 24 * LENGTH_STEP_NS
#: Qubit z operation that spectator levels scale; one name for the pure check and the class.
SPECTATOR_UNIT_OP = "spectator_unit"

KEYS_PROBLEM = "Spectator keys differ: qubits={qubits}, levels={levels}, lengths={lengths}."
OWN_PROBLEM = "Spectator {name!r} is the pair control or target qubit."
LENGTH_PROBLEM = ("Spectator {name!r} length {value!r} must be a finite whole multiple of " + str(LENGTH_STEP_NS)
                  + " ns, at least " + str(MIN_LENGTH_NS) + " ns.")
UNIT_PROBLEM = "Spectator {name!r}: the unit operation " + repr(SPECTATOR_UNIT_OP) + " is missing on that qubit."
UNIT_VALUE_PROBLEM = ("Spectator {name!r}: the unit operation " + repr(SPECTATOR_UNIT_OP)
                      + " amplitude {value!r} must be a finite nonzero number.")
LEVEL_PROBLEM = ("Spectator {name!r} level {value!r} must be a finite number with |level / unit| strictly below "
                 + repr(AMP_SCALE_LIMIT) + ".")
DUPLICATE_PROBLEM = "Spectators {first!r} and {second!r} name the same qubit."
TYPE_PROBLEM = "Spectator {what} must be {kind}; received {value!r}."
SLOT_PROBLEM = "The {slot} slot accepts {kinds}; received {value!r}."

#: Sequence step kinds placed before or after the gate pulses.
STEP_KINDS = ("align", "wait", "xy")
#: Keys a step of each kind uses; any other key is refused.
STEP_KEYS = {"align": ("kind",), "wait": ("kind", "length"), "xy": ("kind", "qubit", "operation")}
#: Qubit roles an xy step may name.
XY_ROLES = ("control", "target")
#: Fixed source text of the xy channel of each role; a role never enters the source any other way.
XY_CHANNEL = {"control": "pair.qubit_control.xy", "target": "pair.qubit_target.xy"}
#: Operation names an xy step may carry: ASCII letters, digits, underscore, dot and dash.
OPERATION_TOKEN = re.compile(r"[A-Za-z0-9_.\-]+")
STEPS_PROBLEM = "The sequence steps {where} the gate must be a list; received {value}."
STEP_TYPE_PROBLEM = "Sequence step {number} {where} the gate must be a mapping with a kind; received {value}."
STEP_KIND_PROBLEM = "Sequence step {number} {where} the gate has the unknown kind {value}; use align, wait or xy."
STEP_KEY_PROBLEM = ("Sequence step {number} {where} the gate has the key {value}, which a step of kind {kind}"
                    " does not use.")
WAIT_PROBLEM = ("Sequence step {number} {where} the gate waits {value} ns, which must be an integer, a multiple of "
                + str(LENGTH_STEP_NS) + " ns, at least " + str(MIN_LENGTH_NS) + " ns and at most " + str(MAX_WAIT_NS)
                + " ns.")
XY_QUBIT_PROBLEM = "Sequence step {number} {where} the gate names the qubit {value}; use control or target."
XY_OP_PROBLEM = ("Sequence step {number} {where} the gate names the operation {value}, which must be a non-empty"
                 " name of letters, digits, underscore, dot or dash.")
XY_MISSING_PROBLEM = ("Sequence step {number} {where} the gate plays {operation!r}, which the {role} qubit xy"
                      " channel lacks.")
#: Docstring lines of a class with steps, after the two lines that list the steps.
STEP_TIMING_NOTE = ("    Steps are statements in this order. An xy play starts when its own channel is free.",
                    "    A wait holds each gate channel for that time from the moment that channel is free; it does not",
                    "    wait for other channels. Steps on different channels overlap unless an align step is between them.",
                    "    Channels that share a core run one after the other, never together.")


@dataclass
class SlotSpec:
    class_name: str
    slots: frozenset[str]
    spectators: bool = False
    before: tuple = field(default_factory=tuple)
    after: tuple = field(default_factory=tuple)

    def __post_init__(self):
        for name in ("before", "after"):
            if isinstance(getattr(self, name), list):
                setattr(self, name, tuple(getattr(self, name)))
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
    for where in ("before", "after"):
        problem = sequence_steps_problem(getattr(spec, where, ()), where)
        if problem:
            problems.append(problem)
    return problems


def _shown(value) -> str:
    """Return short ASCII text of a value for a message; never raises."""
    try:
        if type(value) is int and value.bit_length() > 64:
            return "a whole number too large to show"
        text = ascii(value)
        if not text.isprintable():
            text = ascii(text)
    except Exception:
        return "a value that cannot be shown"
    return text if len(text) <= 60 else text[:57] + "..."


def _wait_length_bad(value) -> bool:
    if type(value) is not int or value > MAX_WAIT_NS or value % LENGTH_STEP_NS or value < MIN_LENGTH_NS:
        return True
    return bool(length_rule({"length": value}))


def sequence_steps_problem(steps, where="before") -> str | None:
    """Check plain sequence steps; return None or one sentence naming the step, never raise.

    Each step is a dict: {"kind": "align"}, {"kind": "wait", "length": ns} or
    {"kind": "xy", "qubit": "control" | "target", "operation": name}. Steps are numbered from 1.
    Only exact types pass: a dict step, a str kind, role and operation, an int length. A subclass,
    a float, a bool and a key the kind does not use are refused.
    """
    try:
        if type(steps) not in (list, tuple):
            return STEPS_PROBLEM.format(where=where, value=_shown(steps))
        for number, step in enumerate(steps, 1):
            if type(step) is not dict or "kind" not in step:
                return STEP_TYPE_PROBLEM.format(number=number, where=where, value=_shown(step))
            kind = step["kind"]
            if type(kind) is not str or kind not in STEP_KINDS:
                return STEP_KIND_PROBLEM.format(number=number, where=where, value=_shown(kind))
            unused = sorted(_shown(key) for key in step if type(key) is not str or key not in STEP_KEYS[kind])
            if unused:
                return STEP_KEY_PROBLEM.format(number=number, where=where, value=unused[0], kind=kind)
            if kind == "wait" and _wait_length_bad(step.get("length")):
                return WAIT_PROBLEM.format(number=number, where=where, value=_shown(step.get("length")))
            if kind == "xy":
                role, operation = step.get("qubit"), step.get("operation")
                if type(role) is not str or role not in XY_ROLES:
                    return XY_QUBIT_PROBLEM.format(number=number, where=where, value=_shown(role))
                if type(operation) is not str or not OPERATION_TOKEN.fullmatch(operation):
                    return XY_OP_PROBLEM.format(number=number, where=where, value=_shown(operation))
        return None
    except Exception as exc:  # never raise on any input shape
        return f"Sequence steps {where} the gate could not be read: {type(exc).__name__}."


def _plain_steps(steps, where):
    """Rebuild steps from plain values for rendering; raise ValueError with a sentence otherwise.

    Rendering reads only what this returns. A length is an int of at most 64 bits or a str, an
    operation is a str, a role is one of XY_ROLES; any other value is replaced by its short text.
    """
    if type(steps) not in (list, tuple):
        raise ValueError(STEPS_PROBLEM.format(where=where, value=_shown(steps)))
    plain = []
    for number, step in enumerate(steps, 1):
        if type(step) is not dict:
            raise ValueError(STEP_TYPE_PROBLEM.format(number=number, where=where, value=_shown(step)))
        kind = step.get("kind")
        if type(kind) is not str or kind not in STEP_KINDS:
            raise ValueError(STEP_KIND_PROBLEM.format(number=number, where=where, value=_shown(kind)))
        if kind == "wait":
            length = step.get("length")
            keep = type(length) is str or (type(length) is int and length.bit_length() <= 64)
            plain.append({"kind": "wait", "length": length if keep else _shown(length)})
        elif kind == "xy":
            role, operation = step.get("qubit"), step.get("operation")
            if type(role) is not str or role not in XY_ROLES:
                raise ValueError(XY_QUBIT_PROBLEM.format(number=number, where=where, value=_shown(role)))
            plain.append({"kind": "xy", "qubit": XY_ROLES[XY_ROLES.index(role)],
                          "operation": operation if type(operation) is str else _shown(operation)})
        else:
            plain.append({"kind": "align"})
    return plain


def _describe(steps):
    """Return the docstring words of plain steps; an unchecked value is named, never shown."""
    words = []
    for step in steps:
        if step["kind"] == "wait":
            words.append(f"wait {step['length']} ns" if type(step["length"]) is int else "wait an unchecked length")
        elif step["kind"] == "xy":
            shown = step["operation"] if OPERATION_TOKEN.fullmatch(step["operation"]) else "an unchecked operation"
            words.append(f"play {shown} on the {XY_ROLES[XY_ROLES.index(step['qubit'])]} xy channel")
        else:
            words.append("align")
    return ", ".join(words) if words else "nothing"


def _step_lines(steps):
    lines = []
    for step in steps:
        if step["kind"] == "align":
            lines.append("        align(*channels)")
        elif step["kind"] == "wait":
            lines.append(f"        wait(int({ascii(step['length'])}) // {LENGTH_STEP_NS}, *channels)")
        else:
            lines.append(f"        {XY_CHANNEL[step['qubit']]}.play({ascii(step['operation'])})")
    return lines


def _step_guards(before, after):
    lines = []
    for where, steps in (("before", before), ("after", after)):
        for number, step in enumerate(steps, 1):
            if step["kind"] == "wait":
                value = ascii(step["length"])
                lines += [f"        if (type({value}) is not int or {value} > {MAX_WAIT_NS}"
                          f" or {value} % {LENGTH_STEP_NS} or {value} < {MIN_LENGTH_NS}):",
                          f"            raise ValueError({WAIT_PROBLEM!r}.format(number={number}, where={where!r},"
                          f" value={ascii(str(step['length']))}))"]
            elif step["kind"] == "xy":
                operation = ascii(step["operation"])
                lines += [f"        if {operation} not in {XY_CHANNEL[step['qubit']]}.operations:",
                          f"            raise ValueError({XY_MISSING_PROBLEM!r}.format(number={number}, where={where!r},"
                          f" role={step['qubit']!r}, operation={operation}))"]
    return lines


def level_scale_problem(level, unit) -> str | None:
    for name, value in (("level", level), ("unit", unit)):
        try:
            valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            return f"Spectator {name} must be a finite number."
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
    """Return the qubit name of "#/qubits/<name>" or of a bare name; None for anything else.

    Whitespace around the whole reference is stripped. The name must be non-empty and contain no
    "/", no "#" and no whitespace. Relative references and other collections are not qubit references.
    """
    if not isinstance(ref, str):
        return None
    ref = ref.strip()
    name = ref[len("#/qubits/"):] if ref.startswith("#/qubits/") else ref
    if not name or "/" in name or "#" in name or any(ch.isspace() for ch in name):
        return None
    return name


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
        seen = {}
        for name, ref in qubits.items():
            qname = qubit_name(ref)
            if qname is None:
                return TYPE_PROBLEM.format(what=f"{name!r} qubit", kind="a reference string or name", value=ref)
            if qname in own:
                return OWN_PROBLEM.format(name=name)
            if qname in seen:
                return DUPLICATE_PROBLEM.format(first=seen[qname], second=name)
            seen[qname] = name
            value = lengths[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value % LENGTH_STEP_NS:
                return LENGTH_PROBLEM.format(name=name, value=value)
            problem = spectator_length_problem(value)
            if problem or value < MIN_LENGTH_NS:
                return LENGTH_PROBLEM.format(name=name, value=value)
            unit = unit_amplitudes.get(name)
            if unit is None:
                return UNIT_PROBLEM.format(name=name)
            if level_scale_problem(0.0, unit) is not None:
                return UNIT_VALUE_PROBLEM.format(name=name, value=unit)
            if level_scale_problem(levels[name], unit):
                return LEVEL_PROBLEM.format(name=name, value=levels[name])
        return None
    except Exception as exc:  # never raise on any input shape
        return f"Spectator rows could not be read: {type(exc).__name__}."


def _channel(slot):
    return "pair.coupler" if slot == "coupler" else f"pair.qubit_{slot}.z"


def _region(spec, before, after):
    slots = tuple(s for s in SLOTS if s in spec.slots)
    steps = bool(before or after)
    lines = [f"    # slots = {slots!r}", f"    # spectators = {spec.spectators!r}"]
    if steps:
        lines += [f"    # before = {ascii(before)}", f"    # after = {ascii(after)}"]
    lines += ['    """Control and target slots accept operation-name strings.',
              '    The coupler slot accepts operation-name strings or inline Pulse objects.']
    if steps:
        lines += [f"    Before the gate pulses: {_describe(before)}.",
                  f"    After the gate pulses: {_describe(after)}.", *STEP_TIMING_NOTE]
    lines += ['    """']
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
                  "        seen = []",
                  "        for name, qubit in qubits.items():",
                  "            if qubit is pair.qubit_control or qubit is pair.qubit_target:",
                  f"                raise ValueError({OWN_PROBLEM!r}.format(name=name))",
                  "            for first, other in seen:",
                  "                if other is qubit:",
                  f"                    raise ValueError({DUPLICATE_PROBLEM!r}.format(first=first, second=name))",
                  "            seen.append((name, qubit))",
                  "            value = lengths[name]",
                  f"            if isinstance(value, bool) or not isinstance(value, (int, float)) or value % {LENGTH_STEP_NS}"
                  f" or value < {MIN_LENGTH_NS}:",
                  f"                raise ValueError({LENGTH_PROBLEM!r}.format(name=name, value=value))",
                  "            if (SPECTATOR_UNIT_OP not in qubit.z.operations",
                  "                    or qubit.z.operations[SPECTATOR_UNIT_OP].amplitude is None):",
                  f"                raise ValueError({UNIT_PROBLEM!r}.format(name=name))",
                  "            unit, level = qubit.z.operations[SPECTATOR_UNIT_OP].amplitude, levels[name]",
                  "            finite = []",
                  "            for number in (unit, level):",
                  "                try:",
                  "                    finite.append(not isinstance(number, bool) and isinstance(number, (int, float))",
                  "                                  and abs(float(number)) < float('inf'))",
                  "                except OverflowError:",
                  "                    finite.append(False)",
                  "            if not finite[0] or unit == 0:",
                  f"                raise ValueError({UNIT_VALUE_PROBLEM!r}.format(name=name, value=unit))",
                  "            try:",
                  f"                in_range = finite[1] and abs(level / unit) < {AMP_SCALE_LIMIT!r}",
                  "            except OverflowError:",
                  "                in_range = False",
                  "            if not in_range:",
                  f"                raise ValueError({LEVEL_PROBLEM!r}.format(name=name, value=level))"]
    lines += _step_guards(before, after)
    lines += _step_lines(before)
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
                  f"                 duration=length_ns // {LENGTH_STEP_NS})"]
    lines += ["        align(*channels)"]
    for s in ("control", "target"):
        lines += [f"        if phase_shift_{s} is not None:",
                  f"            pair.qubit_{s}.xy.frame_rotation_2pi(phase_shift_{s})",
                  f"        elif abs(self.phase_shift_{s}) > 1e-6:",
                  f"            pair.qubit_{s}.xy.frame_rotation_2pi(self.phase_shift_{s})"]
    lines += ["        align(*channels)"]
    lines += _step_lines(after)
    return "\n".join(lines) + "\n"


def render(spec: SlotSpec) -> str:
    problems = validate(spec)
    if problems:
        raise ValueError(" ".join(problems))
    before, after = _plain_steps(spec.before, "before"), _plain_steps(spec.after, "after")
    return (f"{HEADER_MARK}, version {TEMPLATE_VERSION}.\n"
            "from dataclasses import field\nfrom typing import Union\n"
            + ("from qm.qua import align, amp, play, wait\n"
               if any(s["kind"] == "wait" for s in before + after) else "from qm.qua import align, amp, play\n")
            + "from quam.core import quam_dataclass\n"
            "from quam.components.macro import QubitPairMacro\n"
            "from quam.components.pulses import Pulse\n\n"
            + (f'SPECTATOR_UNIT_OP = "{SPECTATOR_UNIT_OP}"\n\n' if spec.spectators else "")
            + f"@quam_dataclass\nclass {spec.class_name}(QubitPairMacro):\n"
            + REGION_BEGIN + "\n" + _region(spec, before, after) + REGION_END + "\n")


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
        before = after = ()
        if lines[start + 3].startswith("    # before = "):
            before = tuple(ast.literal_eval(lines[start + 3].removeprefix("    # before = ")))
            after = tuple(ast.literal_eval(lines[start + 4].removeprefix("    # after = ")))
            if not (before or after):
                raise ValueError("Empty sequence step lists must not be written.")
        spec = SlotSpec(name, frozenset(slots), spectators, before, after)
        if render(spec) != text:
            raise ValueError("Template text does not re-render identically.")
        return Parsed(True, version=version, spec=spec)
    except Exception as exc:
        return Parsed(False, str(exc), version)
