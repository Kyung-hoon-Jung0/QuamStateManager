# 291 — Calibration log: one day that holds thousands of runs

**Date:** 2026-10-06 · **Trigger:** "the Calibration log lists the runs in one column;
when thousands of runs pile up, won't it get slow? Do we need pagination?"

Days were already pages of their own, and a day of more than 150 cards sent each row
and fetched its body on open (docs/281). What nobody had measured was ONE day that holds
thousands of runs, such as an overnight auto-calibration loop.

## Measured before

A synthetic ledger (`tools/check_calibration_log_hub.py::_synthetic`, N runs on one day),
server time of `GET /journal/day` on this PC under load:

| runs in one day | cold | uncached re-open | HTML |
|---:|---:|---:|---:|
| 500 | 2.2 s | 0.31 s | 0.82 MB |
| 1,000 | 3.2 s | 0.55 s | 1.63 MB |
| 3,000 | 8.8 s | 1.8 s | 4.96 MB |

For one uncached 3,000-run request, `story._build_day_hub` took 2.0 s. `_run_facts` ran
twice per run, with 12,008 `os.stat` calls. The Jinja render took 1.1 s, and the browser
then parsed 3,000 cards. On a realistic chip (12 changed values per run), the same day was
a **7.35 MB** fragment of 46,242 elements, painted 3.8–4.8 s after the request.

## Design

**A paged day.** A day with more than `PAGE_CARDS` = 300 cards renders the **newest 300
rows of what the filter matches**. Above them, *Show 300 earlier (2,700 more on this day)*
fetches the next slice (`GET /journal/cards?before=<row>`) and puts it in place of the
button. The row the reader is at stays where it is on the screen. The rows stay in time
order: they are positions in the day's sorted list, anchored on a row id, so there is no
duplicate and no gap. A note says *Showing 600 of 3,000 rows on this day.*, so the page is
never shown as if it were the whole day.

**Search and the author filter cover the whole day.** On a paged day the rows carry no
search text, and a filter goes to the server (`reuse=1`). The server filters the whole
day first, then takes the newest page. It reuses the day's last build, the same snapshot a
small day filters in the browser. *Runs N*, *Showing X of N rows that match*, the
per-target strip and *Day total: N new since your last visit* all count the whole day.
That last count comes from every card's instant, sent as millisecond differences rounded
up. A filter's answer is not a visit: it neither moves the visit stamp nor clears the marks.

**Every place that names a row by id still reaches it:**

- strip pills (`#card-N`);
- undo links: `#write-…` on the same day, `/journal?day=…#write-…` from another day;
- a deep link;
- the claim's reload;
- the gate refresh;
- a lazy body (`/journal/card`), which reads the whole day.

A link to a row the page does not hold asks for the rows around it (`at=`), and that window
gets *Show later* as well. A row that cannot be shown says so (*not on this day, or the
filter hides it*); it is never a silent no-op. A same-day reload (a claim, or the gates
arriving) keeps the rows on screen (`from`/`to`).

**The strip.** The per-target strip of a 3,000-run day is 3,000+ pills, 0.57 MB, and 6,400 px
tall. When it holds more runs than a page, it starts folded (*all 3,000 runs*) and arrives
right after the rows (`GET /journal/strip`). Its placeholder has the folded height, so the
rows do not move when it lands (layout shift 0.0006).

**A day under a page renders byte for byte as before.** The golden file was written by the
pre-change code.

**Build cost.** No card's content changes. Dumps of every card before and after are
identical.

- `_run_facts` keeps a per-build record keyed by the normalised folder. A run and the same
  run as the next one's *previous* are stat'ed once, however the path is spelled.
- **The project zone is read once per build.** A project binding re-read the zone setting
  (a stat and a JSON parse) on every `_hub_clock`: 60–170 µs per call, thousands of calls
  per day. The synthetic bench never showed this because it binds an offline zone.
- `previous_in_folder` makes one read per (folder, node) instead of one query per run. It
  still looks at the 50 runs just before each one, and a reference copy of the old
  per-run query is pinned equal on a random ledger.
- On a paged day a card's search text is made only when a search needs it.
- The cached-fragment token hashes the rows shown, not the whole day again.
- A paged day's rows and strip drop the whitespace between tags. Every container there is
  a flex or grid box; attribute values and text are untouched (pinned).

## Why 300

Real Chrome, the realistic rig, re-open of the 3,000-run day:

| page | first HTML | parse + swap | request to paint |
|---:|---:|---:|---:|
| 150 | 0.18 MB | 46 ms | 0.72–0.75 s |
| 300 | 0.34 MB | 85–105 ms | 0.71–0.84 s |
| 600 | 0.65 MB | 157–225 ms | 0.78–0.91 s |

Building the whole day on the server dominates, and that cost does not depend on the page
size. The browser's part is about 0.3 ms and 1.05 KB per row. At 600 the first HTML passes
the 0.6 MB budget and the parse doubles. At 150 a reader presses twice as often, for no
measurable gain.

## Before / after

**Bench.** The same synthetic ledger (10,000 runs, N on the first day, Datasets not open,
so every run is read from its folder). Flask test client, medians of 5, base and change
run back to back:

| runs in day | uncached before → after | served before → after | HTML before → after | show earlier | strip |
|---:|---:|---:|---:|---:|---:|
| 500 | 165 → 95 ms | 148 → 82 ms | 0.82 → 0.31 MB | 18 ms | 4 ms |
| 1,000 | 346 → 176 ms | 257 → 134 ms | 1.63 → 0.32 MB | 30 ms | 12 ms |
| 3,000 | 875 → 485 ms | 715 → 472 ms | 4.96 → 0.33 MB | 32 ms (0.31 MB) | 65 ms (0.36 MB) |

A repeated filter is served in 2–5 ms. The first filter of a paged day makes the search
texts once (about 0.1 s at 3,000). Cold requests (997 → 813, 1,787 → 1,257 and
3,472 → 3,127 ms) are dominated by the gates, which a test app computes inside the request.

**Realistic rig.** A synthetic chip: 3,000 runs on one day across 4 targets, 12 changed
values each, the data folder open in Datasets. Measured in-process after the background
gates finished: **1.36–1.81 s and 7.35 MB before, 0.57–0.77 s and 0.34 MB after.** The
strip's 0.57 MB follows the rows.

**Real Chrome** (headless, CDP, chip loaded):

| | before | after |
|---|---:|---:|
| first open: server / paint | 2.31 s / 4.80 s | 1.19 s / 1.34 s |
| re-open: server / paint | 1.35–1.54 s / 3.75–4.06 s | 0.52–0.72 s / 0.69–0.93 s |
| first HTML | 7.35 MB | 0.34 MB |
| elements in the day | 46,242 | 3,927 |
| *Show 300 earlier*, press to paint | – | 0.23–0.33 s |

## Verified

- **Content equality** (`D:/work/sm_qa_rigs/calbig/bench/equality.py`). The fixture is 40
  and 700 runs with real run folders, SM writes and journal lines. Before vs after:
  - every card, loose line, strip and count is identical (JSON);
  - the small day, and the big day with paging off, render byte-identical fragments;
  - all 700 rows reached through the paged body plus every *Show earlier* slice match the
    pre-change rows in the same order. The only differences are the two search attributes
    a paged row leaves out, and whitespace;
  - the strip's pills are identical.
- **Pins.** `tests/test_calibration_log_big_day.py` has 12 tests. One of them drives
  `tests/calibration_log_big_day_selfcheck.cjs`: 11 jsdom pins on real server HTML and the
  shipped `journal.js`. Related files were run a few per process: calibration log, journal,
  journal page, journal search, story, hub query, hub drawer, chip report, run instants,
  agent API, guardrails, htmx options, UI text, bundles, web, focus ring, inspector,
  diagnostics banner, unseen edits, knowledge pack and hub record. Result: 1,229 passed, 19
  skipped (environmental), 0 failed. The four journal selfchecks pass when run directly.
- **Real browser** (`D:/work/sm_qa_rigs/calbig/shots/`, every PNG looked at). Walk: open the
  day, press *Show 300 earlier* twice (900 rows, contiguous, no duplicate, the reader's row
  moved 0 px), search a run of an older slice (found, the count says 1), open its body,
  clear the search, unfold and fold the strip, click a pill of a row not on the page,
  deep-link `#card-30` on a full load, reload, back, forward. The gates arriving refreshed
  the day with its 600 rows kept.
- **Console:** no error from this page. Two CSP `unsafe-eval` errors appear on every full
  page load. They come from the sidebar run tree (`hx-trigger="toggle[this.open] once"`,
  htmx compiles the filter with `Function`). They are there before this change, and they
  are left alone.

## Mutation check

58 mutations (`D:/work/sm_qa_rigs/calbig/mutate.py`, results in `mutations.json`), each
breaking what one new pin guards. The file was restored from its in-memory copy and the
restored bytes were checked. **57 RED**: 56 by an assertion and 1 by an exception (the
fallback build removed). One stayed GREEN, and it is an equivalent mutant: in
`previous_in_folder`, `ord<?` → `ord<=?` adds only the newest asked row, which
`bisect_left` never counts as an earlier run. Two first-round mutations stayed GREEN for a
weak reason and were re-aimed: one targeted the reuse path through a pin about the
fallback, and one was the equivalent mutant above.

## Left alone

- The day is not in the URL, so a reload opens today (as before).
- *Runs 3000* in the section head has no digit grouping; the new note writes *3,000*.
- A 3,000-run strip is still 3,000 pills. A per-segment summary would make it small, but
  that is a design decision.
- What remains of a 3,000-run day's server time is the whole-day pass that the totals,
  the strip and the gates need: the ledger read (`hub_query.timeline`, `SELECT *` plus 36,000
  change rows) and the card loop. A first open after a restart also reads every gate from
  disk.
