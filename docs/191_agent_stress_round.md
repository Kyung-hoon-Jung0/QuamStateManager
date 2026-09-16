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

## 3. Two red pins the round found

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
| new pins | 10 Python + 7 jsdom |
| mutations caught | 14 of 14, plus 4 on the two repaired pins |
| jsdom selfchecks | 132 / 132 |
| pytest (agent / journal / story / chat / setup / natural) | 791 passed |

One of my own pins was vacuous and the sweep found it: the A04 section stayed
open in the harness for the wrong reason, because that harness's status record
never reported a successful test, so the section was never eligible to close.
