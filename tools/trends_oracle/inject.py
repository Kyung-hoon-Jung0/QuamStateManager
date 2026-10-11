"""B.4 hard-case injections on a SECOND rig copy (never the main comparison copy, never the source).

python inject.py <inject rig data root> <manifest out>

Every injected run is a NEW folder whose files are COPIED (never hardlinked) from an existing run of
the rig copy, then edited. The hardlinked files of the copy are only ever read, so the read-only
source dataset is never touched. Each injection records the dot-path it targets and what the oracle
and the surfaces must do with it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta

ROOT = sys.argv[1]
MANIFEST = sys.argv[2]
RUN_RE = re.compile(r"#(\d+)_(.+)_(\d{6})$")


def runs():
    out = []
    for d in sorted(os.listdir(ROOT)):
        dp = os.path.join(ROOT, d)
        if not os.path.isdir(dp):
            continue
        for r in os.listdir(dp):
            m = RUN_RE.match(r)
            rp = os.path.join(dp, r)
            if m and os.path.isfile(os.path.join(rp, "quam_state", "state.json")):
                nd = json.load(open(os.path.join(rp, "node.json"), encoding="utf-8"))
                out.append({"path": rp, "id": int(m.group(1)), "exp": m.group(2),
                            "created": datetime.fromisoformat(nd["created_at"])})
    out.sort(key=lambda r: (r["created"], r["id"]))
    return out


def clone(src, new_id, created, edit_state=None, edit_node=None):
    """Copy run *src* to a new folder dated *created*; apply the edits to the COPIES."""
    day = created.strftime("%Y-%m-%d")
    name = f"#{new_id}_{src['exp']}_{created.strftime('%H%M%S')}"
    dst = os.path.join(ROOT, day, name)
    os.makedirs(os.path.join(dst, "quam_state"))
    for f in ("node.json", "data.json"):
        if os.path.isfile(os.path.join(src["path"], f)):
            shutil.copyfile(os.path.join(src["path"], f), os.path.join(dst, f))
    for f in ("state.json", "wiring.json"):
        shutil.copyfile(os.path.join(src["path"], "quam_state", f), os.path.join(dst, "quam_state", f))
    assert os.stat(os.path.join(dst, "quam_state", "state.json")).st_nlink == 1, "a copy, never a link"
    nd = json.load(open(os.path.join(dst, "node.json"), encoding="utf-8"))
    iso = created.isoformat()
    nd["id"] = new_id
    nd["created_at"] = iso
    md = nd.setdefault("metadata", {})
    md["run_start"] = iso
    md["run_end"] = iso
    nd["patches"] = []
    if edit_node:
        edit_node(nd)
    json.dump(nd, open(os.path.join(dst, "node.json"), "w", encoding="utf-8"))
    sp = os.path.join(dst, "quam_state", "state.json")
    st = json.load(open(sp, encoding="utf-8"))
    if edit_state:
        edit_state(st)
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    return dst, name


def main():
    rs = runs()
    newest = rs[-1]
    mid = rs[len(rs) // 2]
    nxt = rs[len(rs) // 2 + 1]
    base_id = max(r["id"] for r in rs) + 1000
    t = newest["created"]
    st_new = json.load(open(os.path.join(newest["path"], "quam_state", "state.json"), encoding="utf-8"))
    man = {"cases": []}

    # I1: a run of ANOTHER chip (declared other name) in the shared folder
    f01 = st_new["qubits"]["qA1"]["f_01"]

    def other(st):
        st.setdefault("extras", {})["chip_name"] = "another-chip"
        st["qubits"]["qA1"]["f_01"] = f01 + 1.0e6
    d, n = clone(newest, base_id + 1, t + timedelta(seconds=60), other)
    man["cases"].append({"id": "I1_other_chip", "run": n, "path": "qubits.qA1.f_01",
                         "expect": "left out of the lane: no point with this value, counted in a note"})

    # I2: a NaN leaf, then I4 re-saves the original (a change back)
    t1 = st_new["qubits"]["qA2"].get("T1")

    def nan(st):
        st["qubits"]["qA2"]["T1"] = float("nan")
    d, n = clone(newest, base_id + 2, t + timedelta(seconds=120), nan)
    man["cases"].append({"id": "I2_nan", "run": n, "path": "qubits.qA2.T1",
                         "expect": "a gap (null) at this instant; NaN is not a number"})

    # I3: a same-value re-save of the newest state (no change on any path)
    d, n = clone(newest, base_id + 3, t + timedelta(seconds=180))
    man["cases"].append({"id": "I3_same_value_resave", "run": n, "path": "qubits.qA2.T1",
                         "expect": f"T1 back to {t1!r} (a change from the NaN gap); no other path changes"})

    # I4: a pointer swap of the readout alias
    ops = st_new["qubits"]["qA4"]["resonator"]["operations"]
    cur = ops.get("readout")
    alts = sorted(k for k, v in ops.items() if isinstance(v, dict) and "amplitude" in v
                  and isinstance(v["amplitude"], (int, float)) and f"#./{k}" != cur)
    tgt = None
    cur_amp = ops.get(cur[3:], {}).get("amplitude") if isinstance(cur, str) and cur.startswith("#./") else None
    for k in alts:
        if ops[k]["amplitude"] != cur_amp:
            tgt = k
            break

    def swap(st):
        st["qubits"]["qA4"]["resonator"]["operations"]["readout"] = f"#./{tgt}"
    d, n = clone(newest, base_id + 4, t + timedelta(seconds=240), swap)
    man["cases"].append({"id": "I4_pointer_swap", "run": n, "path": "qubits.qA4.resonator.operations.readout.amplitude",
                         "from": cur, "to": f"#./{tgt}",
                         "expect": "the alias path shows the new holder's amplitude as a change at this instant"})

    # I5: a run copied LATE into the middle of the archive (its instant between mid and nxt)
    gap = (nxt["created"] - mid["created"]).total_seconds()
    when = mid["created"] + timedelta(seconds=max(1, int(gap // 2)))
    st_mid = json.load(open(os.path.join(mid["path"], "quam_state", "state.json"), encoding="utf-8"))
    f3 = st_mid["qubits"]["qA3"]["f_01"]

    def late(st):
        st["qubits"]["qA3"]["f_01"] = f3 + 2.0e6
    d, n = clone(mid, base_id + 5, when, late)
    man["cases"].append({"id": "I5_late_copy", "run": n, "path": "qubits.qA3.f_01",
                         "between": [os.path.basename(mid["path"]), os.path.basename(nxt["path"])],
                         "expect": "inserted at its instant; the run after it is re-diffed (a change back)"})
    json.dump(man, open(MANIFEST, "w", encoding="utf-8"), indent=1)
    print(json.dumps(man, indent=1))


if __name__ == "__main__":
    main()
