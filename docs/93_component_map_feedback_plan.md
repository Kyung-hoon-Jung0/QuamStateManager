# 93 — Component-map feedback round 1 (PLAN, not shipped) + main audit

*Status: **PLAN ONLY** — written 2026-08-09 from five user-relayed feedback
items on the docs/92 component maps, plus an independent audit of the three
commits that just landed on main (`8e5fa99..37e7e25`). Every item is
investigated to root cause and the approach is decided; implement as written.
House rules that keep applying: docs/91 §2.4 (component map carries NO
numbers), §3.4 (colour = selected/not-selected... amended by item 4 below,
see §2.4 here), the standing screenshot rule (real-browser screenshots into
`D:\work\sm-screenshots\`, never committed).*

---

## 0. The five reports

> 1. Component 다이어그램 스타일은 좋은데 **크기를 1.8~2.0배** 키워달라.
> 2. Chain A를 누르면 **다른 chain 버튼이 사라진다**. 고치고, 선택한 chain의
>    큐빗을 맵에서 highlight하자.
> 3. Pairs 화면의 edge에 **부등호(>, <) 모양**으로 양쪽 큐빗의 주파수
>    대소를 보여달라 — 요청 매우 많음.
> 4. Resonator의 **feedline마다 서로 다른 색** — modern하게, naive한 색 금지.
> 5. Flux/Coupler에는 pair처럼 **chain A/B 탭이 필요 없다**.

## 1. Findings (root causes, all verified in code)

### 1.1 Chain buttons disappear (item 2) — a two-site ordering bug

`routes.py` computes the `chains` list **after** applying the chain filter, so
filtering to A leaves `chains = {A}` and the template (which renders one button
per entry) drops B/C/D:

- `qubits()` — filter at 3762-3763, `chains` at 3765 (wrong order);
- `_channel_scoped_qubits_page()` (SHARED by /resonators and /flux) — filter
  at 3809-3810, `chains` at 3812 (same bug).

The repo already contains the correct idiom: the `/table` route computes
`chains` from ALL rows at 7022-7025 **before** filtering at 7028. The fix is
literally moving one line up in two places to match it. `_pulses.html` also
has chain tabs but its route is unaffected (different code path — verify with
a pin, don't touch).

### 1.2 Sizes (item 1)

`renderLayout`: `CELL = opts.cell || 64`, stone `R = 0.30·CELL ≈ 19px`,
resonator mark `0.42·R`, flux stub to `1.5·R`. Text is **CSS-fixed**
(`.cm-id` 10.5px, style.css) — a bare CELL bump would grow geometry and leave
tiny text. Width budget at 2×: 21Q physical (5 cols) ≈ 600px, 17Q logical
(6.68 cols) ≈ 802px — both fine, `.cm-scroll` already handles narrow panes.

### 1.3 Frequency inequality on pair edges (item 3)

Data is already client-side: `renderLayout` receives full get_topology nodes
including resolved `f_01`. Constraints found: CR chips carry BOTH directions
as separate edges (draw the glyph once per physical edge); the coupler dot
already occupies the midpoint; missing/non-numeric `f_01` is common on fresh
chips (glyph must be omissible); docs/91 §2.4 forbids numbers ON the map
(tooltips are fine — they already carry port labels).

### 1.4 Feedline colours (item 4)

Currently every feedline bus is `--pico-primary` when highlighted — one hue
for all. Real chips have 2–4 feedlines (the verified 21Q: 4 buses of
6+5+5+5; the 17Q: 3). SM's accent is blue in BOTH themes (light `#0172ad`,
dark `#01aaff`), so blue must stay OUT of the feedline palette — a blue
feedline would impersonate the selection/hover accent. Surfaces measured from
the real app: light `#ffffff`, dark `#181c25`.

### 1.5 Chain tabs on flux (item 5)

`_flux.html` renders chain tabs via the shared helper; `_pairs.html` and
`_couplers.html` never had them. Flux rows are qubits, but the user is right
that chain slicing has no workflow there. Removal is template-only (the
shared route helper keeps handling the param harmlessly; `chains` simply goes
unrendered).

## 2. Decided approaches

### 2.1 Item 2 — fix + chain emphasis on the map

- Move the `chains` computation above the filter in `qubits()` and
  `_channel_scoped_qubits_page()` (mirror `/table`). Pin: with `?chain=A`,
  the response still renders a button per chain incl. All.
- **Chain emphasis**: `_component_map.html` gains
  `data-chain="{{ active_chain or '' }}"`; `ComponentMap.mount` passes it to
  `renderLayout` as `opts.emphasisChain`; `renderLayout` (which already gets
  `chain` on every node) stamps `data-cm-chain="<chain>"` on each stone group
  and, when `emphasisChain` is set, adds `cm-chain-dim` to non-matching
  stones. CSS: matching stones keep full opacity + accent ring; non-matching
  drop to the dimmed tier. Emphasis composes WITH the page highlight (a
  chain-filtered Qubits page shows chain-A stones hot, everything else
  context). No new fetch, no server change beyond the attr.

### 2.2 Item 1 — size

- `_component_map.html` mounts with `cell: 120` (1.875×, inside the asked
  1.8–2.0) — passed as an option, hero map untouched (its own cell stays 96).
- **Text scales with the cell**: `renderLayout` writes `font-size` as an SVG
  attribute `Math.round(CELL * 0.165)` (≈ 20px at 120; ≈ 10.5px at 64 —
  byte-compatible with today's CSS at the old cell). `.cm-id`'s CSS size
  stays as fallback. Same treatment for the (new) legend swatch text if any
  sizing issue appears — legend is HTML, normal font sizes.
- Everything else already scales off CELL/R (marks, stubs, coincident
  fan-out, hit-lines). Verify by screenshot on the 21Q + 17Q chips, both
  themes.

### 2.3 Item 3 — the frequency-inequality chevron

- **One glyph per PHYSICAL edge** (dedupe anti-parallel CR duplicates by the
  sorted endpoint pair), drawn inside the edge group so it dims/hots with the
  pairs layer.
- Geometry: a double-chevron (two `»` strokes) at the edge midpoint, offset
  **perpendicular** by `0.16·CELL` (clear of the coupler dot and CR arrows),
  rotated to the edge axis. **The apex points at the LOWER-f_01 qubit; the
  open side faces the higher** — reading along the edge gives
  `f(qHigh) > f(qLow)`, the inequality the users asked for.
- Honesty gates: no glyph unless BOTH endpoints have numeric `f_01`
  (never a guess); no glyph when `|Δf_01| < 1 MHz` (below any physical
  relevance for this read); tooltip carries the exact signed Δ
  (`qA4 f_01 +212.4 MHz vs qA1`) — numbers stay OFF the drawing (§2.4).
- Note the CZ-orientation interplay: on CZ chips control is usually the
  higher-f qubit (docs/53 czAutoOrient), so chevrons will often agree with
  control→target — but they are INDEPENDENT reads (CR arrows = drive
  direction; chevron = spectrum order) and must both render.
- Pins: chevron count == physical-edge count with both-f known; orientation
  == sign(f_source − f_target) exercised BOTH ways; absent when f missing;
  dedupe on a CR both-directions fixture; still no `<text>` beyond ids.

### 2.4 Item 4 — per-feedline categorical colours (validated, not eyeballed)

This amends docs/91 §3.4 for exactly ONE surface: on the Resonators
highlight, colour additionally encodes **feedline identity** (a categorical
job). Everywhere else colour still means selected/not.

- **Palette** (computed with the dataviz validator against SM's real
  surfaces; the naive first candidate FAILED dark-mode CVD at ΔE 1.6 —
  magenta beside green — which is why these exact slots and this exact ORDER
  are load-bearing):

  | slot | hue | light (`#ffffff`) | dark (`#181c25`) |
  |---|---|---|---|
  | 1 | orange | `#eb6834` | `#d95926` |
  | 2 | aqua | `#1baf7a` | `#199e70` |
  | 3 | yellow | `#eda100` | `#c98500` |
  | 4 | magenta | `#e87ba4` | `#d55181` |
  | 5 | green | `#008300` | `#008300` |
  | 6 | violet | `#4a3aa7` | `#9085e9` |
  | 7 | red | `#e34948` | `#e66767` |

  Adjacent-pair gates PASS in both modes (worst CVD ΔE 9.1 light / 8.4 dark,
  ≥ 8 target; normal-vision 19.6/19.3, ≥ 15). Blue is deliberately absent
  (accent collision, §1.4). Ship as CSS vars `--cm-feed-1..7` with light +
  dark values.
- **Assignment = spatial order**: feedlines take slots in their on-screen
  order along the dominant axis (the order `renderLayout` already sorts bus
  points by). Palette-adjacency then coincides with screen-adjacency, which
  is what the adjacent-pair gate certifies. Assignment is per-render and
  stable for a given chip (derived from rr_port-sorted-by-position, not from
  render order).
- **Secondary encoding is mandatory** (all-pairs past 3 slots cannot clear
  the floors — measured): each slot also gets a distinct dash pattern
  (e.g. `6 3` / `2 3` / `8 3 2 3` / `1 3` …) so hue never carries identity
  alone; the feedline's resonator MARKS wear the same hue (location
  redundancy); and a **legend row** appears under the map on the Resonators
  page — one swatch + port label per feedline (`● con1/fem1/p1 · 6
  resonators`). The legend doubles as the relief the light-mode contrast
  WARN obligates (aqua/yellow/magenta sit below 3:1 on white).
- 8+ feedlines (unseen in real data): remaining groups render neutral gray
  with unique dashes and legend entries — **never cycle hues**.
- Buses thicken slightly when coloured (2 → 2.5px) for the light surface.

### 2.5 Item 5 — flux drops its chain tabs

Delete the `<nav class="chain-tabs">` block from `_flux.html` only. Route
untouched (param stays harmless; deep links keep working). Qubits +
Resonators KEEP their tabs (fixed by 2.1). Couplers/Pairs already have none.
Pin: /flux body contains no chain-tabs; /qubits and /resonators still do.

## 3. Audit — what just landed on main (`8e5fa99..37e7e25`)

Three commits (the runner line's prefix, merged 2026-08-09 to carry the flux
axis fix): docs/78 design doc, P0 foundation, P1 axis fix. 17 files,
+4,146/−77. Independent findings:

1. **The new P0 modules are DORMANT on main.** Precise import-grep: nothing
   outside the runner's own files imports `autofit/{corpus, envmatrix,
   figure_gen, sourceroot}` or the new generator scripts. Blast radius to
   current users: none until the runner's later phases land.
2. **`registry.py` change is additive and correctly ordered** — the new
   coupler-flux recipe registers BEFORE `qubit_spec_vs_flux`, so the more
   specific node name can never be swallowed by the generic matcher.
3. **The axis fix itself re-verified here on live data**: all four
   user-named runs (nodes 06/07/09 across two archives) now render
   X = flux / Y = frequency through this worktree; before the merge the same
   script showed the swapped axes. The 458-line parity test runs green in
   the merged tree.
4. **`fit_audit.py`/`run_fit_audit.py` (the one existing user surface
   touched)**: family registry 2 → 9 with alias coverage (prevents runs
   silently dropping from the backlog), a bool-guard in `_codify`, and an
   honest unit fix (a 12 mV flux drift used to print as "12.00 mHz").
   Coherent and tested (`test_fit_audit` updated; suite green).
5. **Watch item (not a defect)**: the vs_power family's util module renamed
   `resonator_spectroscopy_vs_power_iq` → plain, following the
   source-of-truth node tree. Replaying against an OLD utils tree that only
   ships the `_iq` module would fail that family's replay. Node-NAME aliases
   cover old archives; the MODULE dependency is on the tree Fit Replay is
   pointed at. Surface it in docs if a lab reports it.
6. **The docs/78 numbering collision is now LIVE on main**
   (`78_runner_agent.md` landed; our chain's `78_type_alert_popup.md` is
   still pending in `feat/multi-instance-safety`). Decide the renumber
   before or at the audit chain's merge — docs/90 §6.6 has the options.
7. Integration evidence: merged-tree full suite = **14 failed / 4,913
   passed / 243 skipped** — exactly the environmental baseline, +108 passes
   = the prefix's own tests.

Verdict: **safe as merged**; two follow-ups tracked (items 5 and 6).

## 4. Phasing (each independently shippable)

- **F1 — the two-line chain fix + flux tab removal** (items 2-fix + 5).
  Smallest, pure bug-fix + deletion. Server pins only.
- **F2 — size bump + text scaling** (item 1). One option + one attr change;
  screenshot verification both themes.
- **F3 — chain emphasis on the map** (item 2-highlight). Render-input only;
  jsdom pin + screenshots.
- **F4 — feedline colours + legend + dashes** (item 4). CSS vars + assignment
  + legend row; jsdom pins (assignment stability, no-cycling, legend
  presence) + screenshots on the 4-feedline 21Q chip, both themes.
- **F5 — frequency chevrons** (item 3). The only new geometry; full pin set
  from §2.3 + screenshots on a CZ chip AND a CR chip.

Every phase ends with real-browser screenshots into
`D:\work\sm-screenshots\<date>_<topic>\` (standing rule) — items 1/3/4 are
exactly the kind of visual claims jsdom cannot carry.

## 5. What NOT to do

- No numbers on the component map — the chevron is a symbol, its number
  lives in the tooltip (docs/91 §2.4 stands).
- No hue cycling past the palette; no blue feedline (accent collision).
- Do not touch the hero map's size or the Chip Status surfaces.
- Do not remove chain tabs from Qubits/Resonators (only flux loses them).
- Do not re-order or re-step the palette without re-running the validator
  against BOTH SM surfaces — the first candidate looked fine and failed
  dark-mode CVD at ΔE 1.6.
