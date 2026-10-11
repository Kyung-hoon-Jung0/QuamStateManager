# 303 -- v1.3.0: the production campaign

v1.3.0 closes the follow-up list the v1.2.0 walks left behind (A1-A17) and adds three sweeps meant to find
what nobody had listed yet: a long-session stability and bad-input sweep (B1), every page at two sizes and
both themes (B2), and packaging / first run / upgrade (B3). Every fix has a pin that was mutation-checked
(the fix line reverted, the pin failing by assertion, the file restored). The UI items were also seen in
real headless Chrome on a copy of the customer chip.

## A. The follow-up list

| # | What the user saw | Cause | Fix |
|---|---|---|---|
| A1 | A cold chip open took 6-16 s if the click landed during a Datasets scan; the history catch-up took about 2.4x as long with the chip page open | The Datasets index parse holds the interpreter: about 12 s for 3,500 runs, whatever the worker count (1-32 measured). The topology request also ran an RB lookup the map never shows | Background scans (the global poll, every page's new-run poll, a new `IndexWarmer`) run under `dataset.yielding_scan` and stop at the next 64-run chunk when a request, a catch-up or the read-index build needs the interpreter. The component map asks for `/api/topology?layout=1`. First screen: 18.2 s -> 1.3 s. Catch-up with the page open: 2.4x -> 1.31x |
| A2 | An archived chip's first open took 57-80 s | 2,378 state flattens re-encoded and hashed every long array; 555 of 647 snapshots were settled with three flattens each | Long-array hashes are memoized by value (only lists of plain int/float/str/None, where equality implies the same canonical text); `_segment` is memoized; `diff` sorts only the changed paths; a snapshot equal to the next run's content is settled by content hash. The ledger is byte-identical to v1.2.0's. Offline build: 72 s -> 25 s; first values: 107-143 s -> 54-81 s |
| A3 | Trends charts had different x-ranges | Each chart chose its own range: Plotly autorange, or the F29 focus on its own change points. The printable report's charts each started at their own first point | One window per Trends view, computed over every chart. A chart says "held to <date>" only when one of its own held points lies past the window. A toggle moves an already-drawn chart's axis without a redraw. The report's charts share one window too |
| A4 | The value-history panel covered the other clock buttons | Placement: the panel opened below the clock | It opens beside the pressed clock (right, else left), top-aligned and clamped to the window. A click on another clock retargets it, as the drawer code already did |
| A5 | `htmx:historyCacheError` 85-138 times per session | htmx stored a snapshot of the whole page in localStorage on every navigation; Live State Edit is about 5.6 M characters | `historyCacheSize = 0`: Back/Forward fetch the page from the server. 0 errors over a 28-step walk; open -> navigate -> Back -> Forward -> reload keeps the page intact |
| A6 | Run names broke mid-word in "Set by" | No break hints | `<wbr>` after underscores |
| A7 | Column headers printed min/max unrounded | Raw floats | Compact significant digits |
| A8 | Calibration log run #1: "Its rows are not listed..." beside "FIRST STATE ... 5,352 values"; the day totals left the first state out | Wording; the first state shared another flag's key | First-state card wording; the first state is counted with the day's runs |
| A9 | A nameless chip took its ledger identity from the first run ingested, another chip's in a shared folder | `_hub_live_identity` seeded only named chips | The ledger's chip is the chip the user opened, named or not. A nameless identity is compared part by part: names when both declare one; otherwise the controller (cluster name, else host), then qubit overlap. A wrong v1.2.0 seed heals on the next open (`hub_build.reseed`, 0.6 s on 3,548 runs). Known limit: another nameless chip on the same controller that shares a qubit label cannot be told apart by any file the ledger reads -- declare `extras.chip_name` |
| A10 | A change masked by another chip's run read "writer not proven" although its own patch set it | A seam row carried its proof only from a stored row with the same value | The seam applies the ingest proof (`hub_build._proven`) to the run's own `node.json` patches, read once per seam event and file signature |
| A11 | The cold report printed "gate: not checked yet" for most runs | Gate verdicts were cached per ledger event id, so a rebuilt ledger recomputed them all | Verdicts are keyed by run folder plus each input file's (mtime, size) under `story_cache/gates`, shared by every reader. A second cold start of the server: 0 not checked |
| A12 | State History diff and the live-drift table printed "-" for both null and absent | One rendering for both | `null` and `(absent)` |
| A13 | A raw ISO time in a note | Formatting | Local time with its offset |
| A14 | Calibration age said "today" at 03:00 for a run from the previous evening | Ages counted elapsed 24 h periods | Calendar days in the project's zone |
| A15 | BACKUP badge without a border; a clipped y-axis title | CSS / Plotly margins | Fixed |
| A16 | Ctrl+Z after an Apply wrote the live file at once | Not a defect: docs/160 (user decision) made Ctrl+Z write live by default, with the Settings toggle "Ctrl+Z writes live" | -- |
| A17 | A version could be pinned but not labelled | Missing control | An inline Label on version rows in Versions, State History and the History drawer |

## B. Sweeps

### B1 -- stability, restart, bad input

Two 45-50 minute sessions of real clicking on a copy of a 3,548-run chip. Server RSS, threads and handles
level off after warm-up. The browser kept about 15,000 DOM nodes and 1,250 listeners per inspector close:
htmx's `from:body` trigger listener held the closed pane. Fixed with `releaseHtmxWithin`. The Datasets run
switch kept one stale pane alive through `LabCheck._lastRow`, which is now a weak reference.

Kill and restart checks found no corruption:

- a ledger catch-up killed at 30% and at 70% resumes to the same result;
- 12 kills inside an Apply never tore `state.json` and never lost an edit;
- a second SM process on the same instance had its Apply refused as a conflict;
- an Apply from a stale tab got a 409.

Fixed:

- **Security.** A request is refused (403) unless its Host is loopback, the host `qsm serve --host` binds,
  or a name in `SM_ALLOWED_HOSTS`; this closes DNS rebinding. A GET never spawns an interpreter SM did not
  discover or the user did not choose. A typed interpreter path goes through an origin-checked POST.
- **Bad input.** One helper reads every JSON body: a body that is not an object, or a field of the wrong
  type, gets a 400. NUL or over-long paths and unparsed integers are answered too. A route fuzzer over all
  rules went from 159 real 500s to 0.
- `POST /state/sync` while the live file is mid-save answers 409, marked transient.
- After a kill during Apply, the recovery notice says where the edits are (on live, or kept staged; proven by
  content hash). Only the newest 5 unrecovered files are kept.
- A deep page reloaded after a restart offers "Re-open <chip>".
- `/browse` gives an unreachable share 4 s and says so. A drive's admin share (`\\host\c$`) counts as that
  drive, and the subfolder hint has a 2 s budget.
- A Column History value equal to the cell leaves the cell clean.
- The agent journal setup refuses a relative root.
- `/bulk?vw=1e400` no longer overflows.
- The env probe no longer starts a subprocess on a cache hit (`dict.get` evaluated its default eagerly).

### B2 -- every page, two sizes, both themes

Every page was swept at 1600x950 and 1280x800, in light and dark theme. That covers the chip pages, the
diff workbench, Live State Edit, Json Tree View, Pulses, State History, Param History, the Calibration log,
the runner pages, Datasets with its run tabs, Collections, Trends, Settings, /help, the Versions panel, the
History drawer and the value-history panel.

No page showed a console error in any of the four combinations, and no visible button was dead.

Thirteen defects were fixed, pinned and mutation-checked. The P1s, all at 1280:

- Param History > Changes paths collapsed into a five-line tower of letters.
- The diff list's "Use A" button sat outside the pane.
- The Projects page pushed "Open in SM" off the pane and printed "quam vNone".

The P2s:

- The Versions panel's old -> new -> delta cell ran past the panel edge.
- The topbar pill read "Auto -Sync".
- Active chips were white on cyan in dark theme (2.56:1).
- Low-contrast Settings and topbar text.
- Overview ranges broke inside themselves.
- At 1280 the Chip Status tab bar wrapped and hid the jumped-to section header.
- "~15" now reads "15 changed".
- An empty Collections page claimed a filter was set.
- A heading-sized footnote on Datasets.

The remaining P2 cosmetics are listed in the sweep record and left for a later release. Among them:

- search placeholders cut at 1280;
- map lines drawn over qubit ids;
- Instrument Wiring labels too small in Fit mode;
- a 416 px horizontal overflow on Qubits at 1280.

### B3 -- packaging, first run, upgrade

The wheel is 28.5 MB with 1,730 entries, and its package files are exactly the git-tracked ones. Every
runtime resource lookup resolves in an installed copy. A clean venv installs 27 dependencies and
`pip check` is clean.

A first run with no instance, no `~/.qualibrate` and a missing `QUALIBRATE_CONFIG_FILE` answered about
1,290 requests (every top page, a sample chip's pages, a 13-run data folder) with no 500.

Upgrading from v1.2.0 on the same instance:

- keeps the ledger id and events;
- re-imports nothing;
- logs no migration error.

Fixed:

- `qsm show` / `qsm table` crashed when piped on a cp949 console. Characters the console cannot encode
  now get a short stand-in, and `--json` escapes them.
- Without scipy, h5py or tzdata, pages degrade with a message instead of a 500. Without tzdata, every
  history read used to fail.
- The MCP server's version follows the package version.

## C. Release checks

- **Full suite on the integration branch** after the A1/A2 merge (6f683c33): 14,874 passed, 252 skipped,
  0 failed. After the B1/B3/UI merges (79609179): 15,057 passed, 251 skipped, 1 failed. The failure was
  `test_overnight_run::TestTheMorningSummary`, intermittent and also seen on main. Its cause was in the
  product: the morning summary read a run's `meta.json` with a plain open, so a read that landed on the
  worker's atomic replace fell back to another record's wording of the failure. The summary now reads it
  share-delete with a short retry, and the failure is worded one way whichever record is read. Pinned.
- **Final quiet full suite** on the release commit (68d0a98f), with nothing else running: 15,077 passed,
  251 skipped, 2 failed. Both failures were in tests and the product code did not change after the run:
  - `test_history_source_folder` compared raw markup that B2 had changed on purpose;
  - `vh_retry_selfcheck` counted a background poll that landed in its window.
  Both now check what they mean, still fail on a mutation of the behaviour they pin, and pass.
- **Final real-browser walk on a copy of the customer chip (C3):** all 10 steps passed. Each step was also
  reloaded, and used back/forward where the page has history. The steps:
  1. Open the chip.
  2. Chip Status.
  3. A qubit inspector.
  4. The value history of two fields, the second clock pressed with the panel open.
  5. Edit, Apply to live and Ctrl+Z, with the live file checked on disk at each step.
  6. Label a version and diff it.
  7. The Calibration log, today and yesterday.
  8. Trends on one window.
  9. The printable report, generated and opened offline.
  10. Five Datasets run switches with the Interactive and Raw Data tabs.

  The cold chip open took 4.3 s to its first screen. Every other page's first screen took 0.2-2.2 s.

## Not in this release

- The Datasets page opened during a history catch-up still takes 1.8-2x the catch-up's no-page time.
- The first diagnostics check, the first `/scheduler/status` and the sidebar's own parse still compete with a
  cold catch-up.
- Long-session numbers cover 45-50 minutes in headless Chrome. The WebView2 desktop window and multi-day
  sessions were not measured.
