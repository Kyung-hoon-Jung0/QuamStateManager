"""Pulse class template: render, parse and rewrite a pulse class source file.

A class made here is a plain Python file: a header that says SM made it (with
the template version), then a quam ``Pulse`` subclass whose fields and
waveform formula sit between two marker lines. The form rewrites ONLY the
text between the markers; every byte outside them is kept as the user left
it. A file whose markers are missing, duplicated, out of order, or whose
region no longer parses as the template wrote it is "code view only": SM
never rewrites it, the user edits it in the code view.

This module is text only. It never imports, compiles or runs a class file:
the field list a screen shows comes from the lab-side runner
(``generator/run_pulse_draft.py``), which imports the file in the user's env.
Parsing the region here is only for rewriting it from the form.

The formula is a numpy expression evaluated once per waveform with these
names bound: ``t`` (sample times in ns, ``n`` values spaced ``dt`` apart),
``dt`` (ns per sample, from the port's sampling rate), ``n`` (sample count,
``round(length / dt)``), ``np``, and every field by its own name. Because
``n`` comes from the port's rate, a 2 GSa/s port gives twice as many samples
as the length in ns.
"""

from __future__ import annotations

import ast
import functools
import io
import keyword
import math
import re
import tokenize
from dataclasses import dataclass, field, fields, is_dataclass

TEMPLATE_VERSION = 1

HEADER_MARK = "# Made by QUAM State Manager pulse class template"
REGION_BEGIN = "    # >>> SM template region: fields and formula. The form rewrites the lines up to the end marker. >>>"
REGION_END = "    # <<< SM template region end <<<"

#: field types the form offers (annotation text -> Python type of the default)
FIELD_TYPES = {"float": float, "int": int, "bool": bool, "str": str}

#: names the quam Pulse base already owns (``length`` may be re-declared with
#: a default; the rest may not be shadowed). They are listed here because SM
#: never imports quam; a lab-env test checks the list against the installed
#: ``quam.components.pulses.Pulse``.
PULSE_BASE_NAMES = frozenset({
    "id", "digital_marker", "operation", "channel", "name", "parent",
    "apply_to_config", "calculate_waveform", "config_settings", "digital_marker_name",
    "get_attr_name", "get_attrs", "get_raw_value", "get_reference", "get_root",
    "get_unreferenced_value", "inferred_id", "iterate_components", "play",
    "print_summary", "pulse_name", "set_at_reference", "to_dict", "waveform_function",
    "waveform_name"})
#: the base's names, the formula's own names and the type names (a field
#: called ``float`` would rebind the annotation for every later field). The
#: names the template itself writes are added by :func:`reserved_field_names`.
RESERVED = set(PULSE_BASE_NAMES) | {"t", "dt", "n", "np", "self"} | set(FIELD_TYPES)

#: Windows device names: a folder or file with one of these names (any case,
#: any extension) cannot be made, so a class may not be called that
WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)})

#: pulse length: whole ns, divisible by 4 (the QM config rule, docs
#: Introduction/config.md), and at least 16 ns. The 16 ns floor is the
#: pre-3.3 QOP minimum (docs Guides/timing_in_qua.md; QOP 3.3 and later allow
#: 4 ns from the config); it is kept as a conservative floor that is valid on
#: every QOP version
LENGTH_MIN_NS = 16
LENGTH_STEP_NS = 4
LENGTH_RULE_LINE = (f"Field 'length': the default must be a whole number of ns, at least "
                    f"{LENGTH_MIN_NS} and divisible by {LENGTH_STEP_NS}. The QM config needs a "
                    f"length divisible by {LENGTH_STEP_NS}; {LENGTH_MIN_NS} ns is a conservative "
                    "floor that works on every QOP version")

CODE_VIEW_ONLY_LINE = ("The class structure can only be edited in the code view: "
                       "the template markers in this file were changed by hand.")
REGION_CHANGED_LINE = ("The class structure can only be edited in the code view: "
                       "the lines between the template markers were changed by hand.")
CRLF_LINE = ("The class structure can only be edited in the code view: "
             "this file uses Windows (or other carriage return) line endings, and the "
             "form rewrites only files with plain line endings.")

#: the parent the reserved-name list is written for
PULSE_PARENT = "quam.components.pulses.Pulse"
#: what validate says when the parent is another class whose attribute names
#: it was not given: the reserved-name check covered only the Pulse base
PARENT_NOT_CHECKED_LINE = ("Field names were checked only against the quam Pulse base: "
                           "the attributes of the parent class {parent} were not checked. "
                           "A field with the same name as an attribute of that parent "
                           "shows up as an error only when the class is built.")

#: characters Python or ``str.splitlines`` treat as a line break; a formula may
#: use only the plain newline, which the template splits on
_FORMULA_BREAKS = frozenset("\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


class TemplateError(ValueError):
    """The form's input cannot make a valid class (shown to the user as is)."""


@dataclass
class FieldSpec:
    name: str
    type: str = "float"
    default: object = None
    unit: str = ""


@dataclass
class ClassSpec:
    class_name: str
    fields: list = field(default_factory=list)   # list[FieldSpec]
    formula: str = ""
    parent: str = PULSE_PARENT


@dataclass
class Parsed:
    """What :func:`parse` found. ``editable`` False means code view only."""
    editable: bool
    reason: str | None = None
    version: int | None = None
    made_by_template: bool = False
    spec: ClassSpec | None = None
    region: tuple | None = None   # (start, end) byte offsets of the region body


# ---------------------------------------------------------------- validation

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_ident(name: str, what: str) -> None:
    if not isinstance(name, str) or not _IDENT.fullmatch(name) or keyword.iskeyword(name):
        raise TemplateError(f"{what} {name!r} is not a valid Python name")


def taken_class_names() -> frozenset:
    """Class names a new class may not take: every class the pulse catalog
    knows (casefolded), so the new class can never shadow one of them."""
    from quam_state_manager.core import pulse_catalog as pc
    names = {s.qclass.rsplit(".", 1)[-1] for s in pc.PULSE_CATALOG.values()}
    names |= {q.rsplit(".", 1)[-1] for q in getattr(pc, "_QCLASS_ALIASES", {})}
    names.add("Pulse")
    return frozenset(n.casefold() for n in names)


def check_class_name(name: str) -> None:
    """Raise :class:`TemplateError` when *name* cannot be a new class name."""
    _check_ident(name, "Class name")
    if not name[0].isupper():
        raise TemplateError(f"Class name {name!r} must start with a capital letter")
    if name.casefold() in WINDOWS_DEVICE_NAMES:
        raise TemplateError(f"Class name {name!r} is a reserved Windows device name; "
                            "choose another name")


def validate(spec: ClassSpec, *, parent_names=None) -> list:
    """Raise :class:`TemplateError` naming the first problem in *spec*.

    *parent_names* are the parent's attribute names as
    :func:`public_attribute_names` derived them where the parent can be
    imported (the lab env); a field may not take one of them. Returns the
    notes on what the check could not cover: when the parent is not the quam
    Pulse and no *parent_names* were given, one line
    (:data:`PARENT_NOT_CHECKED_LINE`) says the check covered only the Pulse
    base; otherwise an empty list."""
    check_class_name(spec.class_name)
    if "." not in spec.parent:
        raise TemplateError(f"Parent class {spec.parent!r} must be a full module path")
    for part in spec.parent.split("."):
        _check_ident(part, "Parent class part")
    parent_leaf = spec.parent.rsplit(".", 1)[-1]
    if spec.class_name.casefold() == parent_leaf.casefold():
        raise TemplateError(f"Class name {spec.class_name!r} is the name of its parent class; "
                            "choose another name")
    if spec.class_name.casefold() in taken_class_names():
        raise TemplateError(f"Class name {spec.class_name!r} is already a known pulse class; "
                            "choose another name")
    notes = []
    extra = frozenset()
    if parent_names is not None:
        extra = frozenset(parent_names) - {"length"}
    elif spec.parent != PULSE_PARENT:
        notes.append(PARENT_NOT_CHECKED_LINE.format(parent=spec.parent))
    seen = set()
    for f in spec.fields:
        _check_ident(f.name, "Field name")
        if f.name.startswith("_"):
            raise TemplateError(f"Field name {f.name!r} is reserved: a field name may not "
                                "start with '_'")
        if f.name in reserved_field_names():
            raise TemplateError(f"Field name {f.name!r} is reserved")
        if f.name in extra:
            raise TemplateError(f"Field name {f.name!r} is reserved: the parent class "
                                f"{parent_leaf} already has it")
        if f.name in seen:
            raise TemplateError(f"Field {f.name!r} appears twice")
        seen.add(f.name)
        if f.type not in FIELD_TYPES:
            raise TemplateError(f"Field {f.name!r}: type {f.type!r} is not one of "
                                + ", ".join(FIELD_TYPES))
        want = FIELD_TYPES[f.type]
        d = f.default
        ok = (isinstance(d, bool) if want is bool else
              isinstance(d, want) and not isinstance(d, bool) if want is int else
              isinstance(d, (int, float)) and not isinstance(d, bool) if want is float else
              isinstance(d, str))
        if not ok:
            article = "an" if f.type == "int" else "a"
            raise TemplateError(f"Field {f.name!r}: default {d!r} is not {article} {f.type}")
        try:
            finite = want is not float or math.isfinite(d)
        except OverflowError:      # an int too large for a float (10**400)
            finite = False
        if not finite:
            raise TemplateError(f"Field {f.name!r}: the default must be a finite number, "
                                "not nan or inf")
        if want is str and _UNIT_MARK.strip() in d:
            raise TemplateError(f"Field {f.name!r}: the default may not contain "
                                f"{_UNIT_MARK.strip()!r}")
        if f.name == "length" and (f.type != "int" or d < LENGTH_MIN_NS
                                   or d % LENGTH_STEP_NS):
            raise TemplateError(LENGTH_RULE_LINE)
        # isprintable() refuses every line break Python honours, "\r" too
        if not isinstance(f.unit, str) or not f.unit.isprintable() or "#" in f.unit:
            raise TemplateError(f"Field {f.name!r}: unit must be one line of plain "
                                "text without '#'")
    if "amplitude" not in seen:
        raise TemplateError("A pulse class made from the template needs an 'amplitude' field")
    if "length" not in seen:
        raise TemplateError("A pulse class made from the template needs a 'length' field (ns)")
    if not isinstance(spec.formula, str) or not spec.formula.strip():
        raise TemplateError("The waveform formula is empty")
    if any(ch in _FORMULA_BREAKS for ch in spec.formula):
        raise TemplateError("The waveform formula may use only plain line breaks")
    try:
        tree = ast.parse(spec.formula.strip(), mode="eval")
    except SyntaxError as exc:
        raise TemplateError(f"The waveform formula is not a valid expression: {exc.msg}") from None
    del tree
    if _has_multi_line_string(spec.formula.strip()):
        raise TemplateError("The waveform formula may not hold a string that spans "
                            "lines: the template indents every formula line, which "
                            "would change the string")
    for mark in (REGION_BEGIN, REGION_END):
        if mark.strip() in spec.formula:
            raise TemplateError("The waveform formula may not contain the SM template "
                                "marker text")
    return notes


def public_attribute_names(cls) -> frozenset:
    """Public attributes a field may not shadow, excluding plain dataclass defaults
    and ``length`` for other parents. Standard library only; the quam Pulse
    keeps :data:`PULSE_BASE_NAMES` because SM never imports quam."""
    import inspect

    if cls.__name__ == "Pulse" and (cls.__module__ == "quam.components.pulses"
                                    or cls.__module__.startswith("quam.components.pulses.")):
        return PULSE_BASE_NAMES
    field_names = set()
    if is_dataclass(cls):
        for field in fields(cls):
            default = inspect.getattr_static(cls, field.name, None)
            # An unannotated method override is not a new field declaration.
            owner = next((base for base in cls.__mro__
                          if field.name in vars(base).get("__annotations__", {})
                          and is_dataclass(base)), cls)
            declared_field = next(f for f in fields(owner) if f.name == field.name)
            is_declared_default = default is declared_field.default
            is_field_slot = inspect.ismemberdescriptor(default) and any(
                field.name in vars(base).get("__slots__", ())
                and vars(base).get(field.name) is default
                for base in cls.__mro__)
            if (is_declared_default or is_field_slot
                    or not any(hasattr(type(default), method)
                               for method in ("__get__", "__set__", "__delete__"))):
                field_names.add(field.name)
    return frozenset(n for n in dir(cls) if not n.startswith("_")) - {"length"} - field_names


def _has_multi_line_string(formula: str) -> bool:
    """True when a string literal (or f-string) in *formula* spans lines."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO("(\n" + formula + "\n)\n").readline))
    except (tokenize.TokenError, SyntaxError) as exc:
        # not expected after ast.parse accepted the formula; if it happens the
        # strings cannot be checked, so the formula is refused, never passed
        raise TemplateError("The waveform formula could not be checked for strings "
                            f"that span lines: {exc}") from None
    starts = []
    for tok in toks:
        name = tokenize.tok_name[tok.type]
        if name == "STRING" and tok.start[0] != tok.end[0]:
            return True
        if name == "FSTRING_START":
            starts.append(tok.start[0])
        elif name == "FSTRING_END" and starts and starts.pop() != tok.end[0]:
            return True
    return False


@functools.lru_cache(maxsize=1)
def reserved_field_names() -> frozenset:
    """Every name a field may not take: :data:`RESERVED` plus each method the
    template writes into the class and each ``self.`` attribute its code reads
    (``length`` excepted), taken from the rendered template text itself."""
    skeleton = _file_text("TemplateProbe", PULSE_PARENT,
                          "    def _sm_formula(self, t, dt, n):\n        return 0\n")
    names = set()
    for node in ast.walk(ast.parse(skeleton)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
              and node.value.id == "self"):
            names.add(node.attr)
    names.discard("length")
    return frozenset(names | RESERVED)


# ------------------------------------------------------------------- render

_UNIT_MARK = "  # unit: "

def _default_text(f: FieldSpec) -> str:
    d = f.default
    if f.type == "float":
        d = float(d)
    return repr(d)


def render_region(spec: ClassSpec) -> str:
    """The region body (the lines between the two markers), newline-terminated.

    Notes come only from validate; callers that show a preview must call validate.
    """
    validate(spec)
    lines = []
    for f in spec.fields:
        unit = f"{_UNIT_MARK}{f.unit}" if f.unit else ""
        lines.append(f"    {f.name}: {f.type} = {_default_text(f)}{unit}")
    lines.append("")
    lines.append("    def _sm_formula(self, t, dt, n):")
    for f in spec.fields:
        lines.append(f"        {f.name} = self.{f.name}")
    lines.append("        return (")
    for fl in spec.formula.strip().split("\n"):
        lines.append("            " + fl.rstrip())
    lines.append("        )")
    return "\n".join(lines) + "\n"


def render(spec: ClassSpec) -> str:
    """The whole source file for a new class.

    Notes come only from validate; callers that show a preview must call validate.
    """
    return _file_text(spec.class_name, spec.parent, render_region(spec))


def _file_text(class_name: str, parent_path: str, region: str) -> str:
    mod, _, parent = parent_path.rpartition(".")
    return (
        f"{HEADER_MARK}, version {TEMPLATE_VERSION}.\n"
        "# Edit freely. The form rewrites only the lines between the two SM\n"
        "# template markers; change the markers by hand and the form stops\n"
        "# rewriting this file (code view only).\n"
        f'"""Pulse class {class_name} (made from the SM pulse class template)."""\n'
        "\n"
        "import numpy as np\n"
        f"from {mod} import {parent}\n"
        "from quam.core.quam_classes import quam_dataclass\n"
        "\n"
        f'__all__ = ["{class_name}"]\n'
        "\n"
        "\n"
        "@quam_dataclass\n"
        f"class {class_name}({parent}):\n"
        f"{REGION_BEGIN}\n"
        f"{region}"
        f"{REGION_END}\n"
        "\n"
        "    def waveform_function(self):\n"
        "        dt = 1e9 / self._get_sampling_rate()\n"
        "        n = int(round(self.length / dt))\n"
        "        t = np.arange(n) * dt\n"
        "        wf = np.asarray(self._sm_formula(t, dt, n), dtype=float)\n"
        "        return np.broadcast_to(wf, (n,)).copy()\n"
    )


# -------------------------------------------------------------------- parse

def _header_version(text: str) -> int | None:
    first = text.split("\n", 1)[0]
    m = re.match(re.escape(HEADER_MARK) + r", version (\d+)\.\s*$", first)
    return int(m.group(1)) if m else None


def _parse_region(body: str, class_name: str) -> ClassSpec:
    """Region body -> ClassSpec; raises ValueError when it is not template-shaped."""
    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    fields: list[FieldSpec] = []
    i = 0
    field_re = re.compile(r"^    ([A-Za-z_][A-Za-z0-9_]*): (\w+) = (.*?)(?:  # unit: (.*))?$")
    while i < len(lines) and lines[i] != "":
        m = field_re.match(lines[i])
        if not m:
            raise ValueError(f"region line {i + 1} is not a field line")
        name, typ, dtext, unit = m.group(1), m.group(2), m.group(3), m.group(4) or ""
        try:
            default = ast.literal_eval(dtext)
        except (ValueError, SyntaxError):
            raise ValueError(f"field {name}: default is not a literal") from None
        fields.append(FieldSpec(name, typ, default, unit))
        i += 1
    rest = lines[i + 1:]
    head = "    def _sm_formula(self, t, dt, n):"
    want = [head] + [f"        {f.name} = self.{f.name}" for f in fields] + ["        return ("]
    if rest[:len(want)] != want or not rest or rest[-1] != "        )":
        raise ValueError("the formula block is not as the template wrote it")
    flines = rest[len(want):-1]
    if not flines or any(not fl.startswith("            ") for fl in flines):
        raise ValueError("the formula lines are not indented as the template wrote them")
    formula = "\n".join(fl[12:] for fl in flines)
    spec = ClassSpec(class_name, fields, formula)
    if render_region(spec) != body:
        raise ValueError("the region does not re-render to the same text")
    return spec


def parse(text: str) -> Parsed:
    """Find the region in *text*; never raises."""
    version = _header_version(text)
    made = version is not None
    # markers count only as whole lines
    begins = [m.end() for m in re.finditer("^" + re.escape(REGION_BEGIN) + "\n", text, re.M)]
    ends = [m.start() for m in re.finditer("^" + re.escape(REGION_END) + "\n", text, re.M)]
    if len(begins) != 1 or len(ends) != 1:
        # "\r\n" (Windows) or a bare "\r" (a file with no "\n" at all)
        crlf = (REGION_BEGIN + "\r") in text or (REGION_END + "\r") in text
        return Parsed(False, CRLF_LINE if crlf else CODE_VIEW_ONLY_LINE, version, made)
    b, e = begins[0], ends[0]
    if e < b:
        return Parsed(False, CODE_VIEW_ONLY_LINE, version, made)
    m = None
    for m in re.finditer(r"^class ([A-Za-z_][A-Za-z0-9_]*)\(", text[:b], re.M):
        pass
    if m is None:
        return Parsed(False, CODE_VIEW_ONLY_LINE, version, made)
    parent = None
    pm = re.search(r"^from ([\w.]+) import (\w+)\n(?=from quam\.core\.quam_classes import quam_dataclass)",
                   text[:b], re.M)
    if pm:
        parent = f"{pm.group(1)}.{pm.group(2)}"
    if "\r" in text[b:e]:
        return Parsed(False, CRLF_LINE, version, made)
    try:
        spec = _parse_region(text[b:e], m.group(1))
    except TemplateError as exc:
        return Parsed(False, f"{REGION_CHANGED_LINE} {exc}", version, made)
    except ValueError:
        return Parsed(False, REGION_CHANGED_LINE, version, made)
    if parent:
        spec.parent = parent
    return Parsed(True, None, version, made, spec, (b, e))


def rewrite(text: str, spec: ClassSpec) -> str:
    """*text* with only its region replaced by *spec*'s region.

    Notes come only from validate; callers that show a preview must call validate.

    Raises :class:`TemplateError` with the reason :func:`parse` gave (changed
    markers, a changed region, Windows line endings) when the file cannot be
    rewritten, and when *spec* renames the class (a rename
    touches the class line outside the region)."""
    p = parse(text)
    if not p.editable:
        raise TemplateError(p.reason or CODE_VIEW_ONLY_LINE)
    if spec.class_name != p.spec.class_name:
        raise TemplateError("Renaming the class is done in the code view")
    b, e = p.region
    return text[:b] + render_region(spec) + text[e:]


# ------------------------------------------------------- proportional check

PROPORTIONAL_LINE = "The waveform scales with amplitude."
CANNOT_TELL_LINE = ("Cannot tell whether the waveform scales with amplitude: "
                    "the waveform is zero everywhere.")
NOT_PROPORTIONAL_REJECT = ("Not saved: the waveform does not scale with amplitude "
                           "(ratios seen {lo:.6g} to {hi:.6g}, expected {want:.6g}).")
NOT_PROPORTIONAL_WARN = ("Warning: the waveform does not scale with amplitude "
                         "(ratios seen {lo:.6g} to {hi:.6g}, expected {want:.6g}).")


def proportional_verdict(text: str, check: dict) -> dict:
    """``{"level": "ok"|"reject"|"warn"|"unknown", "line": str}`` from the
    runner's proportional check on the class in *text*. A class still in its
    template shape is refused when not proportional; one edited by hand
    outside the form (code view only) is only warned about."""
    if isinstance(check, dict) and check.get("zero_waveform"):
        # a waveform that is zero everywhere scales with anything: say so
        return {"level": "unknown", "line": CANNOT_TELL_LINE}
    if not isinstance(check, dict) or check.get("proportional") is None:
        return {"level": "unknown", "line": "The proportional check did not run."}
    if check["proportional"]:
        return {"level": "ok", "line": PROPORTIONAL_LINE}
    vals = dict(lo=check.get("ratio_min") or 0.0, hi=check.get("ratio_max") or 0.0,
                want=check.get("ratio_expected") or 0.0)
    if parse(text).editable:
        return {"level": "reject", "line": NOT_PROPORTIONAL_REJECT.format(**vals)}
    return {"level": "warn", "line": NOT_PROPORTIONAL_WARN.format(**vals)}
