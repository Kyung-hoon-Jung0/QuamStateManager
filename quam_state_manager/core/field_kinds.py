"""Per-field KIND of a quam dataclass field, from its TEXTUAL annotation.

Pure and stdlib-only, so it is testable without quam and usable both in the
SM process and inside the env probe (``generator/probe_roles.py`` loads this
file by path). Every rule here is [derived]: there is no paper behind it, it
is a reading of how quam / quam_builder annotate fields (Union[Pulse, str],
``Pulse = None``, ``dict[str, Pulse]``, ``ScalarInt``, ``Any``, an inline
channel such as ``Optional[XYDriveMW]``).

Kinds:

* ``pointer_pulse``     -- a Pulse-typed field; a pulse object or a reference string
* ``pointer_channel``   -- a Channel-typed field WITH a ``str`` arm (a reference)
* ``pointer_or_value``  -- another component type with a ``str`` arm
* ``dict_of_pointers``  -- a dict / mapping whose VALUES are pulses, or references
* ``dict_of_objects``   -- a dict / mapping whose VALUES are components held inline
                           (no ``str`` arm), e.g. a root's ``qubits`` or ``twpas``
* ``scalar_or_pointer`` -- ScalarInt / ScalarFloat (alias or its expanded QUA-expression
                           union) / Union[int|float, str]
* ``inline_object``     -- a component (channel or other) with no ``str`` arm
* ``unknown``           -- Any, empty or unparseable
* ``plain``             -- a literal (int, float, str, bool, list of literals, ...)

Any value may still be a pointer in a real state.json (pointers and null are
always writable); the kind says what the CLASS declares.
"""
from __future__ import annotations

import re

KINDS = ("pointer_pulse", "pointer_channel", "pointer_or_value", "dict_of_pointers",
         "dict_of_objects",
         "scalar_or_pointer", "inline_object", "unknown", "plain")

# Names recognised without an env. An env probe passes the real names it found
# by MRO through ``pulse_names`` / ``channel_names`` / ``component_names``.
_PULSE_RE = re.compile(r"(^|\.)\w*Pulse$")
_CHANNEL_NAMES = {"Channel", "SingleChannel", "IQChannel", "MWChannel", "InOutIQChannel",
                  "InOutSingleChannel", "InOutMWChannel", "InSingleChannel", "InIQChannel",
                  "InMWChannel", "XYDriveIQ", "XYDriveMW", "ReadoutResonatorIQ",
                  "ReadoutResonatorMW", "ReadoutResonatorSingle", "FluxLine", "TunableCoupler"}
_SCALAR_ALIASES = {"ScalarInt", "ScalarFloat", "ScalarBool"}
_LITERALS = {"int", "float", "str", "bool", "complex", "bytes", "None", "NoneType"}
_NONE = {"None", "NoneType", "type(None)"}
_TYPING_PREFIX = re.compile(r"^(typing|typing_extensions|builtins)\.")
_CLASS_REPR = re.compile(r"<class '([^']+)'>")
_FORWARD_REF = re.compile(r"ForwardRef\(\s*['\"]([^'\"]+)['\"][^)]*\)")


def _split_top(s: str, sep: str) -> list[str]:
    """Split *s* on *sep* at bracket depth 0."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return out


def _norm(s: str) -> str:
    s = _CLASS_REPR.sub(lambda m: m.group(1), s.strip())
    s = _FORWARD_REF.sub(lambda m: m.group(1), s)
    while len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":   # forward-ref string
        s = s[1:-1].strip()
    return _TYPING_PREFIX.sub("", s)


def _head_args(s: str) -> tuple[str, list[str] | None]:
    """``Union[A, B]`` -> ("Union", ["A", "B"]); ``A`` -> ("A", None)."""
    i = s.find("[")
    if i < 0 or not s.endswith("]"):
        return s, None
    return s[:i].strip(), _split_top(s[i + 1:-1], ",")


def _arms(s: str) -> list[str] | None:
    """Flatten Union / Optional / ``|`` into its arms; None if unparseable."""
    s = _norm(s)
    if not s or s.count("[") != s.count("]"):
        return None
    bar = _split_top(s, "|")
    if len(bar) > 1:
        out: list[str] = []
        for b in bar:
            sub = _arms(b)
            if sub is None:
                return None
            out += sub
        return out
    head, args = _head_args(s)
    short = head.rsplit(".", 1)[-1]
    if short == "Union" and args:
        out = []
        for a in args:
            sub = _arms(a)
            if sub is None:
                return None
            out += sub
        return out
    if short == "Optional" and args and len(args) == 1:
        sub = _arms(args[0])
        return None if sub is None else sub + ["None"]
    return [s]


def _short(name: str) -> str:
    return _head_args(name)[0].rsplit(".", 1)[-1]


def _role_of(arm: str, pulses: set, channels: set, components: set) -> str:
    """One arm -> pulse / channel / component / scalar_alias / str / literal / any / other."""
    head, _ = _head_args(arm)
    short = head.rsplit(".", 1)[-1]
    if short == "Any":
        return "any"
    if short in _SCALAR_ALIASES:
        return "scalar_alias"
    if head.startswith(("qm.qua.", "qm.qua._")) or re.match(r"_?Qua[A-Z]", short):
        return "qua"
    if short == "str":
        return "str"
    if short in pulses or (not pulses and _PULSE_RE.search(short)):
        return "pulse"
    if short in channels or short in _CHANNEL_NAMES:
        return "channel"
    if short in components:
        return "component"
    if short in _LITERALS:
        return "literal"
    if short in ("list", "List", "tuple", "Tuple", "Sequence", "set", "Set", "Literal"):
        return "literal"
    if short in ("dict", "Dict", "Mapping", "MutableMapping"):
        return "mapping"
    return "other"


def classify(annotation, default=None, *, has_default: bool = False,
             pulse_names=(), channel_names=(), component_names=()) -> dict:
    """Classify one field. Returns ``{"kind", "optional", "targets"}``.

    *annotation* is the textual annotation (``dataclasses.Field.type`` as a
    string, or ``str()`` of a typing object). ``default=None`` with
    *has_default* marks the field optional even when the annotation omits it.
    The name sets let an env probe pass the real class names it saw by MRO;
    without them a name ending in ``Pulse`` counts as a pulse.
    """
    pulses = {_short(n) for n in pulse_names}
    channels = {_short(n) for n in channel_names}
    comps = {_short(n) for n in component_names}
    text = annotation if isinstance(annotation, str) else (
        "" if annotation is None else str(annotation))
    arms = _arms(text) if text.strip() else None
    if arms is None:
        return {"kind": "unknown", "optional": bool(has_default and default is None),
                "targets": []}
    optional = any(_norm(a) in _NONE for a in arms) or (has_default and default is None)
    real = [a for a in arms if _norm(a) not in _NONE]
    roles = [_role_of(a, pulses, channels, comps) for a in real]
    targets = [_short(a) for a, r in zip(real, roles) if r in ("pulse", "channel", "component")]
    res = {"optional": bool(optional), "targets": targets}
    if not real or "any" in roles:
        return {**res, "kind": "unknown"}
    has_str = "str" in roles
    if "pulse" in roles:
        return {**res, "kind": "pointer_pulse"}
    if "channel" in roles:
        return {**res, "kind": "pointer_channel" if has_str else "inline_object"}
    if "component" in roles:
        return {**res, "kind": "pointer_or_value" if has_str else "inline_object"}
    if "mapping" in roles and len(real) >= 1:
        m = real[roles.index("mapping")]
        _, args = _head_args(_norm(m))
        if args and len(args) == 2:
            inner = classify(args[1], pulse_names=pulse_names, channel_names=channel_names,
                             component_names=component_names)
            if inner["kind"] in ("pointer_pulse", "pointer_channel", "pointer_or_value"):
                return {**res, "kind": "dict_of_pointers", "targets": inner["targets"]}
            if inner["kind"] == "inline_object":
                # Values are the objects themselves (qubits, twpas, octaves), not refs.
                return {**res, "kind": "dict_of_objects", "targets": inner["targets"]}
            if inner["kind"] == "unknown":
                return {**res, "kind": "unknown"}
        elif not args:
            return {**res, "kind": "unknown"}
        return {**res, "kind": "plain"}
    if "qua" in roles and all(r in ("qua", "literal", "str") for r in roles):
        # the expanded form of ScalarInt / ScalarFloat (= Scalar[int]): a QUA
        # expression or a number -- treated like the alias
        return {**res, "kind": "scalar_or_pointer"}
    if "scalar_alias" in roles:
        return {**res, "kind": "scalar_or_pointer"}
    if has_str and any(r == "literal" and _short(a) in ("int", "float")
                       for a, r in zip(real, roles)):
        return {**res, "kind": "scalar_or_pointer"}
    if "other" in roles:
        return {**res, "kind": "unknown"}
    return {**res, "kind": "plain"}
