# 289 — What the agent is doing now: a live box with a clock, its tool calls, and a terminal

**Date:** 2026-10-05 · **Trigger:** "the agent runs in the background; I cannot tell if it
is working or not, I just wait until it ends — at least the terminal output should show".

## Measured before

- A real read-only question ("lowest three T1 values") took 18 s.
- For those 18 s the page showed only the question bubble. The header still read "no
  agent session on this chip". The answer then appeared at once, without the tool calls
  it made.
- Cause: the CLI's events reach the feed only as WHOLE messages or finished tool calls.
  An Ask posts nothing to the feed until it stops.

## Facts it is built on (claude 2.1.289, captured 2026-10-05)

- `claude -p --output-format stream-json --verbose --include-partial-messages` prints
  `{"type":"stream_event","event":{…}}` lines carrying the Messages-API stream events:
  - `content_block_start` (thinking / text / tool_use);
  - `content_block_delta` (`text_delta`, `input_json_delta`, `thinking_delta`);
  - `message_start` / `_stop`.

  The whole `assistant` message still follows, so nothing downstream changes.
- **`thinking_delta` text was EMPTY in every capture.** The view can say *Thinking*, never
  what the model thinks.
- Most of a turn is the model computing before it prints anything. Tool calls and the
  writing take under a second each, so a 1 s poll lands mostly on that first stretch. It
  is labelled *Working…*: "Waiting for the model" read as stuck.
- Codex (`exec --json`) streams no partial text. Its `item.started` lines (`reasoning`,
  tool calls, `agent_message`) give the phase.

## Design

- **`core/agent_live.py` — `LiveState`, one per CLI process.** It holds:
  - the phase and when it started;
  - this turn's tool calls, each with readable input (`path=qubits.q1.T1`; ToolSearch →
    `loads state_get, runs`; `{}` → nothing) and its time;
  - the answer as it is written;
  - a capped log of what the process printed (`you / init / call / ok / fail / text / done /
    stderr / think / end`), each tool result as one short preview line.

  It is a VIEW: never journaled, never on disk, never used to decide anything. The feed and
  the journal keep reading the normalized events exactly as before.
- **`AgentProcess`** feeds it from the raw lines, the normalized events, stderr, each sent
  turn, and the exit.
  - `stream_event` lines no longer enter `raw_tail`, so the partial chunks cannot push out
    what a crash diagnosis needs.
  - `finish()` runs AFTER the reader's crash verdict: a crash mid-turn is already an Error
    event. An unexplained exit 0 is an *end*, not a failure.
- **`ClaudeBackend.command`** adds `--include-partial-messages` only when the CLI's own
  `--help` lists it, because an older CLI would refuse the whole launch over an unknown
  option. The check is cached for an hour and costs about 0.5 s.
- **`GET /api/agent/chat/live`** (`?id=` for one item): returns the open chip's driving
  session while its turn is open, plus the panel's questions in flight.
  - A finished item lingers 4 s so its last state is seen.
  - Asks from elsewhere (Setup → Test) are not listed.
- **Client (`agent.js`):** a `.ag-live` box between the feed and the composer, so it stays
  in view while the feed scrolls.
  - The head shows backend, phase, a clock since Send, and question/task.
  - Below it: the last 6 tool calls (the rest as "+ N earlier"), the answer so far while
    writing, and a *Terminal* fold. The fold stays open across updates and follows its
    bottom.
  - It is polled about once a second ONLY while something is in flight: started by Send, by
    a busy session in the feed (another window), and by one look at mount. It stops after
    two empty answers.
  - A finished item fetches the feed at once, for the answer card.
  - Also in the floating panel (without the answer-so-far line).
- **Feed follow fix (found on the way, existing bug):** 40 ms after Send the feed's height
  shrank by 98 px (measured frame by frame). Nothing re-pinned it, so the "follow only at
  the bottom" rule then treated the reader as scrolled up, and answers landed below the
  fold. The feed's ResizeObserver now puts a reader who was at the bottom (known from
  scroll events; a shrinking container fires none) back at the bottom when its height
  changes. Re-measured over a whole turn: the gap stays 0.

## Verification

- **Real Chrome against the real CLI** (sandbox home, a copy of the login with no refresh
  token):
  - Ask: tool calls appear as they finish, the clock runs, the terminal fold shows each
    call and what it returned. The answer lands in view and the box goes.
  - Task session: the same, with `run` / `check_fit` calls.
  - The floating panel: the same.
- **Python** `tests/test_agent_live.py`, 23 pins. The fake CLI now prints the measured
  `stream_event` shapes when given the flag, and `FAKE_SLOW` holds a turn open.
  Mutation sweep: 10/10 red.
- **JS** `tests/agent_live_selfcheck.cjs`, 19 pins. Mutation sweep 10/10 red. One of them
  first exposed a real bug: a kick while the poll already ran waited up to a second.
- `tests/agent_panel_selfcheck.cjs`: the composer-position pin now names the live box
  between the feed and the composer.

## Overhead

- One small JSON request per second, only while a turn is open.
- Partial messages add JSON lines that the reader parses and drops (no events, no disk).
- The flag probe costs about 0.5 s once an hour.
