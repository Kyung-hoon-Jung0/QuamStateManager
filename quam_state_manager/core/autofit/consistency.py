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


# Curated from the corpus: the keys ≥2 of the nine families genuinely claim for
# the SAME physical quantity on the SAME target.
CROSS_CHECKS: tuple[CrossCheck, ...] = (
    CrossCheck(
        quantity="resonator frequency",
        sources=(("resonator_spectroscopy", "frequency"),
                 ("resonator_spectroscopy_vs_power", "resonator_frequency"),
                 ("resonator_spectroscopy_vs_flux", "resonator_frequency")),
        scale_keys=("fwhm",), scale_factor=2.0,
        why="three nodes measure this resonator; two answers differing by more "
            "than a couple of linewidths means one of them fitted the wrong "
            "feature, and each looks fine on its own"),
    CrossCheck(
        quantity="qubit frequency",
        sources=(("qubit_spectroscopy", "frequency"),
                 ("qubit_spectroscopy_vs_power", "frequency"),
                 ("qubit_spectroscopy_vs_flux", "qubit_frequency")),
        scale_keys=("fwhm",), scale_factor=2.0,
        why="the power sweep and the flux map must agree with the plain "
            "spectroscopy at the same bias, or the chain built on it is wrong"),
    CrossCheck(
        quantity="flux sweet spot",
        sources=(("resonator_spectroscopy_vs_flux", "idle_offset"),
                 ("qubit_spectroscopy_vs_flux", "idle_offset")),
        scale_keys=("flux_step", "dv_phi0"), scale_factor=0.1,
        fallback_rel=0.05,
        why="the resonator map and the qubit map look at the SAME flux line; "
            "if they disagree on where the sweet spot is, one of them tracked "
            "the wrong ridge — the classic pair that is each self-consistent "
            "and mutually impossible"),
    # deliberately NOT cross-checked, and the reason is the point:
    #   frequency_shift  — 06 is qubit flux, 07 is coupler flux: different
    #                      knobs, so a difference is expected, not a fault
    #   optimal_power    — 05 and 08b optimise DIFFERENT lines (readout vs
    #                      drive); agreement would be the surprise
)


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


def reconcile(results: dict) -> Report:
    """``results`` = ``{(family, target): fit_entry}`` from a whole plan.

    Returns every pair of families that claim the same physical quantity for
    the same target and disagree by more than the scale those runs reported.
    Reports only — deciding which one to keep is a human's call.
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
