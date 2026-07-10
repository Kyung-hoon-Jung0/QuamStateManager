# 39 · Workbench bridge — closing the Qualibrate ↔ State-Manager loop

The `/workbench` co-display (docs: see the `qualibrate-bridge-direction` memory)
puts Qualibrate and the State Manager (SM) side by side in one tab. This doc
covers the second half — **closing the loop** so a fit Qualibrate applies shows
up in SM as a reviewable **before/after**, and so a *silent no-op* (SM viewing a
different chip than Qualibrate writes) can never happen unnoticed.

Two user-facing features, plus the robustness around them:

1. **Before/after diff** — when Qualibrate writes the live state, SM shows *what
   changed* (working copy = before, Qualibrate's live = after), VS-Code-compare
   style, inline in the Explorer tree, with per-field **accept**.
2. **State-path matching** — SM detects whether the quam_state Qualibrate writes
   is the one SM has open. If not, a "fit" is a silent no-op; the workbench warns
   and offers a one-click "Load the chip Qualibrate is using".

## Why the loop wasn't closed

`GET /workbench/watch` resolves **Qualibrate's** active-project `state_path`
(`qualibrate_config.resolve_live_state_path`), while `/state/sync` and
`/state/review` operate on **SM's** loaded working copy (`ctx["path"]`). Nothing
compared the two. So a fit against a chip SM doesn't have open nudged (on
Qualibrate's path) but synced SM's unchanged path → no change, no warning. The
fix gates everything on a path-match verdict, so it ships first.

## The data layer is reuse, not new

Both features ride on infrastructure that already existed:

- `core/differ.py` `Differ().diff(store, (live_state, live_wiring))` already
  produces `old = working` / `new = live` over the merged raw dict — that *is*
  before/after. `/state/review` (HTML) has used it for the review overlay.
- `renderJsonTree(id, data, {refData})` in `app.js` already had a diff mode
  (`.tree-diff`, the `.tree-delta` pill, `refValue` threaded through lazy
  children). The Explorer just called it without `refData`.
- `openReview()` already shows a flat before/after table (`_state_review.html`:
  `Type | Path | Your copy | Live chip`).

So the new code is thin: a JSON sibling endpoint, a tree "livediff" mode, a
path-match module, and the workbench wiring.

## Components

### `core/path_match.py` — the verdict (P0)

Pure, unit-tested. `verdict(qb_path, sm_path, *, qb_reason) -> {"state", ...}`:

| state                     | meaning                                            |
| ------------------------- | -------------------------------------------------- |
| `linked`                  | same physical folder (`os.path.samefile`)          |
| `linked-different-folder` | same chip, different folder (a per-experiment copy)|
| `mismatch`                | a genuinely different chip                          |
| `qb-unresolved`           | Qualibrate's path couldn't be resolved (+`reason`) |
| `sm-empty`                | no chip loaded in SM                                |
| `indeterminate`           | folders differ and a fingerprint was unreadable    |

**Single-OS assumption** (confirmed with the user): people pick one path
convention (Windows *or* macOS) and never mix WSL and Windows forms — WSL is only
Claude's dev harness. So matching is a **same-namespace compare**:
`os.path.samefile` (ground truth) → `os.path.normcase(resolve())` fallback. No
cross-OS `/mnt/d`↔`D:\` translation in product code. Chip fingerprints
(`history.fingerprint_of` / `align`) are used *only* to tell
`linked-different-folder` from a real `mismatch`.

`chip_label(path)` gives the short bar name: the folder's own basename unless
it's a generic container (`quam_state`/`quam_states`/…), in which case it falls
back to `history.chip_name_for` (so `quam_states/LabA` → `LabA`, and
`Soprano/quam_state` → `Soprano`).

### `GET /workbench/match` (P0)

Wraps `path_match.verdict` over `resolve_live_state_path()` (Qualibrate) and
`_active_path()` (SM). Returns `{state, reason, qb_path, sm_path, qb_name,
sm_name, loadable, load_path}`. `loadable` is true when the Qualibrate dir has a
`state.json` SM can load.

### `GET /state/live-diff` (P1)

The JSON sibling of `/state/review`: same `Differ().diff(store, read_live(wc))`,
returned as `{ok, total, summary, entries:[{dot_path, old, new, change_type}]}`.
`?with_live=1` adds the raw `live_state`/`live_wiring` dicts for the tree's
`refData`. Drives two things:

- the **content-aware nudge** — only fire when `total > 0` (a touch that doesn't
  change any value reports 0, so a no-op save doesn't nudge).
- the **inline Explorer diff** — `with_live=1` ships the live dicts so the tree
  can paint per-field markers.

### Workbench wiring (`workbench.html`)

- **Second bar row** (`#wb-link`): a `✓ linked / ⚠ mismatch / ◍ …` badge plus
  `Qualibrate ▸ <name>` and `State Manager ▸ <name>` chips (full path on hover).
  On `mismatch` + `loadable`, a **"⤵ Load &lt;name&gt; in SM"** button does a
  context switch (`POST /load`) — pending edits on the old chip survive (per-
  folder working copy), then re-polls the match.
- **Nudge** (`#wb-nudge`): "Qualibrate changed **N** field(s) — [Show changes]".
  Gated on the match (`matchLinked`) so a fit on the wrong chip never nudges.
- **Smart show**: when SM is on screen (Split/State-Manager view), the diff
  auto-opens; when Qualibrate is full-screen (`view==='qb'`), the banner waits
  with a "Show changes" button so you're not yanked away. "Show changes" calls
  the SM iframe's `showLiveDiffInline()` (inline Explorer diff, falling back to
  the flat review overlay on any non-Explorer page).

### Inline Explorer diff (`app.js` + `_explorer.html`, P2)

A **"⇄ Live diff"** toggle in the Explorer toolbar fetches `/state/live-diff?
with_live=1` and re-renders the state/wiring trees with `refData` + a new
`valueClick: "livediff"` mode. Per changed leaf:

- `working → live` is shown inline (green `.tree-row-incoming`), with the numeric
  delta read **after − before** (Qualibrate's change direction).
- **✓ accept** posts the raw live value through `/field/edit-batch` (JSON, no
  string round-trip) → the row becomes a normal pending working-copy edit
  (`.tree-row-pending`); the usual **Apply to live** then writes it.
- **✗ keep** just drops the incoming markers (the working copy already holds
  your value).
- **Accept all** sends every remaining change in one atomic batch.

Changed paths auto-expand; collapsed ancestors show a "**N changed**" roll-up
pill. The walk that finds changed leaves (`_collectDiffPairs`) is bounded by the
diff size, not the tree size. Exiting the mode soft-reloads `/explorer` so the
trees reflect any accepted (now-pending) edits.

Per the working-copy model (`docs/28`): **accept never writes the live chip** —
it stages a pending edit you then Apply. We trust researcher input; nothing is
auto-clobbered.

## Robustness (P3)

The watch is a small **state machine** built for a user cycling through many
chips (`pollWatch` in `workbench.html`):

- **`lastPath`** — when Qualibrate switches projects/chips the watched dir
  changes; we rebase the mtime baseline silently (don't nudge the switch itself;
  `pollMatch` re-evaluates the link).
- **`≠`, not `>`** — a reverted or clock-skewed write still registers.
- **settle-debounce (`pendingMtime`)** — a change must hold steady for one poll
  before we read content, so we never read mid-burst during Qualibrate's
  non-atomic `state.json`+`wiring.json` write. (`safe_io.read_state_wiring`
  already armors single-file torn reads; the settle covers the cross-file case.)
- **in-flight guard** — overlapping live-diff reads can't stack.
- **watch-lost** — three consecutive `/workbench/watch` failures surface
  "⚠ watch lost" instead of failing silently; a vanished/unreadable live folder
  surfaces its error in the watch indicator.

## Perf (P4)

- The common case (SM and Qualibrate on the **same** folder) resolves to `linked`
  via two `stat` calls — **no fingerprint read**. Fingerprints are read only for
  the different-folder case.
- The pollers are **visibility-gated**: no watch/fingerprint work while the
  workbench tab is hidden; they resume immediately on refocus.

## Deliberately out of scope (with rationale)

- **pointer↔literal diff noise** — flagged as a theoretical risk, but it can't
  arise in this loop: SM's working copy *is* a snapshot of the live files at
  load, so working and live always share identical pointer structure. A
  Qualibrate fit rewrites the *literal value* of an already-literal field; it
  never converts literal↔pointer. Resolving both sides for equality would only
  add the risk of *hiding* a real pointer change, for zero benefit here.
- **context-registry re-key** (`routes.py` `_activate_quam`, `ctx_name =
  folder.parent.name`) — sibling chips (`quam_states/*`) collide on the key, but
  the real context lives in `_quam_cache` keyed by full path and `active_context`
  always points at the last-loaded chip, so it's benign for the single-active
  model (only one quam chip is active at a time; no feature enumerates
  `contexts`). Re-keying risks regressions across history/dataset/display
  consumers for no functional gain here. Left as a known latent cleanup.

## Tests

- `tests/test_path_match.py` — the verdict matrix (qb-unresolved, sm-empty,
  same-folder incl. trailing slash, same-chip-different-folder, different-chip,
  same-network-different-qubits, indeterminate, `is_linked`, `chip_label` for
  both layouts).
- `tests/test_live_diff_routes.py` — `/state/live-diff` (no-chip 400, fresh 0,
  before/after orientation, `with_live`, touch-without-change 0), accept via
  `/field/edit-batch` closes the diff, accept-all atomic, `/explorer` renders the
  live-diff controls.
- `tests/test_qualibrate_config.py` — path resolution.

## Verifying in the browser

Template + JS changes need a **server restart** (Flask caches Jinja templates
per-process; a browser refresh isn't enough).

1. **Mismatch path** — load chip A in SM, point Qualibrate at chip B → the
   workbench shows **⚠ mismatch** + "Load &lt;B&gt; in SM"; clicking it loads B
   and the badge flips **✓ linked**.
2. **Before/after** — SM and Qualibrate on the same chip; change a value in
   Qualibrate (a per-value checkbox; note Qualibrate's own "accept all" can be
   buggy and not write). In Split view the SM Explorer auto-opens the live diff
   with the changed row(s) `before → after` + a green incoming marker; ✓ turns it
   into a pending edit; Accept-all batches; Apply to live writes it. An unchanged
   chip shows zero marks.
