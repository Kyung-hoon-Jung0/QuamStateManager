"""Per-family exemplar pack for the vision judge (docs/78 P3a).

The judge is shown ONE figure and asked whether it carries a correct
experimental signature. What "correct" looks like differs per family, so the
knowledge lives in **versioned data files** — ``judge_packs/<version>/<family>.json``
— editable by a lab without touching code.

**Clause B is an acceptance criterion, not a style rule** (docs/47): a feature's
position inside the sweep window is an artefact of the window the experimenter
chose, not physics. An exemplar that says "the peak sits near the middle of the
window" actively teaches the judge something false, and it will transfer that
falsehood to a chip whose window is centred elsewhere. So the pack may describe
**shape and relative geometry only** — "the tallest narrow feature", "a sidelobe
under half its height and more than two linewidths away" — never an absolute or
fractional-of-axis position, and never a physical quantity with a unit.

:func:`lint_entry` enforces that mechanically. The shipped pack is pinned clean
by a test; a hand-edited pack that violates it does NOT silently reach the
judge — the offending strings are dropped and the violation is logged (the
never-silent doctrine: a bad exemplar is worse than a missing one).

Name-leak review (no customer/lab/chip names — the repo ships publicly) is
human: the lint can only catch the mechanical shapes (paths, run ids), because
a blocklist of real customer names would itself be the leak.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_VERSION = "v1"
_PACK_ROOT = Path(__file__).parent / "judge_packs"

# every string field the lint walks
_TEXT_FIELDS = ("axes", "notes")
_LIST_FIELDS = ("correct_signature", "abstain_when")

# --- Clause-B lint ---------------------------------------------------------
# a number carrying a physical unit: an absolute claim about someone else's chip
_UNIT_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:GHz|MHz|kHz|Hz|mV|uV|nV|V|dBm|dBc|dB|ns|us|ms|"
    r"µs|μs|A|mA)\b")
# a bare unit token used as a quantity ("in GHz", "volts of flux")
_BARE_UNIT_RE = re.compile(
    r"\b(?:GHz|MHz|kHz|dBm|volts?|hertz|nanoseconds?|microseconds?)\b", re.I)
# position-in-window claims (the actual Clause-B failure)
_POSITION_RE = re.compile(
    r"(?:middle|centre|center|midpoint)\s+of\s+the\s+"
    r"(?:window|axis|axes|range|span|sweep|plot|figure|scan)"
    r"|\b(?:left|right|upper|lower|top|bottom)\s+(?:third|half|quarter)\b"
    r"|\b\d+(?:\.\d+)?\s*%\s*(?:of|from|across|into)\b"
    r"|\bfractional(?:ly)?\s+(?:position|along|across)"
    r"|\bat\s+[xy]\s*=", re.I)
# The same violation with NO digits in it: sizing a feature against the swept
# window. "many times narrower than the swept frequency range" survives every
# number/unit check and is exactly the Clause-B error — the identical physics,
# zoomed in, becomes "a large fraction" and gets rejected. Sizing against the
# FEATURE ("a fraction of the notch's own width", "narrower than the visible
# hump") is legitimate and must pass, so the rule keys on the window noun.
_WINDOW = (r"(?:plotted\s+|swept\s+|whole\s+|entire\s+|full\s+|total\s+)?"
           r"(?:frequency\s+|flux\s+|bias\s+|amplitude\s+|drive\s+|power\s+|"
           r"time\s+)?(?:window|range|span|sweep|axis)")
_WINDOW_FRACTION_RE = re.compile(
    # NOT "part of the sweep": that is a COVERAGE statement ("the ridge breaks
    # up over part of the sweep"), which is legitimate and is itself one of the
    # family gates — only SIZE-against-the-window is the violation.
    r"\b(?:fraction|percent|proportion)\s+of\s+the\s+" + _WINDOW + r"\b"
    r"|\b(?:narrow|wide|broad|small|large|big|short|tall)(?:er)?\s+"
    r"(?:than|compared\s+(?:with|to))\s+the\s+" + _WINDOW + r"\b", re.I)
# mechanically checkable leakage: paths and archive run ids
_PATH_RE = re.compile(r"[A-Za-z]:[\\/]|/mnt/|\\\\|(?<!\w)#\d{1,5}_")

# PROSE Clause-B violations. The rules above catch units and explicit
# window-fractions; an adversarial re-read of the shipped v1 pack found the
# lint has ~0 recall on the ones written in words (docs/78 §22.1), which is the
# form an author actually reaches for. "near the centre" teaches the same
# falsehood as "at 50% of the sweep" — where a feature SITS is a property of
# the window the experimenter chose.
#
# Deliberately NOT caught: "the trace returns to baseline at both EDGES", which
# is a shape statement about the data, not a placement claim; and coverage
# ("spans the full width"), which is a legitimate family gate.
_PROSE_POSITION_RE = re.compile(
    r"\b(?:near|close\s+to|around|about|toward(?:s)?|at)\s+the\s+"
    r"(?:centre|center|middle|midpoint|left|right)\b"
    r"|\bin\s+the\s+(?:middle|centre|center|left|right)\s+"
    r"(?:third|half|quarter|part|portion|region)\b"
    r"|\b(?:sits?|lies?|falls?|appears?|located)\s+(?:near|at|in)\s+the\s+"
    r"(?:centre|center|middle|edge|left|right)\b"
    r"|\bat\s+the\s+(?:left|right)\s+edge\b", re.I)

# Implied absolute SCALE. "a broad feature" is broad compared to what? Unless
# the comparison is named, it can only mean "broad on the sweep the author
# happened to look at" — Clause B in an adjective. A comparison the pack DOES
# name (broader than the linewidth, than the noise, than its neighbours) is
# relative geometry and is exactly what the pack is for, so it passes.
_BARE_SCALE_RE = re.compile(
    r"\b(?:a|an|the|one|single)\s+(?:very\s+|fairly\s+|quite\s+)?"
    r"(?:broad|narrow|wide|shallow|deep|tall|short|small|large|weak|strong)\s+"
    r"(?:[a-z]+\s+){0,2}"
    r"(?:feature|peak|dip|notch|ridge|band|arc|fringe|oscillation|resonance)\b"
    r"(?!\s*(?:,\s*)?(?:than|compared|relative|with\s+respect|versus|vs)\b)",
    re.I)

# Counts that are really window-dependent: how many oscillations you see is a
# statement about how far the sweep went, not about the qubit.
_WINDOW_COUNT_RE = re.compile(
    r"\b(?:about|around|roughly|approximately|some|at\s+least|more\s+than)?\s*"
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|several|many)\s+"
    r"(?:full\s+|complete\s+)?"
    r"(?:oscillations?|periods?|cycles?|fringes?|revivals?|lobes?)\b", re.I)

_LINT_RULES = (
    (_UNIT_RE, "a physical quantity with a unit (absolute, chip-specific)"),
    (_BARE_UNIT_RE, "a physical unit named as a quantity"),
    (_POSITION_RE, "a position-in-window claim (Clause B: window-dependent)"),
    (_WINDOW_FRACTION_RE,
     "a feature sized against the SWEPT WINDOW (Clause B: the same physics "
     "zoomed in would score differently — size it against the feature)"),
    (_PATH_RE, "a file path or archive run id"),
)

# Two tiers, and the split is itself a measurement. The rules above are precise
# enough to DROP on: a unit or an explicit window-fraction is a violation with
# no innocent reading. The prose rules below have real recall — the audit found
# the drop-tier catches almost none of the word-form violations (docs/78
# §22.1) — but running them against the shipped v1 pack flagged ten strings and
# most were FALSE POSITIVES on inspection: "one fringe runs vertical" is a
# shape statement, "instead of a narrow band" is a contrast, and "several
# periods is a legitimate signature" exists precisely to PREVENT a Clause-B
# misjudgement. Dropping those would thin the pack, and P3c measured the
# judge's weak side to be stinginess, not leniency — so they WARN. A maintainer
# rewords; the loader never silently deletes family knowledge on a guess.
_LINT_WARN_RULES = (
    (_PROSE_POSITION_RE,
     "a position-in-window claim written in words (Clause B: where a feature "
     "sits is the experimenter's choice of window, not physics)"),
    (_BARE_SCALE_RE,
     "an unqualified size adjective (broad/narrow compared to WHAT? — name "
     "the comparison, e.g. broader than its own linewidth)"),
    (_WINDOW_COUNT_RE,
     "a count of periodic features (Clause B: how many oscillations are "
     "visible says how far the sweep went, not what the qubit does)"),
)

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def lint_text(text: str) -> list[str]:
    """Clause-B violations that justify DROPPING the string (empty = clean)."""
    out = []
    for rx, why in _LINT_RULES:
        for m in rx.finditer(text or ""):
            out.append(f"{why}: {m.group(0)!r}")
    return out


def warn_text(text: str) -> list[str]:
    """Prose Clause-B suspicions — reported for a human, never auto-dropped."""
    out = []
    for rx, why in _LINT_WARN_RULES:
        for m in rx.finditer(text or ""):
            out.append(f"{why}: {m.group(0)!r}")
    return out


def warn_entry(entry: dict) -> list[str]:
    """Every prose suspicion in one entry, each naming its field.

    This is the recall half of the lint. `lint_entry() == []` means nothing
    was DROPPED; it never meant the entry is clean, and reading it that way is
    what let the word-form violations ship.
    """
    return _walk(entry, warn_text)


def _walk(entry: dict, check) -> list[str]:
    out: list[str] = []
    for field in _TEXT_FIELDS:
        for v in check(str(entry.get(field) or "")):
            out.append(f"{field}: {v}")
    for field in _LIST_FIELDS:
        for i, s in enumerate(entry.get(field) or []):
            for v in check(str(s)):
                out.append(f"{field}[{i}]: {v}")
    for mode, s in (entry.get("failure_appearance") or {}).items():
        for v in check(str(s or "")):
            out.append(f"failure_appearance.{mode}: {v}")
    return out


def lint_entry(entry: dict) -> list[str]:
    """Every DROP-tier violation in one family entry, each naming its field."""
    return _walk(entry, lint_text)


def _scrub(entry: dict) -> tuple[dict, list[str]]:
    """Drop violating strings rather than teach them. Returns (clean, dropped)."""
    dropped: list[str] = []
    out = dict(entry)
    for field in _LIST_FIELDS:
        keep = []
        for s in entry.get(field) or []:
            bad = lint_text(str(s))
            if bad:
                dropped.append(f"{field}: {s!r} — {bad[0]}")
            else:
                keep.append(s)
        out[field] = keep
    fa = {}
    for mode, s in (entry.get("failure_appearance") or {}).items():
        bad = lint_text(str(s or ""))
        if bad:
            dropped.append(f"failure_appearance.{mode}: {bad[0]}")
            fa[mode] = None
        else:
            fa[mode] = s
    out["failure_appearance"] = fa
    for field in _TEXT_FIELDS:
        if lint_text(str(entry.get(field) or "")):
            dropped.append(f"{field}: dropped")
            out[field] = ""
    return out, dropped


def pack_dir(version: str = DEFAULT_VERSION) -> Path:
    return _PACK_ROOT / version


def load_pack(version: str = DEFAULT_VERSION, *, use_cache: bool = True) -> dict:
    """``{family_key: entry}`` for a pack version; missing pack ⇒ ``{}``.

    Entries are scrubbed of Clause-B violations at load (logged, never silent),
    so a hand-edited file can degrade the judge's knowledge but can never teach
    it a window-dependent falsehood.
    """
    if use_cache:
        with _cache_lock:
            hit = _cache.get(version)
        if hit is not None:
            return hit
    out: dict[str, dict] = {}
    d = pack_dir(version)
    for path in sorted(d.glob("*.json")) if d.is_dir() else []:
        if path.name.startswith("_"):
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("judge pack entry unreadable: %s", path.name)
            continue
        key = str(entry.get("family") or path.stem)
        clean, dropped = _scrub(entry)
        if dropped:
            logger.warning("judge pack %s/%s: %d string(s) dropped for "
                           "Clause-B violations: %s", version, key,
                           len(dropped), "; ".join(dropped[:3]))
        clean["lint_dropped"] = dropped
        # the recall half: surfaced for a maintainer, never auto-removed.
        # `lint_dropped == []` means nothing was DROPPED — it never meant the
        # entry is clean, and reading it that way is what let the word-form
        # Clause-B violations ship (docs/78 §22.1).
        warnings = warn_entry(entry)
        if warnings:
            logger.warning("judge pack %s/%s: %d prose Clause-B warning(s) "
                           "(kept — reword, do not delete): %s", version, key,
                           len(warnings), "; ".join(warnings[:3]))
        clean["lint_warnings"] = warnings
        out[key] = clean
    with _cache_lock:
        _cache[version] = out
    return out


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def entry_for(family_key: str, version: str = DEFAULT_VERSION) -> dict | None:
    return load_pack(version).get(family_key)


def prompt_block(entry: dict | None) -> str:
    """The family knowledge, rendered for the judge prompt.

    Returns "" for an unknown family — the judge then works from the generic
    system prompt alone, which is honest: we have taught it nothing about this
    node, so it should lean on abstain.
    """
    if not entry:
        return ""
    lines = [f"FAMILY: {entry.get('label') or entry.get('family')}"]
    if entry.get("axes"):
        lines.append(f"The figure plots: {entry['axes']}")
    sig = entry.get("correct_signature") or []
    if sig:
        lines.append("A CORRECT signature for this family looks like:")
        lines += [f"  - {s}" for s in sig]
    fa = {k: v for k, v in (entry.get("failure_appearance") or {}).items() if v}
    if fa:
        lines.append("Known failure appearances:")
        lines += [f"  - {k}: {v}" for k, v in sorted(fa.items())]
    ab = entry.get("abstain_when") or []
    if ab:
        lines.append("Abstain (do not guess) when:")
        lines += [f"  - {s}" for s in ab]
    if entry.get("localizer") == "none":
        lines.append("This family is a 2-D map with no single axis the feature "
                     "sits on: judge the SIGNATURE only, never a position.")
    lines.append("Describe only shape and relative geometry. A feature's "
                 "position inside the window is an artefact of the sweep that "
                 "was chosen, not physics — never use it as evidence.")
    return "\n".join(lines)
