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

_LINT_RULES = (
    (_UNIT_RE, "a physical quantity with a unit (absolute, chip-specific)"),
    (_BARE_UNIT_RE, "a physical unit named as a quantity"),
    (_POSITION_RE, "a position-in-window claim (Clause B: window-dependent)"),
    (_WINDOW_FRACTION_RE,
     "a feature sized against the SWEPT WINDOW (Clause B: the same physics "
     "zoomed in would score differently — size it against the feature)"),
    (_PATH_RE, "a file path or archive run id"),
)

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


def lint_text(text: str) -> list[str]:
    """Clause-B violations in one string, as human sentences (empty = clean)."""
    out = []
    for rx, why in _LINT_RULES:
        for m in rx.finditer(text or ""):
            out.append(f"{why}: {m.group(0)!r}")
    return out


def lint_entry(entry: dict) -> list[str]:
    """Every violation in one family entry, each naming its field."""
    out: list[str] = []
    for field in _TEXT_FIELDS:
        for v in lint_text(str(entry.get(field) or "")):
            out.append(f"{field}: {v}")
    for field in _LIST_FIELDS:
        for i, s in enumerate(entry.get(field) or []):
            for v in lint_text(str(s)):
                out.append(f"{field}[{i}]: {v}")
    for mode, s in (entry.get("failure_appearance") or {}).items():
        for v in lint_text(str(s or "")):
            out.append(f"failure_appearance.{mode}: {v}")
    return out


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
