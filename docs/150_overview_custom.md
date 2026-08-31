# docs/150 — Overview panels become customizable, without touching a single formula (2026-09-01, customer via user)

Customer proposal, relayed with a scope ruling: *"각 panel의 오른쪽 상단에
햄버거/점3개 버튼으로 즉석에서 key나 value를 정하고, panel 추가/삭제/정정 —
단, avg·med 등 통계 수식 자체는 건드리지 않는다"*. Judged feasible first
(the tiles were already (metric key × aggregate × label) declarations over
one `computeAggregates`), then approved for v1.

## What shipped (v1 — display preferences ONLY)

Every Overview tile gets a stable `id` and a hover-visible **⋮ kebab**
(top-right). The popover it opens depends on what the tile IS:

- **Metric-backed tiles** (1Q/RO fidelities, 2Q gate fid., gate length, the
  four SRB/IRB tiles, T1, T2): **Statistic** select — which aggregate the
  BIG number shows: `avg` (the default since the same-day r2, user-directed;
  it was `median`), `median`, `min`, `max` — plus **Remove**. EVERY big
  number wears its stat tag (`24.0 µs AVG`, `MED`, …) — r2 also made the
  tag unconditional, an untagged number was ambiguous — and the sub line
  always states the complementary aggregates, so nothing hides. The EPC/EPG
  error lines follow the displayed stat (1 minus the number shown). The
  numbers are the SAME `computeAggregates` outputs the tile always
  computed — no new math.
- **Composite tiles** (Chip Size, RB Coverage, Qubits In Spec, Calibration
  Age): **Remove only**, with a note saying why key/statistic can't change
  ("its number is not one metric").
- **User-added tiles**: Metric + Statistic + Remove. The **"+ Add panel"**
  ghost tile at the end opens the same popover; the metric list offers ONLY
  the chip's real metric-record keys (+ `cz_fidelity`) — no free-text key
  typos. A key with no known calibration range gets a neutral accent (never
  an invented verdict); a key with no values renders the honest muted
  "no data" tile, never silently nothing (the docs/94/148 rule).

**Persistence**: `localStorage["quam_overview_tiles_v1"]` =
`{removed: [ids], stats: {id: stat}, added: [{key, stat}]}` — written ONLY
when deviating from the defaults, removed again when the deviation ends.
An **honest indicator** — "customized · reset" — appears beside the
Overview title whenever preferences are active; reset (also in every
popover as "Reset all") restores the defaults in one click and clears the
stored key. Preferences survive re-renders and full re-mounts.

## v2 follow-ups (recorded here, deliberately NOT built)

Per the user's scope ruling these are the follow-up plan, in rough order of
value:

1. **Drag re-ordering** of tiles (persist an `order` list in the same key).
2. **Server-side persistence & sharing** — the layout as part of a chip's
   instance data so a team shares one Overview (needs a write path + the
   docs/55 no-conflict doctrine review).
3. **Custom expression tiles** — a user-defined formula over metric keys
   (e.g. `T2echo/2/T1`). Hardest to keep honest: needs a sandboxed
   evaluator, unit handling, and an explicit "user formula" provenance tag
   so a derived number is never mistaken for a measured one.

## Verification

`overview_custom_selfcheck.cjs` (C1–C5, real app.js+topo-graph+chip-status
under jsdom): defaults store nothing; stat override switches/tags/persists;
composite remove-only; added-tile key list = real keys; value-less added
tile renders muted; reset clears; preferences survive a re-mount. Mutations
red ×4 (removed-filter ignored, stat ignored, persistence dropped, added
tiles dropped). CDP end-to-end on a real served chip: 15 tiles/15 kebabs,
stat override → "24.0 µs avg" + "med …" sub, Chip Size removed, a panel
added over `ro_fidelity_gef_f` (the docs/148b metric) rendering 85.00%,
stored JSON exact, reset restores everything and clears the key. Existing
`chip_status_hero_selfcheck` / `chip_density_selfcheck` / layout + hero
pytest drivers stay green.
