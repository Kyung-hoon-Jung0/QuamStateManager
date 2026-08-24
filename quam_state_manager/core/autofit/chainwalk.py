"""The chain conductor (docs/140; measured first as docs/139's driver).

One qubit, one pile of archived runs, no order given but the clock: this
module walks the WHOLE bring-up chain — resonator spectroscopy, its power
and flux companions, the qubit-spectroscopy joint pair, qubit-vs-flux — to
a set of chain-level conclusions with provenance, using only the walkers SM
already ships. It adds exactly the two things docs/139 measured as missing:

* **cross-family recency** — two families that write the same physical
  quantity (both flux maps write a parking offset; three windows see the
  resonator) are concluded by the LATEST write on the clock, never by
  family rank and never by averaging. On the real pilot day this is what
  made the parking offset land exactly on the operator's value while the
  earlier narrow-window read was superseded.
* **the parking edge** — a qubit frequency is only defined AT a flux
  point, so a parking write that postdates every qubit-frequency read
  makes that frequency STALE: the chain does not vouch it and instead
  directs a 1Q re-measurement at the new flux point. The real operator did
  exactly this (overnight, outside the docs/139 replay window), and the
  contested q2 frequency was this edge showing up as physics.

Ordering truth: run counters reset mid-day, so (date, HHMMSS) from the
folder name is the only valid order — never the run number.

Doctrine unchanged: every number comes from a node's own fitter through a
shipped walker; the conductor only chooses WHICH vouched number concludes,
and says why. Future-blindness holds inside each family walk; the
conductor itself is a C-layer that runs after the evidence exists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from quam_state_manager.core.autofit import knowledge
from quam_state_manager.core.autofit import pathreplay as PR
from quam_state_manager.core.autofit import replaybench as RB

# chain scope: the families between "nothing" and a parked flux point.
# rabi/ramsey/zz are the next chapter (user-deferred packs).
_RB_SOLO = ("resonator_spectroscopy", "resonator_spectroscopy_vs_flux",
            "qubit_spectroscopy_vs_flux")
_POWER = "resonator_spectroscopy_vs_power"
_JOINT = set(RB.JOINT_QUBIT_SPEC)   # the qubit-spec pair is ONE workflow

_TIME_RE = re.compile(r"_(\d{6})$")


def order_key(view: Any) -> tuple:
    """(date, HHMMSS, run_no) — the clock, never the run counter."""
    m = _TIME_RE.search(view.run_id or "")
    return (getattr(view, "date", "") or "",
            int(m.group(1)) if m else 0,
            getattr(view, "run_no", -1))


def stage_views(views: list) -> dict[str, list]:
    """Partition one target's runs into time-ordered family streams.

    The joint pair merges into one stream (key ``"qubit_joint"``); families
    outside the chain scope are returned under ``"_out_of_scope"`` so a
    caller can SAY what it skipped rather than skip silently.
    """
    out: dict[str, list] = {}
    for v in sorted(views, key=order_key):
        fam = RB.pack_family_for(v.run_id)
        if fam in _JOINT:
            key = "qubit_joint"
        elif fam == _POWER or fam in _RB_SOLO:
            key = fam
        else:
            key = "_out_of_scope"
        out.setdefault(key, []).append(v)
    return out


@dataclass
class Write:
    """One vouched value with its provenance on the clock."""
    value: float
    stage: str
    run_id: str
    key: tuple          # order_key of the run that produced it


@dataclass
class ChainResult:
    target: str
    stage_results: dict            # stage -> walker Result (family walks)
    conclusions: dict              # quantity -> Write
    candidates: dict               # quantity -> [Write] (all, time-ordered)
    directives: list               # honest next steps, in English sentences
    cross_notes: list
    skipped: list                  # out-of-scope run ids, named not hidden


def _rb_writes(stage: str, res: Any, runs_by_id: dict) -> dict[str, Write]:
    out = {}
    for fname, val in (res.final_state or {}).items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        reads = (getattr(res, "value_reads", None) or {}).get(fname) or []
        rid = reads[-1][2] if reads else (res.first_value_at or "")
        v = runs_by_id.get(rid)
        out[fname] = Write(float(val), stage, rid,
                           order_key(v) if v is not None else ("", 0, -1))
    return out


def _pr_writes(stage: str, res: Any, runs_by_id: dict) -> dict[str, Write]:
    last: dict[str, tuple] = {}
    for st in getattr(res, "steps", []) or []:
        for k, v in (getattr(st, "adopted", None) or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                last[k] = (float(v), st.run_id)
    out = {}
    for k, (val, rid) in last.items():
        if k in (res.final_state or {}):
            v = runs_by_id.get(rid)
            out[k] = Write(val, stage, rid,
                           order_key(v) if v is not None else ("", 0, -1))
    return out


# which (stage, field) writes speak for which physical quantity
_QUANTITIES = {
    "resonator_frequency": (("resonator_spectroscopy", "frequency"),
                            (_POWER, "resonator_frequency"),
                            ("resonator_spectroscopy_vs_flux",
                             "resonator_frequency")),
    "qubit_frequency": (("qubit_joint", "frequency"),
                        ("qubit_spectroscopy_vs_flux", "qubit_frequency")),
    "flux_idle_offset": (("resonator_spectroscopy_vs_flux", "idle_offset"),
                         ("qubit_spectroscopy_vs_flux", "idle_offset")),
    "readout_optimal_power": ((_POWER, "optimal_power"),),
    "drive_optimal_power": (("qubit_joint", "optimal_power"),),
}


def conclude(writes_by_stage: dict[str, dict],
             directives: list) -> tuple[dict, dict]:
    """Cross-family recency + the parking edge, over already-vouched writes.

    Pure by design so it is testable without an archive: input is
    {stage: {field: Write}}, output (conclusions, candidates). Mutates
    ``directives`` with the honest next steps.
    """
    candidates: dict[str, list] = {}
    for q, sources in _QUANTITIES.items():
        cs = [writes_by_stage[s][f] for s, f in sources
              if f in writes_by_stage.get(s, {})]
        if cs:
            candidates[q] = sorted(cs, key=lambda w: w.key)
    conclusions = {q: cs[-1] for q, cs in candidates.items()}

    park = conclusions.get("flux_idle_offset")
    qf = conclusions.get("qubit_frequency")
    if park is not None and (qf is None or qf.key < park.key):
        if qf is not None:
            directives.append(
                f"the parking write ({park.stage} {park.run_id}) postdates "
                f"every qubit-frequency read — the held {qf.value:.6g} from "
                f"{qf.run_id} was measured at a DIFFERENT flux point and is "
                f"not vouched; re-measure 1Q at the parked offset")
            del conclusions["qubit_frequency"]
        else:
            directives.append(
                "a flux point is parked but no qubit-frequency read is "
                "vouched — measure 1Q at the parked offset")
    elif qf is None:
        directives.append("qubit frequency unresolved — re-measure")
    if park is None:
        directives.append("no flux point established — the flux stage did "
                          "not vouch an offset; the chain's endpoint is "
                          "still open")
    return conclusions, candidates


class _PowerShim:
    """cross_close's duck-typed power partner (its walk is pathreplay)."""

    family = _POWER

    def __init__(self, final_state: dict):
        self.final_state = dict(final_state or {})
        self.closure_notes: list = []


def walk_chain(views: list, target: str, *,
               profile: dict | None = None,
               cross_doc: dict | None = None,
               session_id: str = "chain") -> ChainResult:
    """Walk one target's chain runs through the shipped walkers and conclude.

    ``views`` may hold every run of the day — runs not measuring ``target``
    and families outside the chain scope are filtered here (skips named).
    """
    mine = [v for v in views if target in getattr(v, "outcomes", {})]
    streams = stage_views(mine)
    skipped = [v.run_id for v in streams.pop("_out_of_scope", [])]
    runs_by_id = {v.run_id: v for v in mine}

    stage_results: dict[str, Any] = {}
    writes_by_stage: dict[str, dict] = {}
    cross_inputs: list = []
    for stage, vs in streams.items():
        sess = PR.Session(f"{session_id}__{stage}", vs)
        if stage == _POWER:
            res = PR.replay(sess, target, pack=knowledge.load_family(_POWER))
            writes_by_stage[stage] = _pr_writes(stage, res, runs_by_id)
            cross_inputs.append(_PowerShim(res.final_state))
        else:
            fam = RB.pack_family_for if stage == "qubit_joint" else stage
            res = RB.replay(fam, sess, target, profile=profile)
            writes_by_stage[stage] = _rb_writes(stage, res, runs_by_id)
            cross_inputs.append(res)
        stage_results[stage] = res

    doc = cross_doc if cross_doc is not None else knowledge.load_cross()
    cross_notes = RB.cross_close(cross_inputs, doc=doc, profile=profile)
    # a cross rule may have un-vouched a write after extraction — re-filter
    for stage, res in stage_results.items():
        fs = getattr(res, "final_state", None) or {}
        writes_by_stage[stage] = {k: w for k, w in
                                  writes_by_stage[stage].items() if k in fs}

    directives: list = []
    conclusions, candidates = conclude(writes_by_stage, directives)
    return ChainResult(target=target, stage_results=stage_results,
                       conclusions=conclusions, candidates=candidates,
                       directives=directives, cross_notes=cross_notes,
                       skipped=skipped)
