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
