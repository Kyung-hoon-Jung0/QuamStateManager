# docs/178 — With Auto-Sync armed, "the live chip is untouched" was false

2026-09-11. Found by the write-path stress round, lane 3 (State History restore
+ dataset Apply-to-chip), driving a chip copy in real Chrome.

## What happens

Arm Auto-Sync with **push** ticked. Then press either "stage only" door:

- State History → **Load as working state**
- Datasets → a run → State tab → **Stage only**

Both write the live chip **within the same second**. Staging content sets
`working_dirty`, and the docs/117 `MutationObserver` flushes on exactly that:

```
22:14:17 POST /state-history/20260911_130849_2735/stage
22:14:17 POST /state/apply-to-live
```

Reproduced six times over three separate runs (door A ×4, door B ×2), with the
file diffed each time — e.g. `/qubits/q5/T1` 0.000088888 → 0.000019991805337972195.

## What was, and was not, wrong

**The covenant held.** The arming press licenses the session (docs/117), so this
is not a write without a press, and the lane verified the recovery: *↺ Revert
this session* brought the chip back byte-identical (sha `984c856391`).

**Honesty failed, twice.**

① Four surfaces said the opposite while it happened:

| surface | what it said |
|---|---|
| door A title | "Mode 1 (safe): load as the working state, review, then apply" |
| door A confirm | "…You can then review the diff and Apply to live." |
| door B title | "…the live chip is untouched until you Apply from the top bar." |
| door B result | "…(the live chip is untouched until then)." |

② **The applied-to-live log listed nothing** — the one surface docs/117
designates as the feedback after a flush, and the only place the per-write ✕
lives. It filters on `meta["src"] == "auto"`, and a wholesale unit's `src` names
the *gesture* (`apply-staged`, `restore-live`), not the session. A plain cell
edit in the same armed session **was** listed, which is what made the omission
specific and easy to miss. Worse, in one run the log still carried the previous
session's stale row describing the exact *opposite* change to the one that had
just landed.

## The fix

- `_auto_push_note()` — the one sentence a "stage only" surface owes the user
  while push is armed, and `""` otherwise. Exposed as a Jinja global so a
  button's title and the route's own result message cannot drift apart. It names
  the way out ("disarm it first"), not just the fact.
- A **pull-only** session stays silent: `_auto_apply_state` already reads a
  session only when it authorizes pushing (docs/120 item 8), and warning about a
  write that cannot happen teaches people to ignore the warning.
- The wholesale unit carries `meta["auto"]` when the apply ran inside an armed
  session. `meta["src"]` keeps its own meaning — `story.py` reads it — so the
  armed-ness rides its own flag rather than overloading one that means something
  else.
- A unit past `_WHOLESALE_UNIT_CAP` has **no entries**, and the log's
  `if not ents: continue` dropped it. That made the biggest writes the only
  invisible ones. It now gets a row saying how many values moved and that this
  one cannot be put back value by value (there is nothing to compare and swap) —
  pointing at Revert-last-apply and State History instead.

## Verification

`tests/test_autosync_stage_honesty.py` — 11 pins, **no skips**. Three of them
skipped on the first run (two were guessing at a door name: it is
`/auto-sync/set`, not `/auto-sync/save`) and a skipped pin proves nothing, so
they were rewritten against the real doors. The template pin *renders* both log
row shapes rather than trusting the branch, because the row opens with
`r.entries[-1]` and a no-entries row would raise.

## Also from that lane, not fixed here

After a **forced cross-identity** restore-live, the success message promises
"The prior state was snapshotted first, so this is reversible" and the page then
lists neither that snapshot nor any way back — the identity change re-routes the
history directory, so the sentence points at a snapshot that page can no longer
show. The backup does exist on disk (`kind=backup`), and the tray's *Revert last
apply* does reach it (verified end to end, once). Recorded as low severity and
open.
