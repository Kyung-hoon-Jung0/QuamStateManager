"""Cross-experiment consistency — the review a night's work actually needs
(docs/78 P6c, goal §1.1 #3).

Every gate so far judges ONE run against itself. But the failure that survives
all of them is the pair that is each internally consistent and mutually
impossible: node 06 puts a qubit's flux sweet spot at one bias and node 09 puts
it somewhere else entirely, each with a clean fit and a convincing figure. No
per-run gate can see that, and neither can the judge — it is only visible when
the results are laid side by side.

**What to compare is corpus-derived, not invented.** Harvesting the real
archives shows which quantities more than one family actually claims; those, and
only those, are the cross-checks. The list is then CURATED, because "the same
key name" is not "the same quantity": `frequency_shift` appears in four
families, but node 06's is a qubit-flux response and node 07's is a
coupler-flux response — comparing them would manufacture disagreement.

**The tolerance is a physical scale the runs themselves report**, never a
constant typed here: two measurements of one resonance agree when they agree to
within its linewidth, and two flux sweet spots agree when they agree to within
the sweep step that found them. A hardcoded Hz would be the Clause-B mistake in
numeric form — right for the chip it was written on, wrong for the next.

This module only REPORTS. Reconciling means telling a human "these two cannot
both be true"; deciding which to keep is not something a consistency check has
the standing to do.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CrossCheck:
    """One quantity that more than one family claims for the same target."""
    quantity: str
    sources: tuple[tuple[str, str], ...]   # (family key, fit key)
    scale_keys: tuple[str, ...] = ()       # fit keys giving the physical scale
    scale_factor: float = 2.0              # agree within N x that scale
    fallback_rel: float = 0.001            # when no scale is reported
    why: str = ""


# CORPUS-CALIBRATED (docs/78 §20). The candidate list came from harvesting which
# keys ≥2 families claim; the surviving list, and the factor, came from
# MEASURING how much the lab's own gate-passing runs disagree. Adjacent runs
# (≤5 run ids apart, i.e. the same chip state), fits that pass OUR gates, per
# pair, in units of the reported linewidth:
#
#   pair      n    p50    p90    p99   ⇒ verdict
#   03-05    47  0.108  0.368    4.6   keep
#   03-06    86  0.072  0.791   13.2   keep  (06 reports at the sweet spot)
#   08-08b   84  0.120  0.561   57.1   DROP — see below
#   08-09    62  0.288   46.7     97   DROP
#   06f-09f   3  0.019   1.47   1.47   DROP — 3 samples cannot calibrate anything
#
# The first design used 2.0 for everything and produced a **37.5%
# false-contradiction rate** on real accepted pairs. That is the P2 lesson for
# the third time: a threshold written from physical intuition, however sound the
# reasoning, is a hypothesis until the lab's data has answered it.
CROSS_CHECKS: tuple[CrossCheck, ...] = (
    CrossCheck(
        quantity="resonator frequency",
        sources=(("resonator_spectroscopy", "frequency"),
                 ("resonator_spectroscopy_vs_power", "resonator_frequency"),
                 ("resonator_spectroscopy_vs_flux", "resonator_frequency")),
        scale_keys=("fwhm",), scale_factor=20.0, fallback_rel=0.01,
        why="three nodes measure this resonator. 20 linewidths is far wider "
            "than these nodes ever disagree on real gate-passing data (p99 = "
            "13.2) and far narrower than the spacing between neighbouring "
            "resonators — so it catches 'one of them fitted the WRONG "
            "resonator', which is the failure each run hides on its own"),
)

# DROPPED, and the measurement is the reason — recording it so nobody re-adds
# them from the same intuition that put them here:
#
#   qubit frequency (08 / 08b / 09) — usable only at 86-146 linewidths
#     (340-580 MHz on a 4 MHz line), which is WIDER than the spacing to a
#     neighbouring qubit line. A check that cannot catch the error it exists
#     for is not a loose check, it is not a check.
#     08-vs-09 additionally compares the frequency at the CURRENT bias with the
#     frequency at the SWEET SPOT — different quantities by construction, which
#     is the whole purpose of node 09.
#
#   flux sweet spot (06 / 09) — the one this module was designed around, the
#     "each internally consistent and mutually impossible" pair. Only THREE
#     gate-passing pairs exist in the entire corpus. Three samples cannot
#     calibrate a threshold, and shipping one anyway would be exactly the
#     invented number this module refuses elsewhere. It returns when there is
#     data (docs/78 §20.3).
#
#   frequency_shift — 06 is qubit flux, 07 is coupler flux: different knobs.
#   optimal_power   — 05 and 08b optimise different lines (readout vs drive).


@dataclass
class Finding:
    quantity: str
    target: str
    values: dict                       # family -> value
    spread: float
    tolerance: float
    scale_from: str | None
    why: str
    def as_dict(self) -> dict:         # noqa: E301
        return {"quantity": self.quantity, "target": self.target,
                "values": self.values, "spread": self.spread,
                "tolerance": self.tolerance, "scale_from": self.scale_from,
                "why": self.why}


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    compared: int = 0                  # how many (quantity, target) pairs
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"findings": [f.as_dict() for f in self.findings],
                "compared": self.compared, "skipped": list(self.skipped),
                "ok": not self.findings}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) else None


def _scale(check: CrossCheck, entries: dict) -> tuple[float | None, str | None]:
    """The physical scale these runs themselves reported, if any."""
    for key in check.scale_keys:
        vals = [_num(e.get(key)) for e in entries.values()]
        vals = [v for v in vals if v is not None and v > 0]
        if vals:
            return min(vals), key
    return None, None


def _same_context_only(present: dict, target: str, contexts: dict
                       ) -> tuple[dict, list[str]]:
    """Keep the largest set of mutually-comparable values; report the rest.

    Grouping by context key rather than anchoring on one family is deliberate:
    if a plan re-ran three families under a new gate revision and one under the
    old, the majority is the one worth comparing, and the minority is named
    rather than quietly dropped. Ties break on the sorted family name so the
    report is reproducible.
    """
    from quam_state_manager.core.autofit import verification

    groups: dict[tuple, list[str]] = {}
    unknown: list[str] = []
    for fam in sorted(present):
        ctx = verification.from_dict(contexts.get((fam, target)))
        if ctx is None or ctx.missing():
            unknown.append(fam)
            continue
        groups.setdefault(ctx.key(), []).append(fam)
    if not groups:
        return {}, [f"{target}: no verification context on any value — "
                    f"not compared ({', '.join(unknown)})"]
    keep = sorted(groups.values(), key=lambda g: (-len(g), g[0]))[0]
    dropped = [f for f in present if f not in keep]
    notes = []
    if dropped:
        notes.append(
            f"{target}: {', '.join(sorted(dropped))} produced under a different "
            f"verification context than {', '.join(keep)} — not compared "
            f"(docs/78 D-13: a verdict is only valid inside its own context)")
    return {f: present[f] for f in keep}, notes


def reconcile(results: dict, contexts: dict | None = None) -> Report:
    """``results`` = ``{(family, target): fit_entry}`` from a whole plan.

    Returns every pair of families that claim the same physical quantity for
    the same target and disagree by more than the scale those runs reported.
    Reports only — deciding which one to keep is a human's call.

    ``contexts`` = ``{(family, target): verification context}`` (docs/78 §17
    B3). When given, two values obtained under DIFFERENT verification contexts
    are not compared: a disagreement between a value read by one gate revision
    and one read by another is not a contradiction about the chip, it is a
    category error — and reporting it as physics is how a review loses its
    authority. The skip is recorded with its reason, never silent. Omitting
    ``contexts`` keeps the previous behaviour byte-identically.
    """
    by_target: dict[str, dict[str, dict]] = {}
    for (fam, target), entry in (results or {}).items():
        by_target.setdefault(target, {})[fam] = entry or {}

    rep = Report()
    for target, fams in sorted(by_target.items()):
        for check in CROSS_CHECKS:
            present = {}
            for fam, key in check.sources:
                v = _num((fams.get(fam) or {}).get(key))
                if v is not None:
                    present[fam] = v
            if len(present) < 2:
                continue                       # nothing to cross-check
            if contexts:
                present, dropped = _same_context_only(present, target, contexts)
                rep.skipped.extend(dropped)
                if len(present) < 2:
                    continue
            rep.compared += 1
            entries = {f: fams[f] for f in present}
            scale, scale_from = _scale(check, entries)
            if scale is not None:
                tol = scale * check.scale_factor
            else:
                ref = max(abs(v) for v in present.values()) or 1.0
                tol = ref * check.fallback_rel
                rep.skipped.append(
                    f"{check.quantity}/{target}: no {'/'.join(check.scale_keys)} "
                    f"reported — fell back to a relative tolerance")
            spread = max(present.values()) - min(present.values())
            if spread > tol:
                rep.findings.append(Finding(
                    quantity=check.quantity, target=target, values=present,
                    spread=spread, tolerance=tol, scale_from=scale_from,
                    why=check.why))
    return rep


def summarize(rep: Report) -> str:
    """One-screen text for the morning report."""
    if not rep.findings:
        return (f"cross-experiment review: {rep.compared} comparison(s), "
                f"no contradictions")
    lines = [f"cross-experiment review: {len(rep.findings)} contradiction(s) "
             f"in {rep.compared} comparison(s)"]
    for f in rep.findings:
        vals = ", ".join(f"{k}={v:.6g}" for k, v in sorted(f.values.items()))
        lines.append(f"  {f.target} — {f.quantity}: {vals} "
                     f"(spread {f.spread:.4g} > {f.tolerance:.4g}"
                     + (f", from {f.scale_from}" if f.scale_from else "") + ")")
        lines.append(f"      {f.why}")
    return "\n".join(lines)
