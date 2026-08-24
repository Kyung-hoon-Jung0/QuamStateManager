# 140 — The chain conductor ships

**Date:** 2026-08-24 · **Branch:** `feat/knowledge-pilot` ·
**Builds on:** docs/139, which walked the full bring-up chain for q1/q2 in
a scratchpad driver and named what shipped code still lacked. This round
promotes exactly that — no more, the rabi/ramsey/zz chapter stays deferred.

## `core/autofit/chainwalk.py`

`walk_chain(views, target, *, profile, cross_doc)` takes one target's pile
of archived runs (a whole day is fine — non-target runs and out-of-scope
families are filtered, skips NAMED in the result, never silent), partitions
them into time-ordered family streams (`stage_views`; the qubit-spec pair
merges into the one joint session the walk already ships), runs each stream
through the walker SM already has (`RB.replay`, `pathreplay` for the power
family), applies `cross_close`, then concludes (`conclude`, pure):

* **Ordering truth**: run counters reset mid-day — `order_key` is
  (date, HHMMSS, run_no), the clock, never the counter. Measured on the
  real day: #97 at 12:52 precedes #8 at 17:04.
* **Cross-family recency**: a quantity two families both write (both flux
  maps → parking offset; three windows → resonator frequency) concludes at
  the LATEST write on the clock, with full provenance (`Write` carries
  value/stage/run/clock) and every candidate kept — never family rank,
  never an average. This is what made q2's parking land delta-0 on the
  operator's value in docs/139.
* **The parking edge**: a parking write that postdates every
  qubit-frequency read makes that frequency STALE — the chain does not
  vouch it and directs "re-measure 1Q at the parked offset". A parked
  chain with no frequency at all gets the same directive; an unparked
  chain says "no flux point established — the endpoint is still open".
  The conductor's numbers all come from node fitters through shipped
  walkers; it only chooses WHICH vouched number concludes, and says why.

## Pins — `tests/test_chainwalk.py` (12)

Pure: clock-beats-counter, date-beats-time, partition/joint-merge/named
skips, and the six `conclude` shapes taken from docs/139's measurements
(latest-flux-write-wins with the real #149/#56 numbers, stale pre-parking
frequency, vouched post-parking frequency, parked-without-frequency,
unparked endpoint, three-resonator-windows). Integration (auto-skips
without the pilot archive): the real day end-to-end through the shipped
conductor — q2 parks at the operator's exact offset with the frequency
correctly withheld and the 1Q directive raised; q1 matches both
frequencies, names the open endpoint, and lists its skipped
next-chapter runs.

Still open (unchanged from docs/139 §5): flux-conditioned value validity
as a verification-context extension; the flat-vs-empty reader item; the
engine/realbackend hookup (hardware-gated) — the conductor is the piece
the engine will call when that opens.
