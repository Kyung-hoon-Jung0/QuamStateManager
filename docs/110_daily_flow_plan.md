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


## The integration audit (41 agents, 4 lenses × adversarial verify)

37 findings, 15 confirmed and fixed. The lens that earned its keep was
**cross-feature** — every confirmed defect lived in the seam between two
streams or between a stream and the app it landed in, which is exactly
what per-stream pins cannot see:

- **`/load`'s failure panel never reached the DOM.** htmx drops 4xx
  bodies unless a `beforeSwap` allowance permits the target; `#table-pane`
  was not on that list. The whole docs/114 #1 feature was invisible and
  the user still got the vanishing toast it replaced. Now allowed —
  narrowly, only for a 400 carrying that panel.
- **`live_readonly` repeated the beacon bug exactly.** Like
  `mutation_seq` before it, the flag was stamped by `_render_tray` but
  not by `_ctx()`, so the 🔒 never appeared on the render that FOLLOWS
  opening a chip. Both are now in `_ctx()`, both pinned on a full page.
- **The read-only probe was blind to its own use case.**
  `os.access(dir, W_OK)` on Windows inspects only
  `FILE_ATTRIBUTE_READONLY` — meaningless for directories, blind to
  share/ACL denial — so it reported a read-only lab share as writable.
  Replaced with a real create+delete probe, re-run on every activation
  (a cached chip kept the first answer forever).
- **The permission hint was attached to the wrong failure.** It sat on
  the working-copy save, which never touches the live folder; the
  read-only case fails in `apply_to_live`. Moved to both live-write
  handlers, and the working-copy message no longer implies "live".
- **The landing CTA dead-ended** (`openFolderBrowser()` with no target
  input: Select filled nothing, submitted nothing) — the very first
  action docs/115 added for a first-day student.
- **The "↻ Newest" chip landed in the sidebar** — `.ds-search-wrap`
  names two elements, and `querySelector` took the first.
- **Keyboard collisions:** `j`/`k` claimed Enter/Space away from focused
  buttons and kept firing under the `?` sheet; `/` and `?` fired over
  open modals, stacking a second focus trap so one Escape closed both;
  Ctrl+Enter in a grid cell fired BOTH the row apply and Apply-all.
- **Honesty:** the drift poll stamped "last checked" even when the poll
  FAILED; the digest's "(filtered set)" contradicted the count beside it
  and could restore a band captured on a previous render.
- **Layout/affordance minors:** the teaching line claimed a full flex row
  in the topbar and pushed the Apply bar down on every page; the chip
  overlapped the search-help button; the 🔒 was an unlabelled glyph; the
  Help link rendered underlined among its button siblings; a selection
  had no count, no anchor mark and no key hints (and Ctrl+D outside the
  table reached the browser's bookmark dialog).

Independently, the campaign's own pins caught the merge dropping the
whole docs/114 CSS block, and the real-browser pass caught keep-alive
never firing. Three different mechanisms, three different classes of
defect — none of which a unit test alone would have found.


## Pre-customer-site audit (2026-08-11)

Before taking this build to a customer, a 32-agent audit was scoped to the
five flows they would actually run — load a state · switch project · Generate
/ Re-generate Config · modify values · Live State Edit — and every flow was
ALSO run by hand against the real 21-qubit chip and the machine's real
QUAlibrate projects. 27 findings, 16 confirmed. What mattered:

**Two that would have touched the customer's own files or lost their work.**

- The docs/114 read-only hint was probing by CREATING AND DELETING a file
  inside the live chip folder on every activation. That breaks docs/28 (the
  live files are touched only on an explicit Apply), litters a directory
  labs keep under version control, and leaves the file behind if the process
  dies mid-probe. It now opens the EXISTING `state.json` for update (`r+`)
  and closes it — the same permission an apply needs, nothing created, no
  content/size/mtime changed. Pinned by a test that asserts the directory
  listing and `state.json`'s stat are byte-identical across a probe.
- A mistyped folder path WIPED THE MAIN PANE. The sidebar's State Load form
  is always mounted and hard-targeted `#table-pane`, so docs/114's failure
  panel replaced whatever was open — including an in-progress wizard, with
  no prompt and no draft. The panel now lands in a dedicated sidebar slot;
  verified live that an open grid survives a bad path.

**Three that were plainly visible.**

- On a real chip EVERY pointer rendered red "DANGLING" (26/26, 61/61, 25/25
  measured). The mark was decided by `value == raw` — but that builder never
  resolved pointers at all, which is also the original reason its tooltip
  read "Resolves to: <the pointer itself>". Dangling is now a resolution
  FAILURE, computed in all three row builders; where a row has no anchor to
  resolve from, the tooltip says so rather than claiming a resolution.
  After: 0 false dangling, all 26 badges intact.
- Datasets `j`/`k` was DEAD on every page, and `/` and `?` died permanently
  once any overlay had opened — both guards tested attributes while
  `base.html` always renders `role="dialog"` nodes and the app closes
  overlays with `display:none`. One shared visibility test now.
- Three new global keydown handlers cost **2.34 ms per keystroke app-wide**
  (measured in a real browser on a 4,851-cell grid) because each queried the
  DOM before checking whether the key was even theirs. Key-first: **0.005 ms**
  (468x), feature behaviour re-verified live.

Also fixed: the carry's leave-confirm carve-out could stay armed if the
reload never landed (a silent-discard window); a blank main pane after
browser Back on a kept-alive route; a stale selection anchor across a grid
re-render; and fill/paste skipping the f_01<->RF coupling the manual edit
path applies. One regression this batch introduced — an indentation slip
that dropped the pair CR/ZZ builder's rows out of their loop — was caught by
`test_web`'s `cz_unipolar` pin before it left the branch.
