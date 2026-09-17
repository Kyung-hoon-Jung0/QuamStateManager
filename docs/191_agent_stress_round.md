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

## 5. What clicking found that nothing else could (F01)

A plan was made the way a person makes one -- `/run 05_power_rabi q1` TYPED
into the composer character by character and sent with a real Enter -- and then
its card was pressed with a real mouse.

**The card the person just acted in is the one card that never updates.**
Measured: within a second of pressing Start the server reports the plan
`running`, while the card still reads **DRAFT** and still offers **Start** and
an enabled mode select. It stayed that way for 13 s and through an explicit
`poll(true)`; only a full page load showed `RUNNING` with *Stop after this run*
/ *Stop now*. Pressing the still-live Start again answers `plan is running`,
and the still-live mode select answers `the mode is chosen before Start`.

The cause is one line in `setHtml`:

```js
if (!force && el.contains(document.activeElement) && ...) { el.__agStale = html; return false; }
```

It exists for a real reason (review R2-1: an approval's editable value must not
be wiped under the person's fingers, and the recovery is a `focusout`
listener). But **a real mouse click leaves focus on the button it pressed**, so
pressing Start makes the plan card hold the active element with nothing being
typed -- and focus only leaves when the person clicks elsewhere, which is
exactly what they have no reason to do while waiting to see what their press
did. Every API probe missed it because a scripted `POST` never focuses
anything.

"Typing in it" is now what it says: a focused textarea, select, contenteditable
or text-like input defers the render as before; a focused button, link or
checkbox does not. The focus is put back on **the same control** after the
swap, and never on a different one -- a Start button replaced by *Stop now*
must not inherit the press that replaced it.

## 6. The approval card and two windows, both pressed by hand

**The approval card is correct end to end.** A pending approval was put in the
rig's own store (the card is what is under test, not how the approval was
born), then everything after that was a person: a real click into the editable
value cell, `654` typed character by character, and a real press on *Write to
chip*. The chip moved 600 -> **654** -- the edited number, not the proposed one
-- the card left the feed, and a toast said "written to the chip" (my first
probe sampled 3 s late and read silence; the toast lives 3.5 s and had gone).
A second approval pressed *Reject* wrote nothing: the chip stayed at 654.

**Two windows agree without a reload.**

| what one window did | what the other saw |
|---|---|
| A typed `/run …` and sent it | B showed the DRAFT card in ~4 s |
| A pressed Start | A showed RUNNING at once (the F01 fix), B in ~5 s |
| B ticked observer | B lost Start/Cancel; A kept them -- the flag is per window |
| A pressed Reject on an approval | B dropped the decided card in ~5 s |

Zero console or network errors in either window (the one dialog entry is the
Reject note prompt, which is the product asking).

## 7. Arm and the two Stops, pressed — and what "Arm beside Stop" means

Pressed with a real mouse: **Stop after this run** turned the card and the
server to `STOPPED` together; **Stop now** asked first, with the confirm naming
exactly what it kills ("the agent process is killed and the running node is
cancelled. The OPX finishes its current sequence; SM's chip writes are
atomic…"), and the plan stayed stopped.

**G01 — the strip offered `Arm` beside `Stop after this run` / `Stop now` /
`End session`, and kept offering both for 24 s.** That reads as two mutually
exclusive states, so it was measured rather than assumed, and it is correct:
`armed` and `alive` are different facts. After the stop the session file says
`armed: false, stopped: true` while the CLI process says `alive: true, pid
36480` — a live agent that may talk but may not touch hardware. `Arm` grants
the hardware permission again (rule 0); `End session` closes the process. The
strip is stating both truths at once, and refusing to show either would be the
lie.

Worth recording from the same run: pressing Start really does spawn a session
on the person's own logged-in CLI, and that session stood down correctly — its
own words were "**Stopped — nothing ran.** … No hardware touched, no runs, tray
empty, `live_diverged: false`", with the plan `stopped` and step 0 `cancelled`.
That is the observer guard's whole reason for existing, seen working.

## 8. Two red pins the round found

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
| browser checks (real Chrome) | every Agent surface pressed control by control |
| new pins | 12 Python + 16 jsdom |
| mutations caught | 23 of 23, plus 4 on the two repaired pins |
| jsdom selfchecks | 132 / 132 |
| pytest (agent / journal / story / chat / setup / natural / plan) | 881 passed |

One of my own pins was vacuous and the sweep found it: the A04 section stayed
open in the harness for the wrong reason, because that harness's status record
never reported a successful test, so the section was never eligible to close.

## 9. The surfaces nobody had pressed yet — journal authorship, the pill, the read doors

Three areas were still untouched when the Agent walks ran out: the journal's
authorship form, the topbar pill (the only Agent surface on *every* page), and
the agent's own READ doors — the MCP tools it calls with whatever it has decided,
with nobody watching.

### H01 — "Correct the author" silently deleted the note

The form's summary reads **"Correct the author / add a note"**. Its two boxes
arrived empty, and `claim_run` replaced the whole record. So a person fixing a
misspelt name typed the name, pressed Save, and lost the sentence they had
written — with nothing on screen to warn them. Measured on the rig:

```
1) claim WITH a note      -> {"author": "human:Kyunghoon", "note": "fridge was still warming"}
2) correct the NAME only  -> {"author": "human:Jihoon",    "note": null}
3) `note` omitted entirely-> {"author": "human:Minji",     "note": null}
```

Case 3 is the plainer defect: the caller said nothing about the note and it was
deleted. Three fixes, one per hole:

- `story.claim_run`'s `note` now defaults to a `_KEEP` sentinel — a caller that
  does not mention the note keeps it. An explicitly empty note still clears one:
  that is a person emptying a box they can see (docs/120).
- `journal_claim` forwards `note` only when the key is actually present.
- the form's boxes arrive **carrying what they are about to replace** — the
  stored note, and the current author's name. Leaving a box alone now means
  keeping it, which is what the screen already implied.

End to end in real Chrome, on an untouched run: type a name and a note, come
back, retype only the name — `THE NOTE SURVIVED: True`.

### H02 — the journal asserted two authors for one run, at one time

The journal is append-only and a claim line is stamped with the **run's** own
time (docs/173 S8, so it sits beside the run it is about). Two claims therefore
produced two lines with identical timestamps and different authors, and file
order was the only clue which was said last. A later claim now names what it
overrode:

```
- **01:58:26** `human` run #12 was run by human:Kyunghoon: the fridge was still warming …
- **01:58:26** `human` run #12 was run by human:Jihoon: … (corrects an earlier claim of human:Kyunghoon)
```

A re-claim by the *same* name is someone adding a note, not correcting anyone,
and says nothing.

### H03 — the page says `day=`, the door said `date=`

`GET /api/agent/journal?day=2026-09-07` answered with **today**, under a `date`
field naming today. Honest, but a question nobody asked — and an agent copying
the journal page's own URL would read the empty answer as "nothing happened that
day". The door takes both spellings now.

### H05 — a day was a path (the round's most serious finding)

The chip half of a journal file path goes through `_safe_key`. The day half went
through nothing. Measured on the running rig:

```
GET  /api/agent/journal?date=../../secret   -> 200  "TOP SECRET: the customer's calibration notes"
POST /journal/adopt {"day": "../../victim"} -> {"moved": 1, "ok": true}      … and victim.md was GONE
```

An arbitrary `.md` **read**, and an arbitrary `.md` **delete** — `adopt` reads the
source file, appends its bullets, and then unlinks it, and with a traversing day
source and destination are the same file. A NUL byte in the same argument was
the one 500 in the whole read-door sweep (`ValueError: embedded null byte`,
which `read`'s `except OSError` does not catch).

Fixed at the single choke point: `journal.day_file` refuses anything that is not
`YYYY-MM-DD`, which is the shape `list_days` already used to decide what *is* a
day. `journal.is_day` lets a door answer 400 instead of raising, and both doors
that took the value from a request now do. One contract was deliberately kept:
`journal.read` still returns `""` for a nonsense day rather than raising, because
`story.build_day` has always built an empty page from one — the honest refusal
belongs at the door, not in the reader.

### P01 — the agent's own words took a minute to reach the topbar

The pill is the only Agent surface on every page. docs/173 says it "refreshes
when live-wake wakes (the server bumps its tick on every agent event)". Pressed
and timed on the Pulses page:

| door | bumps `agent_seq` | pokes the run watcher | page heard it after |
|---|---|---|---|
| `POST /api/agent/limits` (and 12 others) | yes | yes | **0.4 s** |
| `POST /api/agent/journal` | yes | **no** | **nothing in 15 s** (57.9 s in the long run — the pill's own safety timer) |

13 of the 14 doors already worked. The exception was the agent writing its own
words — the one thing docs/173 makes *required* of it (`reason` is mandatory for
`kind=agent`) — arriving up to a minute late on every open page.

The narrow fix would have been one `_wake()` call. The wider one was taken
because `_wake()` moves the **run watcher's** tick, which tells every tab "a run
folder changed" — a false statement that costs every open tab a dataset delta
poll and a new-run poll on every agent journal line. Instead `/datasets/wait`
now carries a **second cursor**: a caller that sends `aseq=<its last agent_seq>`
is answered when *either* signal moves, and gets `agent_changed` alongside
`changed`. `live-wake.js` dispatches `sm:agent-changed` for the agent half and
keeps `sm:runs-changed` meaning what its name says, so an agent-only wake never
sends the Datasets table looking for a run. A caller that sends no `aseq` — an
older tab mid-refresh — keeps the byte-identical blocking wait it had.

Measured after: **0.5 s**, for both doors, with the agent-only event firing only
the agent channel.

Structurally this closes the class, not the instance: any future `_bump()`
without a matching `_wake()` is now live by construction.

### What was measured and found sound

- Every read door with 23 hostile inputs each (empty, huge, `1e400`, `nan`, NUL,
  3 000 chars, script tags, SQL, Arabic-Indic and full-width digits, traversals):
  404s and 400s throughout, one 500 (H05's NUL), no silent wrong answers.
- The pill on `/`, `/diagnostics`, `/journal`, `/state-history`: present and
  correct on all of them; its click lands on the Calibration log.
- The pill at 1500 / 1100 / 980 / 820 px: 303 px → 99 px → 94 px, no topbar
  overflow — the `shortText` work from the 2026-09-09 complaint holds.
- The claim line still lands on the **run's** own day, not today's page.

### A measurement that was wrong first

The first pill timing said 58 s for an *approval*, and the conclusion drawn from
it — "the documented live channel does not exist" — was wrong. The rig had
created the approval with a subprocess writing straight to disk, so the server's
`agent_seq` never moved at all; the run's own `adapter.wake()` does bump it. The
finding only became real when the event went through a door. Recorded because
the first number was published in this file's own draft before it was checked.

### R01 — the clamp could only ever be taken off

Round 2 fixed the clamp being decided by CHARACTER count when the clamp itself is
a HEIGHT — but only in one direction: `confirmClamp` could remove a clamp from a
card that hid nothing, and never add one to a card the character count had called
short. Measured in Chrome by narrowing the panel:

| card | 1500 px | 900 px | 700 px | clamped? |
|---|---|---|---|---|
| 778 chars | 299 px | 507 px | **707 px** | **never** |
| 3 233 chars | 435 px | 1 001 px | 1 741 px | yes, to 308 px |

So at a narrow width a 707 px answer — more than twice the 20.5em clamp — filled
the feed with no way to collapse it, while a shorter-looking one beside it wore a
"show more". The character count is now only the *candidate*; the rendered box
decides both ways.

Two things the first cut of that fix got wrong, both caught by its own pins:

- `setHtml` rebuilds a card from a template that only knows the character count,
  so a clamp *added* by `confirmClamp` was wiped by the next re-render — and an
  **open** card was then left with no link to close it. The verdict now lives on
  the card as `data-clamped`, beside `data-expanded`, and is re-applied first.
- The verdict is a height, and a height changes with the column's width — but
  `setHtml` skips an unchanged card, so the verdict was computed once, at
  whatever width the card first rendered at. A `ResizeObserver` on the feed
  re-decides on a **width** change only (acting on the height change a collapsing
  card causes is a loop). Same pattern as `window.PlotHost` (docs/122).

### Two lessons from this round's own pins

The mutation sweep caught three vacuous pins and, separately, a test bug worth
recording: the R01 block re-rendered cards with `feed.cards[feed.cards.length-1]`,
and when a later block pushed one more card that index silently moved — two pins
stopped re-rendering the card they were about and went green against the mutation
they claimed to catch. Cards are addressed by identity now (`touch(n)`).

The other is the standing harness rule from CLAUDE.md, hit again: a Node realm
does not expose `window` properties as bare globals. `agent.js` reads
`ResizeObserver` bare, so a `window.ResizeObserver` stub alone left the watcher
uninstalled and the pin red for the wrong reason.
