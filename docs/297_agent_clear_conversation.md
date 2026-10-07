# 297 — Clear the agent conversation (archive or delete)

Customer feedback on the Agent panel: there was no way to clear the
conversation history. They asked for a full clear (like a CLI's "clear
context") or an archive.

## What Clear does

The status strip of the Agent panel (home and float) offers **Clear…** when a
clear would take something away: a conversation card, an open session, a
session id the next message would resume, or a settled plan or run. (Only
live work or an approval on screen: no Clear.) It opens a sheet under the
strip:

- **Archive and clear** -- the conversation goes to the chip's archive, readable
  later;
- **Delete and clear** -- no copy is kept in SM;
- **Cancel** (or Escape).

Either way:

1. the in-app session ends (as **End session** does, including the docs/253
   grant it drove) and its `session_id` is removed from the chip's session
   record, so the next message starts a **fresh context**; a cleared session id
   is never resumed, not even by an explicit `/start {"resume": id}` (409);
2. the panel's feed starts over in every window (see "The epoch");
3. one Calibration log line says who cleared it and whether it was kept (a
   clear that took nothing away writes none).

What a clear never touches: running plans and runs, plans waiting for Start,
pending approvals (all stay on screen), the plan and run records, the
Calibration log, SM's agent event log `instance/agent_events/<day>.jsonl` (the
audit record of what the agent did and said), and the CLI's own session files
(Claude Code and Codex keep their own transcripts). The sheet says so: "Delete
keeps no copy in SM. Neither edits SM's agent event log or the CLI's own
session files." Finished plans and runs leave the panel with the conversation
when they SETTLED before the clear (a draft kept at the clear stays when it
finishes later); they stay in the Calibration log.

## Order: write first, then end

The index and the archive are written first; only once they are on disk does
the session end and its id go. A clear that fails (unreadable index, a full
disk) answers JSON 500 "nothing was cleared" and has changed nothing -- the
session is still open and resumable, no orphan archive file is left.

## When it is refused (409, said in the sheet)

A clear makes the agent forget the conversation, so it waits until nothing of
it is still working:

- the session's turn is open ("press Stop now or wait");
- a panel question is still being answered;
- a plan is running or stopping;
- a run is starting or running on this chip;
- another agent session (a pid this SM did not start) is alive on the chip;
- the sheet was opened on a conversation another window has cleared since
  (the sheet sends the `since_n` it was opened under).

A failed press keeps the focus in the sheet (no button is ever `disabled`
there: a focused button that is disabled drops the focus to `<body>`, and
Escape stopped working -- measured in Chrome).

## Storage

```
instance/agent_conversations/<chip name>/index.json
    {"since_n", "cleared_at", "closed_sessions": {session id: upto n | null}, "archives": [meta, ...]}
instance/agent_conversations/<chip name>/<archive id>.jsonl
```

Keyed by the chip NAME, the identity the feed itself filters chat events by:
what one panel shows is what its clear puts away.

`since_n` is the chat counter's high-water mark at the clear. The panel's feed
is every chat event with `n > since_n`. Numbering an event and writing it are
one step under a record lock, and the clear reads the counter under the same
lock, so no event can be numbered below `since_n` and still be on its way to
disk. An archive is a self-contained COPY of the cleared chat events: the ring
keeps 800 lines and replays seven days, a conversation can be older, so the
clear reads every day file from the previous clear on (lines screened as bytes,
only chat lines of this chip parsed), plus the ring.

`closed_sessions` names the sessions a clear ended. `null`: every later event
of that session is hidden (its process may still write a trailing Stop/Error
after the clear). The next Start caps it at the counter of that moment, so a
CLI that hands out the same id again is the new conversation from there on,
while the trailing events stay hidden.

Only a missing index means "never cleared". An index that exists but cannot be
read (bad JSON, a permission error) is never rewritten from nothing: a clear or
an archive delete answers 500; a panel read falls back to the last good read
(a transient error must not flash a cleared conversation back).

## More than one SM process on one instance (docs/80)

- The counter's persisted mark `agent_events/chat_n.txt` is re-read on every
  number, not only at start, and a clear lifts it to `since_n` -- another
  process never numbers below a clear made elsewhere.
- A clear in process A cannot end process B's session. A Codex conversation in
  B is between turns (no process, so no "alive elsewhere" refusal); B's next
  `/send` sees that its session started before the clear, ends it and answers
  409 "this conversation was cleared (in another window); send again to start
  a new one".

## The epoch

`GET /api/agent/chat/cards` carries `conversation: {since_n, archives,
resumable}`. A window that sees `since_n` change drops the conversation's
cards and the plan/run cards, resets its cursor and polls again. An approval
card stays, and so does any card the person is typing in: a value typed into
an approval must never be swapped for the proposal under the person's hand.

The strip state: SM knows a session it ended itself (End session, Clear) is
gone once its process is, so the pill no longer says "thinking" for the event
window's 15 minutes afterwards (the clear forgets the session id the record
used to name it; the manager -- or after a restart the closed list -- does).

## Archived conversations

When the chip has archives, the strip's links start with **Archived (N)**. The
list (newest first) names each by its first message, time span, message count
and who cleared it. Opening one shows it **in place of the live feed**,
read-only, with the same card renderer; **Back to the conversation** -- or
sending a message -- shows the live feed again. Delete asks once more ("Delete
for good?", the focus on Keep). A view-only window can read archives but gets
neither Clear nor Delete; turning view-only on closes an open sheet.

Routes: `POST /api/agent/chat/clear {keep: 1|0, since_n?}`,
`GET /api/agent/chat/archives`, `GET /api/agent/chat/archives/<id>` (the newest
2,000 cards, `omitted` counts the rest),
`POST /api/agent/chat/archives/<id>/delete`.

## Review round

An adversarial review of the first commit reported 15 findings (6 P1, 9 P2, no
P0), 11 reproduced; every one is fixed above and pinned in
`TestReviewRound`: a failed write ended the session anyway; a draft kept at the
clear vanished once it finished; another window's clear wiped a value being
typed into an approval; a second SM process's Codex conversation survived the
clear (and its answers were then hidden); a second process's counter numbered
below the shared `since_n`; a permission error on the index read as "absent"
and the archive list was rewritten from nothing; an event numbered during the
clear was lost; two clears archived one conversation twice; the next Start
brought the closed session's trailing events back; Delete's wording overstated
it; a pin could not fail (`sess-fresh` is also what a resume reports); the
index was keyed by folder while the feed is keyed by name; a transient read
error flashed the conversation back; Clear stayed offered with only live work.

Recorded, not changed: the first clear on a chip reads every day file -- the
byte screen took 0.36 s over 158 MB of synthetic event lines, and the largest
real event log on the development machine is under 200 KB; a panel question
answered after the clear lands in the new conversation rather than being
dropped.

## Pins

`tests/test_agent_clear.py` (the routes over the real ChatManager and the fake
CLI) and `tests/agent_clear_selfcheck.cjs` (the real agent.js under jsdom).
Real-Chrome walk on a rig with the fake CLI: the sheet in both themes and in
the float, a refusal during an answer, archive, delete, a fresh session, the
archive list and view, reload and navigation away and back.
