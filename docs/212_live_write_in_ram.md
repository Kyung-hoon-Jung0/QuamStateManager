# docs/212 — The live write path in RAM: the same bytes, big30x Apply click-to-written 22.2 s -> 1.7 s

2026-09-26/27, branch `w7/livewrite` (8 commits), merged `a8c5fbe`. RAM P4
(diff caches) + P9 (Take live) + the live-write doors Apply / Keep mine /
Pull & apply. Rig and baseline: docs/211.

## 1. What was measured

Implementer-measured, real headless Chrome over CDP (`w7_bench.cjs --only
write,sync,lwi`; `lwi` = sync review panel open in the clean / mine / both
states, Versions open, per-row version Diff, reload intact). Before =
`integ/w7` on the same rig. Medians in ms; krs5 n=5, big30x n=3.

| metric | krs5 before | krs5 after | big30x before | big30x after |
|---|---|---|---|---|
| apply_now.file (click->written) | 2526 | 398 | 22195 | 1684 |
| apply_now.status | 4601 | 977 | 37246 | 19666 |
| keep_mine.file / .status | 1121 / 2713 | 183 / 413 | 26038 / 61780 | 1212 / 4893 |
| pull_apply.file / .status | 3430 / 5242 | 303 / 1162 | 33639 / 58300 | 5214 / 18569 |
| pull_apply.settled | 6186 | 2094 | >90000 (no settle) | 19502 |
| auto_apply.file | 854 | 207 | 2873 | 1928 |
| take_live.file / .status / .settled | 256 / 2444 / 4917 | 100 / 402 / 2029 | 1525 / 22109 / 65098 | 1006 / 4301 / 20833 |
| live_diff warm / after edit / after outside write | 1199 / 849 / 916 | 14 / 25 / 16 | 5424 / 7721 / 14853 | 182 / 1771 / 1331 |
| drift warm / after outside write | 1501 / 978 | 24 / 62 | 6 / 23386 | 216 / 7604 |
| versions warm / after edit | 1153 / 788 | 44 / 14 | 5266 / 10805 | 857 / 381 |
| lwi panel open clean / mine / both | 1414 / 2259 / 951 | 529 / 532 / 54 | 9662 / 18834 / 10342 | 732 / 16033 / 101 |
| lwi Versions open / version Diff | 856 / 757 | 91 / 91 | 11111 / 10602 | 587 / 1167 |

Every sample `live_ok=true`, `state_after=synced`; reload intact 1 everywhere.

Independent verifier, own rig, old and new interleaved by swapping the
server code (krs5 new A / old B / new C / old D, n=5; big30x new A / old B /
new C), old -> new: big30x apply_now.file 47559 -> 2137/1771, keep_mine.file
24671 -> 1242/626, pull_apply.file 36860 -> 4084/2491, take_live.status
19294 -> 3730/2662, live_diff warm 4733 -> 149/30, versions warm 5431 ->
860/451; krs5 apply_now.file 2375/3017 -> 412/362, live_diff warm 1306/1334
-> 22/13. Verdict FAIL on one regression (D1, §4); speed claims "broadly
confirmed (5-80x)"; 0 `live_ok=false` samples, 0 settle timeouts on the new
passes (7 on the old big30x pass).

Server path in-process (implementer, `lw_harness.py`, big30x, fresh
instance, no browser, quiet machine, `dee956a`): apply_now file 602-623 ms,
keep_mine 643-1003, pull_apply 849-1051, auto_apply 271-291, take_live
request 780-881, live-diff repeat 17-21, drift repeat 2, versions repeat 2-6.
Old code, same harness: apply_now 12.4 s, keep_mine 7.3 s (+7.6 s preflight),
pull_apply 15.5 s, take_live 11.6 s, live-diff repeat 4.3-5.0 s, versions
repeat 4.5-5.8 s.

## 2. Cause and mechanism

Every live-write door re-read, re-parsed, re-serialised and re-diffed the
whole chip several times per press (baseline leads in docs/211 §5).

- **Content-keyed doc cache** (`core/doc_cache.py`, `98a18bb`; lazy shared
  parses `0465654`; marshal copies for private docs and the baseline
  `bbd9502`): docs are parsed once per content digest (sha256 of the raw
  bytes) and checked on every read — a same-size rewrite with the old mtime
  put back is still detected. The canonical history hash is cached per digest.
- **Piecewise JSON** (`core/json_pieces.py`, `98a18bb`; one-walk writes
  `0465654`): the indent=4 file text and the canonical text come from one
  walk over content-addressed subtree pieces, byte-identical to `json.dumps`;
  an unchanged subtree is never re-serialised.
- **Tree diff** (`core/differ.py`, `98a18bb`): skips strictly identical
  subtrees; the flat diff stays as the parity-pinned reference.
- **Diff memos** (P4 `98a18bb`; `core/diff_cache.py` content-pair sharing
  `bbd9502`; raw-byte keys `ddcd73b`): `/state/live-diff` returns its whole
  body from RAM, keyed on the store token, both live digests, the sync point,
  pending reapply keys, `working_dirty` and the response shape; `/state/drift`
  and the drift view on the baseline hash, `captured_utc` and the live
  digests; `/prev-state-diff` and snapshot-pair diffs from the write-once
  folder-diff memo (`dee956a`).
- **Unmoved-live apply fast path** (`0465654`, corrected by D1): Apply over a
  live chip that has provably not moved skips the pull.
- **Over-cap sync patch** (`core/leaf_patch.py`, `0465654`): past
  `json_diff.WALK_CAP` the sync patch still gets the uncapped answer, at a
  cost that grows with the difference, not the chip.
- **History**: the dedup verdict comes before the 19 MB snapshot copy; the
  capture diffs against the bytes it just wrote; the pull/backup snapshot
  index insert is deferred (`9755a7a`).
- **Background gate** (`core/bg_gate.py`, `0465654`): the deferred history
  index, generated-config warm, post-apply snapshot and exp-ingest wait while
  a live write runs.
- **Lazy SearchIndex**, patched by cell edits (`98a18bb`, pinned `9755a7a`).

Safety contracts unchanged and pinned (implementer): docs/28, /65, /86, /97,
/107, /117, /120, /209 and the busy state.

Byte identity. Implementer: `lw_harness.py` on big30x, old code vs branch,
every door x2 reps: identical sha256 of live and working files, ALL_EQUAL
12/12. Verifier: own 23-step deterministic A/B (`vlw_ab.py`: every door,
same-field collision + ack, a same-size mtime-restored rewrite, auto-apply,
Ctrl+Z, a wiring-only outside write, the two-window unseen-edit gate): 22/23
identical on krs5 (2 runs each) and big30x, the one difference being D1.
Verifier `json_pieces` fuzz, 3000 random docs (NaN/inf/-0.0/2^70, bool vs
int vs float, U+2028, int keys, indent 4/2/0/tab, type flips): BAD 0.
Verifier staleness: after dropping every `KeyedMemo`, warm == cold at every
step (the one lag is the drift poll's documented mtime defer, same on old
code); a racy torn writer lost 0/60 outside writes, old and new.

## 3. Pins

`tests/test_json_pieces.py`, `test_doc_cache.py`, `test_differ_tree_parity.py`,
`test_diff_cache.py`, `test_livewrite_cache_parity.py` (random event
sequence, warm == cold), `test_livewrite_fastpaths.py`
(`test_apply_over_an_unmoved_live_skips_the_pull_and_writes_the_same_bytes`,
`test_a_moved_live_still_pulls`), `test_leaf_patch.py`,
`test_lazy_search_index.py`, `test_live_patch.py`. Implementer: 14
mutations, all RED after fixes (the drift-entries wiring-digest mutant was
GREEN/vacuous until `2ecb895` added wiring-only outside writes to the
sequence); verifier: 4 own mutations, all RED. Implementer full suite at
`dee956a`: 1 failed (`test_diagnostics_refresh.py::test_the_config_viewer_link_keeps_the_pane_under_real_htmx`,
passes alone 5/5 on both codes — load flake), 10945 passed, 251 skipped, 2
deselected; verifier's 36-file set 1925 passed, 6 skipped, 0 failed. No JS
or template touched.

## 4. Found by the independent verification and fixed (`fc3dfd7`)

- **D1 (P1)** — the fast path was gated on content only
  (`live_content_hash == synced_live_hash`), but `apply_to_live` refuses on
  the mtimes first. After a content-identical rewrite (a node's
  `machine.save()` that changed nothing, a touch, an editor re-save) the next
  Apply / Pull & apply refused with "Apply wrote nothing - live changed",
  where integ/w7 wrote it. No wrong bytes, no lost edit. Fix: both halves of
  the sync point must be unmoved —
  `_live_now = None if working_copy.live_changed(wc) else working_copy.live_content_hash(wc)` —
  so a re-save takes the old pull-first path (re-stamping the mtimes was
  rejected: a new sync-point write path with a TOCTOU). Pin
  `test_livewrite_fastpaths.py::test_a_content_identical_resave_of_live_then_edit_then_apply_writes[touch|reformat]`
  (all four files byte-identical to a twin pressed through the forced pull
  path); gate reverted to content-only -> 2 failed, 3 passed. On the
  verifier's harness: 23/23 steps identical to integ/w7 (krs5 files and
  responses, big30x file hashes; big30x responses differ only in the
  intended over-cap `leaf_patch` fields, as in the verifier's run). Real
  Chrome (krs5): re-save -> written after 4154 ms, touch -> 4609 ms
  (inflated, tests ran concurrently).
- **D2 (P3)** — the snapshot-pair / prev-state-diff memo, keyed on
  `(mtime_ns, size)`, served a stale diff after a same-size mtime-restored
  rewrite. Fix: the token is the SHA-256 of the four files' bytes from
  `doc_cache.read_pair(mode='hash')`; `_snap_fp` removed. Pin
  `test_diff_cache.py::test_state_folder_diff_follows_a_same_size_mtime_restored_rewrite[state|wiring]`;
  stat-token mutant -> 2 failed, 92 passed.
- **O1** (observation) — big30x lwi panel_open.mine not faster (verifier: old
  9.8 s; new 15.1 s pass A, 10.3 s pass C): the `analyze_state` lock-hold,
  owned by `w7/liveedit` (docs/213).

Fix-round 30-file contract set: 1720 passed, 5 skipped, 0 failed; the full
suite was not re-run.

## 5. Integration

At the merge (docs/224) livewrite's `LazySearchIndex` was dropped for
coldopen's; the `_load(docs)` handed-over-docs path was kept with coldopen's
`_pair_bytes_digest`; the tree diff was kept with `_diff_flat`'s body now
datasets' `_classify`; the folder-diff memo was kept and datasets'
capture-site flat recall dropped; `bg_gate` kept as-is.

## 6. Open

- Targets not met on big30x (implementer and verifier agree): Apply / Keep
  mine 600 ms (browser 1.2-1.7 s), Pull & apply 1 s (browser 1.8-5.8 s),
  Take live 400 ms (780-881 ms in-process: `sync_from_live` writing 19 MB,
  the snapshot capture diff, `_sync_patch`). Implementer: drift after a live
  change 120 ms is met on krs5 (62 ms), 7.6 s on big30x in the browser.
- Unchanged live-diff (target 35 ms): krs5 14 ms; big30x 47-104 ms idle by
  curl, 182 ms in the browser. The floor is reading + sha256 of the 19.4 MB
  live file (60 ms profiled), by design: the racy-live pin needs the hash.
- big30x drift warm 6 -> 216 ms in the implementer's table is not explained
  in the reports.
- big30x status / settled times are dominated by post-mutation derived work
  (`analyze_state` 12-15 s per call holding `store._lock`) — RAM P5, docs/213.
- After D2 a folder-diff repeat pays a read + sha256 of both folders; the
  `prev_state_diff_repeat <= 5 ms` target is unverified.
- The raw-key diff share (`ddcd73b`) did not fire in the harness Take live
  flow; a deferred index job logged `database is locked` once (healed by the
  existing `_ensure_index_fresh`).
- `w7/datasets`' report flagged for the live-write owner that
  `_refresh_live_diverged` re-hashes the 19 MB live state every 30 s (about
  1 s on big30x) inside a page request; no livewrite report addresses it.
- Shared machine throughout: ratios are indicative (big30x after:
  apply_now.file 1642-3599, pull_apply.file 1849-5854).
