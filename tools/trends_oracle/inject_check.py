"""Per-case outcome of the B.4 injections (wp3/inject): where each injected run landed on the surfaces."""
import importlib.util
import json
import re
import sys

W = sys.argv[1]
spec = importlib.util.spec_from_file_location("c", W + r"\compare.py")
sys.argv = ["c", W + r"\inject"]
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
man = json.load(open(W + r"\inject\manifest.json", encoding="utf-8"))
cur = json.load(open(W + r"\inject\surface\trends_curated.json", encoding="utf-8"))
dr = json.load(open(W + r"\inject\surface\drawer.json", encoding="utf-8"))
ds = json.load(open(W + r"\inject\surface\dstrends.json", encoding="utf-8"))
METRIC = {"f_01": "f_01", "T1": "T1", "amplitude": "readout_amplitude"}
out = []
for case in man["cases"]:
    rid = int(re.match(r"#(\d+)_", case["run"]).group(1))
    path = case["path"]
    q = path.split(".")[1]
    metric = METRIC[path.split(".")[-1]]
    pts, attrs = [], {}
    for part in ("a", "b"):
        for ch in cur[part]["charts"] or []:
            if ch["metric"] == metric:
                for s in ch["series"]:
                    if s["entity"] == q:
                        pts, attrs = s["points"], s["attr"]
    on_chart = [(k, v, (attrs.get(k) or {}).get("label")) for k, v in pts
                if f"#{rid} " in ((attrs.get(k) or {}).get("label") or "")]
    d = C.parse_drawer(dr[path]["html"]) if path in dr else {"rows": [], "not_kept": [], "excursions": []}
    rows = [(r["eid"], r["fill"], r["label"]) for r in d["rows"] if f"#{rid} " in r["label"] or f"#{rid}" == r["label"]]
    listed = [" ".join(x["text"].split())[:200] for x in d["not_kept"] + d["excursions"] if rid in x["runs"]]
    exp = case["run"].split("_", 1)[1].rsplit("_", 1)[0]
    js = (ds["series"].get(exp) or {}).get("json") or {}
    in_ds = [r for r in js.get("runs", []) if r[0] == rid]
    out.append({"case": case["id"], "run": rid, "path": path, "on_chart": on_chart, "drawer_rows": rows,
                "drawer_lists": listed, "datasets_trends_run": in_ds,
                "notes": [n for n in (d.get("foot") or "").split("\u00b7") if "other" in n or "chip" in n][:3]})
json.dump(out, open(W + r"\inject\cases.json", "w", encoding="utf-8"), indent=1)
for o in out:
    print(json.dumps(o, ensure_ascii=False)[:600])
