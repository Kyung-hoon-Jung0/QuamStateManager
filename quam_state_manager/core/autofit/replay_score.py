"""Offline end-to-end scoring: would the agent have done better? (docs/78 P8)

Replay a real session, give the agent the first *k* runs, ask what it would do
next, and compare against what the human actually did.

**The honest metric is not "the agent agrees with the human."** A human who
burned three drive-power attempts and a day before refining the step is the
BASELINE, not the ground truth — agreeing with that is not success. The metric
is **"reaches the same conclusion in fewer runs"**, so a proposal that skips a
dead end scores BETTER than one that reproduces it (docs/56 §6V case C is the
reference: three wasted attempts before the operator densified the grid).

This module is the harness only: it builds decision points out of a real
session and scores answers. It never calls a model — the caller supplies the
proposals, which is what lets the same case set run against a subagent today
and the real API later, and be compared.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class DecisionPoint:
    """One "what would you do next?" question carved out of a real session."""
    session: str
    index: int                          # how many runs the agent may see
    family: str
    target: str
    seen: list[dict] = field(default_factory=list)     # the first k runs
    future: list[dict] = field(default_factory=list)   # the operator's rest
    human_next: dict | None = None      # what the operator actually ran next
    human_runs_to_success: int | None = None   # from HERE to their first pass
    resolved_at: int | None = None      # index of the human's first pass

    def as_dict(self) -> dict:
        return {"session": self.session, "index": self.index,
                "family": self.family, "target": self.target,
                "n_seen": len(self.seen),
                "human_next": self.human_next,
                "human_runs_to_success": self.human_runs_to_success}


def _ok(run: dict, target: str) -> bool:
    return ((run.get("outcomes") or {}).get(target)) == "successful"


def build_points(session: str, runs: list[dict], target: str, *,
                 family: str | None = None,
                 min_seen: int = 1) -> list[DecisionPoint]:
    """Carve decision points out of ONE target's run sequence, oldest first.

    A point is only interesting BEFORE the human succeeded: after that there is
    no decision left to score. `human_runs_to_success` counts from the point
    forward, so a proposal is measured against how much further the operator
    still had to go.
    """
    first_ok = next((i for i, r in enumerate(runs) if _ok(r, target)), None)
    if first_ok is None or first_ok < min_seen:
        return []                       # never resolved, or resolved instantly
    out = []
    for k in range(min_seen, first_ok + 1):
        out.append(DecisionPoint(
            session=session, index=k,
            family=family or str(runs[k - 1].get("family") or ""),
            target=target, seen=runs[:k], future=runs[k:],
            human_next=(runs[k].get("parameters") if k < len(runs) else None),
            human_runs_to_success=first_ok - k + 1,
            resolved_at=first_ok))
    return out


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) else None


def compare_to_human(point: DecisionPoint, proposal: dict | None) -> dict:
    """Score ONE proposal against what the operator did next.

    Returns ``{"changed": [...], "same_direction": bool|None,
    "matches_human": bool|None, "note": str}``. Direction, not magnitude: the
    interesting question is whether the agent turned the same knob the same
    way, since the exact number is the node's business.
    """
    human = point.human_next or {}
    prop = proposal or {}
    keys = sorted(set(prop) & set(human))
    changed, agree, disagree = [], [], []
    prev = (point.seen[-1].get("parameters") if point.seen else {}) or {}
    for k in keys:
        p, h, b = _num(prop.get(k)), _num(human.get(k)), _num(prev.get(k))
        if p is None or h is None or b is None:
            continue
        dp, dh = p - b, h - b
        if dp == 0 or dh == 0:
            # only knobs BOTH parties moved are comparable. Turning one the
            # operator left alone is not "wrong" — it may be the shortcut, and
            # scoring it as disagreement would punish exactly the behaviour
            # this experiment exists to find.
            continue
        changed.append(k)
        (agree if (dp > 0) == (dh > 0) else disagree).append(k)
    if not changed:
        return {"changed": [], "same_direction": None, "matches_human": None,
                "note": "no comparable numeric knob moved by both"}
    return {"changed": changed, "same_direction": not disagree,
            "matches_human": bool(agree) and not disagree,
            "agree_on": agree, "disagree_on": disagree,
            "note": f"{len(agree)}/{len(changed)} knobs moved the same way"}


def runs_saved(point: DecisionPoint, proposal: dict | None,
               future: list[dict], *, rel_tol: float = 0.25) -> dict | None:
    """**The metric that IS computable offline** (docs/78 §20.4).

    Re-measuring a chip from an archive is impossible, so "did the agent's
    proposal work?" cannot be answered directly. But this can:

        the agent proposed at step k what the operator only reached at k+n
        ⇒ n runs saved.

    Pure archive arithmetic — no re-measurement, no hardware, no key. It scores
    exactly the thing that matters (fewer runs to the same conclusion) instead
    of agreement, which is the baseline's opinion of itself.

    ``future`` = the runs AFTER this point, oldest first (i.e. the operator's
    remaining path). Returns ``None`` when the proposal never matches anything
    the operator went on to do — which is not a failure, just unscoreable this
    way: it may have been better or nonsense, and we say so rather than guess.
    """
    prop = {k: _num(v) for k, v in (proposal or {}).items()}
    prop = {k: v for k, v in prop.items() if v is not None}
    if not prop:
        return None
    for n, run in enumerate(future, start=1):
        params = run.get("parameters") or {}
        shared = [k for k in prop if _num(params.get(k)) is not None]
        if not shared:
            continue
        if all(_close(prop[k], _num(params[k]), rel_tol) for k in shared):
            return {"matched_at": n, "runs_saved": n - 1,
                    "on": sorted(shared),
                    "note": (f"the operator reached these values {n} run(s) "
                             f"later" if n > 1 else
                             "the operator did this next — no time saved")}
    return None


def _close(a: float, b: float, rel: float) -> bool:
    """Same DECISION, not the same number: a proposal within a quarter of the
    operator's value is the same move. Demanding equality would score a correct
    call as a miss because the agent said 78 where the human typed 80."""
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= rel


def score(points: list[DecisionPoint], proposals: dict,
          outcomes: dict | None = None) -> dict:
    """Aggregate. ``proposals`` = ``{(session, index): params}``.

    ``outcomes`` optionally gives the MEASURED result of following the agent's
    proposal (``{(session, index): runs_to_success}``) — only then can the real
    metric be computed. Without it the report says so rather than substituting
    agreement, which measures the wrong thing.
    """
    rows, faster, slower, same = [], 0, 0, 0
    saved_total, saved_n = 0, 0
    for p in points:
        prop = proposals.get((p.session, p.index))
        cmp_ = compare_to_human(p, prop)
        got = (outcomes or {}).get((p.session, p.index))
        # the offline metric: did the agent propose early what the operator
        # only reached later? `p.seen` is everything up to k, so the operator's
        # remaining path is what the session held after it.
        saved = runs_saved(p, prop, p.future) if p.future else None
        if saved:
            saved_total += saved["runs_saved"]
            saved_n += 1
        row = {**p.as_dict(), "proposed": prop, "comparison": cmp_,
               "agent_runs_to_success": got, "early_move": saved}
        if got is not None and p.human_runs_to_success is not None:
            if got < p.human_runs_to_success:
                faster += 1
            elif got > p.human_runs_to_success:
                slower += 1
            else:
                same += 1
        rows.append(row)
    measured = faster + slower + same
    return {
        "n_points": len(points),
        "n_answered": sum(1 for r in rows if r["proposed"]),
        "measured": measured,
        "faster": faster, "same": same, "slower": slower,
        # the offline metric — computable from the archive alone
        "early_moves": saved_n,
        "runs_saved": saved_total,
        "runs_saved_mean": (round(saved_total / saved_n, 2) if saved_n else None),
        "agreement_rate": _rate([r["comparison"].get("matches_human")
                                 for r in rows]),
        "rows": rows,
        "caveat": ("agreement with the operator is NOT the metric — a human "
                   "who burned three attempts is the baseline, not the ground "
                   "truth. `runs_saved` is the offline stand-in: what the "
                   "agent proposed at step k that the operator only reached "
                   "later."
                   if not measured else
                   "measured against runs-to-success, which is the metric"),
    }


def _rate(vals) -> str:
    got = [v for v in vals if v is not None]
    if not got:
        return "n/a (0)"
    return f"{100 * sum(1 for v in got if v) / len(got):.0f}% ({len(got)})"
