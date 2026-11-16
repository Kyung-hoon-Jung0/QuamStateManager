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
- the port's ``delay`` is added to each pulse row and each mark on that line.

Anything else (loops, declared variables, conditionals, measure, real-time
arguments) makes the result ``cannot draw: <statement>`` with NO rows: a
guessed row would look as trustworthy as a computed one.

Times include the port delay, before any output filter; the output-time offset
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
    level: float | None
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
        raise _CannotDraw(f"cannot draw: {stmt} (a value set while the program runs, so SM cannot place it)") from None


def _whole(v):
    """*v* as an int when it is a whole number (an int, or an integral
    float such as 16.0), else None. A bool is never a number here."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


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


def _signed_peak(info: dict) -> float | None:
    """The signed sample of largest magnitude, or None when the waveform
    cannot be read or holds a value that is not a number."""
    wf = info.get("waveform")
    if not isinstance(wf, dict) or wf.get("error") or wf.get("nonfinite"):
        return None
    if wf.get("type") == "constant":
        s = wf.get("sample")
        return None if s is None else float(s)
    if any(s is None for s in (wf.get("samples") or [])):
        return None
    samples = [float(s) for s in (wf.get("samples") or []) if s is not None]
    if not samples:
        return 0.0
    return max(samples, key=abs)


def static_timeline(script: str, ops) -> dict:
    """``{"rows": [TimelineRow], "marks": [mark], "cannot_draw": str | None,
    "notes": [str], "filtered_ports": [port]}``. Drawing is all it does: rows
    placed here are not compared with any measurement.

    *marks*: every ``wait`` and ``align`` in program order, so a step that plays
    nothing is still on the timeline: ``{"kind": "wait", "label": "wait 40 ns",
    "element", "statement", "start_ns", "end_ns"}`` and
    ``{"kind": "align", "label": "align", "element", "statement", "t_ns"}``.
    Each covered line gets its own mark at its program clock plus the same
    port delay as its pulses. A wait or align SM cannot place makes the whole
    timeline ``cannot_draw`` with its statement, never a guessed time.

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
    out = {"rows": [], "marks": [], "cannot_draw": None, "notes": notes,
           "filtered_ports": filtered}
    try:
        unknown = sorted({f"{o} on {e}" for (e, o), i in table.items()
                          if isinstance(i, dict) and _signed_peak(i) is None})
        if unknown:
            notes.append("The level is not known for: " + ", ".join(unknown)
                         + " (the waveform cannot be read).")
    except Exception:  # noqa: BLE001
        pass
    try:
        if bad_ops:
            raise _CannotDraw("cannot draw: the pulse data of the program could not be read")
        if not isinstance(script, str):
            raise _CannotDraw("cannot draw: no program text")
        marks: list[dict] = []
        out["rows"] = _place(script, table, marks)
        out["marks"] = marks
        if marks:
            notes[0] = notes[0].replace(NOTE_PROGRAM_TIME,
                                      "Pulses and step marks include each line's port delay, before filters.")
    except _CannotDraw as exc:
        out["cannot_draw"] = str(exc)
        out["rows"], out["marks"] = [], []
    except SyntaxError as exc:
        out["cannot_draw"] = "cannot draw: the program text could not be read"
        out["rows"], out["marks"] = [], []
    except Exception as exc:  # noqa: BLE001 -- never raise to the caller
        out["cannot_draw"] = ("cannot draw: the program or its pulse data has a form "
                              "SM does not read")
        out["rows"], out["marks"] = [], []
    return out


def _port_text(port) -> str:
    if isinstance(port, (list, tuple)):
        return "/".join(str(p) for p in port)
    return str(port)


def _place(script: str, table: dict, marks: list | None = None) -> list[TimelineRow]:
    src = _body(script)
    tree = ast.parse(src)
    clocks: dict[str, int] = {el: 0 for el, _op in table}
    # The same operation data supplies the pulse rows and each covered line's delay.
    # A port that carries no delay applies none (0 ns), for marks exactly as for pulses;
    # a delay that is present but not a whole number of ns is never rounded or guessed.
    delays = {el: 0 if info.get("delay") is None else _whole(info.get("delay"))
              for (el, _op), info in table.items()}

    def mark(kind, el, stmt, start, end=None):
        # Elements that never play have no output lane on this screen.
        if el not in delays:
            return
        delay = delays.get(el)
        if delay is None:
            raise _CannotDraw(f"cannot draw: {stmt} (the port delay of {el} is not a whole number of ns)")
        item = {"kind": kind, "element": el, "statement": stmt,
                "label": f"wait {end - start} ns" if kind == "wait" else "align"}
        if end is None:
            item["t_ns"] = start + delay
        else:
            item.update(start_ns=start + delay, end_ns=end + delay)
        if marks is not None:
            marks.append(item)

    rows: list[TimelineRow] = []
    for node in tree.body:
        stmt = (ast.get_source_segment(src, node) or "").strip().splitlines()[0]
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)):
            raise _CannotDraw(f"cannot draw: {stmt} (SM places only play, wait, align and frame rotations; loops, variables, conditions and measurements are not placed)")
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
            if marks is not None:
                for e in els:
                    mark("align", e, stmt, t)
        elif name == "wait":
            if not call.args:
                raise _CannotDraw(f"cannot draw: {stmt} (the wait has no duration)")
            n = _whole(_const(call.args[0], stmt))
            if n is None:
                raise _CannotDraw(f"cannot draw: {stmt} (the wait is not a whole number "
                                  "of clock cycles)")
            targets = [str(_const(a, stmt)) for a in call.args[1:]] or list(clocks)
            for e in targets:
                start = clocks.get(e, 0)
                clocks[e] = clocks.get(e, 0) + 4 * n
                if marks is not None:
                    mark("wait", e, stmt, start, clocks[e])
        elif name in ZERO_TIME:
            continue
        elif name == "play":
            rows.append(_play(call, kw, stmt, table, clocks, delays))
        else:
            raise _CannotDraw(f"cannot draw: {stmt} (SM places only play, wait, align and frame rotations; loops, variables, conditions and measurements are not placed)")
    return rows


def _play(call, kw, stmt, table, clocks, delays) -> TimelineRow:
    if len(call.args) < 2:
        raise _CannotDraw(f"cannot draw: {stmt} (the play names no element)")
    opnode, el = call.args[0], str(_const(call.args[1], stmt))
    scale = 1.0
    if isinstance(opnode, ast.BinOp) and isinstance(opnode.op, ast.Mult):
        a = opnode.right
        if not (isinstance(a, ast.Call) and getattr(a.func, "id", "") == "amp"
                and len(a.args) == 1):
            raise _CannotDraw(f"cannot draw: {stmt} (the amplitude is scaled by a matrix or a value SM cannot read)")
        scale = float(_const(a.args[0], stmt))
        opnode = opnode.left
    op = str(_const(opnode, stmt))
    for k in kw:
        if k not in ("duration", "amplitude_scale"):
            raise _CannotDraw(f"cannot draw: {stmt} (the play uses the option '{k}', which SM does not model)")
    if "amplitude_scale" in kw:
        scale *= float(_const(kw["amplitude_scale"], stmt))
    info = table.get(_key(el, op))
    if info is None or info.get("length") is None:
        raise _CannotDraw(f"cannot draw: {stmt} (operation not in the config)")
    length = _whole(info["length"])
    if length is None:
        raise _CannotDraw(f"cannot draw: {stmt} (the pulse length {info['length']!r} ns "
                          "is not a whole number)")
    delay = delays.get(el)
    if delay is None:
        raise _CannotDraw(f"cannot draw: {stmt} (the port delay {info.get('delay')!r} ns "
                          "is not a whole number)")
    if "duration" in kw:
        n = _whole(_const(kw["duration"], stmt))
        if n is None:
            raise _CannotDraw(f"cannot draw: {stmt} (the duration is not a whole number "
                              "of clock cycles)")
        length = 4 * n
    peak = _signed_peak(info)
    t0 = clocks.get(el, 0)
    clocks[el] = t0 + length
    return TimelineRow(element=el, port=info.get("port"), operation=op,
                       start_ns=t0 + delay, end_ns=t0 + length + delay,
                       level=None if peak is None else peak * scale, scale=scale)


#: QUA's shortest ``duration=`` / ``wait`` before QOP 3.3 (clock cycles of 4 ns,
#: so 16 ns); from QOP 3.3 on it is 2 cycles (8 ns), and callers pass the chip's
#: floor as *min_cycles*
MIN_CYCLES = 4


def short_timing(script, min_cycles: int = MIN_CYCLES) -> list[tuple[str, str]]:
    """``[(kind, statement)]`` for each ``play(..., duration=n)`` (kind
    ``"play"``) and ``wait(n, ...)`` (kind ``"wait"``) whose literal *n* is
    not a positive whole number of at least *min_cycles* clock cycles.
    A real-time (non-literal) *n* is not judged here. Never raises: an
    unreadable script gives an empty list."""
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
            w = _whole(n)
            if w is None or w < min_cycles:
                out.append((node.func.id, (ast.get_source_segment(src, node) or "").strip()))
        return out
    except Exception:  # noqa: BLE001
        return []
