"""Stop-loss: three tiers, because a counter is not a stop-loss (docs/78 D-8).

A budget says *how much you spent*. A stop-loss says *you are not learning, or
you are making it worse*. Only the first existed.

* **Tier 1 — budget.** Per-target retries (already in `Step.retry_max`), the
  plan **step cap** and the **wall clock** (both absent until now), LLM calls.
  Scoped per target where it matters: a hopeless q3 must not eat the night.
* **Tier 2 — no progress.** (a) the deterministic metric trend, free: the gates
  already extract `contrast`, `r2`, `peak_snr`, spectral presence every attempt,
  so if none improves over K rungs we are not learning. (b) the pairwise vision
  comparison (`Auditor.compare`, shipped in P3b). Both flat ⇒ stop. This is the
  only thing that catches **oscillation** — widen → too coarse → refine → out of
  window → widen — where every individual step is justified and a counter never
  fires.
* **Tier 3 — harm.** Seeds accumulating unconsumed, drive power at the ceiling,
  and the same target escalating upstream twice (⇒ the problem is not where we
  think it is).

**On stop: revert this target, continue to the next**, and collect it into the
morning report. Never hand a human a half-adapted chip — failure must fail
cleanly. Only a common cause halts the whole plan.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

# metrics whose IMPROVEMENT means we are learning. All "bigger is better" —
# the gates already compute them every attempt, so tier 2a is free.
PROGRESS_KEYS = ("peak_snr", "dip_snr", "r2", "contrast",
                 "multipulse_fit_quality", "ridge_amp_snr", "ridge_coverage",
                 "ridge_r2")
# a metric has to move by more than this to count as progress; below it we are
# reading noise and calling it learning
_REL_EPS = 0.05
DEFAULT_NO_PROGRESS_ROUNDS = 3


@dataclass
class Budget:
    """Tier 1. ``None`` = unlimited, which is honest: an unset wall clock is
    not a zero wall clock."""
    max_steps: int | None = None          # plan-level executed-step ceiling
    wall_clock_s: float | None = None     # plan-level deadline
    max_retries_per_target: int | None = None
    started_at: float = field(default_factory=time.monotonic)
    steps_run: int = 0
    retries: dict[str, int] = field(default_factory=dict)

    def note_step(self) -> None:
        self.steps_run += 1

    def note_retry(self, target: str) -> None:
        self.retries[target] = self.retries.get(target, 0) + 1

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def plan_exhausted(self) -> str | None:
        """A reason string when the PLAN must stop, else None."""
        if self.max_steps is not None and self.steps_run >= self.max_steps:
            return (f"plan step cap reached ({self.steps_run}/{self.max_steps})")
        if self.wall_clock_s is not None and self.elapsed_s() >= self.wall_clock_s:
            return (f"wall clock reached ({self.elapsed_s():.0f}s / "
                    f"{self.wall_clock_s:.0f}s)")
        return None

    def target_exhausted(self, target: str) -> str | None:
        if self.max_retries_per_target is None:
            return None
        n = self.retries.get(target, 0)
        if n >= self.max_retries_per_target:
            return (f"retry budget for {target} spent "
                    f"({n}/{self.max_retries_per_target})")
        return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) else None


def metric_trend(history: list[dict]) -> dict:
    """Tier 2a. ``history`` = the fit entry of each attempt, oldest first.

    Returns ``{"improving": bool, "rounds": n, "best": {key: value},
    "moved": [keys]}``. "Improving" means at least ONE progress metric beat its
    previous best by more than the noise floor — a metric that wobbles inside
    ±5% is not learning, and treating it as such is how a loop runs all night
    feeling productive.
    """
    best: dict[str, float] = {}
    moved: list[str] = []
    improving = False
    for entry in history:
        for k in PROGRESS_KEYS:
            v = _num((entry or {}).get(k))
            if v is None:
                continue
            prev = best.get(k)
            if prev is None:
                best[k] = v
                continue
            if v > prev * (1 + _REL_EPS) if prev > 0 else v > prev:
                best[k] = v
                if k not in moved:
                    moved.append(k)
    if history and len(history) >= 2:
        last, prev_best = history[-1] or {}, {}
        for entry in history[:-1]:
            for k in PROGRESS_KEYS:
                v = _num((entry or {}).get(k))
                if v is not None:
                    prev_best[k] = max(prev_best.get(k, v), v)
        for k in PROGRESS_KEYS:
            v, p = _num(last.get(k)), prev_best.get(k)
            if v is None or p is None:
                continue
            if (v > p * (1 + _REL_EPS)) if p > 0 else (v > p):
                improving = True
    return {"improving": improving, "rounds": len(history), "best": best,
            "moved": moved}


def no_progress(history: list[dict], comparisons: list[str] | None = None, *,
                rounds: int = DEFAULT_NO_PROGRESS_ROUNDS) -> str | None:
    """Tier 2. A reason string when BOTH signals are flat, else None.

    Both, not either: the metric trend can miss a change no number captures,
    and a vision comparison can miss a change no picture shows. Stopping on one
    alone would end runs that are genuinely improving on the other axis.
    """
    if len(history) < rounds:
        return None
    recent = history[-rounds:]
    if metric_trend(recent)["improving"]:
        return None
    if comparisons:
        if any(c == "better" for c in comparisons[-rounds:]):
            return None
        if all(c == "same" for c in comparisons[-rounds:]):
            return (f"no progress: {rounds} attempts with no metric gain and "
                    f"no visible improvement")
        if any(c == "worse" for c in comparisons[-rounds:]):
            return (f"getting worse: {rounds} attempts with no metric gain and "
                    f"the figure degrading")
        return None
    return (f"no progress: {rounds} attempts with no metric gain "
            f"(no vision comparison available)")


def harm(*, unconsumed_seeds: int = 0, drive_at_ceiling: bool = False,
         upstream_escalations: int = 0) -> str | None:
    """Tier 3. The signals that mean the loop is doing damage, not work."""
    if upstream_escalations >= 2:
        return ("escalated upstream twice — the problem is not where we think "
                "it is; stopping this target for a human")
    if unconsumed_seeds >= 3:
        return (f"{unconsumed_seeds} scan seeds written and never consumed — "
                f"the window is being moved without ever finding the feature")
    if drive_at_ceiling:
        return "drive power is at the constraint ceiling — no headroom left"
    return None


def should_stop(*, history: list[dict] | None = None,
                comparisons: list[str] | None = None,
                budget: Budget | None = None, target: str | None = None,
                rounds: int = DEFAULT_NO_PROGRESS_ROUNDS,
                **harm_kw) -> dict | None:
    """THE one entry point. Returns ``{"tier", "reason"}`` or None.

    Order is deliberate — harm first (it means we are making things worse),
    then budget (a hard fact), then no-progress (a judgement).
    """
    why = harm(**harm_kw)
    if why:
        return {"tier": 3, "reason": why}
    if budget is not None:
        why = budget.plan_exhausted()
        if why:
            return {"tier": 1, "reason": why, "scope": "plan"}
        if target is not None:
            why = budget.target_exhausted(target)
            if why:
                return {"tier": 1, "reason": why, "scope": "target"}
    why = no_progress(history or [], comparisons, rounds=rounds)
    if why:
        return {"tier": 2, "reason": why}
    return None
