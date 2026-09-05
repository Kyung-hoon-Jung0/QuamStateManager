# 170 — The Datasets panel "got slow again", and a chip that did nothing when clicked

Two customer reports, 2026-09-05, both about the run list:

1. *"The dataset's left panel used to load instantly and got slow again — the
   refresh button is strangely slow too."*
2. *"The **new** mark above the sync button lights up fine, but its number keeps
   growing, and clicking it does nothing — the mark just disappears. Make the
   click do what the dataset list's refresh button does."*

The second is a plain defect with one root cause. The first is not a regression
in the sense the report implies — and the measurement that shows that is the
reason the fixes below are the ones they are.

## 1. What was measured before anything was changed

Every number here is from real Chrome driven over CDP, against the 2,655-run
pilot archive on this machine's NVMe, with a chip loaded (as the customer has
one). Two builds served side by side from two worktrees: the 09-02 build the
customer praised for "data loading got a lot faster" and the 09-05 build they
reported.

| step (a person's clicks) | 09-02 build | 09-05 build |
|---|---|---|
| Datasets, warm re-open — server render | 341 ms | 353 ms |
| … main-thread long tasks (client render) | 186 ms | 228 ms |
| Rescan button — the POST | 5,862 ms | 5,333 ms |
| Rescan button — wall, requests | 7,486 ms, **51 req** | 6,940 ms, **51 req** |
| sidebar ↻ — the POST | 189 ms | 174 ms |

The server code on this path is byte-identical between the two builds
(`git diff 44a213e..367ae0f -- core/dataset.py core/scanner.py` is empty). So
what the customer is feeling is not something the 09-05 build introduced. Three
structural costs were there in both, and the measurement named them:

**(a) The cold build lands on the user's first click.** A data folder's
`DatasetStore` is built once per process — ~11 file operations per run, 29,000
on this archive. docs/142 E bounded that build at 3 s and left the rest to "the
next scan", with correct continuation semantics. But the next scan was whichever
request rescanned next, *without a deadline* — and on a fresh process that is
the user's own click on Datasets. In-process, with every file operation under
the archive delayed by the 1.8 ms/op docs/155 measured on the customer's share:

```
GET /datasets  (cold, builds)        5.3 s      3 s budget + the render
GET /datasets  (the user's click)   31.6 s      the unbounded continuation
GET /datasets  (warm)                 83 ms
```

**(b) The build was paid up to three times at page load.** `live-wake.js`'s
handshake, the new-run poll and the render all reach `_get_or_create_store`
within a second of a page load; each built its own store and the LRU kept one.
Measured in the browser: `/datasets/poll` 3,076 ms and `/datasets/wait` 3,163 ms
side by side, both a 3 s bounded cold scan of the same folder.

**(c) Rescan re-parsed every run, then reloaded the page.** `force_rescan`
poisons every fingerprint so every `node.json` + `data.json` is re-read — its
contract since docs/105 #5 (the same-tick same-size rewrite the stat cannot
see). That re-read then re-PARSED all 2,655 pairs (the JSON decode is the CPU
cost on NVMe), and the route answered with `HX-Redirect`, so the whole page
reloaded: base.html, every script, the sidebar tree, 51 requests, to show a
table.

Why "got slow *again*"? The honest answer is that the report cannot be pinned
to a commit. Two things plausibly moved: the customer's 09-05 install started
with fresh caches, and docs/142 (08-31) turned an *invisible* cold build (the
60 s poll paid it in the background, and a click that came later found it warm)
into a *bounded* one whose remainder lands on the next click — which is exactly
the click a person makes right after opening SM. Both builds behave the same on
this machine; the customer's share multiplies every file operation by ~1,000.

## 2. The chip: one root cause

`base.html` loaded `sync-badge.js` **after** `app.js`. app.js registered the
chip's click handler at load time as

```js
if (window.SyncBadge) { window.SyncBadge.onAck('new', ...) }
```

so `window.SyncBadge` was undefined there, nothing was registered, and the only
thing a click did was the badge's own `clear()`. The count "kept accumulating"
for the same reason: `_ackStamp` — the baseline the server counts "new" from —
moves only in that handler. docs/167's selfcheck loaded the two files in the
right order and registered the handler itself, which is how the pin was green.

Measured in real Chrome on the 09-05 build: `SyncBadge.note('new', {count: 2})`,
a real mouse click on the chip → `chipGone: true, popupShown: false`, stamps
unchanged.

## 3. What changed

### 3a. The chip click refreshes the run lists, and the order cannot disarm it

* `sync-badge.js` now loads before `app.js`, and app.js re-arms the
  registration after the document is parsed regardless — the selfcheck loads
  the two files in the WRONG order on purpose and the click still acknowledges.
* Clicking the chip = pressing the sidebar's ↻ and, when the Datasets table is
  open, its Rescan (`window.refreshRunLists`, which presses the real buttons so
  the spinner, the in-flight guard and the sidebar filter come for free). The
  card is gone from this path: the user asked for the refresh, and after it the
  runs are simply there. Pressing either button yourself is the same
  acknowledgement — the list you just refreshed shows the runs the chip was
  counting.
* Measured after: click → `POST /workspace/refresh` + `POST /datasets/rescan`,
  chip cleared, no card, stamps `seen == ack`.

### 3b. The render is bounded, and says when it is still indexing

`_get_or_create_store` takes a `deadline` (threaded through
`_active_dataset_stores`); the Datasets render passes `_RENDER_SCAN_BUDGET_S`
(3 s, the cold-build budget, for the same reason: a person is waiting on this
thread), the 60 s new-run poll passes the poll budget. A store whose last walk
stopped at its deadline exposes `scan_truncated`; the render turns it into one
muted note beside the count — *⌛ still indexing runs…* — and `dataset-virtual.js`
hides it on the first delta poll that reports a complete scan, correcting the
header's run count to what the table holds.

Two consequences had to be handled honestly:

* **The deadline is checked per run, not only per date dir.** One busy day on
  the pilot archive is 467 runs ≈ 1,900 stats ≈ 3.4 s on the share, so a
  per-dir check let a 3 s budget run to 5.4 s and the 20 s Rescan budget to
  44 s (both measured under emulation). A dir cut mid-walk gets **no**
  fingerprint, so the next walk re-lists it in full — the B27 short-circuit
  must never cache a partial run set as a complete one.
* **Backfill is not "new".** Rows the continuation indexes arrive through the
  same delta poll as a run that just finished. The table now measures "new"
  against the newest run it already holds: an older row lands silently, never
  announced by the arrival pill or flashed.

### 3c. One builder per folder

Construction is single-flight per data folder: the first request builds, the
others wait on that folder's lock and share the result. Measured: one 3 s cold
scan at page load where there were two in parallel.

### 3d. Rescan re-reads without re-parsing, and swaps the table

`_parse_run_folder` reads both files as bytes, hashes them (blake2b, kept on
`RunInfo.content_fp`) and, when the pair hashes the same as the run it already
holds, hands that object back — no parse, no key-metric / sort-scalar / facet
extraction, `last_parsed` untouched (docs/105 #3). The docs/105 #5 contract
holds exactly: every run is re-READ, and a same-tick same-size rewrite has
different bytes, so it is parsed. The `has_*` flags are re-stat'ed only when
the folder's own mtime moved (a file appeared or vanished inside it), and a
flag that changed advances the row's cursor so the delta poll ships it.
`open_shared` stopped rebuilding its `CreateFileW` prototype per call (5,308
`LoadLibrary` + 5,308 `__build_class__` in one Rescan's profile).

The route answers with the same partial the Datasets link swaps, on the date tab
it was pressed from (`hx-include`), disabled while in flight; and its lookup
passes `rescan=False` (the docs/155 F4 shape) — the budgeted `force_rescan` IS
the scan the button means, and the lookup's own unbudgeted rescan was running
first.

## 4. Measured after

Real Chrome, same archive, same chip:

| step | before | after |
|---|---|---|
| Rescan — wall, requests | 6,940 ms, 51 req | **3,499 ms, 11 req** |
| Rescan — the POST | 5,333 ms | 2,750 ms |
| chip click | clears itself | ↻ + Rescan pressed, no card, acknowledged |
| cold start → click Datasets at +1.4 s → panel | (unbounded continuation) | painted **3.5 s** after the click, 768 runs + the note; 2,560 runs and the note gone by +11 s |

`force_rescan` on the archive in-process: 4.5 s → 3.1 s (2,560 runs re-parsed →
0; the remaining cost is reads and stats).

In-process, every operation under the archive at the share's 1.8 ms:

| request | before | after |
|---|---|---|
| GET /datasets, the click after a cold build | 31.6 s | **3.1 s** (and 3.0 s for each later click while indexing) |
| GET /datasets/poll while indexing | unbounded | 8.5 s (the poll budget) |
| POST /datasets/rescan on a partly-built store | 46 s | **23.7 s** (the 20 s budget + a 3 s render) |

## 5. Pins

* `tests/test_datasets_refresh.py` — `scan_truncated` after a cut build and
  after the continuation; the deadline cutting INSIDE a date dir and that dir
  walked again; unchanged runs keep their objects and cursor across
  `force_rescan`; the same-stat rewrite still re-read; a file landing beside
  unchanged JSON refreshes the flags and ships the row; a half-written run
  never reused; four threads build one store; the render hands its stores a
  deadline; the partial note renders only when a store is truncated; Rescan
  answers with the table, keeps the date tab, stays on Collections, and a
  plain browser POST still redirects; the chip wiring facts.
* `tests/newrun_poll_selfcheck.cjs` — the click acknowledges and refreshes
  with no card; the two scripts loaded in the WRONG order still arm the click;
  pressing ↻ yourself acknowledges.
* `tests/dataset_poll_selfcheck.cjs` — a backfilled (older) row lands without
  being announced or flashed while a newer one in the same delta is; a
  complete scan hides the note.
* `tests/test_sync_badge.py` — `sync-badge.js` before `app.js` in base.html.
* `tests/test_datasets_robustness.py` — the `_get_or_create_store` signature
  pin updated for the keyword-only deadline.

Runs green: the new file (20), `test_dataset_rescan`, `test_poll_scan_budgets`,
`test_share_io_cost`, `test_predelivery_audit_fixes`, `test_multifolder_datasets`,
`test_datasets_robustness`, `test_run_watch`, `test_sync_badge`,
`test_dataset_apply_to_chip`, `test_ds_flow`, `test_scheduler_scope`,
`test_multi_instance`, `test_state_versions` (225 in that batch); the three
selfchecks above plus `sync_badge_selfcheck` and `ds_flow_selfcheck`.

## 6. What this does not do, and what was seen on the way

* **Rescan on the share is still a full re-read** — by contract. With the
  per-run deadline it now stops at its 20 s budget (23.7 s measured with the
  render, against 46 s) and the poll continues the re-check; on NVMe the
  whole thing is ~3 s. A cheaper Rescan would need a different contract
  (re-stat instead of re-read, losing the same-tick same-size case), which
  is the customer's call, not this round's.
* **The cold build is still paid once per process.** Warm start of the
  sidebar has a persistent listing cache (docs/142 A′); the run table has
  none. On the customer's ~5,000-run share that is ~80 s of file operations
  spread over the delta poll's 8 s ticks after Datasets is opened, each click
  bounded at 3 s meanwhile. The next step, if that minute matters, is the
  docs/142 A′ shape for `DatasetStore`: persist `_date_fp` + `_folder_fp` +
  the compact rows per root, verify by the date-dir mtimes on the first scan.
  Not done here — it is a design, not a fix.
* **Two `test_poll_stability` pins flake on this machine in both builds**
  (`test_a_new_run_appears_exactly_once`, `…stays_partial_still_completes_later`:
  2 of 6 runs pass on the fix worktree AND on the 09-02 worktree). Timing, not
  this change; recorded rather than "fixed" by loosening.
* **95 rows are re-stamped by every `force_rescan`, before and after.** The
  archive has 2,655 run folders and 2,560 run ids: the duplicate ids flip-flop
  between their two folders each scan (a known per-root-uniqueness limit, see
  the vanish-pass comment in `_scan`). Unrelated to this round.
* **35 CSP `EvalError`s per page in the console**, from htmx compiling an
  event-filter expression with `Function` under the app's `unsafe-eval`-free
  CSP — almost certainly the sidebar's `hx-trigger="toggle[this.open] once"`,
  which is the only bracket filter in the markup. htmx catches it and installs
  the trigger without the filter, which is why a person opening a group still
  works and why docs/164's programmatic restore never fired it. Noted, not
  changed.
* **`_enqueue_exp_ingest` on a `ts=0` delta** enqueued every run with a
  `quam_state` (~750 here) and, under TESTING, drained them synchronously —
  14 s in the profile harness. Real clients never send `ts=0`; noted.
