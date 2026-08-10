# 110 — Daily-flow campaign plan: Tier 3 + Tier 4, all of it (docs/104 #10–#16)

*2026-08-10. The user approved the ENTIRE remaining docs/104 backlog in one
discussion, with one decisive reframe and one gate. This is the binding plan;
each workstream's implementation doc records its own decisions.*

## The #10 reframe (user, verbatim intent)

> 제발 다른 탭에 가도 이전 탭으로 돌아갔을 때, 그 상태 그대로 유지해주세요.
> 예를 들어, json tree view에서 특정 검색해서 보다가 다른 탭 들어가고 다시
> json tree 가면 초기화되어있음!

What users beg for is not a split screen — it is **surface-state
preservation across navigation**. Every main-pane navigation currently
destroys the previous surface wholesale (search text, expanded tree nodes,
scroll, half-typed input). So #10 ships in two phases:

- **#10-A (NOW)**: per-pane DOM keep-alive with an honesty gate — on
  navigation the outgoing pane's DOM is parked (detached, keyed by route);
  returning re-attaches it instead of re-fetching, restoring EVERYTHING for
  free. The gate: if `store.mutation_seq` (or the relevant staleness token)
  moved since parking, the pane is REFETCHED, never restored stale — the
  docs/28/87 honesty doctrine applied to UI state.
- **#10-B (deferred decision)**: side-by-side / run pin-bar. Re-evaluate
  AFTER #10-A lands — preserved state halves the pain of pane-swapping, and
  the layout surgery may no longer be worth it.

## Workstreams (each its own branch off main, integrate at the end)

1. **`feat/pane-state` — #10-A** (M): the keep-alive registry in app.js;
   Explorer (Json Tree) is the proving surface (the named complaint), then
   the registry opts in the other heavy surfaces (Datasets detail, bulk
   grids, Param History). Workflow AUDIT immediately after this stream —
   it is the architectural risk of the campaign (htmx re-init, duplicate
   IDs, event rebinding, memory bounds, staleness).
2. **`feat/grid-editing` — #11** (L): fill-down, paste-a-column,
   multi-select, column/row pinning, dyn-column toggle without a
   full-grid reload and without threatening unsaved edits.
3. **`feat/datasets-flow` — #12** (S–M): j/k/Enter/Space keyboard nav,
   an "↻ Newest" affordance when the restored sort buries today, digest
   band follows the filter.
4. **`feat/keyboard-polish` — #13** (M, each item gated on LOW overhead —
   the user's condition): `/` focus-search, Ctrl+Enter apply-all, `?`
   cheat-sheet, ndview per-run view-state memory, banner group-collapse,
   staleness "last checked Ns ago".
5. **`fix/honest-failures` — #15 + #16(preflight/anchor/clock)** (M):
   non-chip folder → inline explanation (not a vanishing toast), folder
   browser checks before submitting, dangling pointers say "dangling —
   target missing" instead of a tooltip that lies, write-permission
   preflight BEFORE 20 minutes of edits, the /chip-name/set banner-anchor
   bug, value-history 🕘 visible at rest + focusable.
6. **`feat/onboarding` — #14 + README** (M): landing "Open a folder" CTA,
   `/help` + working-copy glossary, tray teaching for fresh users, README
   with screenshots that teaches the model.
7. **integrate**: merge all → Workflow dual-lens audit (coding + user-UX,
   docs/109-stage-2 strength) → full suite vs the environmental baseline →
   real-browser pass on a real chip → report → push.

## Doctrine constraints that bind every stream

- Honesty first: preserved state must never present stale data as fresh
  (the mutation_seq gate); a blank/unknown is always honest.
- SM is additive/extension-shaped: every stream must leave existing chips
  and workflows byte-identical where not opted in.
- No new pollers; ride existing events. No live-file reads on render
  (docs/28). All colors via tokens; SM global styles only.
- Every stream lands with pins (pytest + cjs selfchecks where DOM-level).


---

## Campaign record (2026-08-11) — all seven workstreams shipped

| # | Stream | Doc | Branch |
|---|--------|-----|--------|
| #10-A | tab state survives navigation | docs/110 | `feat/pane-state` |
| #11 | grid editing toolkit | docs/111 | `feat/grid-editing` |
| #12 | datasets daily flow | docs/112 | `feat/datasets-flow` |
| #13 | keyboard polish (overhead-gated) | docs/113 | `feat/keyboard-polish` |
| #15+#16 | failures that explain themselves | docs/114 | `fix/honest-failures` |
| #14 + README | the first hour | docs/115 | `feat/onboarding` |
| — | integration + cross-feature audit | this file | `integrate/daily-flow` |

**Audits ran at every step, as instructed.** The PaneState audit (24
findings) rewrote that stream's architecture before it could spread —
v1 intercepted `htmx:beforeRequest`, which poisoned htmx's own history
snapshot, bypassed its `pushState`, and left a blank pane whenever a
request failed; v2 parks at `beforeSwap` and restores at `afterSwap`,
letting htmx own navigation completely. The grid-editing audit (22
findings, verify pass cut short by a provider limit → triaged by hand)
produced 14 real fixes, each pinned.

**The integration pass earned its keep twice.** Two defects no unit pin
could see: (1) the freshness beacon disagreed with itself — the OOB tray
stamped `mutation_seq` but a full-page render did not, so PaneState read
every ordinary page load as "the chip moved" and **keep-alive never
fired in the real app** (only the SOFT tier did, which is exactly why it
still looked like it worked); (2) the sidebar Help entry rendered a bare
`?` beside two SVG-iconed neighbours. Both fixed and pinned.

## #10-B (side-by-side / run pin-bar) — still deferred, now with evidence

The original finding asked for split panes. #10-A was shipped first
because the user's own words named state loss, not layout. That call
holds: with panes preserved, "go look at the other thing and come back"
costs nothing, which is most of what a split view was for. Revisit only
if the request returns after living with this.
