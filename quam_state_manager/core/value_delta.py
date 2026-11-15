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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

# A display-form number may carry thousands commas (that is how group_digits
# renders it, and what an editable field hands back), and stripping them must
# round-trip exactly. Only the shape group_digits EMITS -- well-formed 3-digit
# groups after 1-3 leading digits -- counts as grouping here: a text
# coordinate like "0,1" is not the number 1 (QA F15), and a grid_location
# pair like "1,1" / "4,8" is not 11 / 48 (JT-08; the same rule as
# type_fix._parse_plain), so it gets no delta, as docs/76 requires of plain
# strings. type_policy's loose INPUT gate (typed "7,662,072100") is
# deliberately not mirrored: that one parses what a user types, this one
# reads stored values. Mirrored in JS (window.ValueDelta).
_GROUPED = re.compile(r"^[+-]?[1-9]\d{0,2}(,\d{3})+(\.\d+)?$")

# Fixed-point is readable up to a point; past these the digits stop being
# informative and exponential is the honest form. Mirrored in JS.
_SCI_HIGH = Decimal("1e15")
_SCI_LOW = Decimal("1e-6")

# S10 walk (v1.2.0): what a delta -- and a before/after value beside it -- shows
# ON SCREEN. Adjacent drawer rows read "-0.000001489640122237915" and
# "-2.420e-07": twenty digits next to exponential for the same magnitude. The
# display keeps SIG_DIGITS significant digits (never fewer than the integer
# digits: a frequency delta is never rounded to fake zeros), fixed-point from
# _COMPACT_LOW up to _SCI_HIGH and exponential outside, so one magnitude always
# reads in one notation. The subtraction stays exact; the exact difference is
# in the hover title. Mirrored in JS (window.ValueDelta), parity-pinned.
SIG_DIGITS = 6
_COMPACT_LOW = Decimal("1e-4")


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


def _strip_frac_zeros(s: str) -> str:
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def compact_magnitude(mag: Decimal, sig: int = SIG_DIGITS) -> str:
    """A NON-NEGATIVE exact decimal as the screen shows it: *sig* (default
    SIG_DIGITS) significant digits, half-up (never fewer than its integer
    digits), trailing zeros dropped, grouped fixed-point in [1e-4, 1e15), else
    exponential (``1.48964e-06``, ``2.42e-07``). Rounds the exact digits
    (``quantize``), never a float: the JS mirror rounds the same digits."""
    if mag == 0:
        return "0"
    adj = mag.adjusted()                    # mag in [10^adj, 10^(adj+1))
    if mag >= _SCI_HIGH or mag < _COMPACT_LOW:
        q = mag.quantize(Decimal(1).scaleb(adj - sig + 1), rounding=ROUND_HALF_UP)
        adj = q.adjusted()                  # a carry (9.999995 -> 10) moves it up
        mant = _strip_frac_zeros(format(q.scaleb(-adj), "f"))
        return f"{mant}e{'-' if adj < 0 else '+'}{abs(adj):02d}"
    decimals = max(0, sig - (adj + 1))
    q = mag.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    s = format(q, "f")
    if "." in s:
        int_part, frac = s.split(".", 1)
        frac = frac.rstrip("0")
        return _group_int_part(int_part) + ("." + frac if frac else "")
    return _group_int_part(s)


def format_delta(d: Decimal) -> str:
    """The on-screen difference: ``+100,000,000`` / ``-0.0477229`` / ``0`` /
    ``-1.48964e-06`` (:func:`compact_magnitude`)."""
    if d == 0:
        return "0"
    return ("-" if d < 0 else "+") + compact_magnitude(-d if d < 0 else d)


def format_delta_exact(d: Decimal) -> str:
    """The difference with every digit the subtraction produced (the hover
    title): ``-0.000001489640122237915``, extremes ``+1.500e-08``."""
    if d == 0:
        return "0"
    return ("-" if d < 0 else "+") + _format_magnitude(-d if d < 0 else d)


ABSENT = object()


def format_value(value: Any) -> str:
    """A before/after VALUE beside a delta, by the delta's own rule (the State
    History diff printed ``4.374739e-01 -> 3.897510e-01`` beside a plain
    ``-0.04772291024636856``). A non-number is returned as written."""
    if value is ABSENT:
        return "(absent)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, (int, float)):
        return "null" if value is None else str(value)
    d = as_decimal(value)
    if d is None:
        return str(value)
    return ("-" if d < 0 else "") + compact_magnitude(-d if d < 0 else d)


#: S10 walk: the significant digits of a percent CHANGE on screen. One rule for
#: every percent (the delta's :func:`compact_magnitude` with fewer digits): the
#: old magnitude ladder printed "-0.005%" (one digit) beside "+0.625%" (three),
#: and the hero popup's own "%+.1f" printed "+0.0%" for a nonzero change.
PCT_SIG_DIGITS = 3


def format_percent(pct: float) -> str:
    """A signed percent change, without the ``%`` (mirrored in JS,
    ``ValueDelta.formatPercent``): :data:`PCT_SIG_DIGITS` significant digits,
    half-up on the shortest decimal spelling of the float, never fewer than
    its integer digits, grouped fixed-point in [1e-4, 1e15) and exponential
    outside -- ``-0.00512``, ``+0.625``, ``+15.3``, ``+1,235``, ``+7.5e-07``.
    A nonzero change never reads as ``+0`` / ``+0.0``; ``0`` is ``0``.
    Deliberately NOT ``%g``: JavaScript has no ``%g`` and the two would drift.
    """
    d = as_decimal(pct) if isinstance(pct, (int, float)) else None
    if d is None:
        return ""
    if d == 0:
        return "0"
    return ("-" if d < 0 else "+") + compact_magnitude(-d if d < 0 else d, PCT_SIG_DIGITS)


def percent_change(pct: Any) -> str:
    """The Jinja ``pct_change`` filter: a percent change (a number in percent
    units) as :func:`format_percent` writes it, with the ``%`` -- ``""`` for
    a non-number (the caller's own placeholder stands)."""
    if pct is None or isinstance(pct, bool) or not isinstance(pct, (int, float)):
        return ""
    if isinstance(pct, float) and not math.isfinite(pct):
        return ""
    return format_percent(pct) + "%"


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
    both_text = isinstance(old, str) and isinstance(new, str)

    # the screen shows SIG_DIGITS digits; the hover keeps every digit
    title = f"difference: {format_delta_exact(d)}"
    if pct_text:
        title += f" ({pct_text})"
    if coerced:
        title += (" — both sides are stored as text" if both_text
                  else " — one side is stored as text")
    if d == 0:
        title = "same numeric value" + (" (stored type differs)"
                                        if coerced and not both_text else "")

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

    left = format_value(old) if old is None or old is ABSENT else group_digits(old)
    right = format_value(new) if new is None or new is ABSENT else group_digits(new)
    info = compute(old, new)
    tail = ""
    if info:
        tail = f"  (Δ {info['text']}"
        tail += f", {info['pct_text']})" if info["pct_text"] else ")"
    return f"{left} → {right}{tail}"


def describe_moved(old: Any, new: Any, unit: str = "") -> str:
    """``old → new unit (Δ …)`` for a value a run moved, in result sentences.

    The same arrow and delta text as :func:`describe`, with a unit. An old
    value that is missing or not a number gives only the new value (no arrow
    to nothing, no invented delta); an unchanged value says so. Never raises."""
    u = f" {unit}" if unit else ""
    right = format_value(new)
    if as_decimal(old) is None or isinstance(old, bool):
        return f"now {right}{u}"
    info = compute(old, new)
    if info is None:
        return f"{format_value(old)} → {right}{u}"
    if info["dir"] == "same":
        return f"{right}{u}, unchanged"
    return f"{format_value(old)} → {right}{u} (Δ {info['text']})"


# ---------------------------------------------------------------------------
# docs/272 / docs/254: ONE row of an agent card, as agent.js ``fmtRow`` writes it.
# The browser formats every card row itself; the server needs the same text for the
# line an agent (or the MCP) reads when a press is refused because a value moved.
# This is a port of fmtRow, character for character, including JavaScript's number
# printing: ``toFixed`` / ``toExponential`` round an exact tie up (0.03125 -> "0.0313",
# where Python's round-half-even gives "0.0312"), ``String(5e9)`` is "5000000000" and
# ``JSON.stringify([1e9])`` is "[1000000000]". Pinned against the real agent.js by
# tests/fixtures/card_row_vectors.json (tests/test_approval_now_anchor.py and
# tests/agent_panel_selfcheck.cjs read the same file).

CARD_MORE_DP = 6          # agent.js MORE_DP: at most this many extra decimals to tell values apart


def _js_num(v: Any) -> bool:
    """agent.js ``isNum``: a finite number, never a bool."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v))
    except OverflowError:
        return False


def js_number_text(x: float) -> str:
    """JavaScript ``String(x)`` for a number (Number::toString, radix 10)."""
    x = float(x)
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    t = Decimal(repr(abs(x))).normalize().as_tuple()     # shortest round-trip digits, like JS
    digits = "".join(str(d) for d in t.digits)
    k = len(digits)
    n = k + t.exponent                                    # the decimal point sits after n digits
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        body = (digits[0] + ("." + digits[1:] if k > 1 else "")) + ("e+" if e >= 0 else "e-") + str(abs(e))
    return sign + body


def js_to_fixed(x: float, dp: int) -> str:
    """JavaScript ``x.toFixed(dp)``: the exact binary value, a tie rounded away from zero."""
    x = float(x)
    if not math.isfinite(x) or abs(x) >= 1e21:
        return js_number_text(x)
    if x == 0:
        x = 0.0                                           # (-0).toFixed(2) is "0.00"
    q = Decimal(x).quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)
    return f"{q:f}"


def js_to_exponential(x: float, dp: int) -> str:
    """JavaScript ``x.toExponential(dp)``."""
    x = float(x)
    if not math.isfinite(x):
        return js_number_text(x)
    if x == 0:
        return ("0." + "0" * dp if dp else "0") + "e+0"
    d = Decimal(abs(x))
    e = d.adjusted()
    q = Decimal(1).scaleb(-dp)
    m = d.scaleb(-e).quantize(q, rounding=ROUND_HALF_UP)
    if m >= 10:
        e += 1
        m = d.scaleb(-e).quantize(q, rounding=ROUND_HALF_UP)
    return ("-" if x < 0 else "") + f"{m:f}" + ("e+" if e >= 0 else "e-") + str(abs(e))


def js_json_text(v: Any) -> str:
    """JavaScript ``JSON.stringify(v)`` for JSON-shaped data (no spaces, JS numbers)."""
    import json as _json
    if v is None or isinstance(v, bool):
        return "null" if v is None else ("true" if v else "false")
    if isinstance(v, (int, float)):
        return js_number_text(v) if _js_num(v) else "null"
    if isinstance(v, str):
        return _json.dumps(v, ensure_ascii=False)
    if isinstance(v, dict):
        return "{" + ",".join(_json.dumps(str(k), ensure_ascii=False) + ":" + js_json_text(x)
                              for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(js_json_text(x) for x in v) + "]"
    return _json.dumps(str(v), ensure_ascii=False)


def _card_other(v: Any) -> str:
    """agent.js ``fmtOther``: a value that is not a finite number."""
    if v is None:
        return "null"
    if isinstance(v, (dict, list, tuple)):
        return js_json_text(v)[:60]
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return js_number_text(v)
    return str(v)


def _distinct_at(xs: list, f) -> bool:
    """agent.js ``distinctAt``: no two different values print alike."""
    seen: dict = {}
    for x in xs:
        t = f(x)
        if t in seen and seen[t] != x:
            return False
        seen[t] = x
    return True


def format_card_row(values: list, display: dict | None) -> list[str]:
    """The values of ONE agent-card row as agent.js ``fmtRow(values, display)`` writes them:
    one unit, one number of decimals, and two different values never printed alike."""
    xs = [v for v in values if _js_num(v)]
    disp = display if isinstance(display, dict) else None
    if disp and disp.get("unit") and _js_num(disp.get("scale")):
        scale = float(disp["scale"])

        def sc(v):
            return float(v) * scale
        try:
            dp = max(0, int(disp.get("dp") or 0))
        except (TypeError, ValueError):
            dp = 0
        if not dp and any(not sc(v).is_integer() for v in xs):
            dp = 1
        top = dp + CARD_MORE_DP
        while dp < top and not _distinct_at(xs, lambda v, _d=dp: js_to_fixed(sc(v), _d)):
            dp += 1
        unit = str(disp["unit"])

        def each(v):
            return js_to_fixed(sc(v), dp) + " " + unit
    elif all(float(v).is_integer() for v in xs):
        each = js_number_text
    else:
        m = max(abs(float(v)) for v in xs)
        expo = m >= 1e6 or m < 1e-3
        dp = 4 if expo else max(0, 4 - math.floor(math.log10(m)))
        fx = js_to_exponential if expo else js_to_fixed
        cap = dp + CARD_MORE_DP
        while dp < cap and not _distinct_at(xs, lambda v, _d=dp: fx(v, _d)):
            dp += 1
        while dp > 0 and all(re.search(r"0(e|$)", fx(v, dp)) for v in xs):
            dp -= 1

        def each(v):
            return fx(v, dp)
    return [each(v) if _js_num(v) else _card_other(v) for v in values]
