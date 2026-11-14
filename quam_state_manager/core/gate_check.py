"""SM-side gate check: turns the lab worker's answer into one result per check.

The checks run on two chip copies (:mod:`core.chip_copy`): "before" (the chip
as it is, unsaved edits included) and "after" (plus the new gate's creates).
The lab side is ``generator/run_pulse_waveform.py``'s gate-check item, run by
:func:`core.lab_waveform.run_gate_check`. The checks, per gate:

- ``load_config``: the "after" chip loads and builds its config;
- ``ops``: the macro's ``apply()`` runs and every operation it plays is in the
  config (a missing one shows up as a KeyError naming operation and channel);
- ``preview``: the preview samples equal the config's samples;
- ``timeline``: the static timeline (:mod:`core.static_timeline`);
- ``range``: pulse maximum + channel offset inside the port's output range.

Each result is ``{"status": "pass" | "fail" | "skipped" | "note",
"message": str}``. The first two fail only when "before" passed and "after"
does not, so an old, unrelated fault never blocks a new gate. When "before"
itself does not load or build its config, every check is ``skipped`` with the
chip's own error and ``checked`` is False. A runner crash, a timeout and "no
env selected" come back as a typed result (``status``), never as an exception.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from quam_state_manager.core import chip_copy, static_timeline
# the one source of the output-port sample ranges (direct and amplified LF
# ports are in volts, MW ports are normalised)
from quam_state_manager.core.diagnostics import (
    _LF_AMPLIFIED_BOUND, _LF_DIRECT_BOUND, _MW_SAMPLE_BOUND, _SAMPLE_EPS)

CHECKS = ("load_config", "ops", "preview", "timeline", "range")

#: preview vs config sample tolerance (volts, or normalised units on an MW
#: port). Both sides come from the same class code through float64 math, so a
#: real difference is a different waveform; 1e-6 is far below one DAC step
#: (0.5 V / 2**15 is about 1.5e-5 V on a direct LF port), so a difference it
#: lets through cannot change an output sample, and it absorbs the decimal
#: rounding of a JSON round trip.
PREVIEW_TOL = 1e-6


def _r(status: str, message: str, **extra) -> dict:
    return {"status": status, "message": message, **extra}


def macro_path(pair: str, gate: str) -> str:
    return f"qubit_pairs.{pair}.macros.{gate}"


def _gate_key(g) -> tuple[str, str]:
    if isinstance(g, dict):
        return str(g["pair"]), str(g["gate"])
    pair, gate = g
    return str(pair), str(gate)


def _all(status: str, message: str) -> dict:
    return {c: _r(status, message) for c in CHECKS}


def _unchecked(gates, status: str, error: str) -> dict:
    msg = f"Not checked with the lab's code: {error}"
    return {"status": status, "checked": False, "chip_error": None,
            "error": error, "ok": False, "quam_config": None, "seq": None,
            "gates": {g: {"checks": _all("skipped", msg), "timeline": None}
                      for g in gates}}


def evaluate(gates: Iterable, ran: dict, previews: dict | None = None) -> dict:
    """The per-gate checks from :func:`lab_waveform.run_gate_check`'s answer.

    *gates*: ``[(pair, gate)]`` or ``[{"pair", "gate"}]``. *previews*:
    ``{(pair, gate): {(element, operation): samples list or constant}}``."""
    keys = [_gate_key(g) for g in gates]
    previews = previews or {}
    status = (ran or {}).get("status") or "crash"
    if status != "ok":
        return _unchecked(keys, status, (ran or {}).get("error") or status)
    data = ran.get("data") or {}
    before = data.get("before") or {}
    after = data.get("after") or {}
    out = {"status": "ok", "checked": True, "chip_error": None, "error": None,
           "quam_config": data.get("quam_config"), "seq": None, "gates": {}}
    chip_err = before.get("load_error") or before.get("config_error")
    if chip_err:
        what = "does not load" if before.get("load_error") else "does not build its config"
        msg = (f"The chip as it is {what} with the lab's code, so the gate could "
               f"not be checked: {chip_err}")
        out.update(checked=False, chip_error=chip_err)
        for k in keys:
            out["gates"][k] = {"checks": _all("skipped", msg), "timeline": None}
        out["ok"] = False
        return out
    after_err = after.get("load_error") or after.get("config_error")
    for k in keys:
        out["gates"][k] = _one_gate(k, data, after, after_err, previews.get(k) or {})
    out["ok"] = all(c["status"] != "fail" for g in out["gates"].values()
                    for c in g["checks"].values())
    return out


def _one_gate(key, data, after, after_err, preview) -> dict:
    checks: dict[str, dict] = {}
    path = macro_path(*key)
    g = (data.get("gates") or {}).get(path)
    if after_err:
        what = "does not load" if after.get("load_error") else "does not build its config"
        checks["load_config"] = _r("fail", f"With the new gate the chip {what}: {after_err}")
    else:
        checks["load_config"] = _r("pass", "The chip loads and builds its config with the new gate.")
    if after.get("load_error") or g is None:
        msg = "Not checked: the chip with the new gate does not load."
        for c in CHECKS[1:]:
            checks[c] = _r("skipped", msg)
        return {"checks": checks, "timeline": None}
    if g.get("error"):
        checks["ops"] = _r("fail", _apply_message(g["error"]), error=g["error"])
        msg = "Not checked: the gate's apply() failed."
        for c in CHECKS[2:]:
            checks[c] = _r("skipped", msg)
        return {"checks": checks, "timeline": None}
    ops = g.get("ops") or []
    missing = [o for o in ops if not o.get("found")]
    if after.get("config_error"):
        checks["ops"] = _r("skipped", "Not checked: the config was not built.")
    elif missing:
        checks["ops"] = _r("fail", "; ".join(o.get("error") or
                                             f"{o.get('operation')} is not in {o.get('element')}"
                                             for o in missing))
    else:
        checks["ops"] = _r("pass", f"apply() runs; all {len(ops)} played operations "
                                   "are in the config.")
    found = [o for o in ops if o.get("found")]
    checks["preview"] = _preview_check(found, preview, bool(after.get("config_error")))
    tl = None
    if g.get("script") and not after.get("config_error"):
        tl = static_timeline.static_timeline(g["script"], found)
        if tl["cannot_draw"]:
            checks["timeline"] = _r("note", f"The timeline is not drawn: {tl['cannot_draw']}")
        else:
            checks["timeline"] = _r("pass", f"{len(tl['rows'])} pulses placed.")
    else:
        checks["timeline"] = _r("note", "The timeline is not drawn: no program or config.")
    checks["range"] = _range_check(found, tl, bool(after.get("config_error")))
    return {"checks": checks, "timeline": tl}


_KEY_RE = re.compile(r"[Oo]peration '([^']+)' not found in channel '([^']+)'")


def _apply_message(err: str) -> str:
    m = _KEY_RE.search(err or "")
    if m:
        return (f"The gate plays operation '{m.group(1)}', which channel "
                f"'{m.group(2)}' does not have.")
    return f"The gate's apply() failed: {err}"


def _samples(info: dict) -> list[float] | None:
    wf = info.get("waveform") or {}
    if wf.get("type") == "constant":
        try:
            return [float(wf.get("sample"))] * int(info.get("length") or 1)
        except (TypeError, ValueError):
            return None
    return [float(s) for s in (wf.get("samples") or []) if s is not None]


def _preview_check(found: list, preview: dict, no_config: bool) -> dict:
    if no_config:
        return _r("skipped", "Not checked: the config was not built.")
    if not preview:
        return _r("skipped", "No preview was given to compare.")
    bad, compared = [], 0
    for info in found:
        key = (info["element"], info["operation"])
        if key not in preview:
            continue
        if (info.get("waveform") or {}).get("truncated"):
            bad.append(f"{key[0]} {key[1]}: the config waveform is too long to compare")
            continue
        want = preview[key]
        if isinstance(want, (int, float)):
            want = [float(want)] * int(info.get("length") or 1)
        have = _samples(info) or []
        compared += 1
        if len(want) != len(have):
            bad.append(f"{key[0]} {key[1]}: {len(want)} preview samples, "
                       f"{len(have)} in the config")
            continue
        worst = max((abs(float(a) - float(b)) for a, b in zip(want, have)), default=0.0)
        if not worst <= PREVIEW_TOL:
            n = next(i for i, (a, b) in enumerate(zip(want, have))
                     if not abs(float(a) - float(b)) <= PREVIEW_TOL)
            bad.append(f"{key[0]} {key[1]}: sample {n} differs by {worst:.3g}")
    if bad:
        return _r("fail", "The preview differs from the config: " + "; ".join(bad))
    if not compared:
        return _r("skipped", "No preview matches a played operation.")
    return _r("pass", f"{compared} waveforms equal the preview (tolerance {PREVIEW_TOL:g}).")


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
    scales: dict = {}
    for row in (tl or {}).get("rows") or []:
        scales.setdefault((row.element, row.operation), set()).add(row.scale)
    bad, unknown, checked = [], [], 0
    for info in found:
        key = (info["element"], info["operation"])
        rng = port_range(info)
        if rng is None:
            unknown.append(f"{key[0]} {key[1]}")
            continue
        samples = _samples(info) or [0.0]
        offset = 0.0 if info.get("port_kind") == "mw" else float(info.get("offset") or 0.0)
        for k in sorted(scales.get(key) or {1.0}):
            vals = [s * k for s in samples]
            hi, lo = max(vals) + offset, min(vals) + offset
            checked += 1
            if hi > rng + _SAMPLE_EPS or lo < -rng - _SAMPLE_EPS:
                edge = hi if hi > rng + _SAMPLE_EPS else lo
                bad.append(f"{key[0]} {key[1]}: maximum + offset {edge:+.4f} is outside "
                           f"+/-{rng:g} (offset {offset:+.4f}, scale {k:g})")
    if bad:
        return _r("fail", "Output range exceeded: " + "; ".join(bad))
    if unknown and not checked:
        return _r("skipped", "Skipped: the output range of the port is not known for "
                             + ", ".join(unknown) + ".")
    msg = "Maximum + offset is inside the output range."
    if unknown:
        msg += " Skipped (range not known): " + ", ".join(unknown) + "."
    return _r("pass", msg)


def check_gates(store, python_path: str | None, gates: Iterable,
                creates: Iterable[tuple[str, Any]] = (), *,
                previews: dict | None = None,
                runner: Callable | None = None) -> dict:
    """Copy the chip (before / after *creates*), run the lab check without
    holding the store lock, remove the copies, and evaluate. The result
    carries ``seq``, the store's sequence value at the copy, so a later write
    can refuse when the store moved (:func:`chip_copy.store_at_seq`).
    Writes nothing into the store."""
    keys = [_gate_key(g) for g in gates]
    if not python_path:
        return _unchecked(keys, "no-env", "no Python environment is selected")
    if runner is None:
        from quam_state_manager.core.lab_waveform import run_gate_check as runner
    copies = None
    try:
        copies = chip_copy.make_chip_copies(store, creates)
        try:
            ran = runner(python_path, str(copies.before), str(copies.after),
                         [macro_path(*k) for k in keys])
        except Exception as exc:  # noqa: BLE001 -- a typed result, never a raise
            ran = {"status": "crash", "error": f"{type(exc).__name__}: {exc}", "data": None}
        res = evaluate(keys, ran, previews)
        res["seq"] = copies.seq
        return res
    except Exception as exc:  # noqa: BLE001 -- the copy itself failed
        return _unchecked(keys, "crash", f"{type(exc).__name__}: {exc}")
    finally:
        chip_copy.remove_chip_copies(copies)
