"""Fetch every Trends surface WP3 compares, from a running SM server, into wp3/surface/*.json.

Usage: python fetch.py <base url> <chip folder> <out dir> [--sample sample.json] [--only part,part]
Parts: load, curated, pairs, leaves, drawer, dstrends, uids, report, zone, legacy.
Stdlib only; a "being built" answer is waited for (polled), never recorded as data.
"""
from __future__ import annotations

import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from html_checks import saved_provenance_note, saved_writer_is_not_proven, validate_legacy_runs

BASE, CHIP, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
ARGS = sys.argv[4:]
SAMPLE = ARGS[ARGS.index("--sample") + 1] if "--sample" in ARGS else None
ONLY = set(ARGS[ARGS.index("--only") + 1].split(",")) if "--only" in ARGS else None
os.makedirs(OUT, exist_ok=True)
CURATED = ["T1", "T2ramsey", "T2echo", "gate_fidelity_avg", "gate_fidelity_x180", "gate_fidelity_x90",
           "f_01", "assignment_fidelity", "assignment_fidelity_gef", "readout_amplitude",
           "x180_amplitude", "x90_amplitude"]
VALUE_PATHS = {"T1": "T1", "T2ramsey": "T2ramsey", "T2echo": "T2echo", "f_01": "f_01",
               "gate_fidelity_avg": "gate_fidelity.averaged", "gate_fidelity_x180": "gate_fidelity.x180",
               "gate_fidelity_x90": "gate_fidelity.x90",
               "readout_amplitude": "resonator.operations.readout.amplitude",
               "x180_amplitude": "xy.operations.x180_DragCosine.amplitude",
               "x90_amplitude": "xy.operations.x90_DragCosine.amplitude"}
DATA_RE = re.compile(r'<script type="application/json" id="topo-trends-data">(.*?)</script>', re.S)
LOG = []


def want(part):
    return ONLY is None or part in ONLY


def req(path, data=None, headers=None, timeout=300):
    url = BASE + path
    h = {"Origin": BASE}
    h.update(headers or {})
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=h, method="POST" if data is not None else "GET")
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            out = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        out = e.code, e.read().decode("utf-8", "replace")
    LOG.append({"path": path, "status": out[0], "ms": int((time.time() - t0) * 1000), "len": len(out[1])})
    return out


def save(name, obj):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def trends(query, tries=200):
    """The #topo-trends-data charts of /topology/trends?<query> (waits through 'being built')."""
    for _ in range(tries):
        st, body = req("/topology/trends?" + query)
        m = DATA_RE.search(body)
        if st == 200 and m:
            return {"status": st, "charts": json.loads(html.unescape(m.group(1))) if "&#" in m.group(1)
                    else json.loads(m.group(1)), "html_len": len(body),
                    "pair_paths": re.findall(r'data-trend-path="([^"]+)"', body),
                    "renames": re.findall(r'data-rename[^>]*', body)[:20],
                    "notes": re.findall(r'class="[^"]*hub-note[^"]*"[^>]*>(.*?)<', body, re.S)[:20]}
        if st >= 500:
            return {"status": st, "charts": None, "error": body[:2000]}
        time.sleep(3)
    return {"status": st, "charts": None, "error": "never ready: " + body[:500]}


def main():
    if want("load"):
        st, body = req("/load", {"folder": CHIP})
        print("load", st, flush=True)
        for i in range(1200):
            st, body = req("/hub/status")
            js = json.loads(body) if st == 200 else {}
            if js.get("state") == "ready":
                break
            if i % 10 == 0:
                print("hub", js.get("state"), js.get("done"), js.get("total"), flush=True)
            time.sleep(3)
        save("hub_status.json", js)
        print("hub ready", js, flush=True)

    if want("curated"):
        out = {"bare": trends("")}
        out["a"] = trends("metrics=" + ",".join(CURATED[:8]))
        out["b"] = trends("metrics=" + ",".join(CURATED[8:]))
        save("trends_curated.json", out)
        print("curated", [len(c["series"]) for k in ("a", "b") for c in out[k]["charts"] or []], flush=True)

    if want("pairs"):
        bare = json.load(open(os.path.join(OUT, "trends_curated.json"), encoding="utf-8"))["bare"]
        out = {}
        for p in bare["pair_paths"]:
            out[p] = trends("metrics=&paths=" + urllib.parse.quote(p, safe=""))
        save("trends_pairs.json", out)
        print("pairs", {p: sum(len(c["series"]) for c in v["charts"] or []) for p, v in out.items()}, flush=True)

    if want("leaves") and SAMPLE:
        sample = json.load(open(SAMPLE, encoding="utf-8"))
        out = {}
        for p in sample["leaves"]:
            out[p] = trends("metrics=&paths=" + urllib.parse.quote(p, safe=""))
        save("trends_leaves.json", out)
        print("leaves", len(out), flush=True)

    if want("drawer"):
        paths = set()
        cur = json.load(open(os.path.join(OUT, "trends_curated.json"), encoding="utf-8"))
        for k in ("a", "b"):
            for c in cur[k]["charts"] or []:
                for s in c["series"]:
                    q, m = s["entity"], s["metric"]
                    if m in VALUE_PATHS:
                        paths.add(f"qubits.{q}.{VALUE_PATHS[m]}")
                    elif m == "assignment_fidelity":
                        paths.update(f"qubits.{q}.resonator.confusion_matrix.{i}.{j}" for i in range(2) for j in range(2))
                    elif m == "assignment_fidelity_gef":
                        paths.update(f"qubits.{q}.resonator.gef_confusion_matrix.{i}.{j}" for i in range(3) for j in range(3))
        rnd = random.Random(1300)
        for fn in ("trends_pairs.json", "trends_leaves.json"):
            fp = os.path.join(OUT, fn)
            if not os.path.exists(fp):
                continue
            for req_path, v in json.load(open(fp, encoding="utf-8")).items():
                got = [entity_path(req_path, s) for c in v["charts"] or [] for s in c["series"]]
                if fn == "trends_leaves.json" and os.environ.get("WP3_LEAF_DRAWERS") == "sample":
                    # the sampled entity itself + two other entities of its family
                    rest = [g for g in got if g != req_path]
                    got = ([req_path] if req_path in got else []) + rnd.sample(rest, min(2, len(rest)))
                paths.update(got)
        out = {}
        for i, p in enumerate(sorted(paths)):
            st, body = req("/field/history?all=1&path=" + urllib.parse.quote(p, safe=""))
            tries = 0
            while st == 200 and "fh-table" not in body and "fh-empty" not in body and tries < 100:
                time.sleep(3)
                st, body = req("/field/history?all=1&path=" + urllib.parse.quote(p, safe=""))
                tries += 1
            out[p] = {"status": st, "html": body}
            if i % 100 == 0:
                print("drawer", i, len(paths), flush=True)
        save("drawer.json", out)

    if want("dstrends"):
        st, body = req("/trends")
        keys = sorted(set(re.findall(r'data-folder-key="([^"]+)"', body)) |
                      set(re.findall(r'name="folders" value="([^"]+)"', body)))
        exps = sorted(set(re.findall(r'data-experiment="([^"]+)"', body)) |
                      set(re.findall(r'<option value="([^"]+)"', body)))
        info = {"status": st, "keys": keys, "exps_on_page": exps, "len": len(body)}
        if SAMPLE:
            exps = json.load(open(SAMPLE, encoding="utf-8"))["experiments"]
        out = {"page": info, "series": {}}
        fk = ",".join(keys[:1]) if keys else ""
        for e in exps:
            q = urllib.parse.urlencode({"experiment": e, "folders": fk} if fk else {"experiment": e})
            for _ in range(100):
                st, body = req("/trends/series?" + q)
                if st != 202:
                    break
                time.sleep(2)
            out["series"][e] = {"status": st, "json": json.loads(body) if st == 200 else body[:500]}
        save("dstrends.json", out)
        print("dstrends", len(out["series"]), "folder keys", keys, flush=True)

    if want("uids"):
        uids = set()
        for fn in ("trends_curated.json", "trends_pairs.json", "trends_leaves.json"):
            fp = os.path.join(OUT, fn)
            if not os.path.exists(fp):
                continue
            d = json.load(open(fp, encoding="utf-8"))
            for v in d.values():
                for c in v.get("charts") or []:
                    for s in c["series"]:
                        for a in (s.get("attr") or {}).values():
                            if a.get("uid"):
                                uids.add((a["uid"], ""))
                            if a.get("saved_uid"):
                                uids.add((a["saved_uid"], "saved"))
        ds = json.load(open(os.path.join(OUT, "dstrends.json"), encoding="utf-8"))
        rnd = random.Random(1300)
        for e, v in sorted(ds["series"].items()):
            runs = (v.get("json") or {}).get("runs") if isinstance(v.get("json"), dict) else None
            for r in rnd.sample(runs, min(3, len(runs))) if runs else []:
                uids.add((r[2], "dstrends"))
        out = {}
        for uid, via in sorted(uids):
            qs = "?via=saved" if via == "saved" else ""
            st, body = req("/dataset/" + urllib.parse.quote(uid, safe=":") + qs, headers={"HX-Request": "true"})
            root = re.search(r'<div id="ds-detail-root"[^>]*>', body)
            tag = root.group(0) if root else ""
            out[uid + "|" + via] = {"status": st, "run_id": (re.search(r'data-run-id="([^"]*)"', tag) or [None, None])[1],
                                    "experiment": (re.search(r'data-experiment="([^"]*)"', tag) or [None, None])[1],
                                    "date": (re.search(r'data-date="([^"]*)"', tag) or [None, None])[1],
                                    "via_saved_words": saved_writer_is_not_proven(body) if via == "saved" else None,
                                    "via_saved_note": saved_provenance_note(body) if via == "saved" else None}
        save("uids.json", out)
        print("uids", len(out), flush=True)

    if want("report"):
        out = {}
        for w in ("all", "30", "7"):
            st, body = req("/chip-status/report/section/trends?window=" + w)
            out[w] = {"status": st, "html": body}
        save("report.json", out)
        print("report", {k: (v["status"], len(v["html"])) for k, v in out.items()}, flush=True)

    if want("zone"):
        st, body = req("/topology")
        m = re.search(r"<html[^>]*>", body)
        save("zone.json", {"status": st, "html_tag": m.group(0) if m else None})
        print("zone", m.group(0) if m else None, flush=True)

    if want("legacy") and SAMPLE:
        # the legacy Trend Tracker (reachable from Compare): three runs' saved states, f_01 of qA1
        runs = json.load(open(SAMPLE, encoding="utf-8")).get("legacy_runs") or []
        validate_legacy_runs(runs)
        form = [("paths", r) for r in runs] + [("props", "f_01"), ("qubits", "qA1")]
        body = urllib.parse.urlencode(form).encode()
        r = urllib.request.Request(BASE + "/trend/chart", data=body, headers={"Origin": BASE}, method="POST")
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                st, html_ = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            st, html_ = e.code, e.read().decode("utf-8", "replace")
        save("legacy.json", {"status": st, "runs": runs, "html": html_})
        print("legacy", st, len(html_), flush=True)

    with open(os.path.join(OUT, "fetch_log.jsonl"), "a", encoding="utf-8") as fh:
        for e in LOG:
            fh.write(json.dumps(e) + "\n")


def entity_path(req_path, s):
    """The concrete path a fan-out series is: scope.<entity>.<tail> (wildcards from the entity label)."""
    parts = req_path.split(".")
    ent = s["entity"]
    if " \u00b7 " in ent:
        e, wild = ent.split(" \u00b7 ", 1)
        ws = wild.split(".")
        out, wi = [], 0
        for i, seg in enumerate(parts):
            if i == 1:
                out.append(e)
            elif seg == "*" and i > 1:
                out.append(ws[wi])
                wi += 1
            else:
                out.append(seg)
        return ".".join(out)
    if len(parts) >= 3 and parts[0] in ("qubits", "qubit_pairs"):
        return ".".join([parts[0], ent] + parts[2:])
    return req_path


if __name__ == "__main__":
    main()
