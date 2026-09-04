# 164 — "loading…" under a date, with nobody running anything

Customer-reported, and reported twice: expanding and collapsing the data folder
a few times leaves a date group showing **"loading…"** forever — while no
experiment is running, so there is nothing to wait for.

## The chain

1. The sidebar tree refetches itself: on a workspace version bump, **and
   unconditionally every 10th poll** (`base.html`, the docs/141 §4ac belt
   against a gate false-negative). With nobody working, that second path is the
   one that fires — which is exactly why the report says "nobody is running an
   experiment".
2. The swap rebuilds every `<details>` from the server, and the server renders
   every date group **closed**, with the lazy placeholder inside (measured: 7
   lazy groups, 0 rendered open).
3. `htmx:afterSwap` restores the sticky open state, re-opening the ones that
   were open.
4. Their runs arrive on exactly one path: `hx-trigger="toggle[this.open] once"`.

Step 4 is true for a person opening the group. It is **not** true for step 3.

## Measured, not reasoned

```
before the refetch:  REQUEST /workspace/tree/group   TOGGLE open=true
after  the refetch:  (neither)
final state:         open:true  hint:true  entries:0
```

Reproduced 4 of 4 at every timing tried (refetch 0 / 60 / 250 / 900 ms after the
expand). htmx **has** processed the element — `hx-get` present,
`htmx-internal-data` present — and dispatching a `toggle` by hand fills the
group instantly. Nothing rings it.

## Three fixes that did not work

Each looked right, shipped nothing, and was caught the same way — `__lazyAsked`
came back **true** on a group that was still stuck, so the code had run and the
group had still not loaded:

1. `htmx.trigger(g, 'toggle')` — a CustomEvent; does not reach this trigger.
2. `g.dispatchEvent(new Event('toggle'))` inside `afterSwap` — too early; htmx
   is still finishing the swap.
3. The same, deferred by a task — still nothing.

Only the manual dispatch *seconds later* ever worked, which is what made the
timing look like the whole story. It is not: the reliable move is to stop
simulating an event at all.

## The fix

After the restore, any `details[data-lazy-group][open]` still showing the
placeholder is **asked directly**, one task later, using the URL and parameters
read off the element's own `hx-get` / `hx-vals` — so the request cannot drift
from what the markup declares. Guards: one ask per element; nothing asked for a
group that closed in between, or whose runs arrived in between.

Result: **0 stuck at all four timings**, where it was 1/1/1/1.

## Pins

`tests/sidebar_lazy_group_selfcheck.cjs` (12 assertions, **5/5 mutations**) plus
`tests/test_sidebar_lazy_group.py`, which both drives it and pins in source that
the restore *asks* rather than simulating a toggle — re-introducing any of the
three failed attempts would read as a simplification.

Two of those five mutations were **green** until the fixture could reach the
state they guard: the inner "filled meanwhile" check is shadowed by the outer
sweep unless the runs land *between* the sweep and the ask, and the "closed
meanwhile" check needs the group collapsed in that same window. Both are now
driven explicitly (`fillDuring`, `closeDuring`). An unobservable guard is an
unpinned one.
