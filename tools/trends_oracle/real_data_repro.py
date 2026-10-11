"""Deterministic real-data repros of the WP3 Trends findings (rig copy of the customer chip + data).

python real_data_repro.py <wp3 dir> [tr01|tr02|tr03|all]

Reads the oracle's raw-file model (oracle_out/, from the run folders and live backups) and the Trends
JSON captured from the v1.3.0 server (surface/trends_curated.json, surface/drawer.json). Exit 1 when
every listed finding still reproduces, 0 when none does.
"""
import importlib.util
import json
import sys

W = sys.argv[1]
WHICH = sys.argv[2] if len(sys.argv) > 2 else "all"
spec = importlib.util.spec_from_file_location("c", W + r"\compare.py")
sys.argv = ["c", W]
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)
o = C.Oracle()
cur = json.load(open(W + r"\surface\trends_curated.json", encoding="utf-8"))


def series(metric, q):
    for part in ("a", "b"):
        for ch in cur[part]["charts"] or []:
            if ch["metric"] == metric:
                for s in ch["series"]:
                    if s["entity"] == q:
                        return s
    return {"points": [], "attr": {}, "held": {}}


def shown_at(s, t_us):
    v = None
    for k, val in s["points"]:
        if C.key_sec(k)[0] * 10**6 <= t_us and k not in (s.get("held") or {}):
            v = val
    return v


hits = {}
if WHICH in ("tr01", "all"):
    # qB3 f_01: runs #238.. (74 recorded states, 3 of them measuring qB3) hold 5681564188.138227;
    # the chart holds #233's 5681560370.126142 until #311
    s = series("f_01", "qB3")
    bad = []
    for r in o.runs:
        if 238 <= r["run_id"] < 311 and r.get("has_state"):
            v = C.eff_value(o.R, "qubits.qB3.f_01", r["k"])[1]
            if not C.same(v, shown_at(s, r["t_us"])):
                bad.append((r["run_id"], v, shown_at(s, r["t_us"])))
    print("TR-01 recorded states that disagree with the chart:", len(bad), bad[:3])
    hits["tr01"] = len(bad) > 0
if WHICH in ("tr02", "all"):
    s = series("assignment_fidelity", "qA5")
    k = o.by_runid[1089][0]["k"]
    cm = [[o.R.at(f"qubits.qA5.resonator.confusion_matrix.{i}.{j}", k) for j in range(2)] for i in range(2)]
    saved = (cm[0][0] + cm[1][1]) / 2
    shown = dict(s["points"]).get("20261002_062642_e1089")
    print("TR-02a qA5 #1089 saved matrix", cm, "fidelity", saved, "chart shows", shown)
    s2 = series("readout_amplitude", "qA4")
    k2 = o.by_runid[1201][0]["k"]
    saved2 = C.eff_value(o.R, "qubits.qA4.resonator.operations.readout.amplitude", k2)
    shown2 = dict(s2["points"]).get("20261002_141553_e1201")
    print("TR-02b qA4 #1201 saved", saved2, "chart shows", shown2)
    hits["tr02"] = (shown is not None and abs(shown - saved) > 1e-12) or (shown2 is not None and shown2 != saved2[1])
if WHICH in ("tr03", "all"):
    n = sum(1 for part in ("a", "b") for ch in cur[part]["charts"] or [] for s in ch["series"]
            for a in s["attr"].values() if a["provenance"] == "run_proven")
    linked = sum(1 for part in ("a", "b") for ch in cur[part]["charts"] or [] for s in ch["series"]
                 for a in s["attr"].values() if a["provenance"] == "run_proven" and a.get("uid"))
    print("TR-03 proven points:", n, "with a run link:", linked)
    hits["tr03"] = n > 0 and linked == 0
print(hits)
sys.exit(1 if hits and all(hits.values()) else 0)
