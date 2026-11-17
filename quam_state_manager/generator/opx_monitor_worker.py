"""OPX monitor worker: runs in a user-selected env that has the QM SDK installed.

SM never imports ``qm`` in its own process. This script is started once per
connection target and answers JSON-line requests on stdin with JSON lines on
stdout:

    {"op": "poll"}                  -> server version, devices, open QMs, jobs
    {"op": "close_qm", "qm_id": X}  -> qm.close() on one QM, only if it is open now
    {"op": "close_qms", "qm_ids": [..]} -> qm.close() on each named QM that is open
                                       now (never close_all_qms: a QM opened
                                       after the user confirmed is not touched)
    {"op": "quit"}

The target arrives once, as the first stdin line, ``{"target": {...}}`` (so a
secret header never appears on a command line). Every SDK call is made
separately; a call that raises is reported under ``refused`` with its error
text and the rest of the answer still arrives. Nothing here writes to disk.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import warnings


CONFIG_MAX_AGE_S = 60.0


def _out(obj):
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def _err_text(exc) -> str:
    return f"{type(exc).__name__}: {str(exc)[:400]}"


def _ts(proto_ts):
    try:
        if proto_ts is None or not proto_ts.seconds:
            return None
        return proto_ts.ToDatetime().isoformat() + "Z"
    except Exception:
        return None


def _num(v):
    """A reading as a float, or None when the SDK handed something else."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _readings(src, name, refused, what):
    out = {}
    try:
        items = dict(src).items()
    except (TypeError, ValueError) as exc:
        refused[f"{name}.{what}"] = _err_text(exc)
        return None
    for k, v in items:
        f = _num(v)
        if f is None:
            if v is not None:
                refused[f"{name}.{what}.{k}"] = f"not a number: {str(v)[:80]!r}"
            continue
        # Kept as sent, 0.0 included. A proto3 double has no presence, so 0.0
        # may also mean "not filled"; SM shows it as such and never judges a
        # limit on it (opx_monitor.metric_known).
        out[str(k)] = f
    return out or None


class QmmTarget:
    """A direct QuantumMachinesManager connection (host/port, optional TLS)."""

    def __init__(self, t):
        from qm import QuantumMachinesManager
        kw = {"host": t["host"]}
        if t.get("port"):
            kw["port"] = int(t["port"])
        if t.get("cluster_name"):
            kw["cluster_name"] = t["cluster_name"]
        if t.get("tls"):
            import ssl
            kw["credentials"] = ssl.create_default_context()
            kw["follow_gateway_redirections"] = False
        if t.get("headers"):
            kw["connection_headers"] = dict(t["headers"])
        self.qmm = QuantumMachinesManager(**kw)
        self._config_cache = {}

    def poll(self, include_jobs=False, job_limit=100):
        refused = {}
        ans = {"kind": "qmm"}

        def call(name, fn):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - every refusal is reported
                refused[name] = _err_text(exc)
                return None

        ans["version"] = call("version_dict", lambda: {str(k): str(v) for k, v in
                                                        dict(self.qmm.version_dict()).items()})
        ans["cluster_name"] = call("cluster_name", lambda: self.qmm.cluster_name)
        def read_devices():
            devs = self.qmm.get_devices()
            controllers = []
            if devs is not None:
                for name, c in dict(devs.controllers).items():
                    row = {"name": str(name), "type": getattr(c, "controller_type", None)}
                    fems = getattr(c, "fems", None)
                    row["fems"] = {str(k): str(v) for k, v in dict(fems).items()} if fems else None
                    try:
                        temps = c.temperatures if hasattr(c, "temperatures") else (
                            {"controller": c.temperature} if hasattr(c, "temperature") else None)
                    except Exception as exc:  # noqa: BLE001
                        refused[f"{name}.temperatures"] = _err_text(exc)
                        temps = None
                    row["temperatures"] = _readings(temps, name, refused, "temperatures") if temps else None
                    try:
                        m = c.metrics if hasattr(c, "metrics") else None
                    except Exception as exc:  # noqa: BLE001
                        refused[f"{name}.metrics"] = _err_text(exc)
                        m = None
                    row["metrics"] = (_readings({k: getattr(m, k, None) for k in
                                                 ("cpu_utilization_percent", "memory_available_gb",
                                                  "qop_cpu_percent", "qop_memory_percent",
                                                  "disk_available_gb")}, name, refused, "metrics")
                                      if m is not None else None)
                    controllers.append(row)
            return controllers if devs is not None else None

        ans["controllers"] = call("get_devices", read_devices)

        qm_ids = call("list_open_qms", lambda: list(map(str, self.qmm.list_open_qms())))
        cache = getattr(self, '_config_cache', {})
        if qm_ids is not None:
            cache = {qid: ports for qid, ports in cache.items() if qid in set(map(str, qm_ids))}
        self._config_cache = cache
        qms = []
        for qid in qm_ids or []:
            row = {"id": str(qid), "jobs": None, "ports": None, "markers": None, "port_map": None}
            qm = call(f"get_qm({qid})", lambda: self.qmm.get_qm(qid))
            if qm is not None:
                row['jobs'] = call(f"qm.get_jobs({qid})", lambda: [_job_row(j) for j in qm.get_jobs()])
                # qm.update_config can change the ports a QM claims: re-read the
                # config when its job set changes and at least every CONFIG_MAX_AGE_S.
                job_ids = tuple(sorted(str(j.get('id')) for j in row['jobs'] or []))
                entry = cache.get(str(qid))
                if (not isinstance(entry, dict) or entry.get('jobs') != job_ids
                        or time.monotonic() - entry.get('at', 0) > CONFIG_MAX_AGE_S):
                    cache.pop(str(qid), None)
                    cfg = call(f"qm.get_config({qid})", qm.get_config)
                    if isinstance(cfg, dict):
                        ports = call(f"qm.get_config({qid})", lambda: _used_fems(cfg))
                        markers = call(f"qm.get_config({qid})", lambda: _marker_names(cfg))
                        port_map = call(f"qm.get_config({qid})", lambda: _port_map(cfg))
                        if port_map is None:  # read, but the map failed: not "not read"
                            port_map = {"error": "the configuration could not be drawn"}
                        cache[str(qid)] = {'ports': ports, 'markers': markers, 'port_map': port_map,
                                           'jobs': job_ids, 'at': time.monotonic()}
                row['ports'] = (cache.get(str(qid)) or {}).get('ports')
                # None = the config could not be read; [] = read, no marker-shaped element
                row['markers'] = (cache.get(str(qid)) or {}).get('markers')
                # None = the config could not be read; {"error": ...} = read, the map failed
                row['port_map'] = (cache.get(str(qid)) or {}).get('port_map')
            qms.append(row)
        ans["open_qms"] = qms if qm_ids is not None else None
        jobs = call("get_jobs", lambda: [_job_row(j) for j in self.qmm.get_jobs()]) if include_jobs else None
        ans["jobs_total"] = len(jobs) if jobs is not None else None
        # the server lists at most 1000 jobs: a full list is a cap, not a total
        ans["jobs_capped"] = jobs is not None and len(jobs) >= 1000
        ans["jobs_requested"] = include_jobs
        # newest first: the server lists them oldest first, hundreds deep
        ans["jobs"] = (sorted(jobs, key=lambda r: r["created_at"] or "",
                              reverse=True)[:max(100, min(1000, job_limit))] if jobs is not None else None)
        ans["refused"] = refused
        return ans

    def close_qm(self, qm_id):
        open_now = {str(q) for q in self.qmm.list_open_qms()}
        if str(qm_id) not in open_now:
            raise RuntimeError(f"{qm_id} is no longer open; nothing was closed")
        qm = self.qmm.get_qm(qm_id)
        return self._verified_close(str(qm_id), qm)

    def _verified_close(self, qid, qm):
        result = qm.close()
        remaining = set(map(str, self.qmm.list_open_qms()))
        if qid in remaining:
            return {'closed': [], 'still_open': [qid], 'failed': {qid: 'still open after close'}}
        if result is False:
            return {'closed': [], 'still_open': [], 'failed': {qid: 'close returned False; no longer listed'}}
        return {'closed': [qid], 'still_open': [], 'failed': {}}

    def close_qms(self, qm_ids):
        """Close each named QM that is open now; report the rest, never close others."""
        open_now = {str(q) for q in self.qmm.list_open_qms()}
        closed, skipped, failed = [], [], {}
        for qid in [str(q) for q in qm_ids]:
            if qid not in open_now:
                skipped.append(qid)
                continue
            try:
                result = self._verified_close(qid, self.qmm.get_qm(qid))
                closed.extend(result['closed'])
                failed.update(result['failed'])
            except Exception as exc:  # noqa: BLE001
                failed[qid] = _err_text(exc)
        return {"closed": closed, "skipped": skipped, "failed": failed}

    def close_idle(self, qm_ids, watched, idle_minutes):
        # Load the pure activity rule without importing the SM package or qm into SM.
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location('monitor_rules', Path(__file__).resolve().parents[1] / 'core' / 'opx_monitor.py')
        rules = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rules)
        closed, skipped, failed, reasons = [], [], {}, {}
        for qid in map(str, qm_ids):
            try:
                if qid not in set(map(str, self.qmm.list_open_qms())):
                    skipped.append(qid)
                    reasons[qid] = 'already gone'
                    continue
                qm = self.qmm.get_qm(qid)
                jobs = [_job_row(j) for j in qm.get_jobs()]
                # the fresh job list must equal what SM watched (a new job or status restarts the watch)
                state = rules.idle_state({'jobs': jobs}, (watched or {}).get(qid), idle_minutes)
                if not state['idle']:
                    skipped.append(qid)
                    reasons[qid] = state['basis']
                    continue
                result = self._verified_close(qid, qm)
                closed.extend(result['closed'])
                failed.update(result['failed'])
            except Exception as exc:
                skipped.append(qid)
                reasons[qid] = 'activity unknown: ' + _err_text(exc)
        return {'closed': closed, 'skipped': skipped, 'failed': failed, 'skip_reasons': reasons}


def _job_row(j):
    md = getattr(j, "metadata", None)
    return {"id": str(getattr(j, "id", "")), "status": str(getattr(j, "status", "")),
            "description": str(getattr(j, "description", "") or ""),
            "user": str(getattr(j, "user_id", "") or "") or None,
            "created_at": _ts(getattr(md, "created_at", None)),
            "started_at": _ts(getattr(md, "started_at", None)),
            "updated_at": _ts(getattr(md, "last_status_updated_at", None)),
            "is_simulation": bool(getattr(j, "is_simulation", False))}


_MARKER_SHAPE = re.compile(r"^qmowner__v[0-9]+__")


def _marker_names(cfg):
    """The owner-marker-shaped element names of a config, nothing else about elements.

    Parsing happens in SM; the worker only filters by shape."""
    return sorted(n for n in (cfg.get("elements") or {}) if isinstance(n, str) and _MARKER_SHAPE.match(n))


def _used_fems(cfg):
    """Which (controller, FEM) slots a QM's config claims, from its ports."""
    used = set()
    for cname, c in (cfg.get("controllers") or {}).items():
        fems = c.get("fems") or {}
        for fem, body in fems.items():
            if any(body.get(k) for k in ("analog_outputs", "analog_inputs",
                                          "digital_outputs", "digital_inputs")):
                used.add(f"{cname}/{fem}")
        if not fems and any(c.get(k) for k in ("analog_outputs", "analog_inputs")):
            used.add(f"{cname}/1")
    return sorted(used)


# Controller port groups, and which group each element input/output key points at.
_PORT_KINDS = ("analog_outputs", "analog_inputs", "digital_outputs", "digital_inputs")
# element key -> (port kind, how the key holds its references)
#   "port":   {"port": ref}                   (singleInput, MWInput, MWOutput, RF_*)
#   "iq":     {"I": ref, "Q": ref, ...}       (mixInputs)
#   "named":  {name: ref}                     (outputs)
#   "inputs": {"inputs": {name: ref}}         (singleInputCollection, multipleInputs)
#   "digital": {name: ref or {"port": ref}}   (digitalInputs, digitalOutputs)
_ELEMENT_REFS = (
    ("singleInput", "analog_outputs", "port"),
    ("mixInputs", "analog_outputs", "iq"),
    ("singleInputCollection", "analog_outputs", "inputs"),
    ("multipleInputs", "analog_outputs", "inputs"),
    ("MWInput", "analog_outputs", "port"),
    ("RF_inputs", "analog_outputs", "port"),
    ("outputs", "analog_inputs", "named"),
    ("MWOutput", "analog_inputs", "port"),
    ("RF_outputs", "analog_inputs", "port"),
    ("digitalInputs", "digital_outputs", "digital"),
    # an element's digital output is read on a controller digital input
    ("digitalOutputs", "digital_inputs", "digital"),
)
# Element keys whose references may name an Octave rather than an OPX controller.
_OCTAVE_KEYS = ("RF_inputs", "RF_outputs")


def _port_key(p):
    """A port number as a string, or None when it is not an integer-like value."""
    if isinstance(p, bool):
        return None
    if isinstance(p, int):
        return str(p)
    if isinstance(p, str) and p.strip().isdigit():
        return str(int(p.strip()))
    return None


def _port_shaped(v):
    """True for a (controller, port) or (controller, fem, port) value."""
    return (isinstance(v, (list, tuple)) and len(v) in (2, 3) and isinstance(v[0], str)
            and all(_port_key(x) is not None for x in v[1:]))


def _count_port_shaped(v, depth=0):
    """How many port-shaped values sit in ``v`` (nested dicts/lists, bounded depth)."""
    if _port_shaped(v):
        return 1
    if depth >= 4:
        return 0
    if isinstance(v, dict):
        return sum(_count_port_shaped(x, depth + 1) for x in v.values())
    if isinstance(v, (list, tuple)):
        return sum(_count_port_shaped(x, depth + 1) for x in v)
    return 0


def _port_map(cfg):
    """The ports a QM's configuration declares, per controller/FEM, and the elements on each.

    Pure dict -> dict (JSON-safe, no qm import). Shape::

        {"fems": [{"key": "con1/1", "controller": "con1", "slot": "1" | None,
                   "type": "LF" | None,
                   "ports": [{"kind": "analog_outputs", "port": "1",
                              "elements": ["q1"], "declared": True}, ...]}, ...],
         "unparsed": n, "octave": m}

    A reference is (controller, fem, port) on an OPX1000 or (controller, port) on a
    controller without FEMs (OPX+), as a tuple or a JSON list. An ``RF_inputs`` /
    ``RF_outputs`` reference naming something that is not an OPX controller of this
    config is an Octave port: counted in ``octave`` and not attributed (the OPX ports
    an Octave drives are in its own connectivity). Any other reference this map
    cannot place (a 2-tuple on a FEM controller, an unknown controller, a malformed
    value, or a port-shaped value under an element key the map does not know) is
    skipped and counted in ``unparsed``. FEM slot keys are normalised, so "01" and 1
    are one slot. Config-declared only: not proof that a running program plays on a port.
    """
    blocks = {}
    unparsed = 0
    octave = 0

    def block(cname, slot, body):
        key = f"{cname}/{slot}" if slot is not None else str(cname)
        b = blocks.get(key)
        if b is None:
            ftype = body.get("type") if isinstance(body, dict) else None
            b = blocks[key] = {"key": key, "controller": str(cname),
                               "slot": None if slot is None else str(slot),
                               "type": str(ftype) if isinstance(ftype, (str, int)) and ftype != "" else None,
                               "ports": {}}
        return b

    controllers = cfg.get("controllers") if isinstance(cfg, dict) else None
    controllers = controllers if isinstance(controllers, dict) else {}
    has_fems = {}
    fem_bodies = {}  # controller -> {normalised slot: fem body}
    for cname, c in controllers.items():
        if not isinstance(c, dict):
            continue
        fems = c.get("fems")
        has_fems[str(cname)] = isinstance(fems, dict) and bool(fems)
        if has_fems[str(cname)]:
            norm = {}
            for k, v in fems.items():
                nk = _port_key(k)
                norm.setdefault(nk if nk is not None else str(k), v)
            fem_bodies[str(cname)] = norm
            bodies = list(norm.items())
        else:
            bodies = [(None, c)]
        for slot, body in bodies:
            if not isinstance(body, dict):
                continue
            b = block(cname, slot, body)
            for kind in _PORT_KINDS:
                ports = body.get(kind)
                if not isinstance(ports, dict):
                    continue
                for p in ports:
                    pk = _port_key(p)
                    if pk is not None:
                        b["ports"].setdefault((kind, pk), {"elements": set(), "declared": True})

    def place(ref, kind, element, key):
        nonlocal unparsed, octave
        if isinstance(ref, dict) and "port" in ref:
            ref = ref.get("port")
        if not isinstance(ref, (list, tuple)) or len(ref) not in (2, 3):
            unparsed += 1
            return
        cname = ref[0]
        if not isinstance(cname, str) or cname not in has_fems:
            if key in _OCTAVE_KEYS and isinstance(cname, str):
                octave += 1
            else:
                unparsed += 1
            return
        if len(ref) == 3:
            slot, pk = _port_key(ref[1]), _port_key(ref[2])
            if not has_fems[cname] or slot is None or pk is None:
                unparsed += 1
                return
            body = fem_bodies.get(cname, {}).get(slot)
            b = block(cname, slot, body if isinstance(body, dict) else {})
        else:
            pk = _port_key(ref[1])
            if has_fems[cname] or pk is None:
                unparsed += 1
                return
            b = block(cname, None, controllers[cname])
        entry = b["ports"].setdefault((kind, pk), {"elements": set(), "declared": False})
        entry["elements"].add(str(element))

    known = {key for key, _, _ in _ELEMENT_REFS}
    elements = cfg.get("elements") if isinstance(cfg, dict) else None
    for ename, el in (elements.items() if isinstance(elements, dict) else ()):
        if not isinstance(el, dict):
            continue
        for key, v in el.items():
            if key not in known and v is not None:
                unparsed += _count_port_shaped(v)
        for key, kind, form in _ELEMENT_REFS:
            if key not in el or el[key] is None:
                continue
            v = el[key]
            if form == "port":
                place(v.get("port") if isinstance(v, dict) else v, kind, ename, key)
            elif form == "iq":
                if not isinstance(v, dict):
                    unparsed += 1
                    continue
                for side in ("I", "Q"):
                    if side in v:
                        place(v[side], kind, ename, key)
            elif form == "inputs":
                ins = v.get("inputs") if isinstance(v, dict) else None
                if not isinstance(ins, dict):
                    unparsed += 1
                    continue
                for ref in ins.values():
                    place(ref, kind, ename, key)
            else:  # "named" and "digital"
                if not isinstance(v, dict):
                    unparsed += 1
                    continue
                for ref in v.values():
                    place(ref, kind, ename, key)

    def sort_key(item):
        (kind, pk), _ = item
        return (_PORT_KINDS.index(kind), int(pk))

    fems = []
    for key in sorted(blocks, key=lambda k: (blocks[k]["controller"],
                                              int(blocks[k]["slot"]) if (blocks[k]["slot"] or "").isdigit() else -1,
                                              blocks[k]["slot"] or "")):
        b = blocks[key]
        b["ports"] = [{"kind": kind, "port": pk, "elements": sorted(e["elements"]),
                       "declared": e["declared"]}
                      for (kind, pk), e in sorted(b["ports"].items(), key=sort_key)]
        fems.append(b)
    return {"fems": fems, "unparsed": unparsed, "octave": octave}



def main():
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    first = sys.stdin.readline()
    try:
        t = json.loads(first)["target"]
        if t.get("kind", "qmm") != "qmm":
            raise ValueError("this target is not a QM server; only a QM server can be monitored")
        target = QmmTarget(t)
        _out({"ok": True, "ready": True})
    except Exception as exc:  # noqa: BLE001
        _out({"ok": False, "ready": False, "error": _err_text(exc)})
        return
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        op = req.get("op")
        t0 = time.time()
        try:
            if op == "quit":
                return
            if op == "poll":
                res = target.poll(include_jobs=req.get("include_jobs", False), job_limit=req.get("job_limit", 100))
            elif op == "close_qm":
                res = target.close_qm(str(req["qm_id"]))
            elif op == "close_qms":
                res = target.close_qms(list(req.get("qm_ids") or []))
            elif op == 'close_idle':
                res = target.close_idle(req.get('qm_ids') or [], req.get('watched') or {}, req.get('idle_minutes', 120))
            else:
                raise ValueError(f"unknown op {op!r}")
            _out({"ok": True, "result": res, "took_s": round(time.time() - t0, 3)})
        except Exception as exc:  # noqa: BLE001
            _out({"ok": False, "error": _err_text(exc), "took_s": round(time.time() - t0, 3)})


if __name__ == "__main__":
    main()
