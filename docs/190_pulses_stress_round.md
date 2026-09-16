# docs/190 — twelve users on one Pulses page, and what they broke

**2026-09-16 → 17, user-ordered.**

> opus, sonnet을 적절히 잘 섞어서, SM을 스트레스 테스트 하자. 1. pulses 의 각 기능 들을
> 클릭해보고 pulses 페이지를 완벽하게 검증해. 아마 숨겨진 버그들 많을 거야 ||| ONLY after
> this … 2. agents 메뉴 기능을 동일하게 스트레스 테스트 해. |||| 스트레스 테스트가 뭔지
> 알지?? 적어도 여러 유저의 행동들을 따라하면서 해야 해!!

A stress test is many users at once. This round put **twelve simulated users on one
server at the same time**, each a persona with a job (a first-time explorer, a physicist
editing every field with every edge value, a creator, a renamer/deleter, an impatient
keyboard user, two lab-mates on one chip, a lab-class/verify user, a pointer user, an
API abuser, a long-session searcher, a physicist checking the numbers against the lab's
own quam, a visual reviewer reading every screenshot), all in **real headless Chrome
over CDP with real mouse and key events**, against the shipped code (origin/main
`ec665fb`) serving a COPY of the customer's real 5-qubit chip (151 pulses, 10 classes,
4 of them the lab's own). Nothing was mocked, and nothing of the customer's was touched.

The session limit killed 11 of the 12 twice. Their transcripts were mined instead of
re-run, and every persona kept a running findings file after the first cut. **57
distinct findings** came out (3 critical, 25 major, 26 minor, 3 cosmetic). This entry
records the ones that were real, what each cost, and how each is pinned. The
consolidated list and every persona's notes live in the session scratchpad (not shipped).

## 1. The three server crashes were all one shape

A value the page let through reached code that had never been handed it:

| | what a user typed | where it died | now |
|---|---|---|---|
| F02 | `inf` / `nan` in a length or amplitude box | `waveform_synth._coerce_param`: `int(float("inf"))` → `OverflowError` → **500** from `/api/pulse/synth`, whose docstring promises "always 200 with ok/error inside" | the float kind refuses a non-finite; the caller's except names `OverflowError`; the route is wrapped so *no* exception is ever a 500 there (`test_synth_route_never_500s_on_an_unexpected_exception`) |
| F02′ | finite params that overflow inside the synth (a DRAG pulse with amplitude 1e308 and anharmonicity 1e-300 — measured, it does) | `jsonify` emitted a bare `Infinity` token, which is not JSON: the page's `response.json()` threw and the plot went blank | `_shape_payload` refuses a non-finite sample as an ordinary `ok=False` payload |
| F03 | Enter on an unchanged name in the Rename box | `modifier.rename_subtree` raises `ValueError("old and new paths are identical")`, caught nowhere → **500** | an early "Already named 'x'" 409 (and the catch names `ValueError` too) |
| F50 | `nan` in the Gaussian CZ form | `_num()` accepted `float("nan")`, the type policy refused the write, the rollback branch answered **500** | the parser rejects non-finite; the rolled-back validation failure is a 400 |

Pins: `TestPulseSynthApi` (+6), `TestPulseRename.test_rename_to_own_name_is_a_409_not_a_500`,
`test_gaussian_cz.py::test_nan_and_inf_are_not_numbers`. Mutation sweep 8 of 8 RED —
one guard (`int` kind, non-finite) went GREEN under mutation because the widened
`except` already covered it, and was **dropped** rather than pinned vacuously.

## 2. The critical one: a re-link that landed on a field the user never opened (F01)

Persona p08 re-linked `q5·y90.amplitude` from `#../x90/amplitude` to
`#../x180/amplitude`, pressed the tray's ⚡ Apply, and read the disk: **y90 still held
the old pointer, and x90.amplitude — a literal the user never touched — had become the
new pointer string.** The UI hid it: y90 still resolved through x90, which now resolved
through x180, so the number on screen was right.

Mechanism: `_capture_change_log_as_updates` tagged every edit `"set"`, and
`_replay_updates`'s `set` branch — the path every pull-and-re-apply and every conflict
retry takes — re-resolves `dot_path` through the pointer **currently on the freshly
pulled state** and writes the captured value *there*. Right for a value-mode edit
(which logs the resolved target); wrong for a re-link or a break-link, whose value
belongs AT the leaf.

Fix: a fourth op tag, `"literal"`, assigned when the entry's new value *or* old value
is a pointer string (the value-mode path never produces such an entry: it logs the
target). `_merge_reapply` composes it like `set`; `_replay_updates` writes it at the
leaf with `coerce=False`. Pinned in `tests/test_replay_ops.py` by the re-link, the
break-link, and the value-through-a-pointer case (unchanged semantics); mutation 2/2
RED. **Verified in real Chrome with a drifted live file**: an experiment wrote `T1`
between the re-link and the Apply, the Apply pulled and replayed, y90 carries the new
pointer on disk, x90 is untouched, the experiment's write survived.

## 3. Two windows, one change log (F06, F07, F05)

Ctrl+Z popped the top of the ONE shared change log whoever pressed. p01 undid
`cz_bipolar…axis_angle → 0.0` from a pulse it had never opened; p06's lab-mate kept a
value on screen that no longer existed anywhere. The docs/120 doctrine — *a press means
what the presser could see* — guarded Apply and `/state/sync` but not `/undo`.

Fix: the UndoQueue declares the tray's `data-change-sig` (docs/179) as `expect_sig`;
`/undo` refuses ONCE with a 409 that names the foreign path, triggers a tray refresh in
that window (`HX-Trigger: undo-foreign`), and the next press undoes what the window
now shows. A client that sends nothing behaves exactly as before. **The base.html trap,
fourth field**: the full-page render stamped `change_sig=""`, so a window opened before
any edit existed declared nothing and the gate waved it through — measured in the
browser before the pin caught it. Pinned in `TestCtrlZDeclaresWhatItSaw` (5) and
verified in two real tabs. F05 (a passive tab's tray is stale until it acts) is handled
in §7 below.

## 4. What the lab's own classes exposed (F12, F13, F14, F23, F25)

- **F12** — a two-flux CZ (`CZGateTwoFlux`) pulses BOTH qubits: `flux_pulse_target` is a
  real `SNZTwoFluxPulse` with its own amplitude, 37 of them on the pilot chip, and
  `pulse_index.GATE_SLOTS` did not know the slot. No row, no search hit, no detail.
  Added everywhere the two known slots are named (index, path regexes, row chip
  `flux·target`, create-form slot list, catalog context, `pulseRootOf`). 151 → 156 rows.
- **F13/F14** — `+ new gate` wrote the five flux macros as bare dicts (no `__class__`,
  no `id`; the CR/Stark/parametric branches always had them), and the flat-top coupler
  slot got a classed `_FlatTopGaussianPulse` with only `amplitude`, which quam 0.6.0
  refuses to load (`flat_length` has no default). The macro now carries the chip's own
  CZGate class (majority evidence, verbatim) or the modern canonical, and the coupler
  slot shares the qubit pulse's timing through pointers as the bipolar branch does.
- **F23** — `QuamStore.reload()` nulled the generated config, and every `/state/sync`
  pull — including another window's — goes through it; the SNZ detail regressed to
  "Generate now" with no notice. The cache is basis-hash-keyed and every reader already
  checks `_config_stale`, so it is kept, and the rebuild re-warms it when stale.
- **F25** — p11 compared SM's synth against `waveform_function()` in the lab env and
  found the two deprecated flux classes (`_FlatTopGaussianPulse`, `_CosineBipolarPulse`)
  shifted by `pad // 2` samples: SM centred the zero padding, **quam 0.6.0 puts all of
  it after** (`np.concatenate((waveform, zero_padding))`, read in three customer envs).
  The golden was captured on quam 0.5.0a3, which centred. Regenerating it in the lab's
  KRISS_CZ env changed exactly the four padded cases and nothing else — the
  measurement that settled it. Mirrors fixed, golden replaced (70 cases, quam 0.6.0).

## 5. The rest of the majors, each one line

- **F04** a 260 px Plotly preview overflowed a 220 px box onto the Create button: a real
  mouse click at the button's centre hit the chart; keyboard submit worked, so three
  personas saw a "silent no-op". CSS clamp (`style.css`), verified by `elementFromPoint`.
- **F08/F10** "Generate now" stayed disabled after one failure, and the failure was a
  300-char flattening of the traceback with a wrong remedy ("Choose environment"). The
  button re-enables; the server's own rendered error is shown; the env link only when
  the server says no env.
- **F11** a pointer to a *non-shared* target had no "write at …" disclosure (the line
  was gated on `shared_by`); typing 600 into `saturation.length` changed `q1.f_01`.
  Every resolved pointer discloses now (runtime self-pointers excepted).
- **F16** a double-click on Duplicate: the first request created the copy, htmx dropped
  its response, the second answered 409 — the LAST thing on screen was a failure for an
  action that succeeded. `hx-sync="this:drop"` + `hx-disabled-elt` on the three forms.
- **F19** the collapsed tray was `max-height:0`, not `inert`: Tab landed on invisible
  Discard buttons and Enter discarded a pending edit with nothing painted.
- **F24** `#status-bar > * { pointer-events:auto }` made the whole toast box swallow the
  click that was correcting the input under it, for 6 s. Only the ✕ takes events now.
- **F26** PaneState's SOFT tier captured the search text but not the channel tab or the
  owner pick, and its dispatched `input` never refetched the rows (the box fetches on
  `keyup`). All three are captured; the rows refetch once. Verified: 15 filtered rows,
  XY tab, text — after a sidebar re-click.
- **F27** a re-link to a bare `#../` (a container) was accepted; the field became a dump
  of its parent dict and one Ctrl+Z did not restore it. Refused up front; dangling
  pointers stay allowed.

Minors landed in the same pass: F30 (toasts showed `&#39;` — entities decoded), F33
(a refused input is marked `aria-invalid`), F35 (the h2 count froze on a partial
refresh — OOB from the rows partial), F41 (Escape closes the inspector), F42 (a
cancelled Rename draft greeted the next open).

## 6. Measured

Batch-1 browser verification on the reset rig: **17/17** single-tab checks and **11/11**
two-tab / drifted-live checks pass (`verify_r1.py`, `verify_r2.py` in the scratchpad).
Test baseline for the pulse-related set: 379 → **819 passed** across the touched areas,
131/131 jsdom selfchecks green.

## 7. Round 2 — ten fix agents, and what the weekly limit did to them

Ten agents were launched on the remaining findings, one worktree and one server
port each. **Nine died on the account's weekly usage limit within fourteen
minutes**; one (F46) finished and came back with a refutation worth more than a
fix: the "same error toast twice" was the persona's own query
(`.toast,[role=alert],#status-bar` matches the toast AND its container, whose
`innerText` repeats the child's), and the suspected cause was refuted too —
`#inspector-pane` is not in the htmx error-swap allow-list, so the route's own
`_status.html` body never reaches the DOM. **Not a defect. No code changed.**

Everything after this point was done by hand, in the same browser.

## 8. The sweep the user actually asked for

> 스트레스 테스트가 뭔지 알지?? 적어도 여러 유저의 행동들을 따라하면서 해야 해!! …
> 한 두명의 유저라도, 진짜 이것저것 다 클릭해보고 … 정말 다양한 기능들을 이리저리
> 전부 클릭하고 엔터누르고 키보드 직접 방향키 누르고 등등 다 진짜 해보는거야

Round 1 was twelve users at once. That is not what was asked for. This round is
**one user pressing everything**: an inventory of every interactive element in
every state of the page (171 distinct visible controls), then each one acted on
with a real mouse or a real key, with the DOM, the URL, the tray, the disk and
the console read after every single action.

What it covered: every channel tab twice, every sortable header three times,
pagination and all four rows-per-page options, seventeen search strings
including the grammar's own operators and a 300-character one, the chip map
(stone, second stone, pair edge, toggle-off, the owner chip's ×, collapse),
the compare bar, the page-header toggle, all four topbar tools, `/` and `?`,
a sixty-stop Tab walk, the arrow keys, Ctrl+Z / Ctrl+Shift+Z from the body and
from inside an input, every header button of the detail with its Escape, a
sixteen-value battery on **every** editable field of a pulse (0, -1, 1e9, 100.5,
2.5e-1, `abc`, empty, whitespace, nan, inf, -inf, a pointer, `1,5`, `0x10`,
`1 2`, an Arabic-Indic digit), Enter vs Tab vs Escape, every slider, the section
summary, the in-view picker, sixteen create classes × three target kinds, a
twelve-name validation battery, the Gaussian CZ form, double-clicks on every
mutating button, a row click with an uncommitted edit, the Review tray
(open/discard/apply), the value-history clocks, Verify vs config on both class
kinds, the plot's own interactions, and the layout at three viewport sizes.

**Ten more defects, all reproduced in the browser, all fixed and pinned:**

| | what a user does | what happened |
|---|---|---|
| N1 | Escape with Settings / Calculator / Versions / issues open | nothing closed |
| N2 | page 2 of 4, then reload or share the URL | back to page 1; the URL never carried `page` |
| N3 | Escape with the Rename box open | the WHOLE inspector closed (§5's F41 fix, too eager) |
| N4 | Enter on a value you did not change | a phantom edit in the Review tray, in the undo stack, in the next Apply |
| N10 | hover any pulse value | no 🕘 — the one surface in the app without value history (measured: 545 clocks on the grid, 0 here) |
| F18 | open any pulse at 1280x800 | zero property rows on screen; the 260 px plot owned the pane |
| F20 | `+ Gaussian CZ…` at 1000x700 | the only submit button below the fold |
| F53 | the same panel | its × on a line of its own, unlike every other inspector header |
| F21 | click another row with a field mid-edit | 9 of 10 times the click was swallowed |
| F36 | sort by length, then search | the sort silently gone |
| F29/F40 | open a pulse you searched for by name | "+ add pulse…" empty — the picker only knew the rendered rows |
| F54 | `target_kind=garbage` from a script | a qubit pulse created anyway |

The two that matter most are **N4** and **F21**, because both are silent. A
physicist tabbing through a pulse to READ it was leaving a trail of edits behind
(`set_value` logs a ChangeEntry unconditionally; the per-field commit door now
compares against the value the user is LOOKING at — a pointer leaf resolves
first, and int→float of the same magnitude is still a real change because the
type moves on disk). And a row click during an edit was a race between the
blur-commit's response and the row's own: the commit is issued first and
re-renders the OLD pulse, so it lands second and wins. The row is what the user
pointed at, so it wins now — decided by the response that lands
(`pathInfo.requestPath`), not by the DOM, because the commit's own swap removes
the form that a DOM check would look for.

**Corrections to my own findings, recorded because the method is the point:**
three "defects" (the tray drawer rendering no rows, Apply writing nothing, the
chip map having no clickable stones) were **my driver**, not the product:
`click_text` matched the ancestor `<li>` that also contains the button's text
and clicked empty space, and one probe toggled the map CLOSED before clicking
into it. Both are fixed in the rig (deepest-match, open-state check). A fourth,
`?` not opening the cheat sheet, is documented behaviour — it focuses the Agent
composer when one is on screen.

## 9. Measured

| | |
|---|---|
| batch-1 browser checks | 17/17 single-tab, 11/11 two-tab / drifted-live |
| batch-2 browser checks | 10/10, then 6/6 on the layout round, 6/6 on the race |
| mutation sweeps | 8/8, 2/2, 6/6, 7/7, 5/5, 4/4 — every fix red under its own mutation |
| jsdom selfchecks | 132/132 |
| pytest (24 affected files) | 1,269 passed, 2 failed — both the `test_web` pair documented as pre-existing in docs/148 |

`tests/escape_ladder_selfcheck.cjs` is new and drives the real `app.js`: the
Escape ladder, the URL sync, the row-click race and the sort memory, because
four of the first pins for these were source greps that a comment edit would
have broken and a behaviour change would not.

## 10. The second batch, by hand (2026-09-17)

The weekly limit took the subagents away, so these were reproduced, fixed,
pinned and mutation-checked one at a time in the same browser.

**F15 — the commit ate a keystroke.** Typing 530, Enter, then select-all and
540 wrote **53540** to the chip. The buffering theory was wrong twice before
the trace settled it: the focus restore hung on htmx's `afterSettle`, a tick
after the content lands, so for 20–120 ms after every commit focus was on
`<body>`. The Ctrl+A went to the DOCUMENT and the Backspace after it ate a
digit of the committed value. Focus now comes back with the content
(`afterSwap`), the settle passes still run for the scroll position (which needs
the final layout), and anything still typed in the hole is buffered and
replayed onto the node that survives. Measured 8/8 clean at zero delay, where
the first trial had been corrupt.

**F05 — a window that is only looking.** Tab B showed 0 unsaved changes and
0.3046 while tab A had staged 0.311, and still did twelve seconds after A wrote
it to the chip. The every-page drift poll carries the change SET now, and a
signal refreshes that window's tray and the values on screen. Two decisions
worth recording: the key is the change *signature*, not the store's mutation
counter (a counter also moves when another window merely OPENS the chip, and a
passive pane must not re-fetch for that — measured, it made the pane blink);
and the pane is only re-read for a window with nobody in it (no keystroke, no
click, no focus inside it for two seconds), so a reader mid-typing keeps their
text. Verified in two real tabs: B's tray and row follow within two seconds
while its search, its open pulse and its half-typed value are untouched.

**F22 — a refused revert that said nothing.** Two halves. The control is
hidden while anything is pending (by design), and with F05 in place a stale
window learns that within seconds. The half that was real: htmx drops 4xx
bodies, and only 409 was allowed through to `#status-bar`, so a forced revert
whose snapshot had been pruned answered 404 *with the reason* and the user got
an empty bar. Any 4xx from that door renders now.

**F17 — three labels, one baseline.** The crossings of one CZ branch are the
same physics a few MHz apart, so three 55 px labels landed within ~90 px and
read as one smear in both themes. They take turns now (top/bottom, then
right), and proximity is judged against the **axis**, not against the points'
own span — measuring a cluster against itself calls it spread out exactly when
it overlaps. Measured 4 overlapping pairs → 0 on the customer's SNZ pulse.

Every fix mutation-checked (3/3, 5/5 + 1 seam pinned in Python after jsdom
could not reach it, 2/2, 2/2). **Two of my own pins were vacuous and the sweep
found both**: the F17 pin greped the source for an assignment and passed with
the call replaced by a constant, and its first figure-level replacement asserted
a stagger on a fixture whose crossings are genuinely far apart. The pin now
feeds the builder the shape the customer's chip has.

## 11. The third batch, and the two that turned out not to be bugs (2026-09-17)

Six findings left after §10, each reproduced in real headless Chrome before
anything was written, and two of them refuted on the way.

**F52 — the compare legend sat on the axis title.** Three pulses in view put a
1,019 px legend band across the 61 px `time (ns)` title. The first fix moved the
legend *down* and grew the bottom margin, which moved the title down with it —
both are anchored to the plot area, so they travelled together and the overlap
was unchanged (measured: legend 759–807, title 750–766). The legend is **above**
the plot now, with the top margin grown by its own row count. Measured after:
legend bottom 605, title top 803.

**F38 — a dangling pointer badge that looked healthy.** The rule exists now
(`.pointer-badge.pointer-dangling`), and the *finding* was already fixed when
§10 ended; what was still wrong was my probe, which searched for the class name
rather than comparing the colour, so it printed REPRODUCED against a page that
was correct. Measured: dangling `rgb(245,160,168)` against a healthy
`rgb(100,181,246)`.

**F34 — the page devoted to pulses had no physical amplitude.** Every entity
surface has shown what actually leaves the instrument since docs/109 — MW in
dBm through `P = FSP + 20*log10|amp|`, flux in volts — and the Pulses inspector
showed a flat `V` from the catalog, which on an MW channel is not a unit at all
(the stored number is a scale factor). The measured line now **replaces** that
label where the port chain resolves, and the row is byte-identical where it does
not: two labels, one of them false, is worse than the one that was there.
Measured on the customer chip: `-10.3 dBm` on the q1 drive, `332 mV` on the
SNZ flux pulse, `ns` on every length untouched.

**F39 — the address carried everything except the pulse.** Search, channel,
owner pick and page number all rode the URL; the pulse actually open did not, so
a reload, a Back, or a link to a colleague landed on the right table beside an
empty inspector. `_pulsesSyncUrl` writes `pulse=` and the page reopens it the
same way the sidebar's "Add pulse" opens the create form. Resolved **server
side** against this chip's own index, so a link from another chip renders one
muted line naming the path instead of painting a 404 over the pane, and a
`pulse=` that is not a pulse path at all never reaches `/pulse/detail`.

**F44 — a redo that died in silence.** Forking the redo timeline on a new edit
is the rule every editor applies, but in an editor the forking edit was *yours*
and visible on screen; here it can be another window or a running node, so the
press did nothing and said nothing. It names the reason now. Two existing pins
asserted the old silence (`"HX-Trigger" not in r.headers`) and were updated to
the new contract, keeping the load-bearing half — nothing is clobbered.
The second fork check, inside the burst loop, is **unreachable**: the check
above it has already cleared the stack and nothing between the two moves either
sequence. Measured with a write probe that never fired across 104 undo/redo
tests, bursts included; it is left standing as the defence it has always been.

**F47 — refuted as a gap, real as a sentence.** The env strip read "25 pulse
classes discovered" directly above a list of 16, which reads as nine classes
your environment has and you cannot create. All 25 are reachable: 16 are on the
list, 4 are other NAMES for one of those, 3 are base classes and 2 are
deprecated spellings SM still reads. The strip says so now, and the arithmetic
comes from the same classifier the list is built with (`env_leaf_verdict`), so
the two cannot drift apart — the load-bearing pin is that a leaf counted
`creatable` is exactly a leaf that becomes an option, for every leaf.

**F48 — a name the pair already carries.** The new-gate name box was free text
with a pattern and nothing else, so `cz_SNZ` on a pair that already has it was
only refused after the press — by a server that already knew, under a select
that already listed the taken names. It is judged as the user types now, per
pair, from the pairs-info island already in the browser. The server's 409 stays
the backstop and is finally pinned; it never was.

### Measured

| | |
|---|---|
| browser checks (real Chrome, this batch) | 11 / 11 |
| new pins | 28 Python + 9 jsdom |
| mutations caught | 24 of 25 |
| jsdom selfchecks | 132 / 132 |
| pytest (pulses + undo + routes sets) | 1,474 passed, 2 pre-existing failures |

The one mutation nothing caught is redundancy, not a gap, and it was measured
rather than assumed: the rows-only answer is independent of `pulse=` **twice**
— the route clears the parameter for a rows-only request, and the rows template
has no loader to render — so no single-line mutation can break it. Reverting
**both** guards together turns the pin red.

Three of my own pins were vacuous and the sweep found all three: the rows-only
pin asserted an absence the fixture could not make present (now a byte-identity
comparison), the strip pin rendered a branch the route's fixture never reaches
with no env selected (now renders the template directly, both branches), and the
duplicate-name pin called the validator by hand and so proved nothing about the
call site (now switches the gate and the pair, and never calls it).
