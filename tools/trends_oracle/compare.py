"""Compare the fetched Trends surfaces with the independent oracle (WP3). stdlib + json only.

python compare.py <wp3 dir>   (reads oracle_out/ + surface/, writes report.json + evidence.json)

Every comparison is classified:
  match      -- the surface shows exactly what the oracle derives
  explained  -- the surface differs AND says why with its own classification (a witness-filtered row
                listed in the drawer, a labelled/doubted point, an observed snapshot, a held point, ...)
  defect     -- an unexplained miss/extra, a wrong number, a wrong instant, an over-claimed writer, ...
"""
from __future__ import annotations

import bisect
import html as _html
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

W = sys.argv[1]
O = os.path.join(W, "oracle_out")
S = os.path.join(W, "surface")
ABS = {"$absent": 1}
LOCAL = timezone(timedelta(hours=9))     # the display zone the surfaces must use (PC zone; project zone unset)


def load(d, n):
    p = os.path.join(d, n)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


# ------------------------------------------------------------------ oracle access
def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def finite(v):
    return is_num(v) and math.isfinite(v)


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


def is_ptr(v):
    return isinstance(v, str) and v.startswith(("#/", "#../", "#./"))


class Seq:
    """A change-list store: value of any holder at position i (bisect)."""

    def __init__(self, changes):
        self.ch = changes
        self.idx = {p: [c[0] for c in ch] for p, ch in changes.items()}

    def at(self, path, i):
        ks = self.idx.get(path)
        if not ks:
            return ABS
        j = bisect.bisect_right(ks, i) - 1
        return self.ch[path][j][1] if j >= 0 else ABS

    def full(self, i):
        out = {}
        for p in self.ch:
            v = self.at(p, i)
            if v is not ABS and v != ABS:
                out[p] = v
        return out


def split_path(path):
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


def ptr_target(ptr, cur):
    """A pointer string held at path *cur* (list of segments) -> the absolute segments it names."""
    if ptr.startswith("#/"):
        segs = [s for s in ptr[2:].split("/") if s != ""]
    elif ptr.startswith("#../"):
        if len(cur) < 2:
            return None
        segs = cur[:-2] + [s for s in ptr[4:].split("/") if s != ""]
    elif ptr.startswith("#./"):
        segs = cur[:-1] + [s for s in ptr[3:].split("/") if s != ""]
    else:
        return None
    return [s.replace(".", "\\.") for s in segs]


def holder(get, path, max_hops=8):
    """The holder *path* names in a state read through *get(path)* (pointer-followed)."""
    segs = split_path(path)
    cur, hops, i = [], 0, 0
    while i < len(segs):
        if cur:
            v = get(".".join(cur))
            if is_ptr(v):
                t = ptr_target(v, cur)
                hops += 1
                if t is None or hops > max_hops:
                    return None
                cur = t
                continue
        cur.append(segs[i])
        i += 1
    while hops <= max_hops:
        v = get(".".join(cur))
        if not is_ptr(v):
            break
        t = ptr_target(v, cur)
        if t is None:
            break
        if get(".".join(t)) is ABS:
            break           # a pointer to an object or to nothing: the value is the pointer string
        cur, hops = t, hops + 1
    return ".".join(cur)


def eff_value(seq, path, i):
    def get(p):
        v = seq.at(p, i)
        return ABS if (v is ABS or v == ABS) else v
    h = holder(get, path)
    if h is None:
        return None, ABS
    return h, get(h)


# ------------------------------------------------------------------ timeline (runs + observed snapshots)
class Oracle:
    def __init__(self):
        self.runs = load(O, "runs.json")
        self.R = Seq(load(O, "run_changes.json"))
        b = load(O, "bak.json")
        self.baks, self.B = b["baks"], Seq(b["changes"])
        self.bak_undated = b["undated"]
        s = load(O, "snaps.json")
        self.snaps, self.SN = s["snaps"], Seq(s["changes"])
        lv = load(O, "live.json")
        self.live, self.chip_name = lv["flat"], lv["chip_name"]
        self.by_runid = defaultdict(list)
        for r in self.runs:
            self.by_runid[r["run_id"]].append(r)
        cut = load(W, "cut.json")
        self.bak_cut = cut["cut_us"] if cut else None
        self.bak_after_cut = [x["name"] for x in self.baks if self.bak_cut and x["t_us"] > self.bak_cut]
        if self.bak_cut:
            # keep the change lists' indexes: drop only the tail beyond the cut
            self.baks = [x for x in self.baks if x["t_us"] <= self.bak_cut]
        self.bak_t = [x["t_us"] for x in self.baks]
        self._timeline()

    def _timeline(self):
        """Lane positions in time order: every run with a readable saved state of THIS chip (a run whose
        saved state declares another chip name leaves the lane), plus every instance snapshot whose state
        differs from both the state before it and the next run's state (a snapshot equal to a run state
        is not a second point)."""
        lane = []
        self.other_chip = []
        for r in self.runs:
            if not r.get("has_state"):
                continue
            cn = r.get("chip_name")
            if cn and self.chip_name and cn != self.chip_name:
                self.other_chip.append(r["k"])
                continue
            lane.append({"t_us": r["t_us"], "kind": "run", "i": r["k"]})
        run_pos = list(lane)
        self.snap_kept, self.snap_dropped = [], []
        # full-state comparisons for snapshots (cached)
        cache = {}

        def full(kind, i):
            if (kind, i) not in cache:
                cache[(kind, i)] = (self.R if kind == "run" else self.SN).full(i)
            return cache[(kind, i)]
        run_t = [p["t_us"] for p in run_pos]
        kept = []
        for j, sp in enumerate(self.snaps):
            t = sp["t_us"]
            a = bisect.bisect_left(run_t, t)
            prev_run = run_pos[a - 1] if a > 0 else None
            next_run = run_pos[a] if a < len(run_pos) else None
            prev_snap = kept[-1] if kept else None
            prev = prev_run
            if prev_snap is not None and (prev is None or prev_snap["t_us"] > prev["t_us"]):
                prev = prev_snap
            fs = full("snap", j)
            eq_prev = prev is not None and same(full(prev["kind"], prev["i"]), fs)
            eq_next = next_run is not None and same(full("run", next_run["i"]), fs)
            rec = {"t_us": t, "kind": "snap", "i": j, "ts": sp["ts"], "trigger": sp["trigger"],
                   "eq_prev": eq_prev, "eq_next": eq_next}
            eq_run = None
            named = [r for r in self.by_runid.get(sp.get("run_id"), []) if r.get("has_state")]
            if not (eq_prev or eq_next) and sp["trigger"] != "experiment" and named:
                if same(full("run", named[0]["k"]), fs):
                    eq_run = named[0]["run_id"]
            rec["eq_run"] = eq_run
            # an 'experiment' snapshot is SM's copy of a run's own saved state (not an observation of
            # the chip); a snapshot equal to the state of the run its own meta names is that run, not a
            # second point (B.4); one equal to an OLDER run state is an observation (the chip went back)
            if eq_prev or eq_next or eq_run is not None or sp["trigger"] == "experiment":
                self.snap_dropped.append(rec)
            else:
                kept.append(rec)
                self.snap_kept.append(rec)
        lane += kept
        lane.sort(key=lambda p: (p["t_us"], 0 if p["kind"] == "snap" else 1, p["i"]))
        self.lane = lane
        for n, p in enumerate(lane):
            p["pos"] = n

    def seq(self, p):
        return self.R if p["kind"] == "run" else self.SN

    def series(self, path):
        """Effective change points [(pos, holder, value)] of *path* over the lane."""
        out, prev = [], ABS
        for p in self.lane:
            h, v = eff_value(self.seq(p), path, p["i"])
            if not (prev is ABS and v is ABS) and not (prev is not ABS and v is not ABS and same(prev, v)):
                out.append((p["pos"], h, v))
                prev = v
        return out

    def derived_series(self, q, which):
        """assignment_fidelity (GE: mean of the 2x2 diagonal) / _gef (mean of the n>=3 diagonal) from the
        matrix elements in force at each lane position."""
        base = f"qubits.{q}.resonator.{'confusion_matrix' if which == 'ge' else 'gef_confusion_matrix'}"
        out, prev, prev_cells = [], ABS, {}
        for p in self.lane:
            sq, i = self.seq(p), p["i"]
            cells = {}
            for a in range(4):
                for b in range(4):
                    v = sq.at(f"{base}.{a}.{b}", i)
                    if v is not ABS and v != ABS:
                        cells[(a, b)] = v
            n = 1 + max((max(a, b) for a, b in cells), default=-1)
            val = None
            if n and all(finite(cells.get((a, b))) for a in range(n) for b in range(n)):
                if which == "ge" and n >= 2:
                    val = (cells[(0, 0)] + cells[(1, 1)]) / 2
                elif which == "gef" and n >= 3:
                    val = sum(cells[(a, a)] for a in range(n)) / n
            if prev is ABS or not ((prev is None and val is None) or
                                   (prev is not None and val is not None and abs(prev - val) <= 1e-12)):
                moved = sorted(f"{base}.{a}.{b}" for a, b in set(cells) | set(prev_cells)
                               if not same(cells.get((a, b), ABS), prev_cells.get((a, b), ABS)))
                out.append((p["pos"], base, val, moved))
                prev = val
            prev_cells = cells
        return out

    def proven(self, k, holder_path, value):
        """Exact-leaf proof: a node.json patch (add|replace) naming exactly this holder with the same value."""
        for path, op, v, _old in self.runs[k].get("patches") or []:
            if op in ("add", "replace") and path == holder_path and same(v, value):
                return "exact"
        for path, op, v, _old in self.runs[k].get("patches") or []:
            if op in ("add", "replace") and path and holder_path.startswith(path + "."):
                sub = v
                for s_ in split_path(holder_path[len(path) + 1:]):
                    if isinstance(sub, dict):
                        sub = sub.get(s_, ABS)
                    elif isinstance(sub, list) and s_.isdigit() and int(s_) < len(sub):
                        sub = sub[int(s_)]
                    else:
                        sub = ABS
                        break
                if sub is not ABS and same(sub, value):
                    return "ancestor"
        for path, op, v, _old in self.runs[k].get("patches") or []:
            if path and path.split(".")[-1] == holder_path.split(".")[-1] and same(v, value):
                return "alias"     # a patch naming another spelling (an alias) of this leaf
        return None

    def bak_value(self, path, i):
        return eff_value(self.B, path, i)[1]


# ------------------------------------------------------------------ surface helpers
KEY_RE = re.compile(r"^(\d{8})_(\d{6})_e(\d+)$")


def key_sec(key):
    m = KEY_RE.match(key or "")
    if not m:
        return None, None
    dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp()), int(m.group(3))


def sec(t_us):
    return t_us // 1_000_000


def runid_in(attr):
    m = re.search(r"#(\d+)", (attr or {}).get("label", "") + " " + (attr or {}).get("sub", ""))
    return int(m.group(1)) if m else None


def vstr(v):
    if v is ABS or v == ABS:
        return "(absent)"
    return json.dumps(v)[:80]


class Tally:
    def __init__(self):
        self.c = Counter()
        self.ev = []

    def add(self, surface, cls, what, **detail):
        self.c[(surface, cls, what)] += 1
        if cls != "match" and len([e for e in self.ev if e["what"] == what]) < 60:
            self.ev.append({"surface": surface, "class": cls, "what": what, **detail})


# ------------------------------------------------------------------ drawer parse
ROW_RE = re.compile(r'<tr class="vh-row[^"]*"\s+data-eid="(\d+)" data-provenance="([^"]+)">(.*?)</tr>', re.S)
LI_RE = re.compile(r'<li data-eid="(\d+)"([^>]*)>(.*?)</li>', re.S)


def parse_drawer(body):
    rows = []
    for eid, prov, inner in ROW_RE.findall(body):
        fill = re.search(r'<td class="fh-val" title="([^"]*)"', inner)
        ts = re.search(r'<td class="fh-ts">.*?data-utc="([^"]+)"', inner, re.S)
        lab = re.search(r'class="fh-exp vh-by"[^>]*>(.*?)</span>', inner, re.S)
        flags = re.findall(r'<span class="vh-flag[^"]*"[^>]*>(.*?)</span>', inner, re.S)
        data = re.search(r'hx-get="/dataset/([^"?]+)(\?via=saved)?"', inner)
        rows.append({"eid": int(eid), "prov": prov, "fill": _html.unescape(fill.group(1)) if fill else None,
                     "ts": ts.group(1) if ts else None,
                     "label": re.sub(r"<[^>]+>", "", _html.unescape(lab.group(1))) if lab else "",
                     "flags": [re.sub(r"<[^>]+>", "", _html.unescape(f)).strip() for f in flags],
                     "uid": data.group(1) if data else None, "via_saved": bool(data and data.group(2))})
    nk_block = body.split('class="vh-not-kept"', 1)
    excursions, not_kept = [], []
    exb = re.search(r'vh-excursions">(.*?)</details>', body, re.S)
    if exb:
        for eid, _a, inner in LI_RE.findall(exb.group(1)):
            txt = re.sub(r"<[^>]+>", "", _html.unescape(inner))
            saved_by = txt.split("not confirmed")[0]
            rng = re.search(r"from #(\d+) to #(\d+)", saved_by)
            runs = list(range(int(rng.group(1)), int(rng.group(2)) + 1)) if rng else []
            for a_, b_ in re.findall(r"#(\d+)-#(\d+)", saved_by):
                runs += list(range(int(a_), int(b_) + 1))
            runs += [int(x) for x in re.findall(r"#(\d+)", saved_by)]
            excursions.append({"eid": int(eid), "text": txt, "runs": runs})
    nkb = re.search(r'<details class="vh-not-kept">(.*?)</details>', body, re.S)
    if nkb:
        for eid, attrs, inner in LI_RE.findall(nkb.group(1)):
            code = re.search(r"<code>(.*?)</code>", inner, re.S)
            not_kept.append({"eid": int(eid), "text": re.sub(r"<[^>]+>", "", _html.unescape(inner)),
                             "runs": [int(x) for x in re.findall(r"#(\d+)", re.sub(r"<[^>]+>", "", _html.unescape(inner)).split("not in the next")[0])],
                             "display": _html.unescape(code.group(1)) if code else None,
                             "witness": re.search(r'data-witness="([^"]*)"', attrs).group(1)})
    foot = re.search(r'<p class="fh-foot vh-foot">(.*?)</p>', body, re.S)
    return {"rows": rows, "excursions": excursions, "not_kept": not_kept,
            "foot": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", _html.unescape(foot.group(1)))).strip() if foot else None,
            "empty": "fh-empty" in body}


# ------------------------------------------------------------------ the per-series check
SHOWN: dict = {}
def check_series(orc, T, surface, path, sm, ochanges, drawer, derived=False):
    """*sm*: one chart series {points, held, attr}; *ochanges*: the oracle's [(pos, holder, value, ...)]."""
    lane = orc.lane
    held = sm.get("held") or {}
    attr = sm.get("attr") or {}
    pts = []
    for key, v in sm.get("points") or []:
        s_, eid = key_sec(key)
        if s_ is None:
            T.add(surface, "defect", "unparsable_key", path=path, key=key)
            continue
        pts.append({"key": key, "sec": s_, "eid": eid, "v": v, "held": key in held, "attr": attr.get(key)})
    real = [p for p in pts if not p["held"]]
    hold = [p for p in pts if p["held"]]
    SHOWN[path] = [(p["sec"], p["v"]) for p in real]
    # oracle changes with their instants
    oc = []
    for c in ochanges:
        lp = lane[c[0]]
        oc.append({"pos": c[0], "holder": c[1], "v": c[2], "sec": sec(lp["t_us"]), "t_us": lp["t_us"],
                   "kind": lp["kind"], "i": lp["i"], "elems": c[3] if len(c) > 3 else None,
                   "run_id": orc.runs[lp["i"]]["run_id"] if lp["kind"] == "run" else None})
    used = set()

    def shown(v):
        """What a chart shows for an oracle value: a number, or a gap (None)."""
        if v is ABS or v is None:
            return None
        if derived:
            return v
        return v if finite(v) else None

    anchors = {}
    for p in real:
        rid = runid_in(p["attr"])
        prov = (p["attr"] or {}).get("provenance")
        cands = [c for c in oc if c["sec"] == p["sec"] and c["pos"] not in used]
        if rid is not None and prov not in ("observed", "sm"):
            cands = [c for c in cands if c["run_id"] == rid] or cands
        if prov == "observed":
            cands = [c for c in cands if c["kind"] == "snap"] or cands
        good = [c for c in cands if (shown(c["v"]) is None and p["v"] is None) or
                (shown(c["v"]) is not None and p["v"] is not None and
                 (abs(shown(c["v"]) - p["v"]) <= 1e-12 if derived else same(shown(c["v"]), p["v"])))]
        if good:
            c = good[0]
            used.add(c["pos"])
            anchors[p["key"]] = c
            T.add(surface, "match", "point_value_time", path=path)
            continue
        if cands:
            c = cands[0]
            T.add(surface, "defect", "point_wrong_value", path=path, key=p["key"], shown=p["v"],
                  oracle=vstr(c["v"]), run=c["run_id"], prov=prov)
            used.add(c["pos"])
            anchors[p["key"]] = c
            continue
        # no oracle change at that second: same run at another instant? same value one event off?
        if rid is not None and orc.by_runid.get(rid):
            r = orc.by_runid[rid][0]
            T.add(surface, "defect", "point_run_at_other_instant", path=path, key=p["key"], shown=p["v"],
                  run=rid, run_sec_off_h=round((p["sec"] - sec(r["t_us"])) / 3600, 3), prov=prov)
        else:
            near = [c for c in oc if abs(c["sec"] - p["sec"]) <= 1 and c["pos"] not in used]
            T.add(surface, "defect" if prov not in ("sm", "held_before_write") else "explained",
                  "point_without_oracle_change", path=path, key=p["key"], shown=p["v"], prov=prov,
                  label=(p["attr"] or {}).get("label"), near=[(c["run_id"], vstr(c["v"])) for c in near][:3])
    # ---- provenance of anchored run points
    for p in real:
        c = anchors.get(p["key"])
        a = p["attr"] or {}
        prov = a.get("provenance")
        if c is None:
            continue
        if c["kind"] == "snap":
            ok = prov == "observed" and not a.get("uid") and not a.get("saved_uid")
            T.add(surface, "match" if ok else "defect", "observed_words", path=path, key=p["key"], prov=prov)
            continue
        k = c["i"]
        if derived:
            proofs = [orc.proven(k, e, orc.R.at(e, k)) == "exact" for e in (c["elems"] or [])]
            oproof = "exact" if proofs and all(proofs) else None
        else:
            oproof = orc.proven(k, c["holder"], c["v"])
        if prov == "run_proven":
            if oproof == "exact":
                T.add(surface, "match", "writer_named_proven", path=path)
            else:
                T.add(surface, "defect", "writer_named_without_proof", path=path, key=p["key"], run=c["run_id"],
                      oracle_proof=oproof, holder=c["holder"])
            uid = a.get("uid") or ""
            if not uid:
                T.add(surface, "defect", "proven_point_has_no_run_link", path=path, key=p["key"], run=c["run_id"])
            elif not uid.endswith(":" + str(c["run_id"])):
                T.add(surface, "defect", "uid_not_this_run", path=path, key=p["key"], uid=uid, run=c["run_id"])
        elif prov in ("run_saved", "first_record", "run_uncertain_chip"):
            if oproof == "exact" and prov == "run_saved":
                T.add(surface, "defect", "proven_writer_not_named", path=path, key=p["key"], run=c["run_id"],
                      holder=c["holder"])
            else:
                T.add(surface, "match", "unproven_not_named", path=path)
            if a.get("uid"):
                T.add(surface, "defect", "uid_on_unproven_point", path=path, key=p["key"], uid=a.get("uid"))
            su = a.get("saved_uid")
            if su and not su.endswith(":" + str(c["run_id"])):
                T.add(surface, "defect", "saved_uid_not_this_run", path=path, key=p["key"], uid=su, run=c["run_id"])
        else:
            T.add(surface, "explained", "run_point_other_provenance", path=path, key=p["key"], prov=prov)
    # ---- oracle changes the series does not show
    seg_vals = []           # (sec, eid, value) of SM's real points, in order
    for p in real:
        seg_vals.append((p["sec"], p["eid"], p["v"]))
    nk_runs = {r for x in (drawer or {}).get("not_kept", []) for r in x["runs"]}
    ex_runs = {r for x in (drawer or {}).get("excursions", []) for r in x["runs"]}
    for c in oc:
        if c["pos"] in used:
            continue
        # the value the series shows at that instant
        i = bisect.bisect_right([(s_, e) for s_, e, _v in seg_vals], (c["sec"], 10 ** 12)) - 1
        cur = seg_vals[i][2] if i >= 0 else None
        sv = shown(c["v"])
        if (sv is None and cur is None) or (sv is not None and cur is not None and
                                            (abs(sv - cur) <= 1e-12 if derived else same(sv, cur))):
            T.add(surface, "explained", "oracle_change_already_in_force", path=path, run=c["run_id"],
                  why="the series shows this value at that instant (restoring row / same value re-saved)")
            continue
        if c["run_id"] is not None and c["run_id"] in nk_runs:
            T.add(surface, "explained", "witness_not_kept", path=path, run=c["run_id"])
            continue
        if c["run_id"] is not None and c["run_id"] in ex_runs:
            T.add(surface, "explained", "witness_excursion", path=path, run=c["run_id"])
            continue
        if i < 0 and not real:
            T.add(surface, "defect", "series_missing", path=path, run=c["run_id"], oracle=vstr(c["v"]))
            continue
        T.add(surface, "defect", "oracle_change_missing", path=path, run=c["run_id"], kind=c["kind"],
              sec=c["sec"], oracle=vstr(c["v"]), shown_then=cur,
              ts=c.get("i") if c["kind"] == "snap" else None)
    # ---- held point: the newest event of the view, carrying the last value
    for h in hold:
        last = real[-1] if real else None
        ok = last is not None and same(h["v"], last["v"])
        T.add(surface, "match" if ok else "defect", "held_point_value", path=path, key=h["key"])
    return real, oc


#: the surface's own unconfirmed-on-the-chip words (hub_witness.DOUBT_TEXT shorts, the spec's labels)
DOUBT_SHORT = ("no later run has read the chip yet", "changed again before the chip was read",
               "the next read of the chip found another value", "not in the next recorded state")


def chip_truth(orc, T, surface, path, real, derived=False, oc=()):
    """Every backup inside an SM step segment must hold the segment's value; plain vs labelled."""
    if derived:
        return
    segs = []
    for n, p in enumerate(real):
        t0 = p["sec"]
        t1 = real[n + 1]["sec"] if n + 1 < len(real) else None
        segs.append((t0, t1, p))
    nxt_v = {id(p): (real[n + 1]["v"] if n + 1 < len(real) else ABS) for n, p in enumerate(real)}
    for t0, t1, p in segs:
        a = bisect.bisect_right(orc.bak_t, (t0 + 1) * 1_000_000 - 1)
        b = bisect.bisect_left(orc.bak_t, t1 * 1_000_000) if t1 is not None else len(orc.bak_t)
        attr = p["attr"] or {}
        # unconfirmed: a doubt flag, an uncertain chip, or the words that say no later state read it
        labelled = (bool(attr.get("flag_text")) or attr.get("provenance") in ("run_uncertain_chip",)
                    or any(w in (attr.get("sub") or "") + " ".join(attr.get("flag_text") or [])
                           for w in DOUBT_SHORT))
        bad = []
        early = []
        filtered_held = []
        n_in = 0
        held_any = False
        for j in range(a, b):
            if orc.baks[j].get("precision") == "min":
                continue
            bv = orc.bak_value(path, j)
            if bv is ABS:
                if p["v"] is None:
                    n_in += 1
                    held_any = True      # absent on the chip agrees with a gap
                continue
            n_in += 1
            shownv = bv if finite(bv) else None
            if not ((shownv is None and p["v"] is None) or
                    (shownv is not None and p["v"] is not None and same(shownv, p["v"]))):
                # a value a run of THIS segment saved and the series left out (witness filter): the
                # backup is that run's value on the chip, not a contradiction of the shown value
                hidden = [c for c in oc if t0 <= c["sec"] <= orc.baks[j]["t_us"] // 10**6
                          and (t1 is None or c["sec"] < t1) and same(c["v"], bv)]
                nv = nxt_v[id(p)]
                if hidden:
                    filtered_held.append((orc.baks[j]["name"], vstr(bv), hidden[-1]["run_id"]))
                elif nv is not ABS and ((shownv is None and nv is None) or
                                        (shownv is not None and nv is not None and same(shownv, nv))):
                    early.append((orc.baks[j]["name"], vstr(bv)))
                else:
                    bad.append((orc.baks[j]["name"], vstr(bv)))
            else:
                held_any = True
        if not n_in:
            continue
        if early:
            T.add(surface, "explained", "chip_had_the_next_value_before_its_record", path=path, key=p["key"],
                  baks=early[:3], n=len(early),
                  why="a backup inside the segment already holds the value of the NEXT point: an edit no "
                      "run or SM write recorded, dated by the next recorded state")
        if filtered_held:
            T.add(surface, "explained", "chip_held_a_value_the_series_filtered", path=path, key=p["key"],
                  baks=filtered_held[:3], n=len(filtered_held),
                  why="a backup holds the value a run in this segment saved; the series left that run out "
                      "(witness filter: 'not confirmed' / 'not in the next recorded state')")
        if p["attr"] and (p["attr"].get("provenance") == "observed"):
            held_any = True          # SM read this value off the live chip at the point's own instant
        kind = "held_then_changed" if held_any else "never_held"
        if not bad:
            if not filtered_held and not early:
                T.add(surface, "match", "chip_truth_segment", path=path)
        elif labelled:
            T.add(surface, "explained", f"chip_truth_unconfirmed_label_{kind}", path=path, key=p["key"],
                  shown=p["v"], flag=attr.get("flag_text"), sub=attr.get("sub"), baks=bad[:3], n_bad=len(bad),
                  n_in=n_in)
        elif held_any:
            T.add(surface, "explained", "chip_truth_plain_held_then_changed", path=path, key=p["key"],
                  shown=p["v"], prov=attr.get("provenance"), label=attr.get("label"), baks=bad[:3],
                  n_bad=len(bad), n_in=n_in,
                  why="the chip held the shown value in this segment, then an edit no run or SM write recorded "
                      "changed it (the next recorded state carries it)")
        else:
            T.add(surface, "defect", "chip_truth_plain_never_held", path=path, key=p["key"], shown=p["v"],
                  prov=attr.get("provenance"), label=attr.get("label"), sub=attr.get("sub"), baks=bad[:3],
                  n_bad=len(bad), n_in=n_in)


def shown_at(series_pts, t_us):
    """The value a chart's step line shows at instant *t_us* (ABS before its first point)."""
    v = ABS
    for sec_, val in series_pts:
        if sec_ * 10**6 <= t_us:
            v = val
        else:
            break
    return v


def not_kept_truth(orc, T, path, drawer, series_pts=()):
    """A 'not in the next recorded state' row is wrong if the chip (a backup) held that value after the
    next recorded state, for longer than the next run's gap (VPLAN B.3), before any later run saved
    that value again."""
    if not drawer:
        return
    for nk in drawer.get("not_kept", []):
        if not nk["runs"]:
            continue
        rs = orc.by_runid.get(nk["runs"][0]) or []
        if not rs:
            continue
        r = rs[0]
        lane_runs = [p for p in orc.lane if p["kind"] == "run" and p["t_us"] > r["t_us"]]
        if not lane_runs:
            continue
        t_next = lane_runs[0]["t_us"]
        saved = orc.R.at(holder_of(orc, path, r["k"]), r["k"])
        resave = next((p["t_us"] for p in lane_runs[1:]
                       if same(eff_value(orc.R, path, p["i"])[1], saved)), None)
        gap = t_next - r["t_us"]
        lo, hi = t_next + gap, (resave if resave is not None else 10 ** 18)
        a = bisect.bisect_right(orc.bak_t, lo)
        b = bisect.bisect_left(orc.bak_t, hi)
        held_late = [orc.baks[j]["name"] for j in range(a, b) if same(orc.bak_value(path, j), saved)
                     and not same(shown_at(series_pts, orc.baks[j]["t_us"]), saved)]
        a0 = bisect.bisect_right(orc.bak_t, r["t_us"])
        b0 = bisect.bisect_right(orc.bak_t, lo)
        held_soon = [orc.baks[j]["name"] for j in range(a0, b0) if same(orc.bak_value(path, j), saved)]
        if held_late:
            T.add("drawer", "explained", "not_kept_but_chip_held_it_later", path=path, run=r["run_id"],
                  value=vstr(saved), baks=held_late[:3], n=len(held_late),
                  why="contract (hub_witness.WORDS): the row says only that the next recorded state did not "
                      "carry it; a lab's backups may show such a value live while another line of runs "
                      "recorded something else")
        elif held_soon:
            T.add("drawer", "explained", "not_kept_chip_held_it_briefly", path=path, run=r["run_id"],
                  value=vstr(saved), baks=held_soon[:3],
                  why="the words say only what the records prove (not in the next recorded state); a backup "
                      "shows the value live within one run gap")
        else:
            T.add("drawer", "match", "not_kept_verdict", path=path)


def holder_of(orc, path, k):
    def get(p):
        v = orc.R.at(p, k)
        return ABS if (v is ABS or v == ABS) else v
    return holder(get, path)


# ------------------------------------------------------------------ main
CURATED_PATHS = {"T1": "T1", "T2ramsey": "T2ramsey", "T2echo": "T2echo", "f_01": "f_01",
                 "gate_fidelity_avg": "gate_fidelity.averaged", "gate_fidelity_x180": "gate_fidelity.x180",
                 "gate_fidelity_x90": "gate_fidelity.x90",
                 "readout_amplitude": "resonator.operations.readout.amplitude",
                 "x180_amplitude": "xy.operations.x180_DragCosine.amplitude",
                 "x90_amplitude": "xy.operations.x90_DragCosine.amplitude"}


def drawer_check(orc, T, path, d):
    """The drawer's rows vs the oracle: every row's value is a value the oracle has at that run; the run
    named (Data button) only with proof; the time shown is the run's instant in the display zone."""
    if d is None:
        return
    for r in d["rows"]:
        rid = runid_in({"label": r["label"]})
        if r["prov"] == "run_proven":
            rs = orc.by_runid.get(rid) or []
            if not rs:
                T.add("drawer", "defect", "drawer_proven_unknown_run", path=path, eid=r["eid"], label=r["label"])
                continue
            k = rs[0]["k"]
            h = holder_of(orc, path, k)
            v = orc.R.at(h, k)
            pr = orc.proven(k, h, v)
            T.add("drawer", "match" if pr == "exact" else "defect", "drawer_writer_named", path=path,
                  eid=r["eid"], run=rid, proof=pr)
            if not r["uid"]:
                T.add("drawer", "defect", "drawer_proven_row_has_no_data_link", path=path, eid=r["eid"], run=rid)
            elif not r["uid"].endswith(":" + str(rid)) or r["via_saved"]:
                T.add("drawer", "defect", "drawer_data_link_wrong", path=path, eid=r["eid"], uid=r["uid"])
        elif r["prov"] == "run_saved":
            if r["uid"] and not r["via_saved"]:
                T.add("drawer", "defect", "drawer_saved_opens_without_via", path=path, eid=r["eid"])
            else:
                T.add("drawer", "match", "drawer_saved_link", path=path)
        elif r["prov"] == "observed":
            T.add("drawer", "match" if not r["uid"] else "defect", "drawer_observed_no_run", path=path)
        # time: the row's data-utc (what the client shows in the display zone) == the run's instant
        if rid is not None and r["prov"] in ("run_proven", "run_saved", "first_record") and r["ts"]:
            rs = orc.by_runid.get(rid) or []
            if rs:
                try:
                    shown = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
                except ValueError:
                    shown = None
                want = datetime.fromtimestamp(rs[0]["t_us"] // 1_000_000, timezone.utc)
                if shown is not None and int(shown.timestamp()) == int(want.timestamp()):
                    T.add("drawer", "match", "drawer_time_is_run_instant", path=path)
                else:
                    T.add("drawer", "defect", "drawer_time_not_run_instant", path=path, eid=r["eid"],
                          shown=r["ts"], want=want.isoformat(), run=rid,
                          off_h=round((shown.timestamp() - want.timestamp()) / 3600, 3) if shown else None)

def main():
    orc = Oracle()
    T = Tally()
    ev = {"lane": {"positions": len(orc.lane), "runs": sum(1 for p in orc.lane if p["kind"] == "run"),
                   "snapshots_kept": [s["ts"] for s in orc.snap_kept],
                   "snapshots_dropped": len(orc.snap_dropped), "other_chip_runs": len(orc.other_chip),
                   "runs_without_state": [r["rel"] for r in orc.runs if not r.get("has_state")],
                   "baks": len(orc.baks), "baks_undated": len(orc.bak_undated),
                   "baks_after_rig_copy_ignored": len(orc.bak_after_cut)}}
    drawer_raw = load(S, "drawer.json") or {}
    drawers = {p: parse_drawer(v["html"]) for p, v in drawer_raw.items() if v["status"] == 200}
    counts = Counter()

    # ---- Chip Status > Trends: curated
    cur = load(S, "trends_curated.json")
    if cur:
        for part in ("a", "b"):
            for ch in cur[part]["charts"] or []:
                m = ch["metric"]
                seen = set()
                for s in ch["series"]:
                    q = s["entity"]
                    seen.add(q)
                    counts["curated_series"] += 1
                    counts["curated_points"] += len(s["points"])
                    if m in CURATED_PATHS:
                        path = f"qubits.{q}.{CURATED_PATHS[m]}"
                        real, oc_ = check_series(orc, T, "chip_trends", path, s, orc.series(path), drawers.get(path))
                        chip_truth(orc, T, "chip_trends", path, real, oc=oc_)
                    else:
                        which = "ge" if m == "assignment_fidelity" else "gef"
                        dch = orc.derived_series(q, which)
                        mat = "confusion_matrix" if which == "ge" else "gef_confusion_matrix"
                        n = 2 if which == "ge" else 3
                        merged = {"not_kept": [], "excursions": []}
                        for i_ in range(n):
                            for j_ in range(n):
                                dd = drawers.get(f"qubits.{q}.resonator.{mat}.{i_}.{j_}") or {}
                                merged["not_kept"] += dd.get("not_kept", [])
                                merged["excursions"] += dd.get("excursions", [])
                        check_series(orc, T, "chip_trends", f"{m}:{q}", s, dch, merged, derived=True)
                # a qubit whose oracle series has a finite value but no chart line
                for q in sorted({k.split(".")[1] for k in orc.live if k.startswith("qubits.")}):
                    if q in seen:
                        continue
                    if m in CURATED_PATHS:
                        o = orc.series(f"qubits.{q}.{CURATED_PATHS[m]}")
                        if any(finite(c[2]) for c in o):
                            T.add("chip_trends", "defect", "series_missing_for_qubit", path=f"{m}:{q}",
                                  n_finite=sum(1 for c in o if finite(c[2])))
                        else:
                            T.add("chip_trends", "match", "no_series_no_finite_value", path=f"{m}:{q}")
    # ---- pairs + leaves (typed paths)
    for fn, surface in (("trends_pairs.json", "chip_trends_pairs"), ("trends_leaves.json", "chip_trends_leaves")):
        d = load(S, fn) or {}
        for req_path, v in d.items():
            if v.get("charts") is None:
                T.add(surface, "defect", "request_failed", path=req_path, status=v.get("status"))
                continue
            for ch in v["charts"]:
                for s in ch["series"]:
                    path = entity_path(req_path, s)
                    counts[surface + "_series"] += 1
                    counts[surface + "_points"] += len(s["points"])
                    real, oc_ = check_series(orc, T, surface, path, s, orc.series(path), drawers.get(path))
                    chip_truth(orc, T, surface, path, real, oc=oc_)
    # ---- drawers
    for path, d in drawers.items():
        counts["drawer_paths"] += 1
        counts["drawer_rows"] += len(d["rows"])
        counts["drawer_not_kept"] += len(d["not_kept"])
        counts["drawer_excursions"] += sum(1 for _ in d["excursions"])
        drawer_check(orc, T, path, d)
        not_kept_truth(orc, T, path, d, SHOWN.get(path, ()))
    ev["counts"] = counts

    # ---- Datasets > Trends
    ds = load(S, "dstrends.json")
    if ds:
        ds_check(orc, T, ds, counts)
    uids = load(S, "uids.json")
    if uids:
        for key, v in uids.items():
            uid, via = key.split("|")
            rid = int(uid.rsplit(":", 1)[1])
            rs = orc.by_runid.get(rid) or []
            base = rs[0]["rel"].split("/")[-1] if rs else None
            counts["uids_opened"] += 1
            if v["status"] != 200:
                T.add("dataset_link", "defect", "uid_not_200", uid=uid, status=v["status"])
            elif rs and str(rs[0]["folder_id"]) == v.get("run_id") and rs[0]["exp"] == v.get("experiment")                     and rs[0]["rel"].split("/")[0] == v.get("date"):
                T.add("dataset_link", "match", "uid_opens_same_run", uid=uid)
            else:
                T.add("dataset_link", "defect", "uid_opens_other_run", uid=uid, want=base, got=(v.get("run_id"), v.get("experiment"), v.get("date")))
            if via == "saved":
                T.add("dataset_link", "match" if v.get("via_saved_words") else "defect",
                      "via_saved_says_not_proven", uid=uid)
    rep = load(S, "report.json")
    if rep:
        report_check(orc, T, rep, cur, counts)
    zone = load(S, "zone.json")
    if zone:
        tag = zone.get("html_tag") or ""
        ev["zone_tag"] = tag
        # project zone unset -> no data-sm-zone: the client falls back to the viewer's zone (B.1)
        T.add("zone", "match" if "data-sm-zone" not in tag else "explained",
              "display_zone_is_pc_zone" if "data-sm-zone" not in tag else "project_zone_set", tag=tag)
    legacy = load(S, "legacy.json")
    if legacy:
        m = re.search(r"var data = (\[.*?\]);\s*\n", legacy.get("html") or "", re.S)
        rows = json.loads(m.group(1)) if m else []
        if legacy["status"] != 200 or not rows:
            T.add("legacy_trend", "defect", "legacy_smoke", status=legacy["status"])
        for row in rows:
            for v in row["values"]:
                rid = int(re.search(r"#(\d+)", v["label"]).group(1))
                k = orc.by_runid[rid][0]["k"]
                want = orc.R.at(f"qubits.{row['qubit']}.{row['property']}", k)
                loc = datetime.fromtimestamp(orc.runs[k]["t_us"] / 1e6, LOCAL).strftime("%Y-%m-%d %H:%M:%S")
                ok = same(want, v["value"]) and loc in v["label"]
                T.add("legacy_trend", "match" if ok else "defect", "legacy_value_and_label", run=rid,
                      shown=v["value"], oracle=vstr(want), label=v["label"])

    tally = defaultdict(lambda: Counter())
    for (surface, cls, what), n in T.c.items():
        tally[surface][cls] += n
    by_what = defaultdict(dict)
    for (surface, cls, what), n in sorted(T.c.items()):
        by_what[surface][f"{cls}:{what}"] = n
    report = {"tally": {s: dict(c) for s, c in tally.items()}, "by_kind": by_what,
              "counts": dict(counts), "lane": ev["lane"], "zone_tag": ev.get("zone_tag")}
    json.dump(report, open(os.path.join(W, "report.json"), "w", encoding="utf-8"), indent=1, default=str)
    json.dump(T.ev, open(os.path.join(W, "evidence.json"), "w", encoding="utf-8"), indent=1, default=str)
    print(json.dumps(report["tally"], indent=1))
    for s, d in by_what.items():
        for k, n in d.items():
            if not k.startswith("match"):
                print(f"  {s:22s} {k:55s} {n}")


def entity_path(req_path, s):
    parts = req_path.split(".")
    ent = s["entity"]
    if " \u00b7 " in ent:
        e, wild = ent.split(" \u00b7 ", 1)
        ws = wild.split(".")
        out, wi = [], 0
        for i, sg in enumerate(parts):
            if i == 1:
                out.append(e)
            elif sg == "*" and i > 1:
                out.append(ws[wi])
                wi += 1
            else:
                out.append(sg)
        return ".".join(out)
    if len(parts) >= 3 and parts[0] in ("qubits", "qubit_pairs"):
        return ".".join([parts[0], ent] + parts[2:])
    return req_path


def ds_check(orc, T, ds, counts):
    """/trends/series == fit_results of exactly the complete runs of that experiment (run-id order, single
    folder), x == created_at instant (ms), uid == '<folder key>:<run_id>', non-finite -> null."""
    for exp, v in ds["series"].items():
        js = v.get("json")
        if v.get("status") != 200 or not isinstance(js, dict):
            T.add("datasets_trends", "defect", "series_request_failed", exp=exp, status=v.get("status"))
            continue
        if js.get("indexing"):
            T.add("datasets_trends", "explained", "payload_says_indexing", exp=exp)
            continue
        runs = [r for r in orc.runs if r["exp"] == exp]
        complete = [r for r in runs if r.get("node_ok", True) and r.get("data_ok") is not False]
        complete.sort(key=lambda r: r["folder_id"])
        counts["ds_experiments"] += 1
        counts["ds_runs"] += len(js["runs"])
        ids = [x[0] for x in js["runs"]]
        if ids != [r["folder_id"] for r in complete]:
            T.add("datasets_trends", "defect", "run_set_or_order", exp=exp, shown=len(ids), oracle=len(complete),
                  first_diff=next(((a, b["folder_id"]) for a, b in zip(ids, complete) if a != b["folder_id"]), None))
        else:
            T.add("datasets_trends", "match", "run_set_and_order", exp=exp)
        if js.get("n_runs") != len(complete):
            T.add("datasets_trends", "defect", "n_runs_count", exp=exp, shown=js.get("n_runs"), oracle=len(complete))
        und = sum(1 for x in js["runs"] if x[1] is None)
        T.add("datasets_trends", "match" if js.get("undated") == und == 0 else "defect", "undated_count",
              exp=exp, shown=js.get("undated"))
        inc = sum(1 for r in runs if r.get("data_ok") is False or not r.get("node_ok", True))
        T.add("datasets_trends", "match" if js.get("incomplete") == inc else "defect", "incomplete_count",
              exp=exp, shown=js.get("incomplete"), oracle=inc)
        byid = {r["folder_id"]: r for r in complete}
        fk = None
        zig = 0
        prev_t = None
        for rid, t_ms, uid in js["runs"]:
            r = byid.get(rid)
            if r is None:
                continue
            if t_ms != r["t_us"] // 1000:
                T.add("datasets_trends", "defect", "x_not_created_at", exp=exp, run=rid, shown=t_ms,
                      oracle=r["t_us"] // 1000, off_h=round((t_ms - r["t_us"] // 1000) / 3.6e6, 3) if t_ms else None)
            else:
                T.add("datasets_trends", "match", "x_is_created_at", exp=exp)
            fk = uid.rsplit(":", 1)[0]
            if uid != f"{fk}:{rid}":
                T.add("datasets_trends", "defect", "uid_format", exp=exp, uid=uid)
            if prev_t is not None and t_ms is not None and t_ms < prev_t:
                zig += 1
            prev_t = t_ms if t_ms is not None else prev_t
        if zig:
            T.add("datasets_trends", "explained", "run_id_order_zigzags_in_time", exp=exp, n=zig,
                  why="a single folder is ordered by run id while x is time (B.2 note)")
        # values
        want = {}
        for n, r in enumerate(complete):
            for q, qv in (r.get("fit_results") or {}).items():
                if not isinstance(qv, dict):
                    continue
                for m, raw in qv.items():
                    if m == "success" or not isinstance(raw, (int, float)):
                        continue
                    val = raw if not (isinstance(raw, float) and not math.isfinite(raw)) else None
                    want.setdefault((q, m), [None] * len(complete))[n] = ("set", val)
        shown = {(s_["q"], s_["m"]): s_["v"] for s_ in js["series"]}
        for key, arr in want.items():
            vals = [x[1] if x else None for x in arr]
            if not any(x is not None for x in vals):
                if key in shown and all(x is None for x in shown[key]):
                    T.add("datasets_trends", "explained", "all_nonfinite_series_kept_as_empty_chart", exp=exp,
                          q=key[0], m=key[1],
                          why="dataset.trend_point: a metric whose fit failed in every run keeps its (empty) chart")
                elif key in shown:
                    T.add("datasets_trends", "defect", "all_null_series_shown_with_values", exp=exp, q=key[0], m=key[1])
                else:
                    T.add("datasets_trends", "explained", "all_nonfinite_series_not_drawn", exp=exp, q=key[0], m=key[1],
                          why="every value of the metric is non-finite or absent; the payload drops all-null series")
                continue
            sv = shown.get(key)
            if sv is None:
                T.add("datasets_trends", "defect", "series_missing", exp=exp, q=key[0], m=key[1])
                continue
            counts["ds_values"] += len(vals)
            bad = [(complete[i]["folder_id"], vals[i], sv[i]) for i in range(min(len(vals), len(sv)))
                   if not ((vals[i] is None and sv[i] is None) or
                           (vals[i] is not None and sv[i] is not None and same(vals[i], sv[i])
                            and type(vals[i]) is type(sv[i]) or (is_num(vals[i]) and is_num(sv[i]) and vals[i] == sv[i]
                                                                and not isinstance(vals[i], bool))))]
            if bad or len(vals) != len(sv):
                T.add("datasets_trends", "defect", "series_values", exp=exp, q=key[0], m=key[1], bad=bad[:5],
                      n_bad=len(bad), len_shown=len(sv), len_oracle=len(vals))
            else:
                T.add("datasets_trends", "match", "series_values", exp=exp)
        for key in shown:
            if key not in want:
                T.add("datasets_trends", "defect", "series_extra", exp=exp, q=key[0], m=key[1])


def report_check(orc, T, rep, cur, counts):
    """The printable report: legend 'newest recorded value' == the live Trends' last point; the window
    crop; the caption."""
    live_last = {}
    if cur:
        for part in ("a", "b"):
            for ch in cur[part]["charts"] or []:
                for s in ch["series"]:
                    if s["points"]:
                        live_last[(ch["metric"], s["entity"])] = s["points"][-1][1]
    for w, v in rep.items():
        h = v["html"]
        if v["status"] != 200:
            T.add("report", "defect", "report_status", window=w, status=v["status"])
            continue
        counts["report_windows"] += 1
        if "400" in re.sub(r"<[^>]+>", " ", h):
            T.add("report", "defect", "stale_400_caption", window=w,
                  text=re.search(r"[^.<>]*400[^.<>]*", re.sub(r"<[^>]+>", " ", h)).group(0)[:160])
        if w != "all":
            continue
        for fig in re.findall(r'<figure class="rep-fig" data-rep-metric="([^"]+)">(.*?)</figure>', h, re.S):
            metric, body = fig
            for name, val in re.findall(r'<span class="rc-key"><svg.*?</svg>([^<]+?)\s*<b>([^<]*)</b></span>', body, re.S):
                counts["report_legend_entries"] += 1
                live = live_last.get((metric, name.strip()))
                got = parse_qty(_html.unescape(val))
                if live is None:
                    T.add("report", "defect", "legend_entity_not_on_live_page", metric=metric, entity=name, shown=val)
                elif got is None:
                    T.add("report", "explained", "legend_value_unparsed", metric=metric, entity=name, shown=val)
                elif close_display(live, got):
                    T.add("report", "match", "legend_newest_value", metric=metric)
                else:
                    T.add("report", "defect", "legend_newest_value_differs", metric=metric, entity=name,
                          shown=val, live_last=live)


_PREFIX = {"G": 1e9, "M": 1e6, "k": 1e3, "m": 1e-3, "\u00b5": 1e-6, "\u03bc": 1e-6, "u": 1e-6, "n": 1e-9, "p": 1e-12}


def parse_qty(text):
    """'5.07871 GHz' -> (5.07871e9, digits) ; '98.1 %' -> (0.981, ...) ; '0.0154' -> (0.0154, ...)."""
    m = re.search(r"([-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?)\s*(\S*)", text.strip())
    if not m:
        return None
    num = m.group(1).replace(",", "")
    unit = m.group(2)
    mant = num.split("e")[0].split("E")[0]
    decimals = len(mant.split(".")[1]) if "." in mant else 0
    exp = int(num.split("e")[1]) if "e" in num else (int(num.split("E")[1]) if "E" in num else 0)
    scale = 1.0
    if unit.startswith("%"):
        scale = 0.01
    elif len(unit) >= 2 and unit[0] in _PREFIX:
        scale = _PREFIX[unit[0]]
    v = float(num) * scale
    half = 0.5 * 10 ** (exp - decimals) * scale
    return v, half


def close_display(live, got):
    v, half = got
    return live is not None and abs(live - v) <= half * 1.0001 + 1e-15

if __name__ == "__main__":
    main()
