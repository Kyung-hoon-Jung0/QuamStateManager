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
import keyword
import re
from dataclasses import dataclass, field

TEMPLATE_VERSION = 1

HEADER_MARK = "# Made by QUAM State Manager pulse class template"
REGION_BEGIN = "    # >>> SM template region: fields and formula. The form rewrites the lines up to the end marker. >>>"
REGION_END = "    # <<< SM template region end <<<"

#: field types the form offers (annotation text -> Python type of the default)
FIELD_TYPES = {"float": float, "int": int, "bool": bool, "str": str}

#: names the quam Pulse base already owns (``length`` may be re-declared with
#: a default; the rest may not be shadowed), plus the formula's own names
RESERVED = {"id", "digital_marker", "operation", "channel", "name", "parent",
            "t", "dt", "n", "np", "self"}

CODE_VIEW_ONLY_LINE = ("The class structure can only be edited in the code view: "
                       "the template markers in this file were changed by hand.")


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
    parent: str = "quam.components.pulses.Pulse"


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
    if not isinstance(name, str) or not _IDENT.match(name) or keyword.iskeyword(name):
        raise TemplateError(f"{what} {name!r} is not a valid Python name")


def validate(spec: ClassSpec) -> None:
    """Raise :class:`TemplateError` naming the first problem in *spec*."""
    _check_ident(spec.class_name, "Class name")
    if not spec.class_name[0].isupper():
        raise TemplateError(f"Class name {spec.class_name!r} must start with a capital letter")
    if "." not in spec.parent:
        raise TemplateError(f"Parent class {spec.parent!r} must be a full module path")
    for part in spec.parent.split("."):
        _check_ident(part, "Parent class part")
    seen = set()
    for f in spec.fields:
        _check_ident(f.name, "Field name")
        if f.name in RESERVED:
            raise TemplateError(f"Field name {f.name!r} is reserved")
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
            raise TemplateError(f"Field {f.name!r}: default {d!r} is not a {f.type}")
        if not isinstance(f.unit, str) or "\n" in f.unit or "#" in f.unit:
            raise TemplateError(f"Field {f.name!r}: unit must be one line without '#'")
    if "amplitude" not in seen:
        raise TemplateError("A pulse class made from the template needs an 'amplitude' field")
    if "length" not in seen:
        raise TemplateError("A pulse class made from the template needs a 'length' field (ns)")
    if not isinstance(spec.formula, str) or not spec.formula.strip():
        raise TemplateError("The waveform formula is empty")
    try:
        tree = ast.parse(spec.formula.strip(), mode="eval")
    except SyntaxError as exc:
        raise TemplateError(f"The waveform formula is not a valid expression: {exc.msg}") from None
    del tree


# ------------------------------------------------------------------- render

def _default_text(f: FieldSpec) -> str:
    d = f.default
    if f.type == "float":
        d = float(d)
    return repr(d)


def render_region(spec: ClassSpec) -> str:
    """The region body (the lines between the two markers), newline-terminated."""
    validate(spec)
    lines = []
    for f in spec.fields:
        unit = f"  # unit: {f.unit}" if f.unit else ""
        lines.append(f"    {f.name}: {f.type} = {_default_text(f)}{unit}")
    lines.append("")
    lines.append("    def _sm_formula(self, t, dt, n):")
    for f in spec.fields:
        lines.append(f"        {f.name} = self.{f.name}")
    lines.append("        return (")
    for fl in spec.formula.strip().splitlines():
        lines.append("            " + fl.rstrip())
    lines.append("        )")
    return "\n".join(lines) + "\n"


def render(spec: ClassSpec) -> str:
    """The whole source file for a new class."""
    region = render_region(spec)
    mod, _, parent = spec.parent.rpartition(".")
    return (
        f"{HEADER_MARK}, version {TEMPLATE_VERSION}.\n"
        "# Edit freely. The form rewrites only the lines between the two SM\n"
        "# template markers; change the markers by hand and the form stops\n"
        "# rewriting this file (code view only).\n"
        f'"""Pulse class {spec.class_name} (made from the SM pulse class template)."""\n'
        "\n"
        "import numpy as np\n"
        f"from {mod} import {parent}\n"
        "from quam.core.quam_classes import quam_dataclass\n"
        "\n"
        f'__all__ = ["{spec.class_name}"]\n'
        "\n"
        "\n"
        "@quam_dataclass\n"
        f"class {spec.class_name}({parent}):\n"
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
    nb, ne = text.count(REGION_BEGIN + "\n"), text.count(REGION_END + "\n")
    if nb != 1 or ne != 1:
        return Parsed(False, CODE_VIEW_ONLY_LINE, version, made)
    b = text.index(REGION_BEGIN + "\n") + len(REGION_BEGIN) + 1
    e = text.index(REGION_END + "\n")
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
    try:
        spec = _parse_region(text[b:e], m.group(1))
    except (ValueError, TemplateError):
        return Parsed(False, CODE_VIEW_ONLY_LINE, version, made)
    if parent:
        spec.parent = parent
    return Parsed(True, None, version, made, spec, (b, e))


def rewrite(text: str, spec: ClassSpec) -> str:
    """*text* with only its region replaced by *spec*'s region.

    Raises :class:`TemplateError` with :data:`CODE_VIEW_ONLY_LINE` when the
    file cannot be rewritten, and when *spec* renames the class (a rename
    touches the class line outside the region)."""
    p = parse(text)
    if not p.editable:
        raise TemplateError(CODE_VIEW_ONLY_LINE)
    if spec.class_name != p.spec.class_name:
        raise TemplateError("Renaming the class is done in the code view")
    b, e = p.region
    return text[:b] + render_region(spec) + text[e:]


# ------------------------------------------------------- proportional check

PROPORTIONAL_LINE = "The waveform scales with amplitude."
NOT_PROPORTIONAL_REJECT = ("Not saved: the waveform does not scale with amplitude "
                           "(ratios seen {lo:.6g} to {hi:.6g}, expected {want:.6g}).")
NOT_PROPORTIONAL_WARN = ("Warning: the waveform does not scale with amplitude "
                         "(ratios seen {lo:.6g} to {hi:.6g}, expected {want:.6g}).")


def proportional_verdict(text: str, check: dict) -> dict:
    """``{"level": "ok"|"reject"|"warn"|"unknown", "line": str}`` from the
    runner's proportional check on the class in *text*. A class still in its
    template shape is refused when not proportional; one edited by hand
    outside the form (code view only) is only warned about."""
    if not isinstance(check, dict) or check.get("proportional") is None:
        return {"level": "unknown", "line": "The proportional check did not run."}
    if check["proportional"]:
        return {"level": "ok", "line": PROPORTIONAL_LINE}
    vals = dict(lo=check.get("ratio_min") or 0.0, hi=check.get("ratio_max") or 0.0,
                want=check.get("ratio_expected") or 0.0)
    if parse(text).editable:
        return {"level": "reject", "line": NOT_PROPORTIONAL_REJECT.format(**vals)}
    return {"level": "warn", "line": NOT_PROPORTIONAL_WARN.format(**vals)}
