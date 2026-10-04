"""Static SVG line charts for the shareable chip report (docs/277 section 6).

The report's file opens with no network and no scripts, so a chart in it is a
picture: an ``<svg>`` with polylines, axes and tick labels, plus an HTML
legend. The NUMBERS come from the caller -- the same functions the live page
plots -- and this module only places them. Nothing here computes a quantity.

Colours are the validated dark categorical palette (eight slots, fixed order,
assigned by the caller's entity order so a qubit keeps its colour across
charts); past eight entities the hue repeats with a different dash pattern,
so identity is never colour alone, and a legend is always drawn.
"""

from __future__ import annotations

import html
import math
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Iterable, Sequence

#: Categorical slots, dark surface (validated set; docs/277 section 6).
PALETTE = ("#3987e5", "#d95926", "#199e70", "#c98500",
           "#d55181", "#008300", "#9085e9", "#e66767")
#: Secondary encoding for the 9th entity onwards: solid, dashed, dotted, dash-dot.
DASHES = ("", "6 3", "1.5 3", "8 3 1.5 3")
MUTED = "var(--pico-muted-color, #8a8a8a)"


def series_style(i: int) -> tuple[str, str]:
    """(colour, dasharray) of the *i*-th entity (0-based, the caller's order)."""
    return PALETTE[i % len(PALETTE)], DASHES[(i // len(PALETTE)) % len(DASHES)]


def _nice_step(span: float, target: int = 5) -> float:
    if not span or not math.isfinite(span) or span <= 0:
        return 1.0
    raw = span / max(1, target)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    step = _nice_step(hi - lo, target)
    start = math.ceil(lo / step - 1e-9) * step
    out, v = [], start
    while v <= hi + step * 1e-9 and len(out) < 50:
        out.append(0.0 if abs(v) < step * 1e-9 else v)
        v += step
    return out


def fmt_tick(v: float, step: float) -> str:
    """A tick label with just enough decimals for *step*."""
    if v == 0:
        return "0"
    if abs(v) >= 1e5 or abs(v) < 1e-4:
        return f"{v:.3g}"
    dec = max(0, -int(math.floor(math.log10(step))) if step else 0)
    return f"{v:.{min(dec, 6)}f}"


_TIME_STEPS = (3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400,
               7 * 86400, 14 * 86400, 30 * 86400, 91 * 86400, 182 * 86400,
               365 * 86400)


def time_ticks(lo: float, hi: float, tz: tzinfo | None, target: int = 6
               ) -> tuple[list[float], list[str]]:
    """Tick positions (epoch seconds) and labels in *tz* for a time axis."""
    span = max(hi - lo, 1.0)
    step = next((s for s in _TIME_STEPS if span / s <= target), _TIME_STEPS[-1])
    t0 = datetime.fromtimestamp(lo, tz)
    if step >= 86400:
        t0 = t0.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        t0 = t0.replace(minute=0, second=0, microsecond=0)
        t0 = t0.replace(hour=(t0.hour // (step // 3600)) * (step // 3600))
    ticks, labels = [], []
    t = t0
    fmt = "%m-%d" if span >= 2 * 86400 else "%m-%d %H:%M"
    if span >= 300 * 86400:
        fmt = "%Y-%m-%d"
    while t.timestamp() <= hi + 1 and len(ticks) < 40:
        ts = t.timestamp()
        if ts >= lo - 1:
            ticks.append(ts)
            labels.append(t.strftime(fmt))
        t = t + timedelta(seconds=step)
    return ticks, labels


def log_ticks(lo: float, hi: float) -> tuple[list[float], list[str]]:
    """Decade ticks for a time axis in NANOSECONDS, labelled with their unit
    (the /zline page's own decade labels: 1 ns, 10 ns, ... 1 ms)."""
    units = ((1e6, "ms"), (1e3, "µs"), (1.0, "ns"))
    ticks, labels = [], []
    e = math.floor(math.log10(max(lo, 1e-3)))
    while 10 ** e <= hi * 1.0001 and len(ticks) < 20:
        x = 10 ** e
        if x >= lo * 0.9999:
            for scale, u in units:
                if x >= scale * 0.999:
                    labels.append(f"{round(x / scale):g} {u}")
                    break
            else:
                labels.append(f"{x:g} ns")
            ticks.append(x)
        e += 1
    return ticks, labels


def _e(s: str) -> str:
    return html.escape(str(s), quote=True)


def line_chart(series: Sequence[dict], *, x_kind: str = "linear",
               x_label: str = "", y_label: str = "", y_factor: float = 1.0,
               width: int = 760, height: int = 230, tz: tzinfo | None = None,
               title: str = "", x_range: tuple[float, float] | None = None,
               markers: bool | None = None) -> str:
    """One static chart. Each series: ``{"name", "xs", "ys"}`` plus optional
    ``"color"``, ``"dash"``, ``"width"``. ``xs`` are epoch seconds
    (``x_kind="time"``), nanoseconds on a log axis (``"log"``), or plain
    numbers. ``ys`` may hold ``None`` (a gap, never bridged). Values are
    multiplied by *y_factor* for placement and tick labels only."""
    ml, mr, mt, mb = 62, 14, 10, 40
    pw, ph = width - ml - mr, height - mt - mb
    pts_all = [(x, y * y_factor) for s in series for x, y in zip(s["xs"], s["ys"])
               if y is not None and x is not None and math.isfinite(y)
               and (x_kind != "log" or x > 0)]
    if not pts_all:
        return (f'<svg class="rep-chart" viewBox="0 0 {width} {height}" role="img"'
                f' aria-label="{_e(title or y_label)}: no data"></svg>')
    xs = [p[0] for p in pts_all]
    ys = [p[1] for p in pts_all]
    x0, x1 = x_range if x_range else (min(xs), max(xs))
    y0, y1 = min(ys), max(ys)
    if y1 - y0 <= max(abs(y0), abs(y1)) * 1e-9:
        pad = (abs(y0) * 0.05) or 1.0          # a constant series: a band around it
        y0, y1 = y0 - pad, y1 + pad
    else:
        pad = (y1 - y0) * 0.06
        y0, y1 = y0 - pad, y1 + pad
    if x_kind == "log":
        lx0, lx1 = math.log10(max(x0, 1e-3)), math.log10(max(x1, 1e-3))
        if lx1 <= lx0:
            lx1 = lx0 + 1
        fx = lambda x: ml + (math.log10(x) - lx0) / (lx1 - lx0) * pw  # noqa: E731
    else:
        if x1 <= x0:
            x0, x1 = x0 - 1, x1 + 1
        fx = lambda x: ml + (x - x0) / (x1 - x0) * pw  # noqa: E731
    fy = lambda y: mt + (1 - (y - y0) / (y1 - y0)) * ph  # noqa: E731

    parts = [f'<svg class="rep-chart" viewBox="0 0 {width} {height}" role="img"'
             f' aria-label="{_e(title or y_label)}" preserveAspectRatio="xMinYMin meet">']
    # grid + y ticks
    ystep = _nice_step(y1 - y0)
    for v in nice_ticks(y0, y1):
        y = fy(v)
        parts.append(f'<line class="rc-grid" x1="{ml}" x2="{ml + pw}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text class="rc-tick" x="{ml - 6}" y="{y + 3.5:.1f}" text-anchor="end">'
                     f'{_e(fmt_tick(v, ystep))}</text>')
    # x ticks
    if x_kind == "time":
        tv, tl = time_ticks(x0, x1, tz)
    elif x_kind == "log":
        tv, tl = log_ticks(x0, x1)
    else:
        tv = nice_ticks(x0, x1, 6)
        st = _nice_step(x1 - x0, 6)
        tl = [fmt_tick(v, st) for v in tv]
    for v, lab in zip(tv, tl):
        if x_kind == "log" and v <= 0:
            continue
        x = fx(v)
        if x < ml - 0.5 or x > ml + pw + 0.5:
            continue
        # a label at either edge is anchored inward, so it is never clipped
        half = 3.0 * len(lab)                # ~half the label's width at 10 px
        anchor = ("end" if x + half > width - 2 else "start" if x - half < 2 else "middle")
        parts.append(f'<line class="rc-grid" x1="{x:.1f}" x2="{x:.1f}" y1="{mt}" y2="{mt + ph}"/>'
                     f'<text class="rc-tick" x="{x:.1f}" y="{mt + ph + 14}" text-anchor="{anchor}">'
                     f'{_e(lab)}</text>')
    parts.append(f'<rect class="rc-frame" x="{ml}" y="{mt}" width="{pw}" height="{ph}"/>')
    if x_label:
        parts.append(f'<text class="rc-label" x="{ml + pw / 2:.1f}" y="{height - 4}"'
                     f' text-anchor="middle">{_e(x_label)}</text>')
    if y_label:
        parts.append(f'<text class="rc-label" transform="translate(12 {mt + ph / 2:.1f}) rotate(-90)"'
                     f' text-anchor="middle">{_e(y_label)}</text>')
    # series
    for i, s in enumerate(series):
        color = s.get("color") or series_style(i)[0]
        dash = s.get("dash") if s.get("dash") is not None else series_style(i)[1]
        w = s.get("width", 1.6)
        segs: list[list[str]] = [[]]
        n = 0
        for x, y in zip(s["xs"], s["ys"]):
            if (y is None or x is None or not math.isfinite(y) or (x_kind == "log" and x <= 0)):
                if segs[-1]:
                    segs.append([])
                continue
            pt = f"{fx(x):.1f},{fy(y * y_factor):.1f}"
            n += 1
            if segs[-1] and segs[-1][-1] == pt:
                continue                    # same pixel as the point before it
            segs[-1].append(pt)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        for seg in segs:
            if len(seg) >= 2:
                parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="{w}"{da}'
                             f' stroke-linejoin="round" points="{" ".join(seg)}"/>')
        show = markers if markers is not None else n <= 60
        if show:
            dots = "".join(f"M{p.replace(',', ' ')}h0" for seg in segs for p in seg)
            if dots:
                parts.append(f'<path d="{dots}" stroke="{color}" stroke-width="5"'
                             f' stroke-linecap="round" fill="none"/>')
    parts.append("</svg>")
    return "".join(parts)


def legend(items: Iterable[dict]) -> str:
    """HTML legend: ``{"name", "color", "dash", "value"}`` per entry. Text
    wears text colour; only the swatch carries the series colour."""
    out = ['<div class="rc-legend">']
    for it in items:
        dash = it.get("dash") or ""
        da = f' stroke-dasharray="{dash}"' if dash else ""
        val = it.get("value")
        out.append(
            f'<span class="rc-key"><svg width="22" height="8" aria-hidden="true">'
            f'<line x1="1" y1="4" x2="21" y2="4" stroke="{it["color"]}" stroke-width="2.4"{da}/>'
            f'</svg>{_e(it["name"])}'
            + (f' <b>{_e(val)}</b>' if val not in (None, "") else "") + "</span>")
    out.append("</div>")
    return "".join(out)
