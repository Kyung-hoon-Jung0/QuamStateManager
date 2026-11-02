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
import sys
import time
import warnings


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

    def poll(self):
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
        devs = call("get_devices", self.qmm.get_devices)
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
        ans["controllers"] = controllers if devs is not None else None

        qm_ids = call("list_open_qms", self.qmm.list_open_qms)
        qms = []
        for qid in qm_ids or []:
            row = {"id": str(qid), "jobs": [], "ports": None}
            qm = call(f"get_qm({qid})", lambda: self.qmm.get_qm(qid))
            if qm is not None:
                jobs = call(f"qm.get_jobs({qid})", qm.get_jobs)
                for j in jobs or []:
                    row["jobs"].append(_job_row(j))
                cfg = call(f"qm.get_config({qid})", qm.get_config)
                if isinstance(cfg, dict):
                    row["ports"] = _used_fems(cfg)
            qms.append(row)
        ans["open_qms"] = qms if qm_ids is not None else None
        jobs = call("get_jobs", self.qmm.get_jobs)
        # newest first: the server lists them oldest first, hundreds deep
        ans["jobs"] = (sorted((_job_row(j) for j in jobs), key=lambda r: r["created_at"] or "",
                              reverse=True) if jobs is not None else None)
        ans["refused"] = refused
        return ans

    def close_qm(self, qm_id):
        open_now = {str(q) for q in self.qmm.list_open_qms()}
        if str(qm_id) not in open_now:
            raise RuntimeError(f"{qm_id} is no longer open; nothing was closed")
        qm = self.qmm.get_qm(qm_id)
        return bool(qm.close())

    def close_qms(self, qm_ids):
        """Close each named QM that is open now; report the rest, never close others."""
        open_now = {str(q) for q in self.qmm.list_open_qms()}
        closed, skipped, failed = [], [], {}
        for qid in [str(q) for q in qm_ids]:
            if qid not in open_now:
                skipped.append(qid)
                continue
            try:
                self.qmm.get_qm(qid).close()
                closed.append(qid)
            except Exception as exc:  # noqa: BLE001
                failed[qid] = _err_text(exc)
        return {"closed": closed, "skipped": skipped, "failed": failed}


def _job_row(j):
    md = getattr(j, "metadata", None)
    return {"id": str(getattr(j, "id", "")), "status": str(getattr(j, "status", "")),
            "description": str(getattr(j, "description", "") or ""),
            "user": str(getattr(j, "user_id", "") or "") or None,
            "created_at": _ts(getattr(md, "created_at", None)),
            "started_at": _ts(getattr(md, "started_at", None)),
            "updated_at": _ts(getattr(md, "last_status_updated_at", None)),
            "is_simulation": bool(getattr(j, "is_simulation", False))}


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
                res = target.poll()
            elif op == "close_qm":
                res = target.close_qm(str(req["qm_id"]))
            elif op == "close_qms":
                res = target.close_qms(list(req.get("qm_ids") or []))
            else:
                raise ValueError(f"unknown op {op!r}")
            _out({"ok": True, "result": res, "took_s": round(time.time() - t0, 3)})
        except Exception as exc:  # noqa: BLE001
            _out({"ok": False, "error": _err_text(exc), "took_s": round(time.time() - t0, 3)})


if __name__ == "__main__":
    main()
