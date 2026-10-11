"""Pick the 50 random leaves (seeded) and the experiment list from the oracle output.

Eligible leaf: a holder path that changed at least once across the run-saved states after it first
appeared, held a finite number at some point, and is not one of the curated holders or a confusion
matrix element (those are covered by the curated pass). Seeded so a re-run picks the same leaves.
"""
import json
import math
import os
import random
import re
import sys

OUT = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 50
rc = json.load(open(os.path.join(OUT, "run_changes.json"), encoding="utf-8"))
runs = json.load(open(os.path.join(OUT, "runs.json"), encoding="utf-8"))
CUR = re.compile(r"^qubits\.[^.]+\.(T1|T2ramsey|T2echo|f_01|gate_fidelity\.(averaged|x180|x90)|"
                 r"resonator\.operations\.readout\.amplitude|xy\.operations\.x(180|90)_DragCosine\.amplitude|"
                 r"resonator\.(gef_)?confusion_matrix\.\d+\.\d+)$")


def num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


elig = sorted(p for p, ch in rc.items()
              if len(ch) >= 2 and any(num(v) for _k, v in ch) and not CUR.match(p))
rnd = random.Random(1300)
leaves = rnd.sample(elig, min(N, len(elig)))
exps = sorted({r["exp"] for r in runs})
json.dump({"leaves": leaves, "experiments": exps, "n_eligible": len(elig)},
          open(os.path.join(os.path.dirname(OUT.rstrip("/\\")), "sample.json"), "w", encoding="utf-8"), indent=1)
print("eligible", len(elig), "picked", len(leaves), "experiments", len(exps))
for p in leaves[:60]:
    print(" ", p, len(rc[p]))
