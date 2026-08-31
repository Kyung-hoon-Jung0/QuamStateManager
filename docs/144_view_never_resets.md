# docs/144 — pull/push never resets the view (2026-08-31, customer principle)

The user's principle, verbatim intent: **"sync 버튼은 SM의 사용자 화면을
초기화하지 않도록 한다"** — and not just Sync: Apply-to-chip too. The screen
the user is looking at (Json Tree search + expanded area, Live Edit state,
scroll, an open inspector) never changes because state was pulled or pushed —
but a value that IS on screen and DID change must update immediately, in
place, and none of this may make pull/push slower.

## What was already true, verified first

The core of this shipped 2026-08-27 (commit 0226a35): `/state/sync` responses
carry `{changes, structural}` (`_leaf_snapshot`/`_sync_patch`, ~10-15 ms
added to a 0.8 s pull) and `window.LiveSurfacePatch` patches both grids,
rendered tree leaves, collapsed-subtree lazy snapshots and inspector inputs
in place. Four CDP repros against a live server confirmed the main
`doStateSync` flows already preserve the view — including, surprisingly, key
adds/removes (the structural fallback's `_keepPaneScroll` + PaneState soft
restore carried search and scroll). So the reported reset had to come from
the paths the 08-27 round did NOT cover, and the four readers found exactly
those.

## What still reset, and the fix

**The four `stateRestored` emitters** replaced the working copy wholesale,
shipped no patch, and the listener closed the inspector + re-fetched the
whole pane:

* `/auto-sync/pull` (the automatic pull the drift poll presses),
* Dataset **"Apply to chip"** / load-state (both response branches),
* State History **stage** and **restore-live** (also the tray's
  "Revert last apply", which rides stage).

Each now takes `_pre_leaves = _leaf_snapshot(ctx)` before its mutation and
emits `_state_restored_trigger(ctx, _pre_leaves, ...)`: an HX-Trigger JSON
whose `stateRestored` detail is `{changes, structural}`. Because this rides
a response HEADER, the cap is `_HEADER_PATCH_CAP = 150` entries (an
experiment writes 2–4 leaves; a wholesale stage of a very different snapshot
correctly overflows to structural). The client listener patches in place and
keeps the inspector when the detail says non-structural; a bare-string
trigger (any unbracketed route) or structural detail keeps the exact old
wholesale behavior — scroll kept, soft restore, inspector closed.

**A fully-patched pull kept closing the inspector** (`doStateSync`'s
`!cleanApply` branch ran `closeInspector()` even when `LiveSurfacePatch` had
just patched the inspector's own inputs). Now gated:
`!cleanApply && patchResult !== "patched"`. The gentle `pulses-changed`
refresher also fires after a patched pull, so Pulses rows re-render in place.

**The patch now writes the client MODEL too**: `_patchTree` calls
`window._treeModelSet` for every `.json-tree` (fails closed on foreign
paths) and nulls `_flatIndex` — a search typed after a pull used to judge,
and show, the pre-pull value out of the stale `_treeData`.

## Two latent defects found while verifying (both shipped code)

* `if (window.LiveEditUndo) LiveEditUndo.clear();` — the docs/78 trap class
  (guard on `window.X`, call bare `X`), three occurrences, all now
  guard-consistent (`window.LiveEditUndo.clear()`).
* `doStateSync`'s catch reported EVERY in-chain error as "Sync failed
  (network error)" — a real response-handling bug was indistinguishable from
  a dead server. It now `console.error`s the actual error first. (This is
  what let the LiveEditUndo throw hide during diagnosis.)

And a harness rule extension (the docs/125 realm rule's dual): reassigning
`window.fetch` from the NODE realm never reaches the jsdom realm's bare
`fetch` — a mock must be installed inside the realm
(`window.eval("fetch = window.fetch = ...")`), with response data bridged as
a window property.

## Verification

CDP end-to-end on a scratch server: State History stage while /explorer had
an active search — search text and all 20 highlights survived, and the
staged value landed in both the DOM leaf and `_treeData`. Pins:
`tests/test_live_patch.py` (8 — the stage trigger names the changed leaf
with its value; the header cap degrades to structural; the shared helper
merges extras) + `tests/live_patch_selfcheck.cjs` (model patched +
flat-index invalidated; stateRestored-with-detail patches with NO re-GET and
keeps the inspector; structural and bare forms keep the wholesale path; a
mocked full doStateSync pull lands values and keeps the inspector).
**Mutation-verified 4/4 red**: a route reverting to the bare trigger, the
header cap ignored, the listener never patching, a patched pull closing the
inspector. Full harness sweep green; the sync/apply suites: 245 passed — the
3 remaining fails (`test_auto_apply.py` TestArming ×2 + revert-label) fail
IDENTICALLY on clean HEAD (stale pins from an earlier merged round, recorded
here, not caused by docs/144). Three pins asserting the superseded contracts
were updated intent-preserved (one had been red since 08-27).

Speed: each bracketed route pays two `json_diff.flatten` calls (~5 ms each
at 8.8k leaves) — nothing else; the patch path REMOVES the wholesale
re-fetch (an 8.8 MB /bulk render in the worst measured case), so pull/push
got faster, not slower.
