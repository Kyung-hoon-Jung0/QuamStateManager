"""SM-side gate check: turns the lab worker's answer into one result per check.

The checks run on two chip copies (:mod:`core.chip_copy`): "before" (the chip
as it is, unsaved edits included) and "after" (plus the new gate's creates).
The lab side is ``generator/run_pulse_waveform.py``'s gate-check item, run by
:func:`core.lab_waveform.run_gate_check`. The checks, per gate:

- ``load_config``: the "after" chip loads and builds its config;
- ``ops``: the macro's ``apply()`` runs and every operation it plays is in the
  config (a missing one shows up as a KeyError naming operation and channel);
- ``length``: every played pulse length is an integer, a multiple of 4 ns and
  at least 16 ns;
- ``preview``: the preview samples equal the config's samples;
- ``timeline``: the static timeline (:mod:`core.static_timeline`);
- ``range``: pulse maximum * |scale| + channel offset inside the port's range.

Each check is ``{"status": "pass" | "fail" | "skipped" | "note",
"message": str}``. ``load_config`` and ``ops`` fail only for what the new
state caused: "before" must have passed, and a gate that already exists in
"before" fails only with an error that is new or different there. When
"before" itself does not load or build its config, every check is ``skipped``
with the chip's own error and ``checked`` is False. A runner crash, a timeout
and "no env selected" come back as a typed ``status``, never as an exception.

Only the I (or single) waveform of an operation and its port are checked; the
Q waveform and Q port of an IQ or microwave element are not modelled.

The result is JSON-serialisable::

    {"status": "ok" | "no-env" | "timeout" | "crash" | "invalid-create",
     "checked": bool, "ok": bool, "chip_error": str | None,
     "error": str | None, "seq": int | None, "quam_config": {...} | None,
     "gates": [{"pair", "gate", "checks": {name: {"status", "message"}},
                "unchecked": [name], "timeline": {"rows": [{element, port,
                operation, start_ns, end_ns, level, scale}], "cannot_draw",
                "notes", "filtered_ports"} | None}]}

``ok`` means "nothing failed", not "everything was compared": the checks
that were skipped are listed per gate in ``unchecked``.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from quam_state_manager.core import chip_copy, static_timeline
# the one source of the output-port sample ranges (direct and amplified LF
# ports are in volts, MW ports are normalised)
from quam_state_manager.core.diagnostics import (
    _LF_AMPLIFIED_BOUND, _LF_DIRECT_BOUND, _MW_SAMPLE_BOUND, _SAMPLE_EPS)

CHECKS = ("load_config", "ops", "length", "preview", "timeline", "range")

#: preview vs config sample tolerance (volts, or normalised units on an MW
#: port). Both sides come from the same class code through float64 math, so a
#: real difference is a different waveform; 1e-6 is far below one DAC step
#: (0.5 V / 2**15 is about 1.5e-5 V on a direct LF port), so a difference it
#: lets through cannot change an output sample, and it absorbs the decimal
#: rounding of a JSON round trip.
PREVIEW_TOL = 1e-6

#: the length rule for every played pulse (ns)
LENGTH_STEP_NS = 4
LENGTH_MIN_NS = 16

#: the program clock the preview is drawn at (samples per second)
_BASE_RATE = 1e9

_IQ_NOTE = (" Only the I waveform and its port are checked; the Q waveform of an "
            "IQ or microwave element is not.")


def _r(status: str, message: str, **extra) -> dict:
    return {"status": status, "message": message, **extra}


def macro_path(pair: str, gate: str) -> str:
    return f"qubit_pairs.{pair}.macros.{gate}"


def gate_key(g) -> tuple[str, str]:
    """``(pair, gate)`` from ``{"pair", "gate"}``, a 2-tuple of strings or
    ``"<pair>/<gate>"``; anything else is a TypeError (never a silent miss)."""
    if isinstance(g, dict) and isinstance(g.get("pair"), str) and isinstance(g.get("gate"), str):
        return g["pair"], g["gate"]
    if isinstance(g, str) and g.count("/") == 1 and all(g.split("/")):
        pair, gate = g.split("/")
        return pair, gate
    if isinstance(g, (tuple, list)) and len(g) == 2 and all(isinstance(x, str) and x for x in g):
        return g[0], g[1]
    raise TypeError(f"not a gate key: {g!r}")


def gate_result(res: dict, pair: str, gate: str) -> dict | None:
    """The entry of *res* for one gate."""
    for g in res.get("gates") or []:
        if g["pair"] == pair and g["gate"] == gate:
            return g
    return None


def _all(status: str, message: str) -> dict:
    return {c: _r(status, message) for c in CHECKS}


def _entry(key, checks, timeline=None) -> dict:
    tl = None
    if timeline is not None:
        tl = dict(timeline)
        tl["rows"] = [r._asdict() if hasattr(r, "_asdict") else dict(r)
                      for r in timeline.get("rows") or []]
    return {"pair": key[0], "gate": key[1], "checks": checks,
            "unchecked": [c for c in CHECKS if checks[c]["status"] == "skipped"],
            "timeline": tl}


def _unchecked(keys, status: str, error: str, msg: str | None = None) -> dict:
    msg = msg or f"Not checked with the lab's code: {error}"
    return {"status": status, "checked": False, "chip_error": None,
            "error": error, "ok": False, "quam_config": None, "seq": None,
            "gates": [_entry(k, _all("skipped", msg)) for k in keys]}


_ADDR = re.compile(r"0x[0-9a-fA-F]+")


def _norm_err(err) -> str:
    return _ADDR.sub("0x?", str(err or "")).strip()


def evaluate(gates: Iterable, ran: dict, previews: dict | None = None) -> dict:
    """The per-gate checks from :func:`lab_waveform.run_gate_check`'s answer.

    *gates*: gate keys (see :func:`gate_key`). *previews*: ``{gate key:
    {(element, operation): samples list or constant}}``, keyed through the
    same :func:`gate_key`."""
    keys = [gate_key(g) for g in gates]
    previews = {gate_key(k): v for k, v in (previews or {}).items()}
    status = (ran or {}).get("status") or "crash"
    if status != "ok":
        return _unchecked(keys, status, (ran or {}).get("error") or status)
    data = ran.get("data") or {}
    before = data.get("before") or {}
    after = data.get("after") or {}
    out = {"status": "ok", "checked": True, "chip_error": None, "error": None,
           "quam_config": data.get("quam_config"), "seq": None, "gates": []}
    chip_err = before.get("load_error") or before.get("config_error")
    if chip_err:
        what = "does not load" if before.get("load_error") else "does not build its config"
        msg = (f"The chip as it is {what} with the lab's code, so the gate could "
               f"not be checked: {chip_err}")
        out.update(checked=False, chip_error=chip_err, ok=False)
        out["gates"] = [_entry(k, _all("skipped", msg)) for k in keys]
        return out
    after_err = after.get("load_error") or after.get("config_error")
    for k in keys:
        try:
            checks, tl = _one_gate(k, data, after, after_err, previews.get(k) or {})
            out["gates"].append(_entry(k, checks, tl))
        except Exception as exc:  # noqa: BLE001 -- one gate never takes the others down
            out["gates"].append(_entry(k, _all(
                "skipped", f"Not checked: the answer for this gate could not be read "
                           f"({type(exc).__name__}: {exc})")))
    out["ok"] = all(c["status"] != "fail" for g in out["gates"]
                    for c in g["checks"].values())
    return out


def _one_gate(key, data, after, after_err, preview):
    checks: dict[str, dict] = {}
    path = macro_path(*key)
    g = (data.get("gates") or {}).get(path)
    old = (data.get("before_gates") or {}).get(path)
    if after_err:
        what = "does not load" if after.get("load_error") else "does not build its config"
        checks["load_config"] = _r("fail", f"With the new gate the chip {what}: {after_err}")
    else:
        checks["load_config"] = _r("pass", "The chip loads and builds its config with the new gate.")
    if after.get("load_error") or g is None:
        msg = "Not checked: the chip with the new gate does not load."
        for c in CHECKS[1:]:
            checks[c] = _r("skipped", msg)
        return checks, None
    if g.get("error"):
        if old is not None and old.get("error") and _norm_err(old["error"]) == _norm_err(g["error"]):
            checks["ops"] = _r("note", "The gate already failed the same way before this "
                                       f"change: {_apply_message(g['error'])}", error=g["error"])
        else:
            checks["ops"] = _r("fail", _apply_message(g["error"]), error=g["error"])
        msg = "Not checked: the gate's apply() failed."
        for c in CHECKS[2:]:
            checks[c] = _r("skipped", msg)
        return checks, None
    ops = g.get("ops") or []
    no_cfg = bool(after.get("config_error"))
    missing = [o for o in ops if not o.get("found")]
    if no_cfg:
        checks["ops"] = _r("skipped", "Not checked: the config was not built.")
    elif missing:
        checks["ops"] = _r("fail", "; ".join(o.get("error") or
                                             f"{o.get('operation')} is not in {o.get('element')}"
                                             for o in missing))
    else:
        checks["ops"] = _r("pass", f"apply() runs; all {len(ops)} played operations "
                                   "are in the config.")
    found = [o for o in ops if o.get("found")]
    checks["length"] = _length_check(found, no_cfg)
    checks["preview"] = _preview_check(found, preview, no_cfg)
    tl = None
    if g.get("script") and not no_cfg:
        tl = static_timeline.static_timeline(g["script"], found)
        if tl["cannot_draw"]:
            checks["timeline"] = _r("note", f"The timeline is not drawn: {tl['cannot_draw']}")
        else:
            checks["timeline"] = _r("pass", f"{len(tl['rows'])} pulses placed.")
    else:
        checks["timeline"] = _r("note", "The timeline is not drawn: no program or config.")
    checks["range"] = _range_check(found, tl, no_cfg)
    return checks, tl


_KEY_RE = re.compile(r"[Oo]peration '([^']+)' not found in channel '([^']+)'")


def _apply_message(err: str) -> str:
    m = _KEY_RE.search(err or "")
    if m:
        return (f"The gate plays operation '{m.group(1)}', which channel "
                f"'{m.group(2)}' does not have.")
    return f"The gate's apply() failed: {err}"


def _length_check(found: list, no_cfg: bool) -> dict:
    if no_cfg:
        return _r("skipped", "Not checked: the config was not built.")
    bad = []
    for o in found:
        n = o.get("length")
        if (isinstance(n, bool) or not isinstance(n, int) or n % LENGTH_STEP_NS
                or n < LENGTH_MIN_NS):
            bad.append(f"{o['operation']} on {o['element']} is {n!r} ns")
    if bad:
        return _r("fail", f"A pulse length must be a multiple of {LENGTH_STEP_NS} ns and at "
                          f"least {LENGTH_MIN_NS} ns: " + "; ".join(bad))
    if not found:
        return _r("skipped", "No played operation to check.")
    return _r("pass", f"All {len(found)} pulse lengths are multiples of {LENGTH_STEP_NS} ns "
                      f"and at least {LENGTH_MIN_NS} ns.")


def _samples(info: dict) -> list[float] | None:
    wf = info.get("waveform") or {}
    if wf.get("type") == "constant":
        try:
            return [float(wf.get("sample"))] * max(1, int(info.get("length") or 1))
        except (TypeError, ValueError):
            return None
    return [float(s) for s in (wf.get("samples") or []) if s is not None]


def _iq_note(infos) -> str:
    return _IQ_NOTE if any(i.get("iq") for i in infos) else ""


def _preview_check(found: list, preview: dict, no_config: bool) -> dict:
    if no_config:
        return _r("skipped", "Not checked: the config was not built.")
    if not preview:
        return _r("skipped", "No preview was given to compare.")
    bad, partial, rate_skip, compared, used = [], [], [], 0, []
    for info in found:
        key = (info["element"], info["operation"])
        if key not in preview:
            continue
        used.append(info)
        want = preview[key]
        if isinstance(want, (int, float)):
            want = [float(want)] * int(info.get("length") or 1)
        have = _samples(info) or []
        wf = info.get("waveform") or {}
        truncated = bool(wf.get("truncated"))
        rate = info.get("sampling_rate")
        if not truncated and len(want) != len(have):
            if isinstance(rate, (int, float)) and rate != _BASE_RATE:
                rate_skip.append(f"{key[0]} {key[1]}: {len(want)} preview samples, "
                                 f"{len(have)} in the config at {rate:g} samples/s")
            else:
                bad.append(f"{key[0]} {key[1]}: {len(want)} preview samples, "
                           f"{len(have)} in the config")
            continue
        n_cmp = min(len(want), len(have)) if truncated else len(have)
        compared += 1
        diffs = [i for i in range(n_cmp) if not abs(float(want[i]) - float(have[i])) <= PREVIEW_TOL]
        if diffs:
            i = diffs[0]
            bad.append(f"{key[0]} {key[1]}: sample {i} differs by "
                       f"{abs(float(want[i]) - float(have[i])):.3g}")
        elif truncated:
            partial.append(f"{key[0]} {key[1]}: the first {n_cmp} of "
                           f"{wf.get('total')} samples compared, the rest was not")
    if bad:
        return _r("fail", "The preview differs from the config: " + "; ".join(bad))
    if partial or rate_skip:
        text = "; ".join(partial + rate_skip)
        if rate_skip and not compared:
            return _r("skipped", "Not compared at another sampling rate: " + text)
        return _r("note", "Partly compared: " + text + _iq_note(used))
    if not compared:
        return _r("skipped", "No preview matches a played operation.")
    return _r("pass", f"{compared} waveforms equal the preview (tolerance {PREVIEW_TOL:g})."
              + _iq_note(used))


def port_range(info: dict) -> float | None:
    """The port's output range when the config states it, else None."""
    kind = info.get("port_kind")
    if kind == "mw":
        return _MW_SAMPLE_BOUND
    if kind == "lf":
        mode = info.get("output_mode")
        if mode == "direct":
            return _LF_DIRECT_BOUND
        if mode == "amplified":
            return _LF_AMPLIFIED_BOUND
    return None


def _range_check(found: list, tl, no_config: bool) -> dict:
    if no_config:
        return _r("skipped", "Not checked: the config was not built.")
    if not found:
        return _r("skipped", "No played operation to check.")
    if tl is None or tl.get("cannot_draw"):
        return _r("skipped", "Skipped: the played scale is not known because the timeline "
                             "could not be drawn.")
    scales: dict = {}
    for row in tl.get("rows") or []:
        scales.setdefault((row.element, row.operation), set()).add(row.scale)
    bad, unknown, long_, checked = [], [], [], 0
    for info in found:
        key = (info["element"], info["operation"])
        rng = port_range(info)
        if rng is None:
            unknown.append(f"{key[0]} {key[1]}")
            continue
        if (info.get("waveform") or {}).get("truncated"):
            long_.append(f"{key[0]} {key[1]}")
            continue
        samples = _samples(info) or [0.0]
        offset = 0.0 if info.get("port_kind") == "mw" else float(info.get("offset") or 0.0)
        for k in sorted(scales.get(key) or ()):
            # max * k and min * k cover both signs of the scale and the level
            vals = [s * k for s in samples]
            hi, lo = max(vals) + offset, min(vals) + offset
            checked += 1
            if hi > rng + _SAMPLE_EPS or lo < -rng - _SAMPLE_EPS:
                edge = hi if hi > rng + _SAMPLE_EPS else lo
                bad.append(f"{key[0]} {key[1]}: maximum + offset {edge:+.4f} is outside "
                           f"+/-{rng:g} (offset {offset:+.4f}, scale {k:g})")
    if bad:
        return _r("fail", "Output range exceeded: " + "; ".join(bad))
    extra = ""
    if unknown:
        extra += (" Not checked, the output range of the port is not known: "
                  + ", ".join(unknown) + ".")
    if long_:
        extra += (" Not checked, the waveform was too long to check fully: "
                  + ", ".join(long_) + ".")
    if not checked:
        return _r("skipped", "Skipped." + extra)
    return _r("pass", "Maximum + offset is inside the output range." + extra
              + _iq_note(found))


def check_gates(store, python_path: str | None, gates: Iterable,
                creates: Iterable[tuple[str, Any]] = (), *,
                previews: dict | None = None,
                runner: Callable | None = None) -> dict:
    """Copy the chip (before / after *creates*), run the lab check without
    holding the store lock, remove the copies, and evaluate. The result
    carries ``seq``, the store's sequence value at the copy, so a later write
    can refuse when the store moved (:func:`chip_copy.store_at_seq`).
    A create the real write would refuse gives ``status`` "invalid-create"
    with ``load_config`` failed, naming the path. Writes nothing into the
    store."""
    keys = [gate_key(g) for g in gates]
    if not python_path:
        return _unchecked(keys, "no-env", "no Python environment is selected")
    if runner is None:
        from quam_state_manager.core.lab_waveform import run_gate_check as runner
    copies = None
    try:
        try:
            copies = chip_copy.make_chip_copies(store, creates)
        except chip_copy.CreateError as exc:
            res = _unchecked(keys, "invalid-create", str(exc),
                             "Not checked: the gate cannot be written.")
            for g in res["gates"]:
                g["checks"]["load_config"] = _r(
                    "fail", f"The write would be refused at {exc.path}: {exc.reason}")
                g["unchecked"] = [c for c in CHECKS if g["checks"][c]["status"] == "skipped"]
            return res
        try:
            ran = runner(python_path, str(copies.before), str(copies.after),
                         [macro_path(*k) for k in keys], root_class=copies.root_class)
        except Exception as exc:  # noqa: BLE001 -- a typed result, never a raise
            ran = {"status": "crash", "error": f"{type(exc).__name__}: {exc}", "data": None}
        res = evaluate(keys, ran, previews)
        res["seq"] = copies.seq
        return res
    except Exception as exc:  # noqa: BLE001 -- the copy itself failed
        return _unchecked(keys, "crash", f"{type(exc).__name__}: {exc}")
    finally:
        chip_copy.remove_chip_copies(copies)
