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

## 10. The WRITE doors, asked the same hostile questions

H05 came from a string that reached a file path, so the doors that *change*
something got the same sweep: twelve fields across the agent's and the journal's
write doors, each given traversals, NULs, 5 000-character strings, `1e400`,
`nan`, `[]`, `true` and empty. A canary file was planted outside every store and
its bytes re-read after every single request.

**Nothing wrote or deleted outside its own store.** Two 500s, one refuted alarm.

### H06 — a key is a directory name, so it has a length

```
POST /api/agent/journal      {"chip": "a"*5000}    -> 500   (the OS refused the path)
POST /api/agent/journal/root {"root": "\x00canary"} -> 500  (ValueError, not OSError)
```

`_safe_key` bounded the character set and not the length, and the root door
caught `OSError` but the NUL raises `ValueError` from the OS call — the same
wrong-kind mistake `journal.read` made in H05. A key is now capped at 80
characters, and a truncated one carries a 10-character digest of the whole name
so two long chip names never fold into one folder (verified: `…-fb16efd732` vs
`…-9d0641debb`). Names that always fitted are byte-identical, and pinned so.

### The alarm that was wrong

`POST /api/agent/limits {"max_runs_per_hour": "abc"}` answered 200 and the value
read back as `None`, which looked exactly like a safety limit being silently
switched off. It is not: **there is no limit called `max_runs_per_hour`** — the
key does not exist, `validate` ignores it by design, and the answer says so:
`{"ignored": ["max_runs_per_hour"], "note": "not saved -- there is no limit
called max_runs_per_hour; the ones there are: …"}`. That is docs/191 C02 doing
its job. Every limit that *does* exist refuses every one of those ten values
with a 400 and stores nothing:

```
max_writes_per_plan  "abc"=400 "nan"=400 "-1"=400 "1e400"=400 "1.5"=400 "[]"=400 "true"=400 null=400 ""=400 "inf"=400
stoploss_target      … identical …      stoploss_plan … identical …      human_recent_min … identical …
```

Recorded because the first reading of it was published in this file's draft as a
finding before it was checked — the second time in this round that a measurement
had to correct a conclusion.

### One thing the sweep did that is worth saying out loud

`POST /api/agent/journal/root {"root": "/etc/passwd"}` returned 200 and **created
that folder**. That is the documented behaviour of a user setting ("the folder
must exist or be creatable") and the journal root is meant to be a folder the
person picks — their Obsidian vault, typically. It is not a finding. It is worth
naming because the probe left a real `C:\etc\passwd` directory on the machine,
which was removed; a sweep that writes needs to clean up after itself, and this
one did not until it was told to.

## 11. The composer in Korean, and two "findings" that were the rig

The user works in Korean, and on a Korean IME the Enter that **commits** the last
syllable is the same key that **sends**. Driven with Chrome's own
`Input.imeSetComposition` (a real composition state, not a synthesised keydown),
composing 안녕 one jamo at a time and then pressing Enter:

```
what the page saw: {composing: true, keyCode: 13, shift: false}   sent: []
```

Correct — it committed the syllable and sent nothing. After the composition ends,
a plain Enter sends `안녕하세요` whole; Shift+Enter gives `첫째 줄\n둘째 줄` and
sends nothing; and a panel repaint mid-composition keeps both the focus and the
half-typed 한 (F01's fix covers a composing textarea too).

**No defect. But the guard was not pinned** — `ev.isComposing || ev.keyCode === 229`
could have been refactored away and nothing would have caught it sending 안녕
instead of 안녕하세요. Three pins now, both signals separately (browsers do not
agree on one), all four mutations RED.

### Two results that were my rig, not the product

The first run of this probe reported two failures. Both were the harness:

- **"Shift+Enter inserts no newline"** — the probe hand-rolled the key event
  without the `text` field Chrome sends, so nothing *could* be inserted. Through
  the rig's own `press`, it works.
- **"an Enter mid-composition leaves a stray `\n` in the box"** — the page's own
  behaviour is provably right (it sent nothing, `prevented: false` because the key
  is the IME's, not the app's). Whether the textarea then receives a newline is
  the IME's business, and **this rig cannot answer it**: CDP sets the composition
  state but installs no IME to swallow the key, so the dispatched Enter arrives
  carrying `text: "\r"` as an ordinary key would. Recorded as undecidable here,
  not as a finding. It wants a real Korean IME on a real keyboard.

### And one thing the probe did that it should not have

The first probe blocked `/chat/send` but the composer with no live session calls
`/chat/start`, so the Enter **started a real CLI turn on the user's own account**
— a 안녕하세요 and a one-line reply, which then stood down cleanly. Reported to the
user at the time. The corrected probe blocks every send door, through both `fetch`
and `XMLHttpRequest`. A probe that drives the send button has to know every door
the button can open.

## 12. Two the Calibration log's own strip was hiding, and one the customer asked about

### N01 — the day navigation moved once and then stopped

Pressing `‹` four times in Chrome moved **one** day. `/journal/day` swapped
`#jr-body` and the nav lives outside it, so `prev_day`, `next_day`, the `›`
disabled state and the `today` button were whatever the FULL page render had
baked in, and never moved again:

- `‹` went to the same day for ever,
- `›` stayed `disabled` (its state was "we are on today", which stopped being
  true after the first press),
- and `today` renders `{% if not story.is_today %}`, so on a fresh load of today
  it is absent and never appeared — **there was no way back.**

One press and the log's day navigation was stuck until a reload. The nav is its
own partial now, swapped **out-of-band** beside the body (the author filter rides
along, since its options are the day's own), so the server stays the single place
that knows what the neighbouring days are — including the calendar-end clamp from
A02. Verified in Chrome: four presses, four days; `today` appears on leaving and
goes on returning; `›` un-disables in the past and walks back up.

### The three "failures" in the same walk that were my probe

Recorded because the ratio matters: of the leftover controls pressed in that
round, **three apparent defects were all the harness** —

- the tool-group `<details>` opened to "0 tool rows" because a group's rows are
  its **siblings** (`.ag-in-group`, hidden/shown by the summary), not its
  children;
- `#jr-prev` / `#jr-next` / `#jr-today` do not exist — the real controls are
  `.jr-daynav button` and `#jr-day-pick` (`#jr-day` is the form's *hidden* field);
- the presets toggle measured `display: none`, which is correct: `.ag-presets-toggle`
  is shown only in `.ag-compact`, and in a wide panel all three preset chips are
  already on screen.

Pressed with the right selectors, all three work — including the fold surviving
two feed repaints.

### N02 — the search box offered a token its own filter could not match

The customer asked whether the left-hand Datasets search covers tags and notes.
It does not, and the answer is worse than a plain no. Measured in real Chrome:

```
type `zzflag` in the sidebar box   ->  the box offers  "#41: zzflagged  tag"
press Enter                        ->  the box holds   tag:zzflagged
the tree                           ->  39 rows  ->  0
```

The suggestion is **proof the run exists**, and accepting it hid the run. Same
for `note:`. The sidebar typeahead has advertised `tag:`/`note:` since docs/182
while `_SIDEBAR_KNOWN_SCOPES` never contained either, so the token fell through
to free text and matched nothing.

An `ExperimentEntry` has no tags and no note — the tree is built from folder
names plus `node.json`, and giving it either would put a file read on the
scanner's path. So the filter resolves those terms to **run ids**, through
`tag_vocab` — *the same vocabulary the suggestion came from*, which is what makes
the two unable to disagree — and matches on the id the tree already has.
`tag_vocab.build` reads `quashboard_tags.json` directly and never through a
`DatasetStore`, which is the rule the typeahead route already follows: a
keystroke must never be able to trigger the cold run scan docs/170 bounded.

`runs_for` also reports when a matched tag holds **more runs than the vocabulary
remembers** (`MAX_RUNS_PER_TAG` = 200), so a short answer can be named rather
than quietly shown.

Verified in Chrome: `zzflag` → offered → Enter → **`tag:zzflagged`, one row,
#41**; `zzwidg` → `note:zzwidget`, one row, #41.

**Deliberately not changed:** bare `zzflagged` still matches nothing. The
sidebar's free-text branch is narrow on purpose (run ids were excluded from it
because any digit matched everything), and folding a run-id set into free text
would build the vocabulary on every keystroke. The scope is the documented way
in, and it is what the typeahead inserts.

### A process note, the second time in this repo

`a69bb4f` is titled *"README covers the Agent cockpit, and the root loses two
stale handoffs"* and its message describes exactly that. It also contains the
first half of N01 — `journal_routes.py`, `_journal.html`, the two new partials
and 63 lines of `test_journal_page.py` — because it was made with `git add -A`
while that work was in the tree unfinished.

docs/187's review recorded the same trap (`git add -A` capturing a reviewer's
`autoSyncPulledV2` mutation). The commit is pushed, so the record is corrected
here and in the next commit's message rather than rewritten: **N01's route,
template and partials landed in `a69bb4f`, not in the commit whose message
describes them.** A commit that says what it contains is worth more than a tidy
history, and `git add -A` beside an unfinished change cannot produce one.

## 13. The customer's three follow-ups on the search box

### N03 — the kind leads the row

> "파라미터는 param, 태그는 tag, 노트는 note 라고 검색어 바로 앞에 표시하면서
> 뜨게 하자 … param은 배치형식으로 compact하게 살짝 SM의 푸른색 스타일로."

Every suggestion now carries its `kind`, and `_row` draws it as a compact badge
**before** the term:

```
[param]  multiplexed          2 values · 39 runs
[tag  ]  zzflagged                           #41
[note ]  fridge                              #41
```

The badges share a `min-width`, so the three read as one column and the terms
line up. Two consequences taken on purpose: the label is now the **term itself**
(docs/182 asked for the run number to be visible, and it moved to the meta —
where this box already puts *where / how many*), and a heading row is not a
choice, so it wears no badge.

### N04 — a long note word gave way to nothing

The row already shows only the **word that matched**, never the note's text, so
a long *note* was never the problem. A long *word* was. Measured before the fix,
in Chrome:

| row | rendered | panel | what happened |
|---|---|---|---|
| a 62-character note word | 694 px | 520 px | cut mid-word, `text-overflow: clip` |
| a long tag name | 638 px | 520 px | same |

`.sm-th-label` is `white-space: nowrap; flex: 0 0 auto` on purpose — the comment
beside it says *"the key is the IDENTITY: it is never the thing that gets cut"*,
which is right for a parameter key and wrong for a person's free text. Tag and
note labels now ellipsize (keyed on the `data-kind` the badge already stamps) and
keep their full text in the row's `title`. A parameter key is unchanged.

### N05 — create, rename and delete now reach the box

> "note랑 tag는 사용자가 업데이트/신규생성/삭제 할때 잘 작동하게 해야할거야."

`TagVocab.load` was called **once**, lazily, and never again. So a tag created
while the page was open was not offered until a reload, and a deleted one was
offered for ever — inserting a token that now finds nothing, which is the same
defect N02 just fixed, arriving by a different road.

Two paths, because they cover different sources:

- the four tag/note mutations in `app.js` refresh the vocabulary the moment they
  land, so the box is right immediately in the window that did the editing;
- and the box **re-checks on focus**, which is the only thing that can catch an
  edit made in another window. The route is conditional on `?v=` (the tag files'
  size + mtime), so an unchanged archive answers 204.

Measured in Chrome with the page open throughout: create → offered; delete →
gone; and a tag made with no local callback at all → offered after one focus,
with a single conditional request.

### The trap I wrote myself, twice in one file

The focus wiring read `if (window.TagVocab) TagVocab.revalidate();` — a
`window.X` guard beside a **bare `X` call**, which is the standing harness rule
in CLAUDE.md and the docs/125 `CSS` global bug. It throws in a Node realm instead
of degrading, and my own `try/catch` swallowed it. The pin failed for that and
nothing else, and both sides are window-qualified now.

The same rule bit the harness a second time: `tag_typeahead_selfcheck.cjs`
evaluates the file through `window.eval` **without `runScripts`**, so it compiles
in the Node realm and a bare `fetch` never sees `window.fetch`. Both globals are
bridged now, with the reason written beside them.

### And four vacuous pins the sweep caught

Of the first cut's twelve mutations, four came back GREEN:

- the heading-row pin passed with its guard deleted, because the fixture's
  heading carried no `kind` for the guard to suppress;
- the param-kind pin asserted on a fixture that hard-coded `kind: 'param'`, so
  it never touched the real suggester (it drives `SidebarTypeahead.suggest`
  through the real loader now, which made the tail async);
- the focus-wiring pin did not exist at all — `revalidate: null` in the attach
  config passed everything;
- and `T.attach('sidebar-filter-input', …)` in the render pin was a silent no-op,
  because `attach` is idempotent per input and the real module had already
  claimed that id — the REAL suggester answered and the pin asserted on
  *"loading parameters…"*. It has its own probe input now.
