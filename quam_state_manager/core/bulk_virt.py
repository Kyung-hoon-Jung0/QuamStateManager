"""Server-side column virtualization for the Live-Edit qubit grid (docs/141 §4n).

The client (``bulk-edit.js`` ``_virtInit``, docs/105) decides which columns
are *cold* — rendered as an empty ``<td>`` and filled on demand — from a
layout-free estimate: cumulative value-fit widths against the viewport plus a
look-ahead buffer. Rendering those columns on the server only for the client
to detach them was two thirds of the ``/bulk`` document and of its render
time on a real 20Q chip. This module is the SAME estimate on the server,
deliberately CONSERVATIVE — the smallest plausible glyph width and a wide
default viewport — so the server never keeps fewer hot columns than the
client would, and a server-cold column is always one the client would have
detached anyway. Anything the estimate still gets wrong is hydrated by the
client's own first pass (real geometry, on the pruned table).

Pure: no Flask, no store. ``plan`` is the one decision; ``cold_map`` shapes
the value map the client needs for whole-chip search and path-addressed
hydration; ``parse_cols`` reads the hydration request.
"""
from __future__ import annotations

from typing import Any, Iterable

# The client's constants, mirrored (bulk-edit.js: _VIRT_EST_PX_PER_CHAR,
# _VIRT_EST_PAD, the label estimate, _VIRT_BUFFER, _VIRT_MIN_CELLS, _VIRT_MIN_COLD).
PX_PER_CHAR = 8.0          # the 16px-root fallback: the SMALLEST glyph the client ever assumes
PAD_PX = 28.0
LABEL_PX_PER_CHAR = 7.5
LABEL_PAD_PX = 30.0
BUFFER = 1.5               # hydrate-ahead viewports
MIN_CELLS = 600            # pre-filter: a small grid is never touched
MIN_COLD = 800             # the real gate: enough cells must go cold to repay it

DEFAULT_VIEWPORT_PX = 1920     # no hint (a full-page load, an old client): assume a wide screen
MIN_VIEWPORT_PX = 320
MAX_VIEWPORT_PX = 16000


def viewport_px(hint: Any) -> int:
    """The viewport width the plan is made for: the client's ``vw`` hint
    (``screen.availWidth`` — no layout read), clamped; the wide default when
    absent or unreadable."""
    try:
        v = int(float(str(hint).strip()))
    except (TypeError, ValueError):
        return DEFAULT_VIEWPORT_PX
    if v <= 0:
        return DEFAULT_VIEWPORT_PX
    return max(MIN_VIEWPORT_PX, min(MAX_VIEWPORT_PX, v))


def column_width_px(col: dict) -> float:
    """The client's estimate of a column's rendered width: the value-fit input
    (``maxlen`` characters) or the header label, whichever is wider."""
    w = float(col.get("maxlen") or 8) * PX_PER_CHAR + PAD_PX
    lw = len(str(col.get("label") or "")) * LABEL_PX_PER_CHAR + LABEL_PAD_PX
    return max(w, lw)


def plan(columns: list[dict], n_rows: int, viewport: Any = None) -> set[str]:
    """Keys of the columns the server may render cold.

    Mirrors ``_virtInit``: a default-hidden column is cold and takes no width;
    a column whose LEFT edge lies past ``viewport * (1 + BUFFER)`` is cold.
    Below either gate (fewer than MIN_CELLS cells, or fewer than MIN_COLD cells
    going cold) nothing is cold and the render is byte-identical to a
    non-virtualized one — the safety gate every small chip rides.
    """
    if not columns or n_rows <= 0 or len(columns) * n_rows < MIN_CELLS:
        return set()
    edge = viewport_px(viewport) * (1 + BUFFER)
    x = 0.0
    cold: set[str] = set()
    for col in columns:
        key = col.get("key")
        if not key:
            continue
        if not col.get("default_on", True):
            cold.add(key)
            continue
        if x > edge:
            cold.add(key)
        x += column_width_px(col)
    if len(cold) * n_rows < MIN_COLD:
        return set()
    return cold


def cold_map(columns: list[dict], rows: list[dict], cold: Iterable[str]) -> dict[str, Any]:
    """What the client keeps for a cold cell instead of its markup:
    ``{"rows": [id, ...], "cols": {key: [[display, dot_path, resolved], ...]}}``
    in row order — the display string for whole-chip search, the two paths for
    path-addressed repaints (an undo naming the resolved leaf of an alias
    cell). ``resolved`` is ``0`` when it equals ``dot_path``."""
    cold_set = set(cold)
    idx = [i for i, c in enumerate(columns) if c.get("key") in cold_set]
    out: dict[str, list] = {columns[i]["key"]: [] for i in idx}
    for row in rows:
        cells = row.get("cells") or []
        for i in idx:
            cell = cells[i] if i < len(cells) else {}
            dp = cell.get("dot_path") or ""
            rp = cell.get("resolved_path") or ""
            out[columns[i]["key"]].append([
                str(cell.get("display") if cell.get("display") is not None else ""),
                dp,
                0 if (not rp or rp == dp) else rp,
            ])
    return {"rows": [r.get("id") for r in rows], "cols": out}


def parse_cols(raw: Any, known: Iterable[str], limit: int = 400) -> tuple[list[str], list[str]]:
    """Split a hydration request's ``cols`` parameter into (known, unknown)
    keys, order kept, duplicates dropped, capped at ``limit``."""
    known_set = set(known)
    seen: set[str] = set()
    ok: list[str] = []
    bad: list[str] = []
    for k in str(raw or "").split(","):
        k = k.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        (ok if k in known_set else bad).append(k)
        if len(ok) + len(bad) >= limit:
            break
    return ok, bad
