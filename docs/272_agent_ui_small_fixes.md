# docs/272: four small Agent-UI fixes -- one value format, a step's real state, a name per tab, "N waiting" leads to the cards

2026-10-04. Four P2/P3 findings from the agent validation campaign
(`sm_qa_rigs/agent/FINDINGS.md`, stream C):

- **C-12** the pill's / strip's "N waiting" opens `/journal` (the Calibration
  log), which shows no approvals;
- **C-21** a stopped plan's step keeps the running marker ▶;
- **C-23** the person's name lives in one localStorage key shared by every tab,
  so credit goes to the wrong person when two people use two tabs;
- **C-24** values on the cards are formatted inconsistently (`5.71459 G`,
  `3.931e-5` beside `0.000042`).

Codex started the round and stopped on an existing test it could not pass
(`agent_chip_switch_selfcheck`, "0.0000555 ⚠ proposed from 0.000042"). This
round reviewed its work against the findings, kept what held, replaced what did
not, pinned every fix and verified it in a real browser.

## 1. What Codex left, and what happened to it

| Finding | Codex's change | Kept? | Why |
|---|---|---|---|
| C-24 | `valueUnit(path)` in agent.js: a regex copy of `core/units.py`'s field table; formatting through a new options branch of `PlotTheme.siFormat` | **replaced** | A second unit vocabulary (the thing `core/units.py` exists to prevent). It was also applied to node **params**, which are not stored values: `frequency_span_in_mhz=20` would have read "20.0000 Hz" (`/frequency/i` -> Hz), and docs/254 pins params "as sent". agent.js depended on plot-theme.js being loaded; where it was not (two existing selfchecks) every value fell back to `String(v)` -- the "0.0000555" Codex stopped on, and 4 failures in `agent_now_follow_selfcheck` it did not report. plot-theme.js is back to base. |
| C-21 | a running step of a closed plan shows the PLAN's status | **replaced** | The plan's status is not the step's state: a stopped plan's step whose run then finished would read "stopped", one whose run still goes on would lose its ▶. The run registry knows (section 3). |
| C-23 | per-tab sessionStorage seeded from the shared key; a "Records as ... · this tab" label | **kept, extended** | The design holds. But `agent-setup.js` (Connect / Disconnect / Test) and `journal.js` (claims) still read the shared key, so one tab could still record two people. Both now read the tab's name. The name box's tooltip, which Codex had shortened, is whole again. |
| C-12 | pill: swap `href`, remove `hx-get`; strip: link + `showApprovals` | **replaced** | htmx binds a link's request path when it PROCESSES the element; changing or removing `hx-get` afterwards changes nothing, so in Chrome the pill would still have fetched `/journal`. `showApprovals` focused the **Write to chip** button (an Enter after the jump writes to the chip) and set `m.autoscroll = false` forever, which nothing turns back on. |
| tests | `agent_ui_p3_selfcheck.cjs` (20 pins) + wrapper | **rewritten** | 0 of its pins had been mutation-checked. Several pinned the replaced behaviour (params in Hz/µs, plan-status steps, focus on the button) and one was satisfiable without the code under test (the strip's `onclick` attribute was only regex-matched). |

## 2. C-24: one value format, from the unit SM already has

### The unit source

SM's one unit vocabulary is `core/units.py` ("Single source of truth for
physical-unit display across the app"): `_FIELD_FIXED` + `_resolve_field` give a
field its FIXED display unit (T1 always µs, f_01 always GHz, anharmonicity
MHz, durations ns, flux offsets V) and `_FIXED` its decimals. The `qty` Jinja
filter (the inspector, the qubit/pair tables), `unit_hint` (the inspector's
"stored raw in s"), exports and the CLI all read it. Its keys are the
inspector's CURATED keys (`z_joint_offset`), and a qubit pair's own `detuning`
goes through `pair_field_key` (QA F-24: volts, not Hz).

A card holds a full dot path (`qubits.qA1.z.joint_offset`), so two small
functions were added to `core/units.py`, choosing the key exactly the way the
inspector does:

- `path_units_key(path)`: a path matching a curated row's template in
  `param_specs._QUBIT_PROPERTY_MAP` / `_PAIR_PROPERTY_MAP` uses that row's key
  (`z_joint_offset`, `readout_frequency`, `z_delay_ns`, ...); any other path its
  leaf; a field held directly on a pair goes through `pair_field_key`, as
  `routes._build_pair_sections` does.
- `display_spec(path)`: `{"unit", "scale", "dp", "stored"}` from `_FIXED` /
  `_STORED_LABEL` (`shown = stored * scale`, `dp` decimals), or `None` when the
  field has no known unit. Never a guessed unit.

Pinned: for every curated inspector row, `display_spec(path)` gives the unit
and the digits `format_quantity(value, key)` gives -- the card and the
inspector row for the same leaf cannot disagree.

### How it reaches the card

The JavaScript holds no vocabulary. The panel's feed (`/api/agent/chat/cards`)
annotates each value row with `display` (`agent_api._feed_display`): an
approval's writes, a run's writes, a plan's "may change" rows. Rows are copied;
the stored approval records, the run registry's own dicts and every MCP-visible
view (`/runs/agent`, `/run/<key>`, `/plans`, `/approvals`) carry no display
field -- an agent reads stored values, not display text. The panel only takes
plans, runs and approvals from this feed (`absorb`), so no card is ever drawn
from an un-annotated copy.

### The rule (agent.js `fmtRow`) [derived, UI rule]

One row's values are formatted TOGETHER:

- with a `display`: `value * scale` in that unit with the field's decimals; ns
  durations print as integers unless one of the row's values is fractional (then
  1 decimal, as `units._fmt_duration_ns`); if two DIFFERENT values would print
  alike, decimals are added (at most 6 more) until they differ;
- without: integers exactly as stored (`1000000 → 2000000`); otherwise 5
  significant digits of the row's largest value, exponential outside
  [1e-3, 1e6) (the bounds of `units._plain`), then trailing zeros that EVERY
  value of the row carries are dropped (`0.10 → 0.11`, not `0.10000 → 0.11000`).

So old and new of a row always share unit and precision, and a real change is
never shown as "X → X".

Where it applies: the approval row (now / proposed-from / proposed), the run
card's writes (`old → new`, the stored values in the cell's title), the plan's
"now". The approval's input stays the RAW stored value, because that is what is
written (docs/254: an approval is what the person saw). It names its stored unit
(`s`), the formatted value sits beside it, and that value follows what is typed
(`apPreview`). `approve()` parses the input with the same `parseEdit` the preview
uses, so Write sends exactly what the preview showed.

Params stay as sent (docs/254, pinned by `agent_params_selfcheck`): a param is
not a stored value, SM holds no unit for it, and a node names its own
(`frequency_span_in_mhz`).

### Before / after

Executed, not reasoned: one script ran base `fmtNum` (c7f21c10), Codex's
formatter (its `valueUnit` / `fmtNum` / `siFormat` options branch, verbatim from
its diff) and the new `fmtRow` on the same values. "Codex" is with plot-theme.js
loaded (the real page); without it (the selfchecks) Codex printed `String(v)`.

| Row (values) | base | Codex | now |
|---|---|---|---|
| approval T1 moved (5.55e-5 / 4.2e-5) | `5.550e-5 ⚠ proposed from 4.200e-5` | `55.5000 µs ⚠ proposed from 42.0000 µs` (without plot-theme.js: `0.0000555 ⚠ proposed from 0.000042`) | `55.50 µs ⚠ proposed from 42.00 µs` |
| approval T1 on the rig: now / from / proposed | `3.500e-5`, `4.200e-5`, input `0.000033` | `35.0000 µs`, `42.0000 µs`, `33.0000 µs` | `35.00 µs ⚠ proposed from 42.00 µs`, `33.00 µs` [`0.000033`] `s` |
| run T1 (the finding's 3.931e-5 -> 4.2e-5) | `3.931e-5 → 4.200e-5` | `39.3100 µs → 42.0000 µs` | `39.31 µs → 42.00 µs` |
| f_01 +12 kHz (rig) | `5.07871 G → 5.07872 G` | `5.07871 GHz → 5.07872 GHz` | `5.07871 GHz → 5.07872 GHz` (4 dp would print both `5.0787`: one decimal added) |
| f_01 +10 MHz (integer Hz) | `5714590000 → 5724590000` | `5.71459 GHz → 5.72459 GHz` | `5.7146 GHz → 5.7246 GHz` |
| x180 length | `40 → 44` | `40.0000 ns → 44.0000 ns` | `40 ns → 44 ns` |
| z.joint_offset (rig) | `0.004355972 → 0.0045` | `0.00435597 → 0.00450000` (no unit: the leaf `joint_offset` is not in its table) | `0.0044 V → 0.0045 V` |
| pair detuning | `0.1 → 0.2` | `100.000 mV → 200.000 mV` | `0.1000 V → 0.2000 V` |
| amplitude (no unit) | `0.3701739 → 0.373876` | `0.370174 → 0.373876` | `0.37017 → 0.37388` |
| param `frequency_span_in_mhz` | `=20` | `=20.0000 Hz` (wrong: it is MHz) | `=20` (as sent) |

The units and digits are the ones the inspector and the tables print for the
same leaf: T1 `55.50 µs` (2 dp), f_01 4 dp in GHz, a pair's detuning `0.1000 V`.
A frequency therefore shows fewer digits than Codex's 6 significant (`5.7146`
vs `5.71459`), as in the qubit table. Digits are added only where two values of
a row would otherwise read alike, and the exact stored values stay in the
approval's raw input and in each run row's title.

### The existing chip-switch assertion

`agent_chip_switch_selfcheck` passes **unedited**. Its subject is "a moved leaf
is flagged with what it was proposed from"; its fixture rows carry no
`display` (they are not from the feed), so they take the no-unit rule:
`5.55e-5 ⚠ proposed from 4.20e-5` -- one precision, still matching its
`/5\.55/` and `/4\.2/`. The unit form of the same row is pinned in the new
selfcheck with `display` set, as the feed sends it. No existing test was edited.

## 3. C-21: a step shows its run's real state

SM stops moving a step once its plan has ended (`agent_runs._plan_step`: "a
draft or a closed plan never moves"). `agent_plans.stop` cancels PENDING steps
but leaves a running one `running`, so after Stop the record says "running"
forever. The step carries `run_key`; the run registry (`live.runs`) holds that
run's truth. `stepState(s, p)`:

- plan running / stopping / draft, or the step not "running": the step's own
  status (unchanged);
- closed plan, run still `starting`/`running`: ▶ stays, titled "running -- its
  run goes on; the plan was <status> while this step ran" (the marker is true);
- closed plan, run ended: the run's outcome (`done` ✓, `cancelled` —, `failed`
  ✗, `interrupted` ⏹), titled "<outcome> -- how its run ended; ...";
- closed plan, run no longer listed (registry keeps the last 50 / feed the last
  20): `stopped` ⏹, titled "the plan was stopped while this step ran; its run is
  no longer listed".

Client-side only; no record is rewritten.

## 4. C-23: a name per tab

`actorName()` is sessionStorage-first (a browser keeps it per tab), seeded once
when agent.js first runs in the tab -- on every page, since agent.js is a core
script -- from the shared `quam_actor_name`, which stays the default a NEW tab
starts from and which `setActor` keeps current (backward compatible: a name
stored by an older SM is still read). If sessionStorage is blocked, the shared
name is used (the old behaviour) rather than "human". The composer shows
`records human:Kim · this tab` (the server records `human:<X-SM-Actor>`,
`routes._request_actor`). `agent-setup.js` and `journal.js` read the name
through `AgentPanel.actorName`, so Connect / Disconnect / a claim with an empty
"Who" box record the same person as Stop and Start in that tab; the journal's
claim box is prefilled with it, so what an empty box records is visible.
A tab duplicated by the browser copies its sessionStorage and starts with the
same name; after that the two are independent.

The shared key is still read through the literal
`asciiActor(localStorage.getItem("quam_actor_name"))`: an existing pin
(`test_actor_plumbing::TestTheNameBoxIsEnglishOnly`) holds that the stripper is
on the READ path. A first refactor through a helper kept the behaviour but not
the text; the first pytest run caught it and the literal is back (the pin is
unedited).

## 5. C-12: "N waiting" leads to the cards

- The pill (`agent-pill.js`): while its state is `waiting` its link is
  `href="/agent#approvals" hx-get="/agent" hx-push-url="/agent#approvals"`,
  otherwise `/journal` as before; a changed link is re-processed with
  `htmx.process` (only when it changed).
- The strip's `waiting N` is the same link.
- In place: when a VISIBLE panel already holds the cards (the Agent page, or an
  open float the link sits in), the click brings the first approval card into
  view in its own scroller and focuses the CARD (`tabindex=-1`), never Write to
  chip. The strip stops htmx's own handler (`stopImmediatePropagation`); the
  pill does it from a capture-phase listener.
- Elsewhere (or a closed float), the link navigates; on `#approvals`, each
  panel reveals the card once after its first render, then leaves the person
  alone. The panel's follow-the-bottom is not touched.

## 6. Verification

### Pins

`tests/agent_ui_p3_selfcheck.cjs` -- 45 pins on the real agent.js,
agent-pill.js, agent-setup.js and journal.js under jsdom. Each "tab" is its own
JSDOM window with its own sessionStorage over one shared localStorage;
`runScripts: "dangerously"`, so the cards' real inline `onclick` / `oninput`
attributes run; the htmx stub binds a click handler to every `[hx-get]` it is
handed, as htmx does, and records the navigations htmx would have made.

`tests/test_agent_ui_p3.py` -- 22 tests: 12 known paths and 6 unknown ones
through `display_spec`; the pair-detuning leaf rule off the curated map; every
curated inspector row against `format_quantity`; the feed route
(`/api/agent/chat/cards`) annotating approvals, runs and may-change rows while
the stored records, the registry and four MCP views stay unannotated; the
selfcheck wrapper.

### Mutations

A scratch script applied each mutation to the real file, ran the pins it
targets (the jsdom selfcheck, or the pytest class), required EVERY pin it named
to go RED, and restored the original bytes (sha256 of all six source files
compared after the sweep: identical).

**53/53 mutations killed. 45/45 jsdom pins and 21/21 pytest pins were each
turned RED by at least one mutation** (the 22nd pytest test is the wrapper
around the 45). The first sweep was not clean, and what it found was fixed
before the final one:

- m40 (the strip click no longer stops htmx's handler) stayed GREEN: an inline
  handler's `return false` already prevents the default, and the first harness
  had no htmx. In Chrome htmx's own listener would still have re-fetched
  `/agent`. The stub now binds a click handler the way htmx does, and the pins
  assert that no htmx navigation happens on an in-place reveal.
- p02 (the leaf rule's pair handling) had no pin that could reach it: the
  curated map catches `qubit_pairs.<p>.detuning` first. Pinned with the curated
  map emptied (`test_a_pairs_own_detuning_is_volts_even_off_the_curated_map`).
- An unknown-unit integer pin used 100000, which the fixed-point rule also
  prints as `100000`: the integer branch was unguarded. It is 1e6 now
  (`1000000 → 2000000`; without the branch, `1e+6 → 2e+6`).
- A pin draft that could never fail (`... ? true : true`) was deleted before
  the first run; a "seeded at load" pin was added because nothing noticed the
  eager seed being removed (m24).
- The expectation tables of m21, m25, m28, m33 and m09 over-claimed one or two
  pins each; every such pin is killed by another mutation (m26, m29, ...).

| Mutation | | pins RED |
|---|---|---|
| m01 | closed-plan branch gone | 5 (jsdom) |
| m02 | run registry not consulted | 3 (jsdom) |
| m03 | a stopping plan treated as closed | 1 (jsdom) |
| m04 | other steps lose their own title | 2 (jsdom) |
| m05 | no glyph for stopped | 1 (jsdom) |
| m06 | the feed's unit ignored | 8 (jsdom) |
| m07 | distinct values may print alike | 2 (jsdom) |
| m08 | fractional ns rounded away | 1 (jsdom) |
| m09 | shared trailing zeros kept | 2 (jsdom) |
| m10 | no exponential below 1e-3 | 1 (jsdom) |
| m11 | integers not as stored | 1 (jsdom) |
| m12 | moved title swaps the two values | 1 (jsdom) |
| m13 | no formatted proposed value | 4 (jsdom) |
| m14 | stored unit not named at the input | 1 (jsdom) |
| m15 | stored values off the run row title | 1 (jsdom) |
| m16 | plan now without its unit | 1 (jsdom) |
| m17 | params reformatted (Codex's version) | 1 (jsdom) |
| m18 | preview does not follow the input | 1 (jsdom) |
| m19 | Write ignores the edited value | 1 (jsdom) |
| m20 | a unit guessed when the feed names none | 1 (jsdom) |
| m21 | the shared key again (no per-tab name) | 6 (jsdom) |
| m22 | setActor forgets the tab | 2 (jsdom) |
| m23 | setActor does not move the new-tab default | 2 (jsdom) |
| m24 | seeded at first use, not at load | 1 (jsdom) |
| m25 | the label says nothing about the tab | 1 (jsdom) |
| m26 | label not refreshed on setActor | 2 (jsdom) |
| m27 | setup reads the shared key | 1 (jsdom) |
| m28 | journal reads the shared key | 1 (jsdom) |
| m29 | journal prefill from the legacy key only | 1 (jsdom) |
| m30 | blocked tab storage records no one | 1 (jsdom) |
| m31 | the seed is the shared name, not the tab's | 1 (jsdom) |
| m32 | seed not stored for the tab | 1 (jsdom) |
| m33 | the pill still opens the log | 5 (jsdom) |
| m34 | htmx not told the link changed | 4 (jsdom) |
| m35 | an unchanged link re-processed | 2 (jsdom) |
| m36 | the pill never goes back to the log | 1 (jsdom) |
| m37 | the pill click always navigates | 2 (jsdom) |
| m38 | the jump focuses Write to chip | 4 (jsdom) |
| m39 | the strip still a plain count | 2 (jsdom) |
| m40 | the strip click not stopped | 1 (jsdom) |
| m41 | a closed float counts as visible | 1 (jsdom) |
| m42 | #approvals ignored on arrival | 1 (jsdom) |
| m43 | reveal on every poll | 1 (jsdom) |
| p01 | curated keys skipped | 4 (pytest) |
| p02 | a pair's own detuning not volts | 1 (pytest) |
| p02b | curated pair key not mapped | 2 (pytest) |
| p03 | the stored unit shown instead of the display unit | 9 (pytest) |
| p04 | ns shown as seconds | 3 (pytest) |
| p05 | an unknown path gets a guessed unit | 7 (pytest) |
| p06 | decimals not the inspector's | 1 (pytest) |
| p07 | the feed carries no unit | 1 (pytest) |
| p08 | run rows annotated in place (the registry's own dict) | 1 (pytest) |
| p09 | MCP run views annotated too | 1 (pytest) |


### Test files

All in the `cqt` env, on the final code, no existing test edited:

- every `tests/agent_*selfcheck.cjs` (13 files) and `journal_page_selfcheck.cjs`
  (journal.js was touched): 14/14 exit 0, 466 `ok` lines, 0 FAIL. This
  includes `agent_chip_switch_selfcheck` (11/11) and `agent_now_follow_selfcheck`
  (5/5), the two Codex's version broke;
- pytest, 39 files: every file under `tests/` mentioning `agent_panel`,
  `agent_pill`, `agent.js`, `actor` (as a word) or `chip_switch` (30 files),
  plus the ones covering the other files touched (`test_units`,
  `test_physical_units`, `test_pair_detuning_unit`, `test_value_delta`,
  `test_cli`, `test_journal_page`, `test_bundles`, `test_dbm_pulse_class`,
  `test_mcp_bridge_p2`): **1195 passed, 8 skipped, 0 failed** (6 min 19 s).
  The first run of the same set had 1 failure, the source-text pin in
  `test_actor_plumbing` (section 4), fixed in the code.

The real-browser round below ran before that last, text-only change to
`actorName` (same behaviour: the C-23 pins and the seven C-23 mutations were
re-run on the final code, 7/7 RED).

### Real browser

Headless Chrome 154 over CDP on 9455 (`tests/browser/journeys/cdp.cjs`, real
mouse clicks and real text input). SM served from this worktree on 5135 by
waitress (cqt env), with a sandboxed `USERPROFILE`/`HOME` and a fresh instance.
The chip was a COPY of `sm_qa_rigs/agent/rC/chip` in
`sm_qa_rigs/codex/agui_rig/` (wiring network kept at `127.0.0.1:1`; the copy's
`extras.data_folder` pointed at a copied `data/`). Cards were seeded through
SM's own writers (`approvals.add`, `agent_plans.add/update`, a run meta): a
writes approval on qA1 whose T1 moved since the proposal, a stopped plan whose
running step's run ended cancelled, a stopped plan whose run is no longer
listed, a draft plan. The feed on that chip carried `display` exactly as
`units.display_spec` gives it (T1 µs, f_01 GHz, joint_offset V, amplitude
none; may-change rows f_01, xy.RF_frequency, T2ramsey).

All 23 journey checks passed:

- C-24: the approval rows read `35.00 µs ⚠ proposed from 42.00 µs` /
  `33.00 µs [0.000033] s`, `5.07871 GHz` / `5.07872 GHz [5078722199.903097]
  Hz`, `0.0044 V` / `0.0045 V`, amplitude `0.35183` / `0.35535` with no unit;
  the run card `35.00 µs → 33.00 µs` (title `stored: 0.000035 → 0.000033`),
  `40 ns → 44 ns`; the draft's `now 5.0787 GHz`, `now 47.33 µs`; params
  `frequency_span_in_mhz=20 num_shots=100000`; typing `0.00005` (real key
  input) turned the preview to `50.00 µs`.
- C-21: the stopped plan's step `— | cancelled -- how its run ended; the plan
  was stopped while this step ran`; the other `⏹ | stopped -- ...; its run is
  no longer listed`; no ▶ on a closed plan.
- C-12: on the Agent page the strip's `waiting 1` brought the approval card to
  8 px from the top of the cards and focused it, same URL, no reload (a
  window marker survived). From the Calibration log, the pill (`1 waiting`)
  landed on `/agent#approvals` with the card in view and focused; Back ->
  the Calibration log intact, its pill still leading to the approvals; reload
  -> intact; a full load of `/agent#approvals` -> the card revealed.
- C-23: tab 1 typed `Kim`; a new tab started as `Kim` and typed `Lee`; tab 1,
  reloaded, still `records human:Kim · this tab`. A real server record from
  each tab (the draft plan's mode change), from the instance journal:
  `mode set to ask-all by human:Kim` (tab 1), `mode set to auto by human:Lee`
  (tab 2).
- Console errors: 0 in both tabs over the whole journey (after cdp.cjs's
  documented CSP-eval filter).

Screenshots, read one by one: `sm_qa_rigs/codex/agui_shots/` `01`-`10`
(cards, the typed preview, stopped plans, strip in place, the log before the
pill, pill -> card, back, reload, the two tabs), with `journey_log.json` and
`c23_journal_lines.txt`. The server and Chrome (process trees started for this
round only) were stopped, and the rig copy and its Chrome profile deleted.

The red banner in the screenshots is the copied chip's own pre-existing
diagnostics error (`qubits.qB3.xy.RF_frequency` against its port LO), not this round.


## 7. Not done here

- The floating Agent panel was not looked at in Chrome. Nothing in the shell
  opens it any more (customer feedback 2026-09-08 made the Agent a page; the
  only `toggleAgentPanel()` caller left is the float's own close button). Its
  paths are pinned under jsdom: a closed float never swallows a "waiting"
  click (`c12-a-closed-float-does-not-swallow-the-click`), and an open one
  reveals in place through the same `revealApprovals`.
- Run-request approvals (kind `run`) show no value rows; nothing to format.
- The approval's raw input is still a plain text box: a value typed in another
  unit (`33 µs`) does not parse as a number and is sent as the string, as
  before. The only new signal is that the formatted value beside the input goes
  blank for it; converting typed units was out of scope.

## Files

- `quam_state_manager/core/units.py`: `path_units_key`, `display_spec`.
- `quam_state_manager/web/agent_api.py`: `_with_display`, `_feed_display`
  (`chat_cards` only).
- `quam_state_manager/web/static/agent.js`: `fmtRow` / `fmtNum`, `apTexts` /
  `apPreview` / `parseEdit`, `stepState`, per-tab `actorName`, `revealApprovals`
  / `showApprovals`, the strip link.
- `quam_state_manager/web/static/agent-pill.js`: `DEST`, `setDestination`, the
  capture-phase click.
- `quam_state_manager/web/static/agent-setup.js`, `journal.js`: the tab's name.
- `quam_state_manager/web/static/style.css`: the proposed-value cell, the actor
  label.
- `tests/agent_ui_p3_selfcheck.cjs`, `tests/test_agent_ui_p3.py`.
