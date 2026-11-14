"""SM-side gate check: turns the lab worker's answer into one result per check.

The checks run on two chip copies (:mod:`core.chip_copy`): "before" (the chip
as it is, unsaved edits included) and "after" (plus the write's creates). The
lab side is ``generator/run_pulse_waveform.py``'s gate-check item, run by
:func:`core.lab_waveform.run_gate_check`. The checks, per gate:

- ``load_config``: the chip with the write loads and builds its config;
- ``ops``: the gate's ``apply()`` runs, it plays at least one pulse, and every
  operation it plays is in the config;
- ``length``: every played pulse length is a whole number of ns, a multiple of
  4 ns and at least 16 ns; a play-time ``duration=`` is at least 4 clock
  cycles (a short ``wait`` is a note);
- ``preview``: the preview samples equal the config's samples;
- ``timeline``: the static timeline (:mod:`core.static_timeline`); ``pass``
  means "drawn", not "compared with a measurement";
- ``range``: each side of the pulse (maximum and minimum, times the played
  scale, plus the channel offset) inside the port's output range.

Each check is ``{"status": "pass" | "fail" | "skipped" | "note",
"message": str}``. The before / after comparison is used for the chip-level
check only: when the chip as it is does not load or build its config, every
check is ``skipped`` with the chip's own error and ``checked`` is False, so an
unrelated fault of the chip is never blamed on the gate. A gate under check
that cannot run always fails, also when it already failed before the write.

The result is JSON-serialisable::

    {"status": "ok" | "no-env" | "timeout" | "crash" | "busy" | "invalid-create"
               | "no-gates",
     "checked": bool, "ok": bool, "chip_error": str | None,
     "error": str | None, "seq": int | None, "store_id": int | None,
     "leftover": str | None, "quam_config": {...} | None,
     "gates": [{"pair", "gate", "checks": {name: {"status", "message"}},
                "unchecked": [name], "partly_checked": [name],
                "timeline": {"rows": [{element, port, operation, start_ns,
                end_ns, level, scale, samples, constant, total, truncated,
                sampling_rate, length_ns, range_v, offset_v, port_kind}],
                "cannot_draw", "notes", "filtered_ports"} | None}]}

Each timeline row also carries the config's samples of the operation it
plays (``samples``: at most ``ROW_SAMPLE_CAP`` of ``total``, ``truncated``
when cut; ``constant`` for a constant waveform), the port's output range
``range_v`` (None when unknown) and the channel offset ``offset_v``.

"no-gates": the gate list was empty, an input error (nothing is copied or
run). ``ok`` means: nothing failed AND every gate was actually run by the lab's
code (its ``ops`` check is ``pass`` or ``note``), so a caller that reads only
``ok`` is safe. It does not mean everything was compared: ``unchecked`` lists
the checks that did not run (skipped, or a timeline that could not be drawn),
``partly_checked`` the checks that compared only some of the gate's
operations (their status is ``note``).
``seq`` and ``store_id`` are what :func:`chip_copy.store_at_seq` needs.

For the product rule the result also carries, for every ``status``:
``failed`` (any check of any gate is ``fail``, including the chip-level
check that the write broke), ``all_checked`` (the lab's code ran every gate
and no check was skipped, not run or only partly done), ``why_not_checked``
(a plain sentence when neither ``failed`` nor ``all_checked``, else null) and,
per gate, ``ran`` (``load_config`` and ``ops`` both ``pass`` or ``note``).
``ok`` == not ``failed`` and every gate's ``ops`` ran.
``leftover`` is a temporary folder that could not be removed; it is the one
path in the result, meant to be shown only to the local user.

Only the I (or single) waveform of an operation and its port are checked; the
Q waveform and Q port of an IQ or microwave element are not modelled.

Known limits: all gates of one run share one loaded machine on the lab side,
so a lab ``apply()`` that mutates the machine could make the order matter;
creates carry no key position, so the key order of "after" may differ from a
later write that inserts at an index (the content is equal).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
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
_WAIT_NOTE = ("QUA documents a minimum wait of 4 clock cycles (16 ns); check that this "
              "runs on your controller version")


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
            "unchecked": [c for c in CHECKS if checks[c]["status"] == "skipped"
                          or checks[c].get("not_run")],
            "partly_checked": [c for c in CHECKS if checks[c].get("partly")],
            "timeline": tl}


def _base(status, *, checked, error=None, chip_error=None, quam_config=None) -> dict:
    return {"status": status, "checked": checked, "ok": False, "chip_error": chip_error,
            "error": error, "seq": None, "store_id": None, "leftover": None,
            "quam_config": quam_config, "gates": []}


def _no_gates() -> dict:
    """An empty gate list is an input error: nothing is copied or run."""
    return _finalize(_base("no-gates", checked=False, error="no gate was given"))


def _unchecked(keys, status: str, error: str, msg: str | None = None) -> dict:
    msg = msg or f"Not checked with the lab's code: {error}."
    out = _base(status, checked=False, error=error)
    out["gates"] = [_entry(k, _all("skipped", msg)) for k in keys]
    return _finalize(out)


#: a copy folder name (and the path token around it) in a message
_COPY_NAME = re.compile(r"[^\s'\"]*sm_chipcopy_[A-Za-z0-9_]+[^\s'\"]*")


def scrub_copy_names(text: str) -> str:
    """Remove copy folders from *text* when the copies' own paths are not
    known: first the temp base joined with the copies' prefix, as a literal
    in every spelling (so a space in the user's folder name does not
    matter), then any remaining token holding a copy folder name."""
    import os
    import tempfile
    base = os.path.normpath(tempfile.gettempdir())
    for b in sorted({base, base.replace("\\", "/"), base.replace("\\", "\\\\")},
                    key=len, reverse=True):
        for sep in ("\\\\", "\\", "/"):
            pat = re.escape(b + sep + "sm_chipcopy_") + r"[A-Za-z0-9_]*"
            text = re.sub(pat, "<chip>", text, flags=re.IGNORECASE)
    return _COPY_NAME.sub("<chip>", text)

_WHY_STATUS = {
    "no-gates": "No gate was given to check.",
    "no-env": "No Python environment is selected, so the lab's code did not run.",
    "busy": "The lab environment is busy with another check, so the gate was not checked; "
            "try again.",
    "timeout": "The lab's code did not answer in time, so the gate was not checked.",
}

_CHECK_WORDS = {"load_config": "loading the chip", "ops": "running the gate",
                "length": "the pulse lengths", "preview": "the preview comparison",
                "timeline": "the timeline", "range": "the output range"}


def _names(names: list[str]) -> str:
    if len(names) == 1:
        return f"the gate {names[0]}"
    return "the gates " + ", ".join(names[:-1]) + " and " + names[-1]


def _gates_sentence(gates: list) -> str | None:
    """One short sentence per reason, over ALL gates: first the gates the
    lab's code did not run, then the gates not fully checked; gates with the
    same reason are named together."""
    not_run: dict[str, list[str]] = {}
    partly: dict[str, list[str]] = {}
    for g in gates:
        if not g["ran"]:
            ops = g["checks"]["ops"]
            if ops["status"] == "skipped" and "program text" in ops["message"]:
                why = "the program text could not be read"
            else:
                why = "the lab's data for it could not be read"
            not_run.setdefault(why, []).append(g["gate"])
            continue
        parts = list(dict.fromkeys(g["unchecked"] + g["partly_checked"]))
        if parts:
            what = ", ".join(_CHECK_WORDS.get(p, p) for p in parts)
            partly.setdefault(what, []).append(g["gate"])
    out = [f"For {_names(n)}, {why}." for why, n in not_run.items()]
    out += [f"For {_names(n)}, not fully checked: {what}." for what, n in partly.items()]
    return " ".join(s[0].upper() + s[1:] for s in out) or None


def _finalize(out: dict, *, write_broke: bool = False) -> dict:
    """Add ``failed``, ``all_checked``, ``why_not_checked`` and each gate's
    ``ran`` (see the module docstring). *write_broke*: the chip-level check
    failed because of this write ("after" does not load or build its config
    while "before" does); that is a failure even if no gate carries it."""
    gates = out.get("gates") or []
    for g in gates:
        g["ran"] = all(g["checks"][c]["status"] in ("pass", "note")
                       for c in ("load_config", "ops"))
    failed = write_broke or any(c["status"] == "fail" for g in gates
                                for c in g["checks"].values())
    all_checked = (out.get("status") == "ok" and bool(out.get("checked")) and bool(gates)
                   and all(g["ran"] and not g["unchecked"] and not g["partly_checked"]
                           for g in gates))
    why = None
    if not failed and not all_checked:
        status = out.get("status")
        if status in _WHY_STATUS:
            why = _WHY_STATUS[status]
        elif status != "ok":
            why = f"The lab check could not run: {str(out.get('error') or status).rstrip('.')}."
        elif out.get("chip_error"):
            why = ("The chip as it is does not load or build its config with the lab's code, "
                   "so the gate could not be checked.")
        else:
            why = _gates_sentence(gates) or "No gate was checked."
    out["failed"] = failed
    out["all_checked"] = all_checked
    out["why_not_checked"] = why
    return out


def evaluate(gates: Iterable, ran: dict, previews: dict | None = None) -> dict:
    """The per-gate checks from :func:`lab_waveform.run_gate_check`'s answer.

    *gates*: gate keys (see :func:`gate_key`). *previews*: ``{gate key:
    {(element, operation): samples list or constant}}``, keyed through the
    same :func:`gate_key`."""
    keys = [gate_key(g) for g in gates]
    if not keys:
        return _no_gates()
    previews = {gate_key(k): v for k, v in (previews or {}).items()}
    status = (ran or {}).get("status") or "crash"
    if status != "ok":
        return _unchecked(keys, status, (ran or {}).get("error") or status)
    data = ran.get("data") or {}
    before = data.get("before") or {}
    after = data.get("after") or {}
    chip_err = before.get("load_error") or before.get("config_error")
    if chip_err:
        what = "does not load" if before.get("load_error") else "does not build its config"
        msg = (f"The chip as it is {what} with the lab's code, so the gate could not be "
               f"checked. The lab's error: {chip_err}")
        out = _base("ok", checked=False, chip_error=chip_err,
                    quam_config=data.get("quam_config"))
        out["gates"] = [_entry(k, _all("skipped", msg)) for k in keys]
        return _finalize(out)
    out = _base("ok", checked=True, quam_config=data.get("quam_config"))
    after_err = after.get("load_error") or after.get("config_error")
    for k in keys:
        checks, tl = _one_gate(k, data, after, after_err, previews.get(k) or {})
        out["gates"].append(_entry(k, checks, tl))
    out["ok"] = bool(out["gates"]) and all(
        c["status"] != "fail" for g in out["gates"] for c in g["checks"].values()) and all(
        g["checks"]["ops"]["status"] in ("pass", "note") for g in out["gates"])
    return _finalize(out, write_broke=bool(after_err))


def _guard(fn, *args) -> dict:
    """One check; data it cannot read makes only THIS check skipped."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return _r("skipped", "Not checked: the lab's data for this check has a form SM "
                             "does not read.")


def _one_gate(key, data, after, after_err, preview):
    name = key[1]
    try:
        checks: dict[str, dict] = {}
        g = (data.get("gates") or {}).get(macro_path(*key))
        old = (data.get("before_gates") or {}).get(macro_path(*key))
        if after_err:
            what = "does not load" if after.get("load_error") else "does not build its config"
            checks["load_config"] = _r("fail", f"With this write the chip {what} with the "
                                               f"lab's code. The lab's error: {after_err}")
        else:
            checks["load_config"] = _r("pass", "The chip loads and builds its config with "
                                               "this write.")
        if after.get("load_error") or g is None:
            for c in CHECKS[1:]:
                checks[c] = _r("skipped", "Not checked: the chip with this write does not load.")
            return checks, None
        if g.get("error"):
            msg = _apply_message(name, g["error"])
            if old is not None and old.get("error"):
                if _norm_err(old["error"]) == _norm_err(g["error"]):
                    msg += " This gate already failed this way before this change."
                else:
                    msg += " This gate also failed before this change, with another error."
            checks["ops"] = _r("fail", msg)
            for c in CHECKS[2:]:
                checks[c] = _r("skipped", f"Not checked: the gate {name} does not run.")
            return checks, None
        no_cfg = bool(after.get("config_error"))
        ops = g.get("ops") or []
        if g.get("script_unreadable"):
            checks["ops"] = _r("skipped", f"Not checked: the program text of the gate {name} "
                                          "could not be read, so SM cannot tell what it plays.")
        else:
            checks["ops"] = _guard(_ops_check, name, ops, int(g.get("unresolved_plays") or 0),
                                   no_cfg)
        found = [o for o in ops if isinstance(o, dict) and o.get("found")]
        checks["length"] = _guard(_length_check, found, no_cfg, g.get("script"))
        checks["preview"] = _guard(_preview_check, found, preview, no_cfg)
        tl = None
        if g.get("script") and not no_cfg:
            tl = static_timeline.static_timeline(g["script"], found)
            if tl["cannot_draw"]:
                why = tl["cannot_draw"]
                if why.startswith("cannot draw: "):
                    why = why[len("cannot draw: "):]
                checks["timeline"] = _r("note", f"The timeline could not be drawn: {why}.",
                                        not_run=True)
            else:
                checks["timeline"] = _r("pass", f"{len(tl['rows'])} pulses placed.")
        else:
            checks["timeline"] = _r("note", "The timeline could not be drawn: the gate's "
                                            "program or the config is missing.", not_run=True)
        checks["range"] = _guard(_range_check, found, tl, no_cfg)
        return checks, with_samples(tl, found)
    except Exception:  # noqa: BLE001 -- one gate never takes the others down
        return _all("skipped", f"Not checked: the lab's data for the gate {name} has a form "
                               "SM does not read."), None


_ADDR = re.compile(r"0x[0-9a-fA-F]+")


def _norm_err(err) -> str:
    """The error text as compared: memory addresses do not count (the temp
    folders were already replaced by the runner)."""
    return _ADDR.sub("0x?", str(err or "")).strip()


_KEY_RE = re.compile(r"[Oo]peration '([^']+)' not found in channel '([^']+)'")


def _apply_message(name: str, err: str) -> str:
    m = _KEY_RE.search(err or "")
    if m:
        return (f"The gate {name} plays operation '{m.group(1)}', which channel "
                f"'{m.group(2)}' does not have.")
    return f"The gate {name} does not run with the lab's code. The lab's error: {err}"


def _ops_check(name, ops, unresolved, no_cfg) -> dict:
    if no_cfg:
        return _r("skipped", "Not checked: the config was not built.")
    missing = [o for o in ops if not o.get("found")]
    if missing:
        return _r("fail", " ".join(f"The operation {o.get('operation')} is not in the "
                                   f"config of {o.get('element')}." for o in missing))
    if not ops and not unresolved:
        return _r("fail", f"The gate {name} plays no pulse.")
    if unresolved:
        return _r("note", f"{len(ops)} played operations are in the config. {unresolved} "
                          "plays use a name SM could not read; they were not checked.",
                  partly=True)
    return _r("pass", f"The gate {name} runs; all {len(ops)} played operations are in "
                      "the config.")


def _whole(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return int(v)
    return None


def _length_check(found: list, no_cfg: bool, script=None) -> dict:
    if no_cfg:
        return _r("skipped", "Not checked: the config was not built.")
    bad, notes, stored = [], [], []
    for kind, stmt in static_timeline.short_timing(script):
        if kind == "play":
            bad.append(f"The statement {stmt} plays for less than "
                       f"{static_timeline.MIN_CYCLES} clock cycles "
                       f"({static_timeline.MIN_CYCLES * LENGTH_STEP_NS} ns) or not a "
                       "whole number of cycles")
        else:
            notes.append(f"The statement {stmt} waits less than "
                         f"{static_timeline.MIN_CYCLES} clock cycles")
    for o in found:
        n, w = o.get("length"), _whole(o.get("length"))
        where = f"{o['operation']} on {o['element']}"
        if w is None:
            bad.append(f"A pulse length must be a whole number of ns: {where} is {n!r} ns")
        elif w % LENGTH_STEP_NS or w < LENGTH_MIN_NS:
            bad.append(f"A pulse length must be a multiple of {LENGTH_STEP_NS} ns and at "
                       f"least {LENGTH_MIN_NS} ns: {where} is {w} ns")
        elif not isinstance(n, int):
            stored.append(f"The length of {where} is stored as {n!r}")
    if bad:
        return _r("fail", _sentences(bad))
    if not found and not notes:
        return _r("skipped", "No played operation to check.")
    extra = (" " + _sentences(stored)) if stored else ""
    if notes:
        return _r("note", _sentences(notes) + " " + _WAIT_NOTE + "." + extra)
    return _r("pass", f"All {len(found)} pulse lengths are multiples of {LENGTH_STEP_NS} ns "
                      f"and at least {LENGTH_MIN_NS} ns." + extra)


def _sentences(parts) -> str:
    return " ".join(p + "." for p in parts)


def _unreadable(info: dict) -> str | None:
    """Why the operation's waveform cannot be judged, else None."""
    wf = info.get("waveform")
    if not isinstance(wf, dict):
        return "the lab sent no waveform for it"
    if wf.get("error"):
        return str(wf["error"])
    if wf.get("type") == "constant" and wf.get("sample") is None and not wf.get("nonfinite"):
        return "the constant waveform has no sample"
    return None


def _nonfinite(info: dict) -> bool:
    wf = info.get("waveform") or {}
    if wf.get("nonfinite"):
        return True
    vals = [wf.get("sample")] if wf.get("type") == "constant" else (wf.get("samples") or [])
    for v in vals:
        if v is None and wf.get("type") != "constant":
            return True
        if isinstance(v, (int, float)) and not math.isfinite(float(v)):
            return True
    return False


def _samples(info: dict) -> list[float] | None:
    """The finite samples of the waveform, or None when it cannot be read or
    holds a non-finite value (never a shortened or substituted list)."""
    if _unreadable(info) or _nonfinite(info):
        return None
    wf = info.get("waveform") or {}
    if wf.get("type") == "constant":
        return [float(wf.get("sample"))] * max(1, _whole(info.get("length")) or 1)
    return [float(s) for s in (wf.get("samples") or [])]


def _finite_list(values) -> bool:
    try:
        return all(math.isfinite(float(v)) for v in values)
    except (TypeError, ValueError):
        return False


def _iq_note(infos) -> str:
    return _IQ_NOTE if any(i.get("iq") for i in infos) else ""


def _preview_check(found: list, preview: dict, no_config: bool) -> dict:
    if no_config:
        return _r("skipped", "Not checked: the config was not built.")
    if not preview:
        return _r("skipped", "No preview was given to compare.")
    bad, partial, skipped, compared, used = [], [], [], 0, []
    played = {(i["element"], i["operation"]) for i in found}
    for info in found:
        key = (info["element"], info["operation"])
        if key not in preview:
            continue
        used.append(info)
        name = f"{key[1]} on {key[0]}"
        want = preview[key]
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            want = [float(want)] * max(1, _whole(info.get("length")) or 1)
        if not isinstance(want, (list, tuple)) or not _finite_list(want):
            skipped.append(f"For {name}: the preview of this pulse holds values that are "
                           "not numbers")
            continue
        why = _unreadable(info)
        if why:
            skipped.append(f"For {name}: {why}")
            continue
        if _nonfinite(info):
            bad.append(f"For {name}: the waveform contains values that are not numbers")
            continue
        have = _samples(info) or []
        wf = info.get("waveform") or {}
        truncated = bool(wf.get("truncated"))
        rate = info.get("sampling_rate")
        if not truncated and len(want) != len(have):
            if isinstance(rate, (int, float)) and rate != _BASE_RATE:
                skipped.append(f"For {name}: {len(want)} preview samples, {len(have)} in the "
                               f"config at {rate:g} samples/s, so they were not compared")
            else:
                bad.append(f"For {name}: {len(want)} preview samples, {len(have)} in the config")
            continue
        n_cmp = min(len(want), len(have)) if truncated else len(have)
        compared += 1
        diffs = [i for i in range(n_cmp) if not abs(float(want[i]) - float(have[i])) <= PREVIEW_TOL]
        if diffs:
            i = diffs[0]
            bad.append(f"For {name}: sample {i} differs by {abs(float(want[i]) - float(have[i])):.3g}")
        elif truncated:
            partial.append(f"For {name}: the first {n_cmp} of {wf.get('total')} samples were "
                           "compared, the rest was not")
    no_preview = [f"{o} on {e}" for e, o in sorted(played) if (e, o) not in preview]
    extra = [f"{o} on {e}" for e, o in sorted(preview) if (e, o) not in played]
    cover = ""
    if no_preview:
        cover += " No preview for: " + ", ".join(no_preview) + "."
    if extra:
        cover += " Preview given for operations the gate does not play: " + ", ".join(extra) + "."
    if bad:
        return _r("fail", "The preview differs from the config. " + _sentences(bad) + cover)
    if partial or skipped:
        text = _sentences(partial + skipped)
        if not compared:
            return _r("skipped", "Not compared. " + text + cover)
        return _r("note", f"Partly compared ({compared} of {len(found)} played). " + text
                  + cover + _iq_note(used), partly=True)
    if not compared:
        return _r("skipped", "No preview matches a played operation." + cover)
    if cover:
        return _r("note", f"{compared} of {len(found)} played waveforms equal the preview "
                          f"(tolerance {PREVIEW_TOL:g})." + cover + _iq_note(used), partly=True)
    return _r("pass", f"{compared} of {len(found)} played waveforms equal the preview "
                      f"(tolerance {PREVIEW_TOL:g})." + _iq_note(used))


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


def _v(x: float) -> str:
    """A voltage in plain units: ``3 V``, ``0.55 V`` (4 significant digits)."""
    return f"{float(x):.4g} V".replace("-0 V", "0 V")


def _port_text(port) -> str:
    if isinstance(port, (list, tuple)):
        return "/".join(str(p) for p in port)
    return str(port)


def _range_check(found: list, tl, no_config: bool) -> dict:
    if no_config:
        return _r("skipped", "Not checked: the config was not built.")
    if not found:
        return _r("skipped", "No played operation to check.")
    nan_ops = [i["operation"] for i in found if not _unreadable(i) and _nonfinite(i)]
    if nan_ops:
        return _r("fail", _sentences([f"The waveform of {op} contains values that are not "
                                      "numbers" for op in nan_ops]))
    if tl is None or tl.get("cannot_draw"):
        return _r("skipped", "The output range of this gate was not checked: the played "
                             "scale comes from the timeline, and the timeline could not be "
                             "drawn (a loop, a variable or a value set while the program "
                             "runs, see the timeline check).")
    scales: dict = {}
    for row in tl.get("rows") or []:
        scales.setdefault((row.element, row.operation), set()).add(row.scale)
    bad, ok_names, not_checked = [], [], []
    for info in found:
        key = (info["element"], info["operation"])
        name = f"{key[1]} on {key[0]}"
        port = _port_text(info.get("port"))
        rng = port_range(info)
        if rng is None:
            not_checked.append(f"{name} (the output range of port {port} is not known)")
            continue
        if (info.get("waveform") or {}).get("truncated"):
            not_checked.append(f"{name} (the waveform was too long to check fully)")
            continue
        why = _unreadable(info)
        if why:
            not_checked.append(f"{name} ({why})")
            continue
        samples = _samples(info)
        offset = 0.0 if info.get("port_kind") == "mw" else float(info.get("offset") or 0.0)
        one_bad = False
        for k in sorted(scales.get(key) or ()):
            vals = [s * k for s in samples]
            hi, lo = max(vals) + offset, min(vals) + offset
            if hi > rng + _SAMPLE_EPS:
                one_bad = True
                bad.append(f"For {name}, port {port}: {_v(hi)} is above this port's limit of "
                           f"{_v(rng)} (channel offset {_v(offset)}, play scale {k:g})")
            if lo < -rng - _SAMPLE_EPS:
                one_bad = True
                bad.append(f"For {name}, port {port}: {_v(lo)} is below this port's limit of "
                           f"{_v(-rng)} (channel offset {_v(offset)}, play scale {k:g})")
        if not one_bad:
            ok_names.append(name)
    if bad:
        return _r("fail", "The output range is exceeded. " + _sentences(bad))
    if not ok_names:
        return _r("skipped", "The output range was not checked for: "
                  + ", ".join(not_checked) + ".")
    if not_checked:
        return _r("note", "Inside the output range: " + ", ".join(ok_names) + ". Not checked: "
                  + ", ".join(not_checked) + "." + _iq_note(found), partly=True)
    return _r("pass", "Inside the output range: " + ", ".join(ok_names) + "."
              + _iq_note(found))


#: the most samples one timeline row carries to the screen
ROW_SAMPLE_CAP = 4000


def _row_samples(info: dict | None) -> dict:
    """The config's samples of one played operation for the picture, plus
    the port's range and the channel offset. A list longer than
    :data:`ROW_SAMPLE_CAP` (or one the lab already shortened) is cut and
    marked ``truncated``; a waveform SM cannot read carries no samples."""
    out = {"samples": None, "constant": None, "total": None, "truncated": False,
           "sampling_rate": None, "length_ns": None, "range_v": None, "offset_v": 0.0,
           "port_kind": None}
    if not isinstance(info, dict):
        return out
    out["sampling_rate"] = info.get("sampling_rate")
    out["length_ns"] = info.get("length")
    out["range_v"] = port_range(info)
    out["port_kind"] = info.get("port_kind")
    try:
        out["offset_v"] = (0.0 if info.get("port_kind") == "mw"
                           else float(info.get("offset") or 0.0))
    except (TypeError, ValueError):
        out["offset_v"] = None
    if _unreadable(info) or _nonfinite(info):
        return out
    wf = info.get("waveform") or {}
    if wf.get("type") == "constant":
        out["constant"] = float(wf.get("sample"))
        out["total"] = _whole(info.get("length"))
        return out
    samples = [float(v) for v in (wf.get("samples") or [])]
    total = wf.get("total") if isinstance(wf.get("total"), int) else len(samples)
    out["total"] = total
    out["truncated"] = bool(wf.get("truncated")) or len(samples) > ROW_SAMPLE_CAP
    out["samples"] = samples[:ROW_SAMPLE_CAP]
    return out


def with_samples(tl: dict | None, found: list) -> dict | None:
    """*tl* with each row as a dict that also carries :func:`_row_samples`
    of the operation it plays (the same config the checks read)."""
    if tl is None:
        return None
    table: dict = {}
    for info in found:
        if isinstance(info, dict):
            table.setdefault((info.get("element"), info.get("operation")), info)
    out = dict(tl)
    rows = []
    for r in tl.get("rows") or []:
        d = r._asdict() if hasattr(r, "_asdict") else dict(r)
        d.update(_row_samples(table.get((d.get("element"), d.get("operation")))))
        rows.append(d)
    out["rows"] = rows
    return out


def _scrub(res: dict, copies) -> dict:
    """Replace the temporary folders in every message by ``<chip>`` (the
    runner already does this; this is the second line for SM-side text)."""
    if copies is None:
        return res
    from quam_state_manager.generator.path_scrub import scrub_paths
    folders = [str(copies.before), str(copies.after)]

    def fix(v):
        return scrub_paths(v, folders) if isinstance(v, str) else v
    for k in ("error", "chip_error"):
        res[k] = fix(res.get(k))
    for g in res.get("gates") or []:
        for c in g["checks"].values():
            c["message"] = fix(c["message"])
    return res


def check_gates(store, python_path: str | None, gates: Iterable,
                creates: Iterable[tuple[str, Any]] = (), *,
                previews: dict | None = None,
                runner: Callable | None = None) -> dict:
    """Copy the chip (before / after *creates*), run the lab check without
    holding the store lock, remove the copies, and evaluate. The result
    carries ``seq`` and ``store_id``, so a later write can refuse when the
    store moved (:func:`chip_copy.store_at_seq`). A create the real write
    would refuse gives ``status`` "invalid-create" with ``load_config``
    failed, naming the path. Writes nothing into the store."""
    keys = [gate_key(g) for g in gates]
    if not keys:
        return _no_gates()
    creates = [(str(p), v) for p, v in creates]
    if not python_path:
        return _unchecked(keys, "no-env", "no Python environment is selected")
    if runner is None:
        from quam_state_manager.core.lab_waveform import run_gate_check as runner
    copies = None
    try:
        try:
            copies = chip_copy.make_chip_copies(store, creates)
        except chip_copy.CopyError as exc:
            res = _unchecked(keys, "crash", str(exc))
            res["leftover"] = exc.leftover
            return _finalize(res)
        except chip_copy.CreateError as exc:
            res = _unchecked(keys, "invalid-create", str(exc),
                             "Not checked: the gate cannot be written.")
            for g in res["gates"]:
                g["checks"]["load_config"] = _r(
                    "fail", f"The write would be refused at {exc.path}: {exc.reason}")
                g["unchecked"] = [c for c in CHECKS if g["checks"][c]["status"] == "skipped"]
            return _finalize(res)
        try:
            ran = runner(python_path, str(copies.before), str(copies.after),
                         [macro_path(*k) for k in keys], root_class=copies.root_class)
        except Exception as exc:  # noqa: BLE001 -- a typed result, never a raise
            ran = {"status": "crash", "error": f"{type(exc).__name__}: {exc}", "data": None}
        res = _scrub(evaluate(keys, ran, previews), copies)
        res["seq"] = copies.seq
        res["store_id"] = copies.store_id
    except Exception as exc:  # noqa: BLE001 -- never a temp path in a message
        from quam_state_manager.generator.path_scrub import scrub_paths
        text = f"{type(exc).__name__}: {exc}"
        if copies is not None:
            text = scrub_paths(text, [str(copies.before), str(copies.after)])
        text = scrub_copy_names(text)
        res = _unchecked(keys, "crash", text)
        if copies is None:
            res["leftover"] = getattr(exc, "sm_leftover", None)
            return _finalize(res)
    res["leftover"] = chip_copy.remove_chip_copies(copies)
    return _finalize(res)
