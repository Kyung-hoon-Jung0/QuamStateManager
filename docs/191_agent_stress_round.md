# docs/191 — the Agent menu, pressed the same way

Phase 2 of the stress round the user ordered (docs/190 is phase 1, the Pulses
page). The definition is theirs: not many concurrent users, but **one person
pressing everything** — every control clicked, Enter/Escape/Tab/arrows pressed
for real, values nobody sensible would type, every surface opened.

The surface: the Agent home (`GET /agent`), the same panel floating over any
page, Agent → Setup, the Calibration log, and the ~55 JSON doors behind them
(`agent_api.py`, `chat_api.py`, `setup_api.py`, `journal_routes.py`).

## 1. The first batch (2026-09-17)

**A01 — the Send button did nothing and said nothing.** `submit` bails on an
empty or whitespace-only draft with a bare `return false`: no request, no card,
no word. Pressing Enter on an empty box and getting nothing is what anyone
expects; pressing a Send button that LOOKS enabled and watching nothing happen
is not. The control says it now — disabled while the draft is blank, with
`title="type something to send"` — which is the house pattern (the create
form's validity, the plan buttons' own disabled states) and needs no message at
all. Verified in real Chrome 6/6, including that a preset fill enables it and
that sending clears the box and locks it again.

**A02 — two ends of the calendar crashed the Calibration log.** The day picker
has no bounds, and two values it can produce returned a 500:

| value | what raised |
|---|---|
| any local date before 1970 | `datetime.timestamp()` → `OSError [Errno 22]` on Windows |
| `9999-12-31` | `datetime.max + 1 day` → `OverflowError` |

Three call sites were involved, and only the first was obvious: the day's
bounds in `story._units_of_day`, each journal LINE's own time in the day-file
parser (reached only when a journal file exists for that day — the mutation
sweep is what showed that catch is load-bearing), and the previous/next-day
arrows in `journal_routes._build`. A day outside the representable range holds
nothing, and a day at the end of the calendar has no neighbour; both are empty
answers, not errors. Measured after: every extreme value the picker can produce
answers 200.

**A04 — the Test section closed over its own answer.** Setup's section 6 runs a
real read-only question through the CLI and promises "the time it took and the
answer, verbatim". A successful test is also what marks the section DONE, and
`sec()` collapses a done section — so the answer arrived and the section shut
over it in the same breath. Measured in real Chrome: "asking claude one
read-only question…", then a bare `✓ 6. Test` with a real 6.8 s answer hidden
inside. While there is a result on screen the section stays open.

### Refuted

**A03 — the setup folder box refusing in silence.** It does not: every refusal
renders the server's own words in place (`root required…`, `cannot use
Z:/…: [WinError 3]…`). My probe searched for `.as-err` where the class is
`.ag-err` — the same probe-error that produced three false findings in
docs/190.

**A05 — "the setup files changed during the walk".** SM wrote nothing. Previews
left all three files byte-identical, as the contract requires; the one file
whose mtime moved is `~/.claude.json`, touched by the Claude CLI itself during
the Test call, at the same size.

**Enter on an empty box, and a message that made two cards** — both were the
async arrival of an earlier card, read as a consequence of the press that
happened to precede it. A `</textarea><script>` payload is stored and rendered
escaped; nothing executed.

## 2. The observer belt, and what an approval approves

**The observer belt holds.** Pressed with a live plan on the board: Start and
Cancel are REMOVED from the panel, the mode select is disabled, the approval
actions become the word "observing", every one of the ten guarded functions
called directly posts nothing, and a submit says why ("Observer mode is on for
this window -- it shows, it does not send"). The preset chips stay live, which
is correct: a preset fills a draft and nothing starts before a plan card's
Start. Recorded as verified rather than as a finding.

**A06 -- an approval could be redirected to a path the run never proposed.**
The client builds `writes` from the approval's own paths and reads only the
VALUE from the editable cell, but the door took `data["writes"]` verbatim: it
staged them and recorded them as the approval's own. Measured on a real chip --
approving a `qubits.q1.T1` approval with a `qubits.q2.f_01` write moved q2 from
6.3 GHz to 5.0 GHz, and the record (and so the journal line) said
`05_power_rabi` had proposed it.

The presser is a person who could edit that field directly, so this is
provenance rather than permission -- and provenance is exactly what the
Calibration log exists for (docs/173: a journal line names its AUTHOR). An
approval decides the writes it proposed now: a stranger path is refused by
name, the chip does not move, and the approval stays PENDING so it can still
be decided properly. Each proposal keeps its own `old` anchor, because the
value is the person's to change and the thing it is compared against is not.
Measured after: the substitution is a 400 naming the path, and the legitimate
case -- the same path with a hand-edited value -- still applies.

## 3. The /run line's server half, and the plan lifecycle

The client grammar was already hard (`/help`, `/RUN`, `/runn` each refused by
name). What a person can still hand the door is a well-formed line whose
CONTENT is wrong, and a plan pressed out of order. Seventeen malformed run
lines and eleven malformed step lists were posted, then a plan was taken
through its whole lifecycle backwards.

**No 500s anywhere, and every refusal names what is wrong** — the node that is
not in the calibrations folder, the targets that are not on the chip (in
natural order), the mode that is not one of three, `at most 60 steps`, `plan is
running`, `plan is cancelled`, `the mode is chosen before Start`, 404 for an
unknown plan, and rule 0 holding against the agent's own header (`only a
person's click starts a plan`). Path-traversal node names are refused as
missing nodes; a `q1; rm -rf /` target list is refused as four unknown targets.
Cancelling an already-cancelled plan answers 200, which is what idempotent
means and is recorded as correct rather than as a finding.

**B01 — a step could name a node and nothing to run it on.** One shape got
through: `{"node": "n", "targets": []}` was accepted with a 200 while every
other malformed step was refused by name. `normalize_steps`'s own docstring
says "node + targets required" and only the node was checked. Both doors reach
that function — the structured one and a `/run <node>` line with nothing after
it — so one check answers for both: `step 0: at least one target required (a
node runs ON something)`. A blank or whitespace target was already dropped by
the normaliser, so it now falls into the same refusal rather than producing an
empty list.

A neighbouring pin needed repair on the way: it proved the 60-step cap using
steps with no targets, which after this change would raise for the targets
reason instead. It uses a well-formed step now, so it still proves the cap.

## 4. Pressed, one control at a time, with a real mouse

The user asked mid-round whether every menu and feature was really being
clicked and typed into one by one. Half of it was: the Pulses round and A01,
A02 and A04 were driven in real Chrome, but A06, B01, C01 and C02 were reached
through the JSON doors and pytest instead. So the whole Agent surface was
walked again with `Input.dispatchMouseEvent` at each control's real screen
position and `Input.dispatchKeyEvent` per character -- never `element.click()`
or `el.value = x` from script, so an overlay that eats a click, a control off
screen, or a handler bound to the wrong event would show up as a press that
changed nothing.

| surface | controls | pressed |
|---|---|---|
| Agent home | 10 | all but the Send button, which is disabled on an empty draft by design |
| Agent → Setup | 17 across 8 sections | all: both Previews, the Runner link, the journal box + Use this folder, Dry run both ways, both Test buttons |
| Calibration log | 6 | the day arrows, the day box, the search box, the author select, Raw .md |

Plus, from the earlier passes: the composer (Enter, Shift+Enter, an empty
send, 4,000 characters, a NUL and an ANSI escape, a `</textarea><script>`
payload, Korean with an emoji), all three preset chips, both backends, the
observer box both ways, six actor names, ten Tab presses and five arrow keys.
**Zero console or network errors across every pass.**

Three things the clicking turned up, and all three were refuted by measuring
them:

**D01 — the sidebar float opened at zero width with no controls.** True as
observed and not a defect: the sidebar's Agent entry is a LINK to `/agent`, and
customer feedback of 2026-09-08 deliberately made the Agent a destination
rather than a floating panel (the template says so in its own comment). The
panel still mounts correctly when something calls `toggleAgentPanel()` --
714x504 with 11 controls -- and its close button is such a caller. What is
stale is the docs/173 §S6 sentence "the sidebar Agent button floats the same
feed on any page", which that feedback superseded.

**D02 — two links vanished between being listed and being pressed.** The panel
does not re-render on its own: a MutationObserver over its subtree recorded
ZERO node replacements in 20 s untouched, both links survived 6 s, and 6 of 6
real clicks reached the Setup page. What wiped them was my own walker's index
attribute, cleared by a render that an earlier press in the same walk caused.
The walker re-finds each control by name now.

**E01 — "Use this folder" and "Test claude" appeared to navigate to
`/scheduler`.** Pressed once each from a freshly loaded page they do exactly
the right thing: the journal section becomes `✓ 4. Journal folder`, the test
section says "asking claude one read-only question…", and only the Runner link
goes to `/scheduler`. The earlier readings were the walker measuring before its
own `history.back()` had settled.

## 5. Two red pins the round found

Neither is a product defect; both had been failing quietly, which is worse than
either.

**`test_it_never_says_anyone_is_logged_in`** guards a real doctrine (docs/175:
SM's whole probe is `<cli> --version`, so it cannot know whether anyone is
logged in). It scanned the whole FILE, so it tripped on a JS comment describing
what the CLI is and on the template's own Jinja comment stating the rule —
statements about the world, not claims the UI makes. It removes comment SPANS
now, because a multi-line comment is a span and not a set of lines each
beginning with a marker; the keyword carve-outs it used instead were a guess at
wording. Mutation-checked in both files.

**`test_unknown_target_names_the_known_ones_in_order`** asserted the Python
list repr (`['qZ2', 'qZ10']`) that `_unknown_targets_msg` was deliberately
written to stop printing. It guards the ORDER now — q2 before q10, which is
what its class is about — in the wording actually shipped, and asserts the
brackets and quotes are gone.

### Measured

| | |
|---|---|
| browser checks (real Chrome) | 6/6 for A01, the rest measured directly |
| new pins | 12 Python + 7 jsdom |
| mutations caught | 17 of 17, plus 4 on the two repaired pins |
| jsdom selfchecks | 132 / 132 |
| pytest (agent / journal / story / chat / setup / natural / plan) | 881 passed |

One of my own pins was vacuous and the sweep found it: the A04 section stayed
open in the harness for the wrong reason, because that harness's status record
never reported a successful test, so the section was never eligible to close.
