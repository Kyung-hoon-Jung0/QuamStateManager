"""Draw pulse waveforms with the LAB's own class code (docs/218 adaptive pulses).

Runs under the *external* interpreter the user selected (like
``probe_state_schema.py``), so at import time it uses ONLY the standard
library; the pulse classes are imported inside :func:`draw`.

Why this exists: ``core/waveform_synth.py`` mirrors quam's own pulse classes.
A class the lab wrote (a CZ flux pulse, a readout-weights pulse) has no mirror
there, and transcribing it into SM would ship a COPY that goes stale the day
the lab edits its module (docs/189 §2). The generated config is one answer,
but it is only as fresh as the last ``generate_config()`` run and it cannot
draw a pulse that does not exist yet (the create form). This script asks the
class itself: build the dataclass from the pulse's fields and call quam's own
``Pulse.calculate_waveform()`` -- the method ``_config_add_waveforms`` calls
when the config is generated, so both answers come from the same line of the
lab's code.

Input (``--in`` JSON)::

    {"items": [{"qclass": "pkg.mod.Class", "params": {...}}, ...],
     "max_samples": 20000}

Output (``--out`` JSON)::

    {"status": "ok", "python": "3.11.9",
     "items": [{"ok": true, "canonical": "...", "i": [...], "q": [...] | null,
                "iq": false, "kind": "arbitrary" | "constant", "length": 88,
                "dropped": ["field the class does not declare"],
                "error": null}, ...],
     "sources": {"<lab file>": [mtime_ns, size], ...},
     "error": null}

``sources`` are the out-of-env files imported to answer (see
``_script_common.out_of_env_sources``) -- the SM side re-stats them on every
read of the cached waveform, so an edit to the lab's module is a miss.

Run standalone::  python run_pulse_waveform.py --in i.json --out o.json
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import math
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_common import out_of_env_sources  # noqa: E402

_MAX_SAMPLES = 20000


def _import_class(path: str):
    """``pkg.mod.Class`` (or ``pkg.mod.Outer.Inner``) -> the class object."""
    parts = path.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        mod_name = ".".join(parts[:cut])
        try:
            obj = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            if exc.name and (mod_name == exc.name or mod_name.startswith(exc.name + ".")):
                continue
            raise
        for attr in parts[cut:]:
            obj = getattr(obj, attr)
        if not isinstance(obj, type):
            raise TypeError(f"{path} is not a class")
        return obj
    raise ModuleNotFoundError(f"no importable module in {path!r}")


def _floats(seq) -> list:
    out = []
    for v in seq:
        f = float(v)
        out.append(f if math.isfinite(f) else None)
    return out


#: quam warns "This component is not part of any QuamRoot, using last
#: instantiated QuamRoot ..." when a pulse is built stand-alone while the
#: PROCESS holds a root from an earlier draw. A cold worker has no such root,
#: a warm one (core/lab_waveform._Worker) does, so the same pulse drew with
#: the note on one and not the other (final QA P3a). It describes this
#: process's history, not the pulse or the lab's class: never reported.
_PROCESS_STATE_WARNINGS = ("not part of any QuamRoot",)


def _is_process_state_warning(message: str) -> bool:
    return any(s in message for s in _PROCESS_STATE_WARNINGS)


#: a parameter of this shape is a QUESTION, not a value (SM's
#: ``core/pulse_refs.LAB_REF_KEY``): ``{"@sm_ref": {"qclass", "params",
#: "attr"}}`` -- build that pulse with its own class and read ``attr`` from it.
#: It is how a reference to another pulse's runtime property (a lab class's
#: ``lobe3_length``) or to a default only the class knows reaches the class
#: being drawn: quam answers it on the object, and so does this.
LAB_REF_KEY = "@sm_ref"


def _is_question(value) -> bool:
    return isinstance(value, dict) and set(value) == {LAB_REF_KEY}


def _answer(question: dict, depth: int = 0):
    """The value a reference question asks for, read from the target pulse
    built by its OWN class (its own questions answered first). Raises with a
    sentence naming what could not be read."""
    if depth > 20:
        raise ValueError("the references go round in a loop")
    qclass = question.get("qclass")
    attr = question.get("attr")
    if not isinstance(qclass, str) or not isinstance(attr, str):
        raise ValueError("a malformed reference question")
    cls = _import_class(qclass)
    fields = {f.name for f in dataclasses.fields(cls) if f.init}         if dataclasses.is_dataclass(cls) else None
    kwargs = {}
    for k, v in (question.get("params") or {}).items():
        if fields is not None and k not in fields:
            continue
        kwargs[k] = _answer(v[LAB_REF_KEY], depth + 1) if _is_question(v) else v
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        obj = cls(**kwargs)
        value = getattr(obj, attr)
    if isinstance(value, str) and value.startswith("#"):
        raise ValueError(f"{cls.__name__}.{attr} does not resolve ({value})")
    return value


def _draw_one(qclass: str, params: dict, max_samples: int) -> dict:
    res = {"ok": False, "canonical": None, "i": [], "q": None, "iq": False,
           "kind": None, "length": None, "dropped": [], "warnings": [],
           "error": None, "refs": {}}
    try:
        cls = _import_class(qclass)
    except BaseException as exc:  # noqa: BLE001 -- SystemExit from a lab import too
        # NOT the value's fault: this env cannot run the class at all (another
        # chip's env, a module not installed). A check that cannot run never
        # blocks -- the caller says so and lets the write through.
        res["error"] = f"could not import {qclass}: {type(exc).__name__}: {exc}"
        res["reason"] = "class-unavailable"
        return res
    res["canonical"] = f"{cls.__module__}.{cls.__qualname__}"
    try:
        from quam.components.pulses import Pulse
    except Exception as exc:  # noqa: BLE001
        res["error"] = f"quam is not importable in this environment: {exc}"
        res["reason"] = "class-unavailable"
        return res
    if not issubclass(cls, Pulse):
        res["error"] = f"{res['canonical']} is not a quam Pulse"
        return res
    if not dataclasses.is_dataclass(cls):
        res["error"] = f"{res['canonical']} is not a dataclass"
        return res

    fields = {f.name: f for f in dataclasses.fields(cls) if f.init}
    kwargs = {k: v for k, v in (params or {}).items()
              if k in fields and k != "__class__"}
    for k, v in list(kwargs.items()):
        if not _is_question(v):
            continue
        q = v[LAB_REF_KEY]
        try:
            kwargs[k] = _answer(q)
        except BaseException as exc:  # noqa: BLE001 -- named, never a raw string passed on
            res["error"] = (f"{k}: could not read {q.get('attr')} of "
                            f"{str(q.get('qclass')).rsplit('.', 1)[-1]}: "
                            f"{type(exc).__name__}: {exc}")
            return res
        if isinstance(kwargs[k], (bool, int, float, str)) or kwargs[k] is None:
            res["refs"][k] = kwargs[k]
    res["dropped"] = sorted(k for k in (params or {})
                            if k not in fields and k != "__class__")
    missing = [n for n, f in fields.items()
               if n not in kwargs
               and f.default is dataclasses.MISSING
               and f.default_factory is dataclasses.MISSING]  # type: ignore[misc]
    if missing:
        res["error"] = ("missing required field" + ("s" if len(missing) > 1 else "")
                        + ": " + ", ".join(missing))
        return res
    import warnings
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            pulse = cls(**kwargs)
            wf = pulse.calculate_waveform()
    except BaseException as exc:  # noqa: BLE001 -- the lab's own validation speaks
        res["error"] = f"{type(exc).__name__}: {exc}"
        return res
    # what the class itself said while drawing -- e.g. quam's "not attached to
    # a channel, cannot determine sampling rate": the pulse is drawn DETACHED
    # from its channel, and a class that reads the channel says so here
    res["warnings"] = list(dict.fromkeys(
        m for m in (str(w.message).strip() for w in caught)
        if m and not _is_process_state_warning(m)))[:5]
    if wf is None:
        res["error"] = "the class's calculate_waveform() returned None"
        return res

    length = None
    try:
        raw_len = pulse.length
        if isinstance(raw_len, (int, float)) and not isinstance(raw_len, bool):
            length = int(raw_len)
    except Exception:  # noqa: BLE001
        length = None

    import numpy as np
    arr = np.asarray(wf)
    if arr.ndim == 0:
        # a constant waveform: quam hands the config a single number, and the
        # LENGTH is what makes it samples. One that does not resolve (a
        # ``#./inferred_total_length`` on a class without that property) has
        # none to draw -- 16 invented samples were drawn here, silently (PU-08)
        if not length or length <= 0:
            try:
                shown = pulse.length
            except Exception as exc:  # noqa: BLE001
                shown = f"{type(exc).__name__}: {exc}"
            res["error"] = (f"its length does not resolve ({shown!r}), so this "
                            "constant waveform has no samples to draw")
            return res
        res["kind"] = "constant"
        arr = np.full(min(length, max_samples), arr.item())
    elif arr.ndim != 1:
        res["error"] = f"unsupported waveform shape {arr.shape}"
        return res
    else:
        res["kind"] = "arbitrary"
    if arr.size > max_samples:
        res["error"] = (f"{arr.size} samples exceeds the preview limit of "
                        f"{max_samples}")
        return res
    # the same test quam's _config_add_waveforms uses to pick I/Q
    is_iq = bool(np.iscomplexobj(arr))
    res["i"] = _floats(np.real(arr).astype(float).tolist())
    res["q"] = _floats(np.imag(arr).astype(float).tolist()) if is_iq else None
    res["iq"] = bool(is_iq)
    res["length"] = length if length is not None else int(arr.size)
    res["ok"] = True
    return res


#: an item whose "qclass" starts with this is a GATE check, not a drawing:
#: ``"@macro:<root QuamRoot class>"`` with params ``{"contents": <state+wiring
#: dict>, "macros": [dot-paths], "config": bool}`` (see :func:`_check_macros`;
#: ``config`` also runs ``generate_config()`` and answers ``config_error``)
MACRO_PREFIX = "@macro:"


def _node_at(root, path: str):
    obj = root
    for seg in path.split("."):
        if isinstance(obj, (list, tuple)):
            obj = obj[int(seg)]
        elif hasattr(obj, "__getitem__") and not hasattr(type(obj), seg)                 and not isinstance(obj, str):
            try:
                obj = obj[seg]
                continue
            except (KeyError, TypeError, IndexError):
                obj = getattr(obj, seg)
        else:
            obj = getattr(obj, seg)
    return obj


def _load_chip(cls, source):
    """``cls.load(source)`` (a contents dict or a state folder), quietly."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return cls.load(source)


def _apply_in_program(machine, path: str):
    """Run the macro at *path*'s own ``apply()`` inside a QUA ``program()``
    and return the program (every node that plays the gate does this)."""
    import warnings
    from qm.qua import program
    mac = _node_at(machine, path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with program() as prog:
            mac.apply()
    return prog


def _check_macros(root_class: str, params: dict) -> dict:
    """Load *contents* with the lab's OWN root class and call every named
    macro's own ``apply()`` inside a QUA ``program()`` -- exactly what every
    node that plays the gate does (CZGateTwoFlux.apply runs
    ``assert_lines_compatible``: control/target flat_length must match).

    ``macros`` answers per path: None (applied) or the error it raised. A
    failure to import or to load is reported, never guessed: the caller
    compares against the same check WITHOUT the edit, so only a macro the edit
    takes from applying to failing is ever refused."""
    res = {"ok": False, "error": None, "macros": {}, "kind": "macro",
           "i": [], "q": None, "iq": False, "length": None, "canonical": None,
           "dropped": [], "warnings": []}
    try:
        cls = _import_class(root_class)
        from qm.qua import program
    except BaseException as exc:  # noqa: BLE001
        res["error"] = f"could not import {root_class}: {type(exc).__name__}: {exc}"
        res["reason"] = "class-unavailable"
        return res
    res["canonical"] = f"{cls.__module__}.{cls.__qualname__}"
    import warnings
    try:
        machine = _load_chip(cls, params.get("contents") or {})
    except BaseException as exc:  # noqa: BLE001
        res["error"] = f"the chip does not load: {type(exc).__name__}: {exc}"
        res["load_failed"] = True
        return res
    first = None
    for path in params.get("macros") or []:
        try:
            _apply_in_program(machine, path)
            res["macros"][path] = None
        except BaseException as exc:  # noqa: BLE001 -- the lab's own check speaks
            msg = f"{type(exc).__name__}: {exc}"[:600]
            res["macros"][path] = msg
            if first is None:
                first = f"{path}.apply(): {msg}"
    if params.get("config"):
        # docs/218 (verifier 4): a delete that leaves a by-name mirror op
        # pointing at nothing still LOADS -- it is generate_config() that
        # fails, for the whole chip. Asked only when SM sends config=True.
        res["config_ran"] = True
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                machine.generate_config()
            res["config_error"] = None
        except BaseException as exc:  # noqa: BLE001 -- the lab's own code speaks
            res["config_error"] = _exc_where(exc)
    res["ok"] = first is None
    res["error"] = first
    return res


def _exc_where(exc: BaseException) -> str:
    """``Type: message`` plus WHERE it was raised -- quam's bare ``assert``
    has no message, and "AssertionError: " alone tells the user nothing."""
    msg = f"{type(exc).__name__}: {exc}".rstrip()
    try:
        tb = traceback.extract_tb(exc.__traceback__)
        if tb:
            fr = tb[-1]
            code = (fr.line or "").strip()
            msg += f" (at {Path(fr.filename).name}:{fr.lineno}" + (
                f": {code}" if code else "") + ")"
    except Exception:  # noqa: BLE001
        pass
    return msg[:600]


#: an item whose "qclass" starts with this only IMPORTS the named class (the
#: warm worker's pre-warm, w9/labwarm): the same imports a check makes before
#: it runs anything -- the lab's class, quam's ``Pulse`` and ``qm.qua`` --
#: and nothing else. No dataclass is built, no waveform drawn, no chip loaded.
IMPORT_PREFIX = "@import:"

#: an item whose class is this loads a state FOLDER (``params["state_folder"]``)
#: with its own ``__class__`` marker, strict (the Config Viewer's
#: ``--strict-input``), and answers ``config`` = ``generate_config()`` made
#: JSON-safe -- the add/remove env check, run on the warm worker
CONFIG_ITEM = "@config"


def _gen_config(params: dict) -> dict:
    import os
    res = {"ok": False, "error": None, "kind": "config", "config": None}
    folder = params.get("state_folder")
    if not isinstance(folder, str) or not folder:
        res["error"] = "no state folder given"
        return res
    old = os.environ.get("QUAM_STATE_PATH")
    try:
        import run_generate_config as rgc
        out = rgc.run_generate_config(Path(folder), strict=True)
        res["config"] = out.get("config")
        res["ok"] = bool(res["config"])
        if not res["ok"]:
            res["error"] = "generate_config() returned nothing"
    except BaseException as exc:  # noqa: BLE001 -- the env's own words
        res["error"] = _exc_where(exc)
    finally:
        if old is None:
            os.environ.pop("QUAM_STATE_PATH", None)
        else:
            os.environ["QUAM_STATE_PATH"] = old
    return res


def _import_only(qclass: str) -> dict:
    res = {"ok": False, "kind": "import", "error": None, "canonical": None,
           "i": [], "q": None, "iq": False, "length": None, "dropped": [],
           "warnings": []}
    try:
        cls = _import_class(qclass)
        from quam.components.pulses import Pulse  # noqa: F401 -- _draw_one's import
        from qm.qua import program  # noqa: F401 -- _check_macros' import
    except BaseException as exc:  # noqa: BLE001 -- SystemExit from a lab import too
        res["error"] = f"could not import {qclass}: {type(exc).__name__}: {exc}"
        res["reason"] = "class-unavailable"
        return res
    res["canonical"] = f"{cls.__module__}.{cls.__qualname__}"
    res["ok"] = True
    return res


#: an item whose class is this checks gates on two chip folders: params
#: ``{"before": <folder>, "after": <folder>, "macros": [dot-paths],
#: "lab_dir": <SM's lab folder or null>, "max_samples": int}`` (see
#: :func:`_check_gates`). Nothing is saved and nothing is written into the
#: folders.
GATE_CHECK_ITEM = "@gatecheck"


def _root_class_of(folder: Path, fallback=None) -> str:
    """The root class state.json names, else *fallback* (the class SM
    passes, the one its other lab checks load the chip with)."""
    raw = json.loads((Path(folder) / "state.json").read_text(encoding="utf-8"))
    root = raw.get("__class__") if isinstance(raw, dict) else None
    if isinstance(root, str) and root:
        return root
    if isinstance(fallback, str) and fallback:
        return fallback
    raise ValueError("state.json names no __class__ and none was given")


def _chip_side(folder, root_class=None):
    """``(machine | None, {"load_error", "config_error"}, config | None)`` of
    one chip folder: loaded with its own root class, then
    ``generate_config()``."""
    side = {"load_error": None, "config_error": None}
    if not isinstance(folder, str) or not folder:
        side["load_error"] = "no chip folder given"
        return None, side, None
    try:
        cls = _import_class(_root_class_of(Path(folder), root_class))
        machine = _load_chip(cls, folder)
    except BaseException as exc:  # noqa: BLE001 -- the lab's own words
        side["load_error"] = _exc_where(exc)
        return None, side, None
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cfg = machine.generate_config()
    except BaseException as exc:  # noqa: BLE001
        side["config_error"] = _exc_where(exc)
        return machine, side, None
    return machine, side, cfg


def _script_body(script: str) -> str:
    import textwrap
    body = script.split("with program() as prog:", 1)[-1]
    body = body.split("\nconfig =", 1)[0]
    lines = []
    for ln in body.splitlines():
        if ln.strip().startswith("with stream_processing"):
            break
        lines.append(ln)
    return textwrap.dedent("\n".join(lines))


def played_ops(script: str) -> list:
    """``[(element, operation)]`` of every ``play`` with literal arguments,
    in order of first appearance."""
    import ast
    try:
        tree = ast.parse(_script_body(script))
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "play"
                and len(node.args) >= 2):
            continue
        opnode = node.args[0]
        if isinstance(opnode, ast.BinOp):
            opnode = opnode.left
        try:
            op, el = ast.literal_eval(opnode), ast.literal_eval(node.args[1])
        except Exception:  # noqa: BLE001 -- a real-time argument
            continue
        key = (str(el), str(op))
        if key not in out:
            out.append(key)
    return out


def _element_port(el_cfg: dict):
    for key in ("singleInput", "MWInput"):
        if isinstance(el_cfg.get(key), dict):
            return list(el_cfg[key].get("port") or []) or None
    rf = el_cfg.get("RF_inputs")
    if isinstance(rf, dict) and rf:
        first = next(iter(rf.values()))
        return list((first or {}).get("port") or []) or None
    if isinstance(el_cfg.get("mixInputs"), dict):
        return list(el_cfg["mixInputs"].get("I") or []) or None
    return None


def _port_cfg(cfg: dict, port):
    """``(port dict | None, kind)``: kind ``"lf"``, ``"mw"`` or ``"opx"`` (a
    controller without FEMs)."""
    if not port:
        return None, None
    con = (cfg.get("controllers") or {}).get(port[0]) or {}
    if len(port) == 3:
        fems = con.get("fems") or {}
        fem = fems.get(port[1], fems.get(str(port[1]))) or {}
        outs = fem.get("analog_outputs") or {}
        pc = outs.get(port[2], outs.get(str(port[2])))
        mw = fem.get("type") == "MW" or (isinstance(pc, dict) and (
            "band" in pc or "full_scale_power_dbm" in pc))
        return pc, ("mw" if mw else "lf")
    outs = con.get("analog_outputs") or {}
    return outs.get(port[1], outs.get(str(port[1]))), "opx"


def op_data(cfg: dict, element: str, operation: str, max_samples: int = _MAX_SAMPLES) -> dict:
    """What the generated config holds for one played operation."""
    rec = {"element": element, "operation": operation, "found": False,
           "error": None, "length": None, "waveform": None, "port": None,
           "port_kind": None, "delay": None, "sampling_rate": None,
           "filter": False, "output_mode": None, "offset": None, "iq": False}
    el_cfg = (cfg.get("elements") or {}).get(element)
    if not isinstance(el_cfg, dict):
        rec["error"] = f"element {element!r} is not in the config"
        return rec
    pname = (el_cfg.get("operations") or {}).get(operation)
    pulse = (cfg.get("pulses") or {}).get(pname) if pname else None
    if not isinstance(pulse, dict):
        rec["error"] = f"operation {operation!r} is not in element {element!r}"
        return rec
    rec["found"] = True
    rec["length"] = pulse.get("length")
    wfs = pulse.get("waveforms") or {}
    wname = wfs.get("single", wfs.get("I"))
    rec["iq"] = "Q" in wfs or any(k in el_cfg for k in ("mixInputs", "MWInput", "RF_inputs"))
    wf = (cfg.get("waveforms") or {}).get(wname) or {}
    if wf.get("type") == "constant":
        rec["waveform"] = {"type": "constant", "sample": wf.get("sample")}
    else:
        samples = list(wf.get("samples") or [])
        rec["waveform"] = {"type": wf.get("type") or "arbitrary",
                           "samples": _floats(samples[:max_samples]),
                           "total": len(samples),
                           "truncated": len(samples) > max_samples}
    port = _element_port(el_cfg)
    rec["port"] = port
    pc, kind = _port_cfg(cfg, port)
    rec["port_kind"] = kind
    if isinstance(pc, dict):
        rec["delay"] = pc.get("delay", 0)
        rec["sampling_rate"] = pc.get("sampling_rate")
        rec["filter"] = bool(pc.get("filter"))
        rec["output_mode"] = pc.get("output_mode")
        rec["offset"] = pc.get("offset")
    return rec


def _quam_config_origin(lab_dir) -> dict:
    """Where this env imports ``quam_config`` from, asked twice: as SM runs
    it (SM's lab folder first on the path) and with SM's lab folder removed
    from the path. ``find_spec`` locates the package without running it."""
    import importlib.util
    import os
    import subprocess
    out = {"with_lab_dir": None, "without_lab_dir": None, "lab_dir": lab_dir,
           "error": None}
    try:
        spec = importlib.util.find_spec("quam_config")
        out["with_lab_dir"] = getattr(spec, "origin", None) if spec else None
    except BaseException as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    env = dict(os.environ)
    parts = [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep) if p]
    if lab_dir:
        norm = os.path.normcase(os.path.abspath(lab_dir))
        parts = [p for p in parts if os.path.normcase(os.path.abspath(p)) != norm]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    code = ("import importlib.util,sys;s=importlib.util.find_spec('quam_config');"
            "sys.stdout.write((s.origin or '') if s else '')")
    try:
        got = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                             capture_output=True, text=True, timeout=60,
                             cwd=str(Path(sys.executable).parent))
        out["without_lab_dir"] = got.stdout.strip() or None
    except BaseException as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _check_gates(params: dict) -> dict:
    """Load the "before" and "after" chip folders and build both configs;
    on "after", run each named macro's ``apply()`` inside ``program()`` and
    turn it into QUA script text (``qm.generate_qua_script``). For every
    operation the script plays: the config's pulse, waveform and port data.
    A named macro that also exists in "before" is applied there too
    (``before_gates``), so the caller can tell an old error from a new one.

    The config is built ONCE per folder; each gate gets its own
    ``program()``, so the order of the gates does not change any answer.

    Never saves a machine and never writes into the folders. The only guard
    added for the lab's code is ``QUAM_STATE_PATH`` pointing at "after"; a
    guard on the qualibrate config is NOT added, because lab code that reads
    it at import would break."""
    import os
    max_samples = int(params.get("max_samples") or _MAX_SAMPLES)
    res = {"ok": False, "kind": "gatecheck", "error": None,
           "before": None, "after": None, "gates": {}, "quam_config": None}
    old = os.environ.get("QUAM_STATE_PATH")
    after_folder = params.get("after")
    if isinstance(after_folder, str) and after_folder:
        os.environ["QUAM_STATE_PATH"] = after_folder
    try:
        root_class = params.get("root_class")
        bmachine, res["before"], _c = _chip_side(params.get("before"), root_class)
        del _c
        machine, res["after"], cfg = _chip_side(after_folder, root_class)
        res["before_gates"] = {}
        if bmachine is not None:
            for path in params.get("macros") or []:
                try:
                    _node_at(bmachine, path)
                except BaseException:  # noqa: BLE001 -- not in "before": a new gate
                    continue
                try:
                    _apply_in_program(bmachine, path)
                    res["before_gates"][path] = {"error": None}
                except BaseException as exc:  # noqa: BLE001
                    res["before_gates"][path] = {"error": f"{type(exc).__name__}: {exc}"[:600]}
        if machine is not None:
            try:
                from qm import generate_qua_script
                qm_error = None
            except BaseException as exc:  # noqa: BLE001
                generate_qua_script = None
                qm_error = f"could not import qm: {type(exc).__name__}: {exc}"
            for path in params.get("macros") or []:
                g = {"script": None, "error": None, "ops": []}
                try:
                    prog = _apply_in_program(machine, path)
                    if generate_qua_script is None:
                        raise RuntimeError(qm_error)
                    g["script"] = generate_qua_script(prog)
                except BaseException as exc:  # noqa: BLE001 -- the lab's own check
                    g["error"] = f"{type(exc).__name__}: {exc}"[:600]
                if g["script"] and cfg:
                    g["ops"] = [op_data(cfg, el, op, max_samples)
                                for el, op in played_ops(g["script"])]
                res["gates"][path] = g
        res["quam_config"] = _quam_config_origin(params.get("lab_dir"))
        res["ok"] = True
    except BaseException as exc:  # noqa: BLE001
        res["error"] = _exc_where(exc)
    finally:
        if old is None:
            os.environ.pop("QUAM_STATE_PATH", None)
        else:
            os.environ["QUAM_STATE_PATH"] = old
    return res


def draw(items: list, max_samples: int = _MAX_SAMPLES) -> dict:
    out = []
    for it in items or []:
        qclass = (it or {}).get("qclass")
        params = (it or {}).get("params") or {}
        if not isinstance(qclass, str) or not qclass:
            out.append({"ok": False, "error": "no class named"})
            continue
        if qclass == GATE_CHECK_ITEM:
            out.append(_check_gates(params if isinstance(params, dict) else {}))
            continue
        if qclass == CONFIG_ITEM:
            out.append(_gen_config(params if isinstance(params, dict) else {}))
            continue
        if qclass.startswith(IMPORT_PREFIX):
            out.append(_import_only(qclass[len(IMPORT_PREFIX):]))
            continue
        if qclass.startswith(MACRO_PREFIX):
            out.append(_check_macros(qclass[len(MACRO_PREFIX):],
                                     params if isinstance(params, dict) else {}))
            continue
        out.append(_draw_one(qclass, params if isinstance(params, dict) else {},
                             max_samples))
    return {"python": sys.version.split()[0], "items": out,
            "sources": out_of_env_sources()}


#: a response line in --serve mode starts with this, so a print() from the
#: lab's own module (which lands on the same pipe only if it writes to fd 1
#: directly) can never be mistaken for an answer
SERVE_MARK = "@@SM-LABWF@@"


def serve() -> int:
    """``--serve``: the WARM worker (core/lab_waveform._Worker). One request
    per stdin line (the ``--in`` JSON), one ``SERVE_MARK``-prefixed JSON line
    per answer on stdout; EOF ends it.

    Staleness is the parent's job and needs ONE thing from here: ``sources``
    carries each file's stat as FIRST SEEN by this process -- i.e. the code it
    actually imported -- never a re-stat. An edit to the lab's module after the
    import therefore disagrees with the recorded stat, and the parent kills
    this worker instead of serving the old class."""
    out = sys.stdout
    try:
        out.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    sys.stdout = sys.stderr          # the lab's prints never reach the pipe
    seen: dict = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        result = {"status": "error", "python": "", "items": [], "sources": {},
                  "error": None}
        try:
            spec = json.loads(line)
            result.update(draw(spec.get("items") or [],
                               int(spec.get("max_samples") or _MAX_SAMPLES)))
            for f, st in (result.get("sources") or {}).items():
                seen.setdefault(f, st)
            result["sources"] = {f: seen[f] for f in (result.get("sources") or {})}
            result["status"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            result["error"] = f"{type(exc).__name__}: {exc}"
        out.write(SERVE_MARK + json.dumps(result) + "\n")
        out.flush()
    return 0


def main() -> int:
    if "--serve" in sys.argv[1:]:
        return serve()
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = {"status": "error", "python": "", "items": [], "sources": {},
              "error": None, "traceback": None}
    try:
        spec = json.loads(Path(args.inp).read_text(encoding="utf-8"))
        result.update(draw(spec.get("items") or [],
                           int(spec.get("max_samples") or _MAX_SAMPLES)))
        result["status"] = "ok"
    except BaseException as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    Path(args.out).write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps({"status": result["status"]}))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
