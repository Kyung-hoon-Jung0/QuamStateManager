# 87 — SM never swaps what you are looking at

*Status: shipped 2026-08-09. Branch `feat/always-ask-on-drift`.
Amends docs/28 (working copy), docs/65 (state roundtrip), docs/86 (the third
choice).*

## The report

> 조용히 흡수하게 하지 말고, 이것도 그냥 SM에서 수정하다가 qualibrate에서 혹은
> 외부에서 수정했을 때와 동일하게 동작하게 하자. 음, 오히려 이건 SM의 동작을
> simple하게 하는거니까 문제없을 것 같은데?

docs/86 gave the drift banner a third button, but a whole class of user never
saw the banner at all. `reconcile_with_live` auto-pulled whenever the working
copy was **provably clean** — and "provably clean" describes exactly the person
most likely to be hurt by a mis-run: someone who had edited nothing in SM, so
SM was holding the good state. Their good state was replaced without a question.

## Why removing the automation does not reinstate the stale-chip bug

The bug the auto-pull was built for (docs/28) was: replace a live folder's files
out-of-band, and SM keeps serving its working copy — the old chip — forever,
across restarts, **with no signal at all**. The essence was the SILENCE, not the
absence of automation. A banner that names the drift and offers the pull closes
it just as well, and the tests that guard it now prove the stronger property:
*old chip kept + banner + one click reaches the new chip.*

Two things were already true and made this cheap:

* The identity gate (C30) already refused to auto-pull a **hardware-different**
  chip. So the silent path only ever covered *same chip, different values* —
  which is both the qualibrate fit-update (fine) and the mis-run (not fine), and
  nothing distinguishes them mechanically.
* docs/86 had already built the banner's three buttons. This change needed no
  new UI.

## The one carve-out: actors, not behaviours

`_reconcile_cached_quam_ctx` has three callers, and they are not the same kind
of thing:

| caller | who | adopts? |
|---|---|---|
| `_activate_quam` cache-hit | the user opened/switched a chip | **no — ask** |
| scheduler worker, post-node | a machine mid-loop | yes |
| autofit engine `reconcile()` | a machine mid-loop | yes |

The machine paths are not a UX preference. Autofit's gates judge every fit
against the **pre-update anchor** (docs/47); a stale store makes the verdict
itself wrong. A robot mid-loop does not stop to ask. So `auto_adopt` is a split
between actors, and the user-visible rule stays single: *SM never swaps what you
are looking at without telling you.*

A consequence worth stating plainly: a run started from SM's own Experiment
Runner still adopts silently, because that machine hook fires before the user's
next render. Only writes from outside SM — qualibrate directly, an IDE, another
window — raise the banner. That is what the report asked for, and the user
confirmed it.

## What the user gives up, and what they get back

SM now shows the OLD values until the pull is clicked. That is a real trade
(silent freshness → loud staleness), and the banner has always said so: *"you
are viewing the working state, which may show an older chip."*

In exchange the banner stopped being vague. `reconcile_with_live` gained an
optional `out` dict handing back the two documents it **already had to read** to
reach its verdict, so `_drift_count` diffs them for free and the banner says
*"N values differ"* — with no live read added to a surface that renders on every
page (docs/28). `None` ⇒ it says nothing rather than inventing a number.

Removed with the silence it announced: the `_auto_pulled` one-shot on
`/state/drift` and its "✓ Live chip updated — N params pulled" toast. A toast
reporting a fait accompli is strictly worse than a banner that asks first and
carries the same count.

## An unexpected result

Four tests on the documented Windows environmental-failure list —
`TestRestartCleanCopy::test_legacy_meta_replaced_shows_banner_not_clobber` and
`TestBannerSlot` ×3 — now **pass**. They asserted the banner appears; the
auto-adopt raced them on Windows mtime semantics. Making the path deterministic
fixed them, so the baseline drops 18 → 14.

## Pins

`tests/test_live_replace_routes.py` — the two "shows the new chip" tests now
pin the full contract (kept + banner + one click → new chip), which is a
*stronger* guard against the original bug than the silent swap was.
`tests/test_sync_robustness.py` — `/state/drift` must not carry `auto_pulled`
even with a stale memo present; a leftover would mean an adoption path survived.
`tests/test_autofit_e2e.py` — unchanged and still green: the machine carve-out
is real.
