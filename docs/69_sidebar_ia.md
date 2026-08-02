# 69 — Sidebar IA rework (r15 feedback ①, 2026-08-02)

User feedback batch ("메인 메뉴 List 피드백"): the sidebar had grown item-by-item
into a flat 15-row chip block with no grouping, the fit/autofit/scheduler trio
confused users, and the entity pages were believed to hide non-active qubits.
This doc records the new IA, the invariants behind it, and what deliberately did
NOT change.

## New order (structure → health)

```
Projects ▾                     (unchanged, docs/63)
[State Load]
──────────────────────────────
Generate Config ▾              (Config Viewer, Re-generate — unchanged)
──────────────────────────────
Instrument Wiring
Chip Components ▾              #chip-components-subnav, default OPEN
    Qubits / Pairs / Resonators* / Flux* / Couplers*     (*has_* conditionals)
Diagnostics
Chip Status ▾                  (8 scroll-spy views — unchanged)
Compare
Live State Edit ▾              #live-edit-subnav, default collapsed
    Json Tree View (= /explorer) / Pulses
State History ▾                #state-history-subnav, default collapsed
    Param History
Experiment Runner              (= /scheduler)
Fit Replay                     (= /fit-audit)
Auto Calibrate                 (= /autofit)
──────────────────────────────
Datasets ▾                     #datasets-subnav, default collapsed
    Collections / Trends
[workspace tree]
```

Rationale for the placement (user decision 2026-08-02): Instrument Wiring +
Chip Components describe what the chip **is** (structure), Diagnostics + Chip
Status describe how it's **doing** (health) — so the two pairs sit adjacent, in
that order, between Generate Config and Compare.

## Renames — display-only, everywhere

| Old label | New label | Route / page token (UNCHANGED) |
|---|---|---|
| Scheduler | **Experiment Runner** | `/scheduler`, `page="scheduler"` |
| Fit Audit | **Fit Replay** | `/fit-audit`, `page="fit-audit"` |
| Autofit | **Auto Calibrate** | `/autofit`, `page="autofit"` |
| Explorer | **Json Tree View** | `/explorer`, `page="explorer"` |

The rename sweep covers: sidebar, command palette, page `<h2>`s, the topbar
scheduler badge, the two edit-lock overlays (scheduler + autofit) in base.html,
the route 409 lock **messages** (the machine-readable `error` codes
`scheduler_running` / `autofit_running` are pinned by tests and unchanged), the
landing getting-started card, the type-alarm banner ("Show in Json Tree View"),
and Explorer-referencing tooltips. Internal identifiers (`SchedulerUI`,
`autofit-nav-badge`, `switchExplorerTab`, localStorage keys, docs filenames)
deliberately keep their names — this is a label change, not a refactor.

## Chip Components group

- Parent `<a>` navigates to `/qubits` but carries **no active class** — the
  Qubits child owns the highlight (avoids double-highlight).
- Resonators/Flux/Couplers keep their `has_resonator/has_flux/has_coupler`
  conditionals inside the subnav.
- The Pulses "Add pulse" subnav row was dropped (its `#pulses-subnav` +
  `quam_pulses_nav_collapsed` registry entry removed); the Pulses page's own
  "+ New pulse" button and `/pulses?create=1` remain the create entries.

## Subnav mechanics (unchanged pattern, 4 new registrations)

`SUBNAVS` (app.js) gains: `chip-components-subnav` (`quam_components_nav_collapsed`,
default open), `live-edit-subnav` (`quam_liveedit_nav_collapsed`),
`state-history-subnav` (`quam_statehistory_nav_collapsed`), `datasets-subnav`
(`quam_datasets_nav_collapsed`) — the last three default collapsed. Server
renders the collapse default page-conditionally (child active ⇒ open, no
flash); the registry's force-expand-on-active-child rule is generic and
applies to all of them.

## All-listed + active-marked (the "active" misconception)

Audit finding: **no SM surface ever filtered by `active_qubit_names` /
`active_qubit_pair_names`** — list pages, channel pages and both bulk grids
always iterated every entity. What was missing was the MARK, so users couldn't
tell actives apart (and assumed filtering). Now:

- `QueryEngine.get_qubit` gains `is_active` (membership in
  `active_qubit_names`; absent/empty list = everything active — QUAM
  semantics, mirroring `cr_semantics.is_active`). `get_pair` gains the same
  universal `is_active` (the CR-vocab channel-gated `active` key stays for
  back-compat).
- All five list pages (`_qubits`, `_pairs` both vocabs, `_resonators`,
  `_flux`, `_couplers`) grew an **Active** column (✓ / "off") and dim
  inactive rows via `tr.row-inactive` (opacity 0.62, full on hover — marked,
  never hidden). The CR pairs table's existing Active column now uses the
  universal flag.
- Qubit/pair detail headers show an `inactive-chip` when the entity is not in
  the active list.

## Bug fixes riding along

- `STATE_PAGES` (app.js) contained stale `"/instrument-wiring"` — the real
  route is `/instrument`, so the chip-swap soft-refresh silently skipped the
  Instrument Wiring page. Fixed; `/resonators`, `/flux`, `/couplers` added to
  the list too (they render chip state).
- Command palette completed: added Re-generate config, Resonators, Flux,
  Couplers, Diagnostics, Fit Replay, Auto Calibrate; fixed the "Qubit Pairs"
  label drift (sidebar says "Pairs"); renamed Explorer/trio entries.

## Pins

`tests/test_web.py::TestSidebarIAr15` (order, group memberships, SUBNAVS
registrations, display-only renames, STATE_PAGES fix, palette coverage),
`tests/test_web.py::TestActiveMarkingR15` (all-listed + marked, absent-list =
all-active, channel pages, detail chip), and the rewritten
`tests/test_pulses_routes.py::test_pulses_nested_under_live_edit_nav`.
`tests/test_project_scope.py`'s Projects-block order + byte-exact palette pin
were preserved unchanged.
