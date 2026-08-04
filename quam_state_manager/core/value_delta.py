"""One source of truth for "how much did this value change?" (Δ) displays.

Every before→after surface in SM shows the same three things — the old value,
the new value, and (since docs/76) the difference between them. The arithmetic
and the formatting therefore live HERE, once, and the JavaScript mirror
(``window.ValueDelta`` in ``web/static/app.js``) is pinned character-for-
character against this module by ``tests/test_value_delta.py``.

Two decisions worth knowing before you touch this file:

**The subtraction is exact decimal arithmetic, not float arithmetic.**
``5.2 - 5.1`` in binary floating point is ``0.10000000000000053``; printing
that as a researcher's "difference" is worse than printing nothing. Both sides
are converted to :class:`~decimal.Decimal` from their SHORTEST round-tripping
decimal spelling (``repr`` for floats, the literal text for stored-as-text
numbers), so the difference reads ``0.1`` — the number a physicist would have
written down.

**The formatting matches the values it sits next to.** Those are rendered by
:func:`core.units.group_digits` — lossless, full-digit, thousands-grouped — so
a delta beside ``5,100,000,000`` reads ``+100,000,000``, not ``+1.000e+08``.
Only genuinely extreme magnitudes fall back to exponential, by an explicit
threshold here (NOT by inheriting ``repr``'s, which differs between Python and
JavaScript and would break parity).
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# Mirrors type_policy._PLAIN_GROUPED_NUMBER: a display-form number may carry
# thousands commas (that is how group_digits renders it, and what an editable
# field hands back), and stripping them must round-trip exactly.
_GROUPED = re.compile(r"^[+-]?\d[\d,]*(\.\d+)?$")

# Fixed-point is readable up to a point; past these the digits stop being
# informative and exponential is the honest form. Mirrored in JS.
_SCI_HIGH = Decimal("1e15")
_SCI_LOW = Decimal("1e-6")


def _strip_grouping(s: str) -> str:
    s = s.strip()
    return s.replace(",", "") if ("," in s and _GROUPED.match(s)) else s


def as_decimal(value: Any) -> Optional[Decimal]:
    """Exact :class:`Decimal` for a numeric value, else ``None``.

    ``bool`` is deliberately NOT numeric — "False → True" is a state flip and
    "Δ +1" would be noise. Numeric STRINGS are accepted because real chips
    store numbers as text (``"0.13"``; docs/56 r14): the difference is still
    the honest answer, and :func:`compute` flags the coercion so the caller can
    say so in a tooltip.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(repr(value))     # shortest round-tripping spelling
    if isinstance(value, str):
        s = _strip_grouping(value)
        if not s:
            return None
        try:
            d = Decimal(s)
        except (InvalidOperation, ValueError):
            return None
        return d if d.is_finite() else None
    return None


def _group_int_part(digits: str) -> str:
    out = []
    for i, ch in enumerate(reversed(digits)):
        if i and i % 3 == 0:
            out.append(",")
        out.append(ch)
    return "".join(reversed(out))


def _pad_exp(s: str) -> str:
    """``1.500e-8`` → ``1.500e-08``.

    Decimal's ``%e`` emits a 1-digit exponent where float's emits 2, and
    JavaScript's ``toExponential`` emits 1 — so BOTH implementations normalise
    to the padded form rather than inheriting either default.
    """
    return re.sub(r"[eE]([+-])(\d)$", r"e\g<1>0\g<2>", s)


def _format_magnitude(mag: Decimal) -> str:
    """Format a NON-NEGATIVE exact decimal the way group_digits would."""
    if mag == 0:
        return "0"
    if mag >= _SCI_HIGH or mag < _SCI_LOW:
        return _pad_exp(f"{mag:.3e}")
    s = format(mag.normalize(), "f")     # no exponent, trailing zeros stripped
    if "." in s:
        int_part, frac = s.split(".", 1)
        return _group_int_part(int_part) + "." + frac
    return _group_int_part(s)


def format_delta(d: Decimal) -> str:
    """``+100,000,000`` / ``-0.0035`` / ``0`` / ``+1.500e-08``."""
    if d == 0:
        return "0"
    return ("-" if d < 0 else "+") + _format_magnitude(-d if d < 0 else d)


def format_percent(pct: float) -> str:
    """Percent with magnitude-appropriate precision (mirrored in JS).

    Deliberately NOT ``%g``: JavaScript has no ``%g`` and the two would drift.
    A change too small for the fixed form is shown in exponential rather than
    rounded to a lying ``+0%``.
    """
    a = abs(pct)
    if a and a < 0.001:
        return _pad_exp(f"{pct:+.2e}")
    if a >= 100:
        digits = 0
    elif a >= 10:
        digits = 1
    elif a >= 1:
        digits = 2
    else:
        digits = 3
    s = f"{pct:+.{digits}f}"
    if digits:                      # 1.500 -> 1.5, 2.00 -> 2
        s = s.rstrip("0").rstrip(".")
    return s


def compute(old: Any, new: Any) -> Optional[dict]:
    """Δ description for an old→new pair, or ``None`` when it is meaningless.

    ``None`` (no delta shown) for: either side non-numeric, boolean, null,
    NaN/inf, a JSON pointer string, a list/dict subtree. Callers render their
    existing "–" placeholder in that case — never a fabricated zero.

    Returns ``{delta, text, pct, pct_text, dir, coerced, title}`` where
    ``text`` is the signed difference, ``pct_text`` the signed percentage
    (omitted when the old value is 0 — there is no percentage of nothing) and
    ``dir`` is ``up``/``down``/``same``.
    """
    a = as_decimal(old)
    b = as_decimal(new)
    if a is None or b is None:
        return None

    d = b - a
    pct: Optional[float] = None
    pct_text: Optional[str] = None
    # No percentage when the old value is 0 (there is no percentage of
    # nothing) and none for an unchanged value ("0" already says it).
    if a != 0 and d != 0:
        try:
            # float division on purpose: the JS mirror has no Decimal, and a
            # percentage is a display figure — matching it exactly matters
            # more than the 28th significant digit.
            pct = float(d) / abs(float(a)) * 100.0
        except (InvalidOperation, ZeroDivisionError, OverflowError, ValueError):
            pct = None
        if pct is not None and math.isfinite(pct):
            pct_text = format_percent(pct) + "%"
        else:
            pct = None

    text = format_delta(d)
    direction = "up" if d > 0 else ("down" if d < 0 else "same")
    coerced = isinstance(old, str) or isinstance(new, str)

    title = f"difference: {text}"
    if pct_text:
        title += f" ({pct_text})"
    if coerced:
        title += " — one side is stored as text"
    if d == 0:
        title = "same numeric value" + (" (stored type differs)" if coerced
                                        else "")

    return {
        "delta": float(d),
        "text": text,
        "pct": pct,
        "pct_text": pct_text,
        "dir": direction,
        "coerced": coerced,
        "title": title,
    }


def describe(old: Any, new: Any) -> str:
    """One-line ``old → new (Δ …)`` for tooltips/toasts. Never raises."""
    from quam_state_manager.core.units import group_digits

    left = "null" if old is None else group_digits(old)
    right = "null" if new is None else group_digits(new)
    info = compute(old, new)
    tail = ""
    if info:
        tail = f"  (Δ {info['text']}"
        tail += f", {info['pct_text']})" if info["pct_text"] else ")"
    return f"{left} → {right}{tail}"
