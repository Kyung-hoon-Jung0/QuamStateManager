"""Static timeline of a gate's QUA program, from statement order and config pulse lengths.

Pure Python (no ``qm`` import): the lab worker hands over the text of
``qm.generate_qua_script(prog)`` for ``with program(): macro.apply()`` and, per
played operation, what the generated config holds for it (length, waveform,
port, port delay, sampling rate, filter). The script text is valid Python, so it
is parsed with ``ast``; nothing is guessed from regular expressions.

Rules:

- every element has its own clock, starting at 0 ns;
- ``align(e1, e2, ...)`` sets those clocks to their maximum; ``align()`` with no
  argument uses every element the calculator knows;
- ``wait(n, e1, ...)`` advances those clocks by 4*n ns (n in clock cycles);
- ``play(op, el)`` starts at the element clock and lasts the config pulse
  length, or 4*n ns with ``duration=n``; ``op * amp(k)`` and
  ``amplitude_scale=k`` scale the level by k;
- ``frame_rotation``, ``frame_rotation_2pi``, ``reset_frame``: zero time;
- the port's ``delay`` is added to the start and the end of each row.

Anything else (loops, declared variables, conditionals, measure, real-time
arguments) makes the result ``cannot draw: <statement>`` with NO rows: a
guessed row would look as trustworthy as a computed one.

Times are program time before any output filter; the output-time offset
between microwave and flux ports is not computed (both are stated in
``notes`` for the screen).
"""

from __future__ import annotations

import ast
import textwrap
from typing import Any, NamedTuple

ZERO_TIME = frozenset({"frame_rotation", "frame_rotation_2pi", "reset_frame"})

NOTE_PROGRAM_TIME = "Times are program time before filters."
NOTE_MW_OFFSET = ("The output-time offset between microwave and flux ports is "
                  "not computed.")


class TimelineRow(NamedTuple):
    element: str
    port: Any
    operation: str
    start_ns: int
    end_ns: int
    level: float
    scale: float


class _CannotDraw(Exception):
    pass


def _body(script: str) -> str:
    """The statements inside ``with program() as prog:`` (no config, no
    stream processing)."""
    marker = "with program() as prog:"
    if marker in script:
        script = script.split(marker, 1)[1]
    if "\nconfig =" in script:
        script = script.split("\nconfig =", 1)[0]
    lines = []
    for ln in script.splitlines():
        if ln.strip().startswith("with stream_processing"):
            break
        lines.append(ln)
    return textwrap.dedent("\n".join(lines)).strip()


def _const(node, stmt: str):
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001 -- any non-literal is a real-time value
        raise _CannotDraw(f"cannot draw: {stmt} (non-constant argument)") from None


def _key(element: str, operation: str) -> tuple[str, str]:
    return (str(element), str(operation))


def ops_by_key(ops) -> dict:
    """``{(element, operation): info}`` from a list of op dicts (the runner's
    shape) or an already keyed mapping."""
    if isinstance(ops, dict):
        return {_key(*k): v for k, v in ops.items()}
    out = {}
    for o in ops or []:
        if isinstance(o, dict) and o.get("element") and o.get("operation"):
            out[_key(o["element"], o["operation"])] = o
    return out


def _signed_peak(info: dict) -> float:
    wf = info.get("waveform") or {}
    if wf.get("type") == "constant":
        return float(wf.get("sample") or 0.0)
    samples = [float(s) for s in (wf.get("samples") or []) if s is not None]
    if not samples:
        return 0.0
    return max(samples, key=abs)


def static_timeline(script: str, ops) -> dict:
    """``{"rows": [TimelineRow], "cannot_draw": str | None, "notes": [str],
    "filtered_ports": [port]}``.

    *ops*: per played operation ``{"element", "operation", "length",
    "waveform": {"type": "constant", "sample"} | {"type": "arbitrary",
    "samples"}, "port", "delay", "sampling_rate", "filter"}``."""
    try:
        table = ops_by_key(ops)
    except Exception:  # noqa: BLE001
        table = {}
        bad_ops = True
    else:
        bad_ops = False
    filtered = []
    for info in table.values():
        if isinstance(info, dict) and info.get("filter") and info.get("port") not in filtered:
            filtered.append(info.get("port"))
    notes = [NOTE_PROGRAM_TIME + (
        " Ports with a filter: " + ", ".join(_port_text(p) for p in filtered) + "."
        if filtered else ""), NOTE_MW_OFFSET]
    out = {"rows": [], "cannot_draw": None, "notes": notes,
           "filtered_ports": filtered}
    try:
        if bad_ops:
            raise _CannotDraw("cannot draw: the operation data is not readable")
        if not isinstance(script, str):
            raise _CannotDraw("cannot draw: no program text")
        out["rows"] = _place(script, table)
    except _CannotDraw as exc:
        out["cannot_draw"] = str(exc)
        out["rows"] = []
    except SyntaxError as exc:
        out["cannot_draw"] = f"cannot draw: the script does not parse ({exc.msg})"
        out["rows"] = []
    except Exception as exc:  # noqa: BLE001 -- never raise to the caller
        out["cannot_draw"] = (f"cannot draw: unexpected script or operation data "
                              f"({type(exc).__name__}: {str(exc)[:200]})")
        out["rows"] = []
    return out


def _port_text(port) -> str:
    if isinstance(port, (list, tuple)):
        return "/".join(str(p) for p in port)
    return str(port)


def _place(script: str, table: dict) -> list[TimelineRow]:
    src = _body(script)
    tree = ast.parse(src)
    clocks: dict[str, int] = {el: 0 for el, _op in table}
    rows: list[TimelineRow] = []
    for node in tree.body:
        stmt = (ast.get_source_segment(src, node) or "").strip().splitlines()[0]
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)):
            raise _CannotDraw(f"cannot draw: {stmt}")
        call = node.value
        name = call.func.id
        kw = {k.arg: k.value for k in call.keywords}
        if name == "align":
            els = [str(_const(a, stmt)) for a in call.args] or list(clocks)
            if not els:
                continue
            t = max(clocks.setdefault(e, 0) for e in els)
            for e in els:
                clocks[e] = t
        elif name == "wait":
            if not call.args:
                raise _CannotDraw(f"cannot draw: {stmt}")
            n = _const(call.args[0], stmt)
            if not isinstance(n, int):
                raise _CannotDraw(f"cannot draw: {stmt} (non-integer wait)")
            targets = [str(_const(a, stmt)) for a in call.args[1:]] or list(clocks)
            for e in targets:
                clocks[e] = clocks.get(e, 0) + 4 * n
        elif name in ZERO_TIME:
            continue
        elif name == "play":
            rows.append(_play(call, kw, stmt, table, clocks))
        else:
            raise _CannotDraw(f"cannot draw: {stmt}")
    return rows


def _play(call, kw, stmt, table, clocks) -> TimelineRow:
    if len(call.args) < 2:
        raise _CannotDraw(f"cannot draw: {stmt}")
    opnode, el = call.args[0], str(_const(call.args[1], stmt))
    scale = 1.0
    if isinstance(opnode, ast.BinOp) and isinstance(opnode.op, ast.Mult):
        a = opnode.right
        if not (isinstance(a, ast.Call) and getattr(a.func, "id", "") == "amp"
                and len(a.args) == 1):
            raise _CannotDraw(f"cannot draw: {stmt} (amp matrix or unknown scale)")
        scale = float(_const(a.args[0], stmt))
        opnode = opnode.left
    op = str(_const(opnode, stmt))
    for k in kw:
        if k not in ("duration", "amplitude_scale"):
            raise _CannotDraw(f"cannot draw: {stmt} (keyword {k})")
    if "amplitude_scale" in kw:
        scale *= float(_const(kw["amplitude_scale"], stmt))
    info = table.get(_key(el, op))
    if info is None or info.get("length") is None:
        raise _CannotDraw(f"cannot draw: {stmt} (operation not in the config)")
    length = info["length"]
    if isinstance(length, bool) or not isinstance(length, int):
        raise _CannotDraw(f"cannot draw: {stmt} (pulse length {length!r} is not an integer)")
    delay = info.get("delay") or 0
    if isinstance(delay, bool) or not isinstance(delay, int):
        raise _CannotDraw(f"cannot draw: {stmt} (port delay {delay!r} is not an integer)")
    if "duration" in kw:
        n = _const(kw["duration"], stmt)
        if not isinstance(n, int):
            raise _CannotDraw(f"cannot draw: {stmt} (non-integer duration)")
        length = 4 * n
    t0 = clocks.get(el, 0)
    clocks[el] = t0 + length
    return TimelineRow(element=el, port=info.get("port"), operation=op,
                       start_ns=t0 + delay, end_ns=t0 + length + delay,
                       level=_signed_peak(info) * scale, scale=scale)


#: QUA's shortest ``duration=`` / ``wait`` (clock cycles of 4 ns, so 16 ns)
MIN_CYCLES = 4


def short_timing(script) -> list[str]:
    """The ``play(..., duration=n)`` and ``wait(n, ...)`` statements whose
    literal *n* is not a positive integer of at least :data:`MIN_CYCLES`
    clock cycles. A real-time (non-literal) *n* is not judged here. Never
    raises: an unreadable script gives an empty list."""
    try:
        src = _body(script if isinstance(script, str) else "")
        tree = ast.parse(src)
        out = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            arg = None
            if node.func.id == "play":
                arg = next((k.value for k in node.keywords if k.arg == "duration"), None)
            elif node.func.id == "wait" and node.args:
                arg = node.args[0]
            if arg is None:
                continue
            try:
                n = ast.literal_eval(arg)
            except Exception:  # noqa: BLE001 -- a real-time value
                continue
            if isinstance(n, bool) or not isinstance(n, int) or n < MIN_CYCLES:
                out.append((ast.get_source_segment(src, node) or "").strip())
        return out
    except Exception:  # noqa: BLE001
        return []
