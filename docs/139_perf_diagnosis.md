# 139 — the constant click delay, and the 18-second Param History (diagnosis)

*2026-08-25. Customer-reported on the real 20Q chip + a ~2,700-run (soon ~4,000+)
workspace. **Diagnosis only** — measured end to end, fixes deliberately deferred
(token budget); every number below is from a real Chrome or a cProfile run, not
an estimate.*

## Symptom 1 — Live State Edit / Json Tree: a constant few seconds per click

Measured in a real Chrome with per-xhr instrumentation (a first attempt with
shared timestamps was polluted by the 31s `/param-history/backfill/status` poll
and re-done keyed on the xhr object):

```
/bulk click     xhr-send +1ms → response +548ms → afterSwap +2,899ms → settle +3,547ms
                 server+network 0.5–1.0s · htmx SWAP 2.35s · settle/hydrate 0.65s
/explorer       network 11–27ms · perceived ~2s
```

- **The server is not the problem** (curl: /bulk 0.5–0.7s, /explorer 0.02s).
- **The swap is**: synchronously parsing + inserting the ~MB grid fragment of a
  452-column × 20-qubit chip, plus its inline init. No longtasks fired — the
  cost is the insert itself, not a runaway script.
- `/bulk` is **not in PaneState's KEEP tier at all** (`KEEP = ['/explorer']`,
  docs/110) — every visit is a cold render by design.
- `/explorer` keep-alive **works** (`paneRestored` fired — an earlier "the
  stash is empty" reading was a measurement artifact: `_historyReset` reassigns
  `stash = {}`, so the exported `_stash` alias points at a dead object; the
  behavioural check overruled it). But docs/110's doctrine "never cancels the
  htmx request itself" means a restore still pays the full fetch + fresh DOM
  insert and then throws that render away. Keep-alive preserves **state**, not
  **time**.
- **"원래는 안그랬거든" is not a regression.** The always-cold render crossed
  from ~1s into 4–5s as the grid grew: docs/94 (column cap 400→1200), docs/126
  (pair port-chain columns), docs/136 (QDAC columns).

**Fix 1 — IMPLEMENTED (2026-08-26).** Two changes in `app.js`'s PaneState:
`/bulk` joined KEEP, and an `htmx:beforeRequest` interceptor cancels the GET
when the arriving KEEP route has a FRESH parked copy (tray seq + chip token
unmoved — the exact `_tryRestore` gate) and restores the parked DOM
synchronously, pushing the URL itself (`{htmx:true}` state, the shape htmx
stamps on its own entries). Everything else about the docs/110 v2 doctrine
stands: a stale/absent copy fetches normally, a same-route click is a
deliberate refresh, and `_verifyRestore` still re-checks the seq against
server truth in the background. **Measured in real Chrome on the 452-column
20Q grid: sidebar return to /bulk 4–5 s → 0.67–0.98 s (click→paneRestored),
requests sent: 0, cancelled at beforeRequest: verified per-event.**

**The Back button needed two follow-on fixes, both found live, not in jsdom:**
① htmx has no snapshot for a skip-pushed entry AND its private
`currentPathForHistory` does not move on a foreign pushState, so on the next
popstate htmx **saved the on-screen content under the WRONG url** (measured:
the bulk grid cached under /explorer, then served back from that poisoned
cache) — the pane now carries a `data-pane-route` stamp (set at afterSwap and
at skip-restore; htmx's snapshots preserve it), and `_historyReset`'s
fallback refetches on **route mismatch**, not only on a blank pane, purging
`htmx-history-cache` when it fires (the poison must not outlive the repair).
An unstamped pane (full page load) is never refetched. ② popstate and
htmx:historyRestore both funnel into the fallback — a 1 s in-flight token
dedupes the double refetch. **Residual, stated honestly:** Back away from the
bulk grid freezes ~2.7 s while htmx serializes the ~MB body into its history
cache (a pre-existing cost, not introduced here), and Back after a skip shows
the outgoing content for the ~1–2 s the repair fetch is in flight before
converging on the right pane. Pinned by `tests/pane_state_selfcheck.cjs`
(28 asserts, **7/7 mutations caught** — interceptor, KEEP membership,
staleness gate, URL push, outgoing park, mismatch refetch, unstamped guard);
the harness needed `global.history` bridged (the standing bare-globals rule).

## Symptom 2 — Param History: 18–22s cold, 0.13s warm

cProfile on a cold instance with the real workspace attached:

```
GET /param-history                            18.3s
└ history.scan_workspace_alignment            17.7s   (97%)
  └ _cached_fingerprint        × 2,653        17.0s
    └ fingerprint_of           × 2,653
      └ safe_io.read_json      × 5,309        15.1s   ← every run's state.json + wiring.json
warm second hit                                0.13s
```

The alignment banner computes a hardware fingerprint for **every run in the
workspace by actually reading both its JSON files**, on the request path. The
cache (`_cached_fingerprint`) is **in-memory only**, so every SM restart pays
the full scan again on the first Param History open — and the workspace only
grows. The user's "always slow" is this cache re-cooling per process (and the
first-open cost scaling with run count).

**Fix 2 — IMPLEMENTED (2026-08-25).** The fingerprint cache is persisted to
`instance/history/_fingerprints.json`, keyed exactly as the in-memory cache
already was: `path -> (state mtime, wiring mtime, fingerprint)`. Run archives
are immutable, so an entry never invalidates; a touched file misses the mtime
key and recomputes — the pre-sidecar behaviour is the worst case everywhere.

- Loaded LAZILY on the first fingerprint ask, folded in via `setdefault` so
  **memory always beats disk** (an in-memory entry is at least as fresh).
- Flushed ONCE at the end of `scan_workspace_alignment` — the only mass
  producer — gated on a dirty counter (4,000 per-entry atomic writes would be
  its own perf bug; a no-op scan writes nothing).
- It is a CACHE, not a file of record: a corrupt/unreadable sidecar is
  ignored, one bad entry never poisons the rest, an unreadable run is never
  persisted as a lie, and a failed write only logs.
- **Measured** with two processes sharing one instance dir against the real
  2,653-run workspace: process 1 (cold) 10.6s, writes a 4.6MB sidecar;
  process 2 — the restart case the user reported as "always slow" — **0.5s**
  (was 18–22s).
- One follow-on defect found by the pinned suite: the flush's own write bumps
  `instance/history`'s dir mtime, and when the instance dir nests inside a
  workspace root the `_workspace_token` sweep read that as "the workspace
  changed" — the scan's own bookkeeping invalidating the scan's own cache
  (`r2 is r1` broke). `_workspace_token` now takes an optional `own_root`
  and skips dirs at/under it (the alignment scan passes `self._root`); it
  STAYS a staticmethod because `routes._dataset_candidate_folders:17730`
  calls it unbound — a first attempt as an instance method made that call
  raise into its `except Exception` and silently rebuild on every poll.

Pinned by `tests/test_fingerprint_sidecar.py` (10 tests, **7/7 mutations
caught** — load-call, setdefault, mtime gate, bad-entry skip, dirty gate,
scan-flush, token exclusion).

## Follow-up (2026-08-25, customer-reported): the 11 false waveform errors

After pulling the docs/135-139 push, the customer's working chip showed 11
Diagnostics ERRORS: `qubits.qN.z.opx_trigger_out.operations.trigger` -
"pulse parameters produce an invalid waveform". Cause: docs/136 made
`pulse_index` enumerate the QDAC trigger pulses (one level deeper than other
qubit pulses), so the waveform DAC-range lint SAW them for the first time -
and `_pulse_peak` did not know the synth payload's `digital_only` marker, so
a digital-marker-only bare `Pulse` (waveform None by design; quam serializes
it as digital_waveforms only, and `generate_config()` accepts it - the
running 20Q chip is the proof) fell through to the hard-error branch. Fixed:
`digital_only` payloads return (None, None) - recognized, valid, nothing for
a DAC check to say. Pinned by
`test_digital_marker_only_pulse_is_never_a_finding` (mutation-checked).

## Status

Fix 2 (Param History) implemented and measured, above. Fix 1 (Live State
Edit swap cost) remains diagnosed-only — awaiting the user's go-ahead.
