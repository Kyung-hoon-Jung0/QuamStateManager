"""Independent Trends oracle (WP3, v1.3.0 verification). stdlib + json only; imports NO SM code.

Reads raw files (read-only) and writes the reference model the Trends surfaces are compared with:

  runs.json      every run folder: identity, created_at instant (UTC us), targets, patches, chip name,
                 whether its saved quam_state was readable; ordered by created_at instant, ties by run id
  run_changes    per flattened holder path: [[k, value], ...] change points over the run-saved states
                 (k = index into runs.json, only runs with a readable saved state); the first entry of a
                 path is its first appearance; a removal is {"$absent": 1}
  bak.json       the live backups state.json.bak_YYYYMMDD_HHMMSS[_label] (naive local time of the lab PC,
                 offset given by --bak-offset), same change-list shape over the backup sequence
  snaps.json     the instance's snapshot folders (<ts>/state.json + wiring.json + meta.json)
  live.json      the flattened live state + wiring

Flatten rule (the documented S2 rule, re-implemented): dict keys recurse (a '.' or '\\' in a key is
escaped with '\\', an empty key is '\\e'); a list longer than 16 whose elements are all scalars is
ONE value {"_array": n, "_hash": sha1}; any other list recurses by index; empty containers have no
leaves. Equality ("same"): numbers by value (1 == 1.0), NaN == NaN, bool/str strict, markers by content.
State and wiring are deep-merged, wiring winning on a collision.

Usage: python oracle.py --data <dataset root> --baks <chip folder with .bak_*> --snaps <history/<chip>>
                        --live <chip folder> --out <dir> [--bak-offset +09:00] [--workers 4]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

ABSENT = {"$absent": 1}
RUN_RE = re.compile(r"#(\d+)_(.+)_(\d{6})$")
BAK_RE = re.compile(r"state\.json\.bak_(\d{8})_(\d{6})(?:_(.*))?$")
#: a backup named by its UTC instant (``bak_YYYYMMDDTHHMMSS.ffffffZ[_label]``)
BAK_UTC_RE = re.compile(r"state\.json\.bak_(\d{8})T(\d{6})\.(\d{1,6})Z(?:_(.*))?$")
#: minute precision only (``bak_YYYYMMDD_HHMM_label``): dated, but never used where a second matters
BAK_MIN_RE = re.compile(r"state\.json\.bak_(\d{8})_(\d{4})_(.*)$")
SNAP_RE = re.compile(r"(\d{8})_(\d{6})_(\d+)$")


# ------------------------------------------------------------------ rules
def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def same(a, b):
    if is_num(a) and is_num(b):
        if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
            return True
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def seg(key):
    return key.replace("\\", "\\\\").replace(".", "\\.") if key else "\\e"


def split_path(path):
    """Inverse of joining escaped segments with '.'."""
    out, cur, i = [], [], 0
    while i < len(path):
        ch = path[i]
        if ch == "\\" and i + 1 < len(path):
            cur.append(path[i:i + 2])
            i += 2
            continue
        if ch == ".":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def unseg(s):
    if s == "\\e":
        return ""
    return s.replace("\\.", ".").replace("\\\\", "\\")


def _hs(v):
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return int(v)
    return v


def flatten(doc):
    out = {}

    def walk(v, parts):
        if isinstance(v, dict):
            for k, c in v.items():
                walk(c, parts + (seg(k),))
        elif isinstance(v, list):
            if len(v) > 16 and all(not isinstance(x, (dict, list)) for x in v):
                txt = json.dumps([_hs(x) for x in v], sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False)
                out[".".join(parts)] = {"_array": len(v), "_hash": hashlib.sha1(txt.encode("utf-8")).hexdigest()}
            else:
                for i, c in enumerate(v):
                    walk(c, parts + (str(i),))
        else:
            out[".".join(parts)] = v

    walk(doc, ())
    return out


def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        cur = out.get(k)
        if isinstance(cur, dict) and isinstance(v, dict):
            out[k] = deep_merge(cur, v)
        else:
            out[k] = v
    return out


def merge(state, wiring):
    return deep_merge(state, wiring or {})


def iso_to_us(s):
    """Aware ISO-8601 -> UTC epoch microseconds; None when naive or unparsable."""
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return int((dt - datetime(1970, 1, 1, tzinfo=timezone.utc)) / timedelta(microseconds=1))


def naive_to_us(d8, t6, offset):
    sign = 1 if offset[0] == "+" else -1
    hh, mm = int(offset[1:3]), int(offset[4:6])
    tz = timezone(sign * timedelta(hours=hh, minutes=mm))
    dt = datetime.strptime(d8 + t6, "%Y%m%d%H%M%S").replace(tzinfo=tz)
    return int((dt - datetime(1970, 1, 1, tzinfo=timezone.utc)) / timedelta(microseconds=1))


def patch_dot(ptr):
    """'/quam/qubits/qA1/f_01' -> 'qubits.qA1.f_01' (JSON-pointer unescape, S2 escape)."""
    if not isinstance(ptr, str):
        return None
    parts = ptr.split("/")[1:]
    if parts and parts[0] == "quam":
        parts = parts[1:]
    return ".".join(seg(p.replace("~1", "/").replace("~0", "~")) for p in parts)


# ------------------------------------------------------------------ readers
def _load(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def read_node(rp):
    """node.json + data.json facts of one run folder."""
    facts = {"node_ok": True}
    try:
        nd = _load(os.path.join(rp, "node.json"))
    except Exception as e:  # noqa: BLE001
        nd, facts["node_ok"], facts["node_err"] = {}, False, type(e).__name__
    md = nd.get("metadata") or {}
    model = ((nd.get("data") or {}).get("parameters") or {}).get("model") or {}
    q = model.get("qubits") or []
    p = model.get("qubit_pairs") or []
    facts.update(run_id=nd.get("id"), created_at=nd.get("created_at"), name=md.get("name"),
                 status=md.get("status"), run_end=md.get("run_end"), run_start=md.get("run_start"),
                 data_path=md.get("data_path"),
                 qubits=q if isinstance(q, list) else [q], pairs=p if isinstance(p, list) else [p],
                 patches=[[patch_dot(x.get("path")), x.get("op"), x.get("value"), x.get("old")]
                          for x in (nd.get("patches") or []) if isinstance(x, dict)])
    try:
        dj = _load(os.path.join(rp, "data.json"))
        fr = dj.get("fit_results") if isinstance(dj, dict) else None
        facts["fit_results"] = fr if isinstance(fr, dict) else {}
        facts["data_ok"] = True
    except FileNotFoundError:
        facts["fit_results"], facts["data_ok"] = {}, None
    except Exception:  # noqa: BLE001
        facts["fit_results"], facts["data_ok"] = {}, False
    return facts


def read_state(rp):
    """(facts, flat merged saved state | None) of one run folder."""
    facts = {}
    sp = os.path.join(rp, "quam_state", "state.json")
    wp = os.path.join(rp, "quam_state", "wiring.json")
    flat = None
    if os.path.isfile(sp):
        try:
            st = _load(sp)
            wi = _load(wp) if os.path.isfile(wp) else {}
            ex = (st.get("extras") or {}) if isinstance(st, dict) else {}
            facts["chip_name"] = ex.get("chip_name")
            facts["renames"] = ex.get("qubit_renames")
            flat = flatten(merge(st, wi))
        except Exception as e:  # noqa: BLE001
            facts["state_err"] = type(e).__name__
    else:
        facts["state_err"] = "missing"
    return facts, flat


def read_flat(paths):
    sp, wp = paths
    st = _load(sp)
    wi = _load(wp) if wp and os.path.isfile(wp) else {}
    return flatten(merge(st, wi))


def changes_over(seq_flats, out):
    """Feed flat states in order; out[path] gets [i, value] at each change."""
    prev = {}
    for i, flat in seq_flats:
        if flat is None:
            continue
        for path, v in flat.items():
            if path in prev:
                o = prev[path]
                if type(o) is type(v) and not isinstance(v, (float, dict)) and o == v:
                    continue
                if same(o, v):
                    continue
            out.setdefault(path, []).append([i, v])
        for path in prev:
            if path not in flat:
                out.setdefault(path, []).append([i, ABSENT])
        prev = flat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--baks", required=True)
    ap.add_argument("--snaps", required=True)
    ap.add_argument("--live", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bak-offset", default="+09:00")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()

    # ---- runs
    folders = []
    for d in sorted(os.listdir(a.data)):
        dp = os.path.join(a.data, d)
        if not os.path.isdir(dp):
            continue
        for r in os.listdir(dp):
            rp = os.path.join(dp, r)
            if os.path.isdir(rp) and RUN_RE.match(r):
                folders.append((d + "/" + r, rp))
    # phase A: identities + order (node.json only, cheap); phase B: saved states streamed in order
    runs = []
    with ProcessPoolExecutor(a.workers) as ex:
        for (rel, rp), facts in zip(folders, ex.map(read_node, [rp for _r, rp in folders], chunksize=16)):
            m = RUN_RE.match(rel.split("/")[-1])
            t_us = iso_to_us(facts.get("created_at"))
            t_src = "created_at"
            if t_us is None:
                t_us, t_src = iso_to_us(facts.get("run_end")), "run_end"
            if t_us is None:
                day = rel.split("/")[0].replace("-", "")
                t_us, t_src = naive_to_us(day, m.group(3), a.bak_offset), "folder_assumed"
            facts.update(rel=rel, path=rp, folder_id=int(m.group(1)), exp=m.group(2), hhmmss=m.group(3),
                         t_us=t_us, t_src=t_src)
            runs.append(facts)
    print("nodes", len(runs), round(time.time() - t0, 1), "s", flush=True)
    runs.sort(key=lambda r: (r["t_us"], r["run_id"] if isinstance(r["run_id"], int) else r["folder_id"]))
    for k, r in enumerate(runs):
        r["k"] = k
    run_changes = {}

    def stream():
        with ProcessPoolExecutor(a.workers) as ex:
            for r, (sfacts, flat) in zip(runs, ex.map(read_state, [r["path"] for r in runs], chunksize=4)):
                r.update(sfacts)
                r["has_state"] = flat is not None
                if r["k"] % 500 == 0:
                    print("states", r["k"], round(time.time() - t0, 1), "s", flush=True)
                yield r["k"], flat
    changes_over(stream(), run_changes)
    for r in runs:
        r.pop("path", None)
    print("run changes", sum(len(v) for v in run_changes.values()), "paths", len(run_changes),
          round(time.time() - t0, 1), "s", flush=True)
    with open(os.path.join(a.out, "runs.json"), "w", encoding="utf-8") as fh:
        json.dump(runs, fh)
    with open(os.path.join(a.out, "run_changes.json"), "w", encoding="utf-8") as fh:
        json.dump(run_changes, fh)
    del run_changes

    # ---- live backups (state only)
    baks = []
    undated = []
    for f in os.listdir(a.baks):
        m, mu, mm = BAK_RE.match(f), BAK_UTC_RE.match(f), BAK_MIN_RE.match(f)
        if m:
            baks.append({"name": f, "t_us": naive_to_us(m.group(1), m.group(2), a.bak_offset),
                         "label": m.group(3) or "", "precision": "s"})
        elif mu:
            baks.append({"name": f, "t_us": naive_to_us(mu.group(1), mu.group(2), "+00:00")
                         + int(mu.group(3).ljust(6, "0")), "label": mu.group(4) or "", "precision": "us_utc"})
        elif mm:
            baks.append({"name": f, "t_us": naive_to_us(mm.group(1), mm.group(2) + "00", a.bak_offset),
                         "label": mm.group(3) or "", "precision": "min"})
        elif f.startswith("state.json.bak"):
            undated.append(f)
    undated.sort()
    baks.sort(key=lambda b: (b["t_us"], b["name"]))
    bak_changes = {}
    with ProcessPoolExecutor(a.workers) as ex:
        bflats = ex.map(read_flat, [(os.path.join(a.baks, b["name"]), None) for b in baks], chunksize=8)
        changes_over(enumerate(bflats), bak_changes)
    with open(os.path.join(a.out, "bak.json"), "w", encoding="utf-8") as fh:
        json.dump({"baks": baks, "undated": undated, "offset": a.bak_offset, "changes": bak_changes}, fh)
    print("baks", len(baks), "undated", len(undated), round(time.time() - t0, 1), "s", flush=True)
    del bak_changes

    # ---- instance snapshots
    snaps = []
    for f in sorted(os.listdir(a.snaps)):
        sp = os.path.join(a.snaps, f, "state.json")
        if SNAP_RE.match(f) and os.path.isfile(sp):
            meta = {}
            try:
                meta = _load(os.path.join(a.snaps, f, "meta.json"))
            except Exception:  # noqa: BLE001
                pass
            snaps.append({"ts": f, "trigger": meta.get("trigger"), "meta_ts": meta.get("timestamp"),
                          "run_id": meta.get("run_id"), "kind": meta.get("kind"), "label": meta.get("label"),
                          "t_us": naive_to_us(f[:8], f[9:15], "+00:00") + int(f[16:].ljust(4, "0")[:4]) * 100})
    snap_changes = {}
    with ProcessPoolExecutor(a.workers) as ex:
        sflats = ex.map(read_flat, [(os.path.join(a.snaps, s["ts"], "state.json"),
                                     os.path.join(a.snaps, s["ts"], "wiring.json")) for s in snaps], chunksize=4)
        changes_over(enumerate(sflats), snap_changes)
    with open(os.path.join(a.out, "snaps.json"), "w", encoding="utf-8") as fh:
        json.dump({"snaps": snaps, "changes": snap_changes}, fh)
    print("snaps", len(snaps), round(time.time() - t0, 1), "s", flush=True)

    # ---- live
    live = read_flat((os.path.join(a.live, "state.json"), os.path.join(a.live, "wiring.json")))
    st = _load(os.path.join(a.live, "state.json"))
    with open(os.path.join(a.out, "live.json"), "w", encoding="utf-8") as fh:
        json.dump({"flat": live, "chip_name": (st.get("extras") or {}).get("chip_name"),
                   "renames": (st.get("extras") or {}).get("qubit_renames")}, fh)
    print("done", round(time.time() - t0, 1), "s", flush=True)


if __name__ == "__main__":
    main()
