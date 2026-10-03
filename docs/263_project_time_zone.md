# docs/263 -- one project time zone, and the run clock asked once

2026-10-03, the state-tracking hub's time step (DESIGN §2 item 1, §3.3), on top
of docs/244 (one time format, `window.SnapTime`) and docs/256 (`run_instant`,
`run_witnesses`, `SKEW_ASK_S`). Branch `feat/project-time-zone` from
`integ/batch4`.

## The user's decisions (binding)

- **Project time zone.** Picked on the project landing, ABOVE the env picker:
  a standard IANA zone, searchable, current UTC offset shown, DST-aware. On a
  pick SM compares at once: the OS zone vs the chosen one (a popup when they
  differ: "PC is UTC-7, you chose UTC+9 (16 h). Is the PC zone wrong, or view
  in Seoul time?") and "now HH:MM in the chosen zone -- does it match your
  watch?", with the NTP status. Saved per project; later launches only SHOW
  it. A new project defaults to the last project's zone. ONE setting: the
  Settings zone (SnapTime, localStorage) merges into it.
- **A run's clock disagrees.** A different zone alone is not an error: shown in
  the viewer's zone, the tooltip says "recorded 01:55 (UTC-7)", one quiet
  project note. A witness skew of 30 min or more is asked ONCE per project
  ("experiment PC clock is wrong / this PC is wrong / ignore"), stored with
  the measured skew, asked again only if the skew changes; a correction only
  after confirmation, always labelled "corrected", the original kept visible.
  Under 30 min: never asked, recorded, ONE Diagnostics info line. The skew comes
  only from witnesses that ARE witnesses (docs/256 review note).

## Where it lives

| piece | file |
|---|---|
| zone setting, clock record, skew policy, in-place test, correction | `core/project_time.py` (new) |
| runs SM saw ARRIVE (first sight bounded by the watcher) | `core/run_arrivals.py` (new) |
| the watcher's per-root gap between two good looks | `core/run_watch.py` (`poll_gap`) |
| routes: `/project-time/clock`, `/zone`, `/watch`, `/status`, `/skew-shown`, `/skew-answer`; landing views; `<html data-sm-zone>`; the open keeps a default; watcher listener + ingest step `run_clock`; Diagnostics line; run detail instant | `web/routes.py` |
| landing picker (above the env picker), card zone fact | `_landing_project_tz.html` (new), `_landing_projects.html` |
| the client: picker, PC-zone popup, watch check, the run-clock question, run times | `web/static/project-time.js` (new) |
| SnapTime reads the project zone; Settings line instead of the select | `app.js`, `base.html` |
| the env picker tells the zone picker which project it shows | `landing-env.js` (one event) |
| one run-clock line | `_diagnostics_clock.html` (new), `_diagnostics.html` |
| a run's instant + correction on the run detail (row and header) | `_dataset_detail.html` |
| the printable report in the project zone | `timefmt.local_text(zone=)` |
| `tzdata` on Windows (zoneinfo has no system database there) | `pyproject.toml` |

## The stored data

`instance/project_time.json`, beside `project_envs.json`, keyed by the same
QUAlibrate project name. Never `~/.qualibrate` (docs/55): a route test holds the
config files' bytes and mtimes identical across picks, watch answers and an
open.

```json
{
  "version": 1,
  "projects": {
    "alpha": {
      "zone": "America/Los_Angeles",
      "how": "picked",                         // picked | default (kept when first opened)
      "at": 1791039140.99,
      "os_check": {"answer": "view",           // view | pc_wrong; null when the pick matched the PC
                   "os_offset": "+09:00", "os_iana": null,
                   "zone": "America/Los_Angeles", "zone_offset": "-07:00", "at": 1791039140.99},
      "watch": {"answer": "matches",           // matches | differs
                "shown": "07:52", "zone": "America/Los_Angeles", "ntp_synced": null, "at": 1791039141.94},
      "clock": {
        "witnesses": [                         // newest 200 by the run's own instant
          {"key": "<root>::2026-10-04/#101_03_resonator_spectroscopy_single_005248",
           "src": "live",                      // live | in_place
           "node_us": 1791042768000000,        // the run's own instant (offset-aware only)
           "node_off": "+09:00",               // the offset its clock wrote
           "seen_us": 1791039168300000,        // SM's first sight (live only)
           "gap_s": 0.5,                       // the watcher's look before it (live only)
           "mtime_us": 1791039168310000,       // folder mtime
           "skew_s": 3599.697,                 // SIGNED: run clock - observer
           "span_s": 3599.69, "at": 1791039170.1}
        ],
        "answers": [                           // one per asked regime
          {"choice": "experiment_pc",          // experiment_pc | this_pc | ignore
           "skew_s": 3599.697, "correction_s": 3600.0, "n": 3, "src": "live",
           "from_us": 1791042768000000,        // the regime's first witnessed run (its own instant)
           "until_us": null,                   // set when a later settled regime differs
           "at": 1791039178.76}
        ],
        "run_offsets": {"+09:00": 4},          // offsets the runs record (the quiet note)
        "archive": {"in_place": false, "reason": "too_few", "n": 1, "span_s": null,
                    "spread_s": null, "skew_s": null, "root": "...", "at": 1791039147.6},
        "shown": {"skew_s": 3599.697, "at": 1791039178.27}   // the question went up once
      }
    }
  },
  "last_zone": {"zone": "America/Los_Angeles", "project": "alpha", "at": 1791039140.99}
}
```

## Behaviour now

**The landing.** Above the env picker, "Time zone for <project>" follows the
project the env picker shows (`sm-landing-project`, dispatched by
`LandingEnv.open`). It shows the zone, its offset now and "now HH:MM"; the
state reads `saved`, `default from <project> -- used when opened` or
`not set -- pick one` (then "UTC+9 (this PC)"). Each card's facts carry
`tz <city> <offset>`. The list is the browser's own IANA list
(`Intl.supportedValuesOf`, plus `UTC`), searched by city, region words or an
offset (`+9`, `UTC-7`, `-3:30`), each row with its offset and wall clock NOW
(DST is Intl's). The clock status (`GET /project-time/clock`) is fetched AFTER
the render; the landing render never calls it (a pin makes the probe raise
during `/` and `/landing/projects`).

**A pick.** Offsets are compared at the server's "now": when the PC's offset
(`clock_health.status().os_zone.utc_offset`) differs from the chosen zone's,
the popup asks first and nothing is saved before the answer (`view` /
`pc_wrong` / Cancel). Then the watch check: "Now HH:MM in <zone> (UTC-7). Does
it match your watch?" -- the SERVER's clock (the one SM stamps its records
with), ticking, with the time-sync line; "No" stores `differs` and says how to
sync. The saved zone applies to the page at once, and the pre-docs/263
`quam_tz` key is removed: one setting.

**Later launches** show the zone and ask nothing. `/qualibrate/open` keeps a
never-set project's offered zone as its own (`how=default`), so a later pick
for another project never moves it; the next new project defaults to the
newest pick.

**One zone everywhere.** Every full page stamps `<html data-sm-zone>` (the open
project's zone, else the zone set most recently) and `data-sm-project`.
`SnapTime.zone()` reads it first; only with no zone set anywhere does it fall
back to an old per-browser choice, then the browser. The Settings group shows
the zone and "Change on Projects" (to `/?landing=1`); the select is gone.

**A run's time** (the run detail's `date` row and header): the instant, shown in
the viewer's zone; the tooltip says `recorded 2026-09-30 13:54:25 (UTC+9)`.
After an `experiment_pc` answer, a run inside that regime shows the corrected
instant, a `corrected` badge, and `recorded ... (UTC+9)` beside it. Without an
instant (no offset-aware clock) the folder clock shows as before.

**The question.** After a page loads (1.5 s) and when a run lands
(`sm:runs-changed`, 4 s debounce) a page with an open project fetches
`/project-time/status`. When `clock.auto_ask`, the dialog goes up with the
evidence (how many runs, each run's own clock vs when SM saw it, a whole-hour
step named as a likely zone/DST mistake, this PC's time-sync line) and is
marked shown at once. It does not pop up again for the same skew; the
Diagnostics line keeps an "Answer..." button until answered. A 409 (the skew
moved since) asks the new question.

**Diagnostics** shows ONE line: under 30 min "agrees with this PC within
2 min (5 runs seen arriving live); below the 30 min ask threshold, so never
asked"; at/over it "waiting for your answer" (amber) or the stored answer; plus
the quiet zone note when the runs record another offset than the one shown.

## How a witness is decided, and why

- **Live (`src=live`).** [derived] A first sight is evidence of the save time
  only when it is BOUNDED from below by an earlier look that did not see the
  folder. The run watcher already looks every 0.5 s; it now records each
  root's gap between two GOOD looks (`poll_gap`, an unreadable look is no
  look). `run_arrivals` lists the newest date folder on each tick of a moved
  root; a folder new since the last listing is an arrival when the watcher's
  gap is at most `LIVE_GAP_S` (120 s). A root's first look is a baseline;
  more than 3 new folders in one tick is a paste or a sync, not runs; a
  folder whose `node.json` is not readable yet waits up to 10 min.
  The docs/256 review note phrased "live" as "first seen within a few minutes
  of the run's own claim". That test is not used: it reads the claim being
  checked, so a real 1 h skew would make every run "not live" and the skew
  could never be measured. The bound comes from SM's own looks instead.
- **In place (`src=in_place`).** [derived] A folder written while its run was
  saved has `run - mtime` equal for every run (the save time plus any skew),
  however far apart the runs are; a copy stamps every folder within its own
  few minutes, so `run - mtime` spreads as widely as the runs. `classify_archive`
  needs >= 5 runs spanning >= 1 h with a P10..P90 spread <= 300 s. A copy that
  kept the times passes, correctly: those mtimes are still the writer's.
  Cross-checked by simulation (300 in-place and 300 copied archives, random
  skews 0 / +-30 / +-60 / 2 min, save delays 0-60 s, copy windows 5 min:
  0 misclassified) and read-only on 7 archives on this PC:

  | archive | n | span | P10..P90 spread | median run-mtime | verdict |
  |---|---:|---:|---:|---:|---|
  | KH_202608_CZ | 60 | 5.3 h | 22,959 s | -4.4 s | copied |
  | Novera9Q | 60 | 3.3 h | 10,146 s | -52.7 d | copied |
  | arbel_20260929 | 60 | 6.4 h | 4.0 s | -2.1 s | in place |
  | KRISS | 60 | 5.4 d | 1,238 s | -40 s | copied |
  | IQCC_QOP37 | 60 | 1.7 d | 1,107 s | -147 s | copied |
  | KRISS_CR | 60 | 3.7 d | 703 s | -47 s | copied |
  | KRISS_CZ_260906 | 39 | 17.1 h | 1.7 s | -1.5 s | in place |

  No archive raised a question; the two in-place ones read a ~2 s skew (the
  save), which is "small". KH and KRISS have small medians but wide spreads
  (folders touched after the save) and are kept out -- conservative: a lost
  witness, never a false one.
- **Only offset-aware run clocks** are witnesses (`node_quality == "offset"`):
  a naive clock would measure SM's guess at its zone, not the clock. A skew
  beyond 26 h (UTC-12 to UTC+14) is no zone or clock mistake (a copied-in old
  run) and is dropped.
- Live witnesses win once there are 3 (they measure against THIS PC); else
  the in-place ones.

## The skew policy (`project_time.summarize`)

- `skew_s` is signed: the run's clock minus the observer (positive = the run's
  clock is ahead). The newest 5 witnesses decide; their median is the skew.
- [product] `small` below `SKEW_ASK_S` (30 min, reused from `timefmt`, never
  redefined); `ask` at or above it once 3 of the newest 5 agree within
  `SAME_SKEW_S` (15 min, half the zone unit); else `unsettled` (one copied-in
  folder never raises the question, and one odd run among agreeing ones does
  not block it).
- The regime starts at the oldest witness, walking back, that keeps the same
  skew (stepping over one that does not). An answer is stored with that start
  (`from_us`). A later settled regime with another skew ends it (`until_us`),
  so a clock fixed later stops the correction and a new skew is asked again.
- [derived] A correction moves a run by `correction_s`: the exact 30-min step
  when the measured skew sits within 2 min of one (a zone or DST mistake is
  exact; the second the measurement adds is the poll), else the measured skew.
  Displayed skews round to the minute once over a minute (the real walk read
  "59 min 59 s" for 3,599 s measured; it now reads "1 h 00 min").
- Only `experiment_pc` corrects a RUN, only inside its regime; runs before the
  first witness keep their time (no evidence). `this_pc` corrects SM's own
  stamps instead (`sm_correction_for`, for the ledger); `ignore` corrects
  nothing.

## Verified

**pytest** (`tests/test_project_time.py`, 54) and **jsdom**
(`tests/project_time_selfcheck.cjs`, 73 assertions, driven by the pytest file).

Related files, env `cqt` (`PYTHONUTF8=1`, `-p no:cacheprovider`): project_time,
project_env, project_scope, qualibrate_config / _location / _routes,
run_instant, time_display, display_timezone, diagnostics (8 files), run_watch,
run_ingest, misc_ui, natural_order_routes, cust_0930_ui, onboarding,
chip_report, report_card: **685 passed, 3 skipped** (two POSIX-only path cases and one "no real generate_config() snapshot on disk"; all environmental). After the last two changes (the UTC card, the named run) the files that render the landing ran again: 261 passed, 1 failed -- `test_qualibrate_location::TestWslBridge::test_native_path_anchors_on_the_config_dir`, a `\\wsl.localhost` path case (the docs/87 OS-behaviour class) that passed in the full run above, alone, and with its whole file (38/38). `snaptime_selfcheck` (44),
`time_display_selfcheck`, `landing_env_selfcheck` green.

One existing test changed, because it pins exactly what this step changes:
`test_display_timezone::test_the_settings_control_says_what_it_does_not_change`
asserted the Settings `<select id="tz-select">`. The select is gone by the
"one setting" decision; the test now asserts it is absent, that the Settings
line and its "Change on Projects" link are there, and still that it says
"display only" and "UTC".

**Real Chrome** (`tests/browser/journeys/project_time.cjs`, own rig on 5123,
CDP 9443, the browser emulated in `America/Los_Angeles` via
`Emulation.setTimezoneOverride`, the PC in UTC+9): **23/23**, console errors 0.
Screenshots read; the passes found four defects that the pins now hold:
Pico draws `<dialog>` as the full-screen flex row, so the dialogs laid their
paragraphs side by side across the screen (now one `<article>` box, pinned);
"59 min 59 s" (above); "not set" said twice on an unset zone; and the
question's footer said "corrected from 09:16 (UTC-7)" -- the first run's own,
skewed clock, an hour off the "seen 08:16" lines above it -- so it now names
the run ("corrected from run #101_... on").

**Mutation sweep: 37/37 RED.** Each mutation was applied to the real file, the pins ran in a fresh process (pytest with its own `--basetemp`, or the jsdom selfcheck), and the file was restored byte-for-byte and checked against git before the next one. Runner: a scratch `mutate.py`; results in `mutations.json`.

| # | mutation | file | result | the pin that went red |
|---|---|---|---|---|
| 1 | thirty-minute boundary: < becomes <= | `project_time.py` | RED | TestWitnesses::test_the_thirty_minute_boundary |
| 2 | two runs are enough to ask | `project_time.py` | RED | TestWitnesses::test_fewer_than_three_agreeing_runs_never_ask |
| 3 | an unbounded first sight counts | `project_time.py` | RED | TestWitnesses::test_only_a_bounded_first_sight_is_a_witness |
| 4 | a naive run clock counts | `project_time.py` | RED | TestWitnesses::test_a_naive_run_clock_is_no_witness |
| 5 | the skew sign flipped | `project_time.py` | RED | TestWitnesses::test_the_skew_is_signed_run_clock_minus_observer |
| 6 | asked again although answered | `project_time.py` | RED | TestAskOnce::test_asked_once_stored_and_not_asked_again |
| 7 | pops up again after being shown | `project_time.py` | RED | TestAskOnce::test_asked_once_stored_and_not_asked_again |
| 8 | every answer corrects a run | `project_time.py` | RED | TestCorrection::test_only_the_experiment_pc_answer_corrects_a_run |
| 9 | a correction before the witnesses | `project_time.py` | RED | TestCorrection::test_only_the_experiment_pc_answer_corrects_a_run |
| 10 | an answered regime never ends | `project_time.py` | RED | TestAskOnce::test_asked_again_when_the_skew_changes |
| 11 | a copied archive passes as in place | `project_time.py` | RED | TestInPlaceArchive::test_simulation |
| 12 | a default overrides a pick | `project_time.py` | RED | TestTheZoneSetting::test_ensure_default_never_overrides_a_pick |
| 13 | a pick does not become the next default | `project_time.py` | RED | TestTheZoneSetting::test_a_new_project_defaults_to_the_last_projects_zone |
| 14 | the zone note even when the offsets agree | `project_time.py` | RED | TestQuietNote::test_another_offset_is_a_note_never_a_warning |
| 15 | a skew read to the second | `project_time.py` | RED | TestCorrection::test_a_measured_skew_reads_to_the_minute |
| 16 | a zone step corrected by the measured skew | `project_time.py` | RED | TestCorrection::test_a_whole_unit_skew_is_corrected_by_the_exact_unit |
| 17 | a first look reports arrivals | `run_arrivals.py` | RED | TestArrivals::test_the_old_runs_of_a_first_look_are_never_arrivals |
| 18 | a burst of folders counts as runs | `run_arrivals.py` | RED | TestArrivals::test_a_burst_of_folders_is_a_copy_not_runs |
| 19 | the watcher never measures its gap | `run_watch.py` | RED | TestArrivals::test_the_watcher_measures_the_gap_between_good_looks |
| 20 | arrivals filed under the open project only | `routes.py` | RED | TestLivePathEndToEnd::test_an_arrival_reaches_its_projects_record |
| 21 | the listener after the ingest kick | `routes.py` | RED | TestLivePathEndToEnd::test_the_watcher_feeds_the_arrival_log_before_the_ingest_kick |
| 22 | the zone picker below the env picker | `_landing_projects.html` | RED | TestLandingAndRoutes::test_the_zone_picker_sits_above_the_env_picker |
| 23 | pages do not carry the zone | `base.html` | RED | TestLandingAndRoutes::test_opening_a_new_project_keeps_the_last_zone_and_every_page_renders_in_it |
| 24 | the landing probes the clock on render | `routes.py` | RED | TestLandingAndRoutes::test_the_landing_never_probes_the_clock_on_render |
| 25 | a pick never asks about the PC zone | `project-time.js` | RED | selfcheck P6: with both offsets and the gap; the question names the city |
| 26 | the old per-browser zone survives a save | `project-time.js` | RED | selfcheck P6: the old per-browser zone is retired (one setting) |
| 27 | the question is never marked shown | `project-time.js` | RED | selfcheck P11: it is marked shown (asked once) |
| 28 | the question pops up every time | `project-time.js` | RED | selfcheck P12: a question already shown does not pop up again |
| 29 | a correction is not shown | `project-time.js` | RED | selfcheck P15: a correction is labelled and keeps the recorded time visible |
| 30 | the render waits for the clock | `project-time.js` | RED | selfcheck P3: the saved zone is shown at once |
| 31 | the dialog has no article box | `project-time.js` | RED | selfcheck P6: the content sits in ONE article box (Pico centres it; loose childr |
| 32 | the watch check reads the browser clock | `project-time.js` | RED | selfcheck P6: the watch check shows now in the chosen zone |
| 33 | the old per-browser zone wins | `app.js` | RED | selfcheck P16: the project zone beats the old per-browser one |
| 34 | a UTC card reads "tz UTC UTC" (server) | `_landing_projects.html` | RED | TestLandingAndRoutes::test_the_zone_picker_sits_above_the_env_picker |
| 35 | a UTC card reads "tz UTC UTC" (client) | `project-time.js` | RED | selfcheck P7b |
| 36 | the question names no run (`from_key` None) | `project_time.py` | RED | TestAskOnce::test_asked_once_stored_and_not_asked_again |
| 37 | the footer falls back to the skewed clock | `project-time.js` | RED | selfcheck P11: the correction starts at a named run |

Two runner lessons, recorded so the numbers can be trusted: the first sweep found mutation 4 GREEN (a naive run clock was refused by the 26 h guard, not by the quality guard; the pin now sees the naive clock at its own instant and is red), and it skipped every multi-line mutation because this checkout is CRLF (fixed in the runner, not hidden). Mutation 25 went red on its assertions (P6: "with both offsets and the gap", "the question names the city"); the harness then crashed on the missing button, which the runner logged as ERROR.

## Not changed here (named, not hidden)

- **The Datasets table's When column, the journal's run times, Trends.** They
  still read the folder clock. S1 (feat/time-s1) is switching those callers;
  the pieces it can reuse: `routes._run_clock_view(run)` (instant, recorded
  clock, correction, header text) and `ProjectTime.applyRunTimes` (`.ts-run`
  spans).
- **clock_health is a stub here.** `project_time.clock_status()` imports
  `core/clock_health` (Codex) when present and reads the agreed interface;
  without it, the OS offset comes from the C runtime and the NTP status is
  "unknown". Pinned against a fake module with the agreed shape.
- **A run seen arriving needs a page open**: the watcher runs while any SM page
  long-polls `/datasets/wait`. No page, no live witness.
- **The PyInstaller bundle** must carry `tzdata` (zoneinfo loads it as data).
  Without it the server checks a zone's shape only and shows no offset; the
  browser still shows every offset. Not verified in a build.
- **The zone is the project's, not the viewer's.** A colleague viewing through
  the url-prefix proxy sees the project zone (the decision); the PC-zone popup
  compares with the SERVER's OS zone and adds "This browser is UTC-x" when the
  browser differs from both.

## For the ledger step

- Order: sort by the run's own instant; mark "order uncertain (+-N min)" with
  `clock_view(project)["uncertainty_s"]` (|skew| of a `small` regime).
- Corrections are a VIEW, never a key: keep `t_utc_us` = the recorded instant,
  apply `correction_for(answers, t)` (runs) / `sm_correction_for(answers, t)`
  (SM events) at read time, label "corrected", keep the original.
- Regimes: `clock.answers[*].from_us / until_us / correction_s / choice`.
- Per-run evidence: `clock.witnesses[*]` keyed `<root>::<date>/<run folder>`
  (run identity includes the root, DESIGN §2 item 3).
- A run that straddles an SM apply (DESIGN §2 item 11) compares SM's own
  stamps with run instants: with a `this_pc` answer, shift SM's stamps by
  `sm_correction_for` first.
