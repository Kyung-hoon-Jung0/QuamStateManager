# docs/216 — Re-opening a chip is a byte digest, not a re-parse; the prewarm pays the cold costs off the request

2026-09-26/27, branch `w7/coldopen` (6 commits, head `1fec054`), merged into `integ/w7` as `27766e5`.
Design: `D:\work\sm_qa_rigs\_findings\ram_design.md` §4, package P10 (cold open; accept: warm re-open
`POST /load` <= 1.0 s, from 1.4-3.1 s).

## 1. What was measured

Implementer: `w7_bench.cjs`, new group `switch` (`gswitch_ext.js`) plus the `cold` and `load` groups, rig
`w7-coldopen`, n=3-5, medians. Independent verifier: rig `w7v-coldopen` (big30x + big20 + krs5), interleaved
A/B on one rig by pointing `srv.bat` at `integ/w7` (`e74b8ce`) or the branch (`2337c70`), n=6 per arm (2 reps
x n=3) unless stated; "the machine was shared with other rigs, so absolute numbers are noisy and only the
ratios mean much". Real headless Chrome, ms.

| chip / step | implementer before -> after | verifier base -> head |
|---|---|---|
| big30x (19.4 MB state), warm `POST /load` (re-open) | 878 / 1188 / 1658 (A/B reps) -> 71 / 60 / 82 | 7144 -> 130 |
| big30x cold `POST /load` | 4242 / 4842 / 7464 -> 1223 / 1508 / 2240 | 7748 -> 3014 (`cold`); 7372 -> 2162 (`coldv`) |
| big30x switch, post_load (chip in the LRU) | 703 -> 51-72 | 486 -> 99 |
| big30x evicted re-open, `POST /load` | 18936 (13254-19623) -> 208-312 | 16900 -> 502 |
| big30x first open of a chip | 5352 -> 1761-1952 | — |
| big20 switch / evicted re-open | 321 -> 42-64 / 5084 -> 144-300 | 429 -> 96 / 8319 -> 252 |
| big20 first open | — | 5770 -> 774 (n=2) |
| krs5 warm `POST /load` | 90 -> 26-51 | 199 -> 86 |
| krs5 cold `POST /load` | 978 -> 352 | 1506 -> 1044 |
| krs5 switch / evicted re-open | 210 -> 42-65 / 2015 -> 77-146 | 227 -> 128 / 2641 -> 268 |
| krs5 first open | — | 1456 -> 388 (n=2) |

Search. Implementer: topbar first key after an open 216 -> 218-219 (the lazy index alone had made it 2884).
Verifier: first key after a switch / evicted re-open unchanged (about 215-230; one 4480 outlier on head);
first key typed immediately after a cold open 22591 -> 25538, dominated by the cold page burst on both arms.
The 2-3 s on keys 2-4 of `q1 chi` exist on base too — a one-letter token scans the whole index (in-process
`search('q1')` 6-9 s base, 5-6 s head); pre-existing.

Memory. Implementer: a tracemalloc pin (three chips of 30/20/5 qubits through a 2-slot context LRU, ten
switches) measures ~0.5 KB per switch against a 2 KB budget; a real server over 15 switches grew +4.9 MB and
+6.6 MB RSS in total (~0.3-0.4 MB per switch); `/debug/ram` total == sum of entries (268,373,728).
Verifier: RSS flat over 3 switch rounds (head 741 -> 744 MB, base 844 -> 845), total == entry sum
(268,756,163) at every step; eviction rounds 813-847 MB head vs 784-880 base — parking 6 chips is not
visible above noise.

## 2. Cause

A chip re-opened after leaving the context LRU was built again even when its bytes had not moved (evicted
re-open 18.9 s on big30x, implementer), and the open built the search index itself (the pin
`test_open_builds_no_index_and_first_search_does` goes red when the open builds it synchronously). The
implementer also listed pointer resolution and env analysis as eager `POST /load` work; the fix round found
that base's `POST /load` never resolved pointers (§4).

## 3. Mechanism

- `df45690` — `SearchIndex.build` is one pass over distinct strings, list-equal to a verbatim copy of the
  per-leaf original (60 random docs + a real chip). `LazySearchIndex` builds on the first search from a
  snapshot of the store and installs only if `mutation_seq` did not move, so an edit racing the build forces a
  rebuild. `core/chip_park.py`: a pristine live context leaving the LRU parks its store, engine and pulse
  index in `KeyedMemo("quam_parked")` (at most 6), keyed on the working files' byte digest
  (`loader.file_digest`); a re-open reads the pair settled, digests it, and takes the models back only if the
  digest matches and the store is still pristine (`loader.is_pristine`) — no json parse when the bytes are
  unchanged; the pointer cache travels with the store. `working_copy.content_hash` is memoized per file
  digest; class harvest and needs-generated-config on the store's mutation counters; one config-warm
  subprocess at a time. The `ws_` listing cache stores resolved run paths (the verify thread re-derives
  them). `safe_io` replace retries 10/30/100 ms first.
- `0f9c7a0` — `/debug/ram` (which `df45690` extended with RSS and the context LRU) leaked a ctypes
  `POINTER` type per call (~8 KB per GET, tracemalloc); the Structure prototype is built once.
- `96de909` — one daemon worker with one weakref slot builds the most recently opened chip's index after
  activation and after a working-copy replace; a burst of switches builds only the last chip. The build is
  `get()` itself, so the `mutation_seq` guard applies unchanged.
- `2337c70` — the prewarm competed with the cold `/bulk` render for the GIL (ttfb 13.6 s lazy-only -> 23.7 s
  with prewarm, vs integ 18.2 s). `core/activity.py` counts in-flight requests (long polls exempt); the worker
  starts after 0.5 s of quiet and pauses between 2048-leaf chunks while a request is in flight, unless a
  foreground `get()` is waiting on the same index.
- `1fec054` (fix round, §4) — before the index, the same worker runs three steps for the opened chip, each
  after a quiet server: `loader.warm_pointer_cache` (chunks of 256 under the store lock, stopping if
  `mutation_seq` or `id(merged)` moved, yielding between chunks to a foreground request; a token skips an
  already-warm content), `diagnostics.lint_state` and `state_env_validate.analysis_for_store` (wired by
  `routes._chip_warm_steps`). All fill the seq-keyed memos the request path reads, and both memos re-check
  under the store lock, so a waiting request takes the background result.

At integration `LazySearchIndex` (coldopen x livewrite) and the foreground gate (`core/activity` x
`run_ingest.Foreground`) were unified, the digest is also recorded on livewrite's handed-over-docs load path
(`_pair_bytes_digest`), and the lint / env memos are keyed on `store_revs.seq_token` — see docs/224.

## 4. Found by the independent verification and fixed

Verdict `PASS_WITH_NOTES`. Staleness journeys J0-J5 on big30x (in-LRU switch after an outside live write;
park by eviction + outside write + re-open; take-live, evict, re-open; edit + apply, evict, re-open; a peer
rewriting the working copy while parked; an edit racing the prewarm): 2 full passes, 0 problems — every value
read through the warm path equals a cold restart and re-open. Verifier mutations: M1 unpark without the
pristine re-check, M2 lazy install without the `mutation_seq` token, M3 digest ignoring wiring bytes — each
RED; M4 `park()` without its own pristine gate — GREEN (22 passed).

- **P2-1 — the cold first `/topology` after `/bulk` was slower than admitted.** Verifier (bench `cold`, n=6
  interleaved, big30x): TTFB median 564 -> 3300 ms (worst 16.6 s), settle 9.8 -> 16.5 s; curl with no wait
  base 6.9/7.3/7.5 s vs head 14.5/7.9/7.3 s; 45 s after the load neutral; krs5 +0.6 s settle. The fix round
  found the attribution only partly right: in-process on big30x the first `GET /topology` after a cold load
  cost ~5 s on BOTH arms (base 5.49 s, head 5.0 s), with 0 pointer-cache entries and no lint cached in either
  — base's `POST /load` never resolved pointers; `lint_state` alone is 6-9 s cold (incl. ~15.6k pointer
  resolutions) plus 1.3-2 s env analysis, and base was fast in the browser only because a `/bulk` XHR linted
  first. The verifier's 3.3 s median did not reproduce in the fix round (head TTFB 349/631/657 in 3 plain
  pairs; the `2337c70` arm 490 median, n=4). Fixed in `1fec054` (§3). Implementer-measured after: in-process
  first `/topology` 5.0 s -> 0.77 s (second hit 0.22 s); real Chrome, interleaved, n=10 per arm, base
  `e74b8ce` -> head `1fec054`: `/topology` TTFB 423 (261-694) -> 384 (230-508), settle 7347 -> 6346,
  `post_load` 6562 -> 1592, `param_history` settle 7264 -> 5352, `pulses` settle 2188 -> 1542.
- **P3-1 — no pin covered `park()`'s own pristine gate.** Pinned in `1fec054` (`TestParkOwnGate`).
- **obs — five "Failed to load resource: 409" console lines** in the verifier's second journey pass. Not
  reproduced: these are `/state/sync` handshake statuses (unseen_changes, collision, ...); a hooked two-pass
  tray journey saw zero responses >= 400. They stay unattributed to a URL; the branch does not touch
  `/state/sync`.

## 5. Pins

- `tests/test_search_index_build_parity.py` — random documents, signed zeros, dotted keys, a real chip.
- `tests/test_chip_park.py` — park/re-open (`test_a_late_edit_on_a_parked_store_is_never_served`,
  `test_a_wiring_only_rewrite_is_not_reused`), `TestParkOwnGate` (2 of 3 red when the gate is deleted),
  `TestLazySearchIndex` (`test_an_edit_racing_the_build_forces_a_rebuild`), `TestWarmPathMemos`,
  `test_randomized_event_sequence_matches_a_cold_build`, `test_ten_chip_switches_hold_memory_flat_and_debug_ram_adds_up`
  (red at 7856 B/switch with the ctypes leak restored; red on the absolute budget with a store leaked per
  park), the prewarm pins.
- `tests/test_prewarm_gate.py` (long polls exempt, start waits for quiet, a paused build never stalls a
  foreground search), `tests/test_coldopen_io.py` (resolved paths, replace retry ladder),
  `tests/test_loader.py::test_load_routes_through_safe_io`.
- `tests/test_chip_prewarm_diag.py` (5 pins, each red on its mutation: pre-steps dropped, pre-edit snapshot,
  seq stop removed, already-warm token removed, either under-lock re-check removed); adapted on `integ/w7` in
  `6541800` for `store_revs.seq_token` — docs/224.
- Suites: implementer targeted 294 passed, 32 skipped (verifier 292 / 32, the 2-test gap not investigated);
  fix round targeted 533 passed, 24 skipped; fix-round full suite (cqt) 1 failed, 9967 passed, 251 skipped —
  `test_share_io_cost::...test_chip_activation_after_a_run_lands_stops_scaling_with_the_archive` ("-2 more
  syscalls"), passing in isolation on head and on `integ/w7`. No JS changed; no `.cjs` run.

## 6. Open / not done

- The lint step holds the store lock for its whole run (seconds on big30x); an edit landing then waits.
- Cost moved, not removed, on a cold start: before `1fec054`, the implementer's big30x sum of cold load +
  bulk + topology + param_history + pulses settle was 56.2 / 63.9 / 105.5 s base vs 62.2 / 66.1 / 90.0 s
  head ("neutral within this shared machine's noise").
- No gain when the user types in the topbar immediately after a cold open; the one-letter-token search scan
  is pre-existing.
- The 409 console lines remain unattributed; no full-suite run of `integ/w7` at `e74b8ce` exists to compare
  the one full-suite failure against.
