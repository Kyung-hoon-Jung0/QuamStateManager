# 192 — Chip Status, pressed the same way

Third surface of the stress round the user ordered (Pulses → Agent → **Chip
Status** → Live Edit + Json Tree). Same method: press what the page offers with a
real mouse and real keys, measure what it does, and believe the measurement over
the reading.

The page is `/topology` — "Chip Status" is its label, not its route. It is ONE
scrolling document: every section is present, the subnav scrolls to a section
rather than swapping a panel, and the later sections load lazily on scroll.

## 1. The inventory

Seven `.topo-section` elements at rest — `overview`, `health`, the hero map (no
`data-topo-section`), `trends`, `fidelity` (2Q), `fid1q`, `fidro` — growing to 32
nodes as lazy loading inserts nested sections. Measured by id, every one renders
its own content:

| section | renders |
|---|---|
| overview | 15 tiles, `⚙ Panels`, a kebab per tile |
| health | in-spec verdict, structural issues, `⚙ Thresholds` |
| hero map | 5 stones, 4 edges, a metric strip, zoom |
| trends | 30 sparklines, 3 Plotly figures |
| fidelity (2Q) | 2Q RB, per-gate panels, S/M/L |
| fid1q | 1Q Gate Fidelity — RB, avg 99.86% |
| fidro | Readout Fidelity (GE), avg 89.72% |

**Zero console or network complaints across the whole page.**

## 2. Three readings that were the harness, not the page

Recorded because the ratio matters as much as the findings:

- **"Four sections are empty."** They fill on scroll — the page lazy-loads. The
  first measurement read them all before any of them was in view.
- **"`fid1q` renders 2Q pair content."** The sections were addressed **by
  position**, and lazy loading grows the list from 7 to 32, so index 5 named a
  different section after the scroll than before it. The same trap as §13's
  `feed.cards[length-1]`. By id, `fid1q` renders 1Q content and `fidro` readout.
- **"The fidelity metrics do not repaint the map."** Pressed one at a time with
  proper settling, all four do: `gate_fidelity_avg` → 99.90 / 99.84 / …,
  `assignment_fidelity` → 92.25 / 88.48 / …, `T1` → 12.9 µs / …, `T2echo` →
  20.0 µs / …, and the numbers agree with the Overview tiles' averages. The first
  run pressed them in quick succession and read too early.

Also checked and sound: the metric buttons carry no class or `aria-pressed`, but
the active one IS marked — white at weight 600 against grey at 400. A check that
looked only for a class or an ARIA attribute would have called that a defect.

## 3. CS01 — the map's tip line taught a key that did nothing

The map states its own contract on screen:

> Tip: click a qubit to inspect it · double-click for its wiring JSON · hover for
> its parameters · **Tab into the grid, ←↑↓→ to move, Enter to inspect, Esc to
> close**

Pressed with real keys, all of it works except the last clause. Measured:

```
focused q1       focus=q1  inspector=   0
ArrowRight       focus=q2  inspector=   0
Enter            focus=q2  inspector=9781   'QUBIT q2 …'
Escape           focus=q2  inspector=9781   ← nothing
Escape again     focus=q2  inspector=9781   ← still nothing
the pane's own × focus=BODY inspector=   0
```

`onKey`'s Escape branch closes the hover popup and the JSON panel; `app.js` has
its own Escape ladder for the inspector, but it returns early unless the pane
holds one of the **Pulses** page's roots (`#pulse-detail-root`,
`#pulse-create-root`, `#gcz-root`). A qubit inspector opened from the map matches
neither, so Escape closed nothing at all while the sentence under the map
promised it would.

Fixed where the sentence lives: inside the grid, Escape closes the inspector that
Enter opened and returns focus to the cell, so the keyboard walk continues. It is
scoped to `[data-kbd-cell]` — page-wide Escape behaviour is unchanged — and the
hover popup still wins, being the more transient thing on screen.

Verified in Chrome: `Enter → 9,781 chars → Escape → 0`, focus still on q2, and a
hovered popup still closes first.

### The fixture could not reach the state

`hero_popup_selfcheck.cjs` built `<div id="topo-hero">` with no `.topo-dashboard`
wrapper, and `decorate()` queries `dash.querySelectorAll(...)` — so no node ever
received `data-kbd-cell` and the keyboard grid was unreachable in the harness.
Every keyboard pin would have been vacuous. The keyboard world builds the real
wrapper (docs/141 §4af: a GREEN means the fixture cannot reach that state).

Pinned by D1–D6; 5/5 mutations red, including "closes from anywhere" and "closes
an already-empty inspector", which are the two ways the fix could have been too
broad.

## 4. The rest of the page, pressed

| control | verdict |
|---|---|
| Overview: per-tile ⋮ → statistic | **sound** — see below |
| Overview: Remove / Reset all / prefs | **sound** |
| hero: metric strip (4 metrics) | **sound** — values follow, active marked by weight + colour |
| hero: zoom − + Fit Aa | **sound** — 2103 → 2412 → 2721 → 2412 → 1237 px; `Aa` is a font toggle, not zoom |
| per-panel S · M · L density | **sound** — `{"2q:StandardRB:cz_SNZ":0.7 → 1 → 0.85}` |
| Health: ⚙ Thresholds | **CS02**, below |

The Overview tile system holds under every press: switching a metric tile's
statistic gives the number it names (`median` → 99.86% MED, `min` → 99.84% MIN,
`max` → 99.90% MAX) with the complements on the sub-line; a non-default choice
persists across a reload as `{"stats":{"gate1q":"max"}}`; Remove persists **and
clears that tile's stale stat**; the page says "customized"; and Reset all puts
15 tiles back and elides the preference to `None` rather than storing an empty
object. A composite tile (`chip_size`) offers only "Remove panel", with a note —
which is docs/150's own rule, not a missing feature.

## 5. CS02 — a threshold typed but not applied looked applied

Commit in the thresholds editor is **explicit**: the "Update colour bands"
button, or Enter in a field. That is a fair choice. What was not fair:

```
type 70, press Tab  ->  the box reads 70
                        the hint reads "your lab's bands for 1 of 7 metrics …
                                        shared with everyone using this SM"
                        the server still holds 60
                        only a reload revealed it
```

Nothing distinguished a typed number from a saved one, while the hint underneath
described the **saved** state — so the screen asserted something untrue about a
setting that decides the in-spec verdict *for everyone using this SM*. Every
other explicit-commit surface in SM marks its pending state (the Review tray,
`bulk-cell-modified`, `data-committed` on inline inputs); this one did not.

Fixed the way the doctrine says rather than by committing on blur: each field
carries `data-saved`, "dirty" is a **comparison** against it (so typing back to
the saved value clears the mark by itself, with no flag to drift), the field is
marked, and the hint becomes *"1 threshold is typed but NOT applied — press
'Update colour bands'"*. Applying clears both and sends it.

Verified in Chrome: `70 → type 80 + Tab → dirty, server still 0.7 → Update →
saved 80.00, server 0.8`. The thresholds this round moved were reset afterwards
(`edited: []`).

### And one line of mine that never ran

The first cut also called the sweep after building the editor. The mutation sweep
stayed **green** when that call was deleted, which is the sweep doing its job:
`buildThresholdEditor` replaces the whole `innerHTML`, so every field returns with
`value === data-saved` and nothing can be dirty at that moment. The call was dead
on arrival and is gone, with the reason recorded beside where it was.

Pinned by `tests/thresh_dirty_selfcheck.cjs` (17 assertions — a new harness,
since nothing reached this editor before); 7/7 mutations red. The fixture needed
the real `nodes`/`edges` topo shape **and** real `defaultThresholds`: a thin one
makes `mount` bail before the threshold functions are even defined, and an empty
one builds an editor with no rows, so every pin would have been vacuous.
