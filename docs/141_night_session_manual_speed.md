# docs/141 — The night session: apply cost, the Config Manual, search speed, the undo trail (2026-08-27/28)

User-directed, autonomous overnight campaign. Rules for the night: accuracy first,
tokens frugal, time unconstrained, a heavy adversarial review after each commit.
Commits: `264a4e3` `03b89fa` (apply), `d08eaac` (manual), `4ffee11` (speed + undo +
review fixes). Every number below was measured, and the ones that were wrong the
first time are corrected in place.

## 1. Apply-to-live: what it cost, honestly

Profiled on a COPY of the PJ 20Q chip. The first report (0.82 s) was taken in
`testing=True` mode, where the route's `defer_index` is off — production already
indexes on a thread, so the real steady-state was ~0.55 s. Two changes:

* **Copy bytes, do not re-dump.** One apply re-serialised the same content six
  times (saver ×2, live write ×2, two history snapshots ×2). `safe_io` gained
  `read_json_raw` / `read_state_wiring_raw` (same share-delete handle, retry ladder
  and double-checked-mtime pair read, also returning the exact bytes parsed) and
  `write_state_wiring_bytes` (same tmp+fsync+replace, same state rollback).
  `check_and_snapshot` and `apply_to_live` use them: a snapshot is a byte copy of
  its source, the live files are byte copies of the working files. `read_json` and
  `read_state_wiring` now delegate to the raw variants (one retry ladder).
  Production-mode, 2 runs each: steady-state apply 0.58/0.52 → 0.27/0.43 s.
* **Heal readers join the deferred index.** A Param History read ~100 ms after an
  apply found the index "behind" while the deferred insert was still running and
  started a FULL rebuild (seconds). `_join_deferred_index` (bounded, never raises)
  runs first in both self-heal readers.

Kept on purpose: both snapshots (pre-apply arms Revert; post-apply is the record).

## 2. The Config Manual (`core/key_manual.py`, `core/key_manual_docs.py`, `manual.js`)

Customers populating a state did not know which keys a node can carry, what
values they take, or what they mean. Two sources, labelled, never blended:

1. **The env's own classes.** `probe_state_schema` parses each class's Google-style
   `Args:` / `Attributes:` docstring sections (MRO-walked, subclass may override,
   an auto-generated dataclass signature is not documentation) into a per-field
   `doc` + class `doc`. The schema cache is gated on `SCHEMA_FORMAT` — on the
   probe AND on the request path (`cached_only`), a review finding: a warm
   pre-upgrade cache would otherwise have hidden every docstring forever.
2. **The official QM docs** for the port/config keys the classes do not describe
   (`band`, `upconverter(s)`, `full_scale_power_dbm`, `downconverter_frequency`,
   `lo_mode`, `sampling_rate`, `upsampling_mode`, `output_mode`, `offset`, `delay`,
   `filter`, `crosstalk`, `gain_db`, `intermediate_frequency`, `time_of_flight`,
   `smearing`, `sticky`, `core`), each naming the page + anchor it came from and a
   verbatim line. The review removed three entries the pages never state (`thread`,
   digital `delay`/`buffer`) and moved three defaults to the page that states them
   (`config.md#controllers`). A test asserts every anchor is a real heading.

`manual_entries()` = one row per (class, field) for every class the chip uses —
type, required, default, Literal choices, description, SOURCE, where used — plus
the docs-only keys; a key nobody describes says "no description". `node_keys()` =
"what can THIS place carry": set / unset / undeclared, a leaf focusing its parent.
Wiring paths are walked verbatim (`wiring.*`, `network.*`).

Surface (user-directed): sidebar **Config Manual** directly below Settings /
Calculator → a body-level movable window (the calculator's anchor + header-drag
pattern) with the house search box; F1 on any state value, a `?` on every LIVE
state tree row (crud renders only), on Live-Edit column headers and inspector rows
opens the "this place" view. Buttons carry data attributes (no inline onclick
strings — a key with a quote would end the script); descriptions are escaped
(third-party docstrings are text). Measured on the real PJ chip with the cqt env:
262 keys — 181 with the class's own words, 19 from the QM docs, 62 stated as
undescribed.

## 3. Search speed — measured in real Chrome, then changed

Headless Chrome 151 + DevTools trace (`cdp_measure.js`), 20Q chip, one keystroke:

| surface | query | main-thread block before | after |
|---|---|---|---|
| Live Edit | `amplitude` | 925 ms (style recalc 371, layout 125) | **186 ms** (51, 6) |
| Live Edit | `T1` | 780 ms | **267 ms** |
| Json Tree | `amplitude` (516 matches) | 429 ms | **no long task** (199 ms total) |
| dataset sidebar | `rabi` | none | none |

* **Grid**: not the safety gates (they run at commit) and not the string matching
  (~110 ms JS) — the search hid excluded columns with ONE attribute-equals rule
  per column, and Chrome indexes rules by attribute NAME, so all ~8,000 tds tested
  all ~300 rules. The template stamps `ck-<index>` on every th/td and the search
  hides by class selector (hashed by class name).
* **Tree**: a broad query materialised every matching subtree. Past 150 matches
  the flat index is LISTED (path + value, first 400, true count shown); a row click
  expands and jumps to that one path; below the cap the classic highlight stays.

Honest residual: the grid keystroke is 190–270 ms, not the sidebar's ~0. The next
step, if wanted, is DOM-shrinking (detach hidden columns' tds) — deliberately not
done tonight: every path-addressed grid feature would have to learn the detached
store.

## 4. Undo — the field you just changed, fast (user: "very important")

* A Ctrl+Z from the inspector or the tree used to cost the 2.4 s whole-grid re-GET:
  a path with NO cell on the grid counted as "uncovered". `revertPaths` (both
  grids) now reports the cells it FOUND but could not repaint honestly as
  `uncovered`; only those, or a structural change, rebuild.
* **The Undo trail** (`undo-trail.js`): a compact bottom-right panel answers every
  Ctrl+Z / Ctrl+Shift+Z with path, from → to, tier (typed / staged), last 8 steps,
  closable, cleared on a state restore — fed by the `/undo`,`/redo` responses and
  by LiveEditUndo (`quam:undo-step`). Its one button, **go to field**, is the only
  thing that navigates: flash + focus when visible, else the owning surface via
  UndoNav — on the press, not automatically.

## 5. Tooling that came out of the night

`scratchpad/cdp_measure.js` / `cdp_act.js` / `cdp_shot.js`: Chrome headless with the
DevTools protocol over Node's built-in WebSocket — real long-task + trace splits and
screenshots without the browser extension (which cannot reach this machine's
localhost). Used for every number above and for four visual checks.

## Pins

`test_safe_io_raw.py`, `test_deferred_index_join.py`, `test_key_manual_probe.py`,
`test_key_manual.py` (+ `TestReviewFixes`), `test_config_manual.py` +
`config_manual_selfcheck.cjs`, `test_undo_trail.py` + `undo_trail_selfcheck.cjs` +
`tree_search_list_selfcheck.cjs`, `bulk_search_selfcheck.cjs` (class-selector hide),
`ctrlz_selfcheck.cjs` (new fallback contract). Every new pin mutation-checked
(3/3, 6/6, 7/7); a wrong mutation and two vacuous pins were found and rewritten.
