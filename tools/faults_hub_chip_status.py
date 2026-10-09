"""S8 fault injection (docs/283): what each surface SAYS.

Every fault is driven through the real mechanism where one exists (a run
folder of another chip identity, a run that retargets a pointer, a real edit
+ Apply + undo, a NaN in the saved states, a run folder deleted and swept);
only the two RAM states (building, idle) are injected at the status seam.
Each surface's own words are extracted from its response: Chip Status Trends
(the point's hover words), the metric meta (the tile's entry), the Param
History grid (its notes / its cell drawer) and Changes (the group head and
the row's words). Synthetic chips in a scratch folder; nothing else is read.

    python tools/faults_hub_chip_status.py --scratch <dir> --report <json>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quam_state_manager.core import hub, hub_sync  # noqa: E402
from tests.test_hub_drawer import chip_state, make_app, patch, run, write_chip  # noqa: E402


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _env(root: Path, runs, live_state):
    data, live = root / "data", root / "chips" / "live"
    for i, (state, patches) in enumerate(runs, start=1):
        run(data, i, state, patches=patches)
    write_chip(live, live_state, data)
    app = make_app(root)
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "data": data, "live": live}


def _base(root: Path):
    """#1 genesis; #2 T1 by its own patch; #3 f_01, no patch; #4 retargets x180."""
    return _env(root, [
        (chip_state(alias="#./x180_Gauss"), None),
        (chip_state(alias="#./x180_Gauss", t1=3.0e-5), [patch("qubits.qA1.T1", 3.0e-5, 1.0e-5)]),
        (chip_state(alias="#./x180_Gauss", t1=3.0e-5, f01=5.1e9, amp=0.2), None),
        (chip_state(alias="#./x180_DragCosine", t1=3.0e-5, f01=5.1e9, amp=0.25), None),
    ], chip_state(alias="#./x180_DragCosine", t1=3.0e-5, f01=5.1e9, amp=0.25))


def _trends(c, query: str) -> list[dict]:
    html = c.get("/topology/trends?" + query).data.decode()
    m = re.search(r'id="topo-trends-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else []


def _point_words(c, query, entity, newest=True) -> str:
    for ch in _trends(c, query):
        for s in ch["series"]:
            if s["entity"] == entity:
                held = s.get("held") or {}
                pts = [p for p in s["points"] if p[0] not in held]
                if not pts:
                    return "no point"
                a = (s.get("attr") or {}).get(pts[-1 if newest else 0][0]) or {}
                return " / ".join(x for x in (str(pts[-1 if newest else 0][1]), a.get("label"), a.get("sub"),
                                              "; ".join(a.get("flag_text") or [])) if x)
            # fall through
    for ch in _trends(c, query):
        if ch.get("note"):
            return ch["note"]
    return "no series"


def _meta(c, metric, entity) -> str:
    d = c.get("/topology/metric-meta").get_json()
    if d.get("mode") in ("building", "preparing"):
        return d.get("message")
    e = (d.get("q") or {}).get(metric, {}).get(entity) or {}
    words = [e.get("label"), e.get("sub")] + list(e.get("flags") or [])
    if e.get("nonfinite"):
        words.append("value: " + e["nonfinite"] + " (not a finite number)")
    if e.get("undone"):
        words.append("undone")
    words.append("names a writer run: " + ("#%s" % e["run"] if e.get("run") is not None else "no"))
    return " / ".join(str(w) for w in words if w)


def _grid(c) -> str:
    html = c.get("/param-history?since=all&props=T1").data.decode()
    # S10 C5: the snapshot path's label class -> not read, the grid has no snapshot arm
    notes = re.findall(r'<p class="vh-(?:note|wait)[^"]*"[^>]*>(.*?)</p>', html, re.S)
    return " | ".join(_text(n) for n in notes) or "(the grid, no note)"


def _drawer(c, prop) -> str:
    html = c.get(f"/param-history/expand?qubit=qA1&prop={prop}").data.decode()
    m = re.search(r'id="phd-data" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        w = re.search(r'<p class="vh-wait">(.*?)</p>', html, re.S)
        return _text(w.group(1)) if w else "(no drawer data)"
    vals = json.loads(m.group(1)).get("values") or []
    if not vals:
        return "(no recorded change)"
    v = vals[-1]
    return " / ".join(x for x in (str(v["value"]), v.get("label"), v.get("sub"), "; ".join(v.get("flags") or []),
                                  "opens run: " + ("yes" if v.get("uid") else "no")) if x)


def _changes(c, needle=None) -> str:
    html = c.get("/param-history/changes").data.decode()
    # S10 C5: the snapshot feed's label class -> not read, Changes has no snapshot arm
    notes = re.findall(r'<p class="vh-(?:note|wait)[^"]*"[^>]*>(.*?)</p>', html, re.S)
    groups = html.split('<div class="ph-change-group">')[1:]
    pick = next((g for g in groups if needle and needle in g), groups[0] if groups else "")
    head = re.search(r'<div class="ph-change-head">(.*?)</div>', pick, re.S)
    rows = re.findall(r'<tr( class="vh-undone")?>.*?>([^<]+)</button>.*?<td class="ph-change-who">(.*?)</td>',
                      pick, re.S)
    row_words = "; ".join(f"{p}: {_text(w) or '-'}{' (struck through)' if u else ''}" for u, p, w in rows[:3])
    out = (" | ".join(_text(n) for n in notes) + " || ") if notes else ""
    return out + (_text(head.group(1)) if head else "(no group)") + (" :: " + row_words if row_words else "")


def scenarios(scratch: Path) -> list[dict]:
    out = []

    def case(name, how, env, trends, meta, grid, changes):
        out.append({"fault": name, "injected_by": how, "trends": trends, "metric_meta": meta,
                    "param_history_grid": grid, "changes": changes})

    # 1. a building ledger (status seam)
    root = Path(tempfile.mkdtemp(dir=scratch))
    e = _base(root)
    real = hub_sync.status
    hub_sync.status = lambda _d: {"state": "building", "done": 2, "total": 9}
    try:
        c = e["client"]
        def wait(url):
            # S10 C5: the Trends wait carries a second class (the client re-asks it)
            m = re.search(r'<p class="vh-wait[^"]*"[^>]*>(.*?)</p>', c.get(url).data.decode(), re.S)
            return (_text(m.group(1)) + " (asks again by itself; no rows)") if m else "(no wait line)"
        case("a building ledger", "hub_sync.status = building 2/9", e, wait("/topology/trends?metrics=T1"),
             _meta(c, "T1", "qA1"), wait("/param-history?since=all"), wait("/param-history/changes"))
    finally:
        hub_sync.status = real
    # 2. an idle ledger (no sync keeps it current in this window)
    hub_sync.status = lambda d: {**real(d), "state": "idle"}
    try:
        c = e["client"]
        note = re.search(r'data-code="idle">(.*?)</p>', c.get("/topology/trends?metrics=T1").data.decode())
        d = c.get("/topology/metric-meta").get_json()
        case("an idle ledger", "hub_sync.status = idle", e,
             _text(note.group(1)) if note else "(no note)",
             "panel line: " + "; ".join(d.get("notes") or []) + " || cell: " + _meta(c, "T1", "qA1"),
             _grid(c), _changes(c))
    finally:
        hub_sync.status = real
    # 3. a run of another chip identity (CHIP_UNCERTAIN), its own patch setting T1
    run(e["data"], 5, chip_state(alias="#./x180_DragCosine", t1=9e-5, f01=5.1e9, amp=0.25, name="another-device"),
        patches=[patch("qubits.qA1.T1", 9e-5, 3e-5)])
    with e["app"].app_context():
        hub_sync.on_roots_moved([str(e["data"])])
    c = e["client"]
    case("a run of another chip identity", "run #5 saved with another chip name, its patch sets qA1 T1", e,
         _point_words(c, "metrics=T1", "qA1"), _meta(c, "T1", "qA1"), _drawer(c, "T1"), _changes(c, "#5"))
    shutil.rmtree(root, ignore_errors=True)

    # 4. a pointer retargeted mid-history (run #4 retargets x180)
    root = Path(tempfile.mkdtemp(dir=scratch))
    e = _base(root)
    c = e["client"]
    case("a pointer retargeted mid-history", "run #4 retargets x180 from x180_Gauss to x180_DragCosine", e,
         "alias path, in force: " + " -> ".join(
             str(p[1]) for ch in _trends(c, "metrics=&path=qubits.qA1.xy.operations.x180.amplitude")
             for s in ch["series"] if s["entity"] == "qA1" for p in s["points"]
             if p[0] not in (s.get("held") or {})) + " (newest: " +
         _point_words(c, "metrics=&path=qubits.qA1.xy.operations.x180.amplitude", "qA1") + ")",
         _meta(c, "x180_amplitude", "qA1"), _drawer(c, "x180_amplitude"),
         _changes(c, "#4"))

    # 5. an SM write later undone (real edit + Apply + Ctrl+Z)
    assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "4.5e-5"}).status_code == 200
    assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "operator"}).status_code == 200
    assert c.post("/undo", headers={"X-SM-Actor": "operator"}).status_code == 200
    tr = []
    for ch in _trends(c, "metrics=T1"):
        for s in ch["series"]:
            if s["entity"] == "qA1":
                held = s.get("held") or {}
                for p in s["points"][-3:]:
                    if p[0] in held:
                        continue
                    a = (s.get("attr") or {}).get(p[0]) or {}
                    tr.append(f"{p[1]} {a.get('label')}")
    case("an SM write later undone", "edit qA1 T1, Apply as operator, then undo", e,
         " -> ".join(tr), _meta(c, "T1", "qA1"), _drawer(c, "T1"), _changes(c, "applied by operator"))
    shutil.rmtree(root, ignore_errors=True)

    # 6. a NaN-only metric
    root = Path(tempfile.mkdtemp(dir=scratch))
    st = chip_state(t1=float("nan"))
    st["qubits"]["qA2"]["T1"] = float("nan")
    e = _env(root, [(st, None), (st, None)], st)
    c = e["client"]
    case("a NaN-only metric", "every run saved T1 = NaN on both qubits", e,
         _point_words(c, "metrics=T1", "qA1"), _meta(c, "T1", "qA1"), _drawer(c, "T1"), _changes(c))
    shutil.rmtree(root, ignore_errors=True)

    # 7. a deleted run folder (SOURCE_GONE)
    root = Path(tempfile.mkdtemp(dir=scratch))
    e = _base(root)
    shutil.rmtree(next((e["data"] / "2026-01-01").glob("#2_*")))
    with e["app"].app_context():
        from quam_state_manager.web import routes
        cs = hub_sync.sync_for(routes._hub_chip_dir(routes._active_ctx()["path"]))
    cs.request(full=True)
    hub_sync._kick(cs)
    c = e["client"]
    case("a deleted run folder", "run #2's folder deleted, full sweep", e,
         _point_words(c, "metrics=T1", "qA1"), _meta(c, "T1", "qA1"), _drawer(c, "T1"), _changes(c, "#2"))
    shutil.rmtree(root, ignore_errors=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    hub.set_inline(True)
    args.scratch.mkdir(parents=True, exist_ok=True)
    rows = scenarios(args.scratch.resolve())
    args.report.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    for r in rows:
        print("##", r["fault"], "--", r["injected_by"])
        for k in ("trends", "metric_meta", "param_history_grid", "changes"):
            print("  ", k + ":", r[k][:400])


if __name__ == "__main__":
    main()
