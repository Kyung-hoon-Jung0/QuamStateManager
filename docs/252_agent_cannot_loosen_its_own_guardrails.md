# docs/252 -- An agent cannot loosen its own guardrails, and SM can say who pressed

## Report

The 2026-10-03 agent validation campaign (`D:\work\sm_qa_rigs\agent\FINDINGS.md`)
found that SM took an agent's word for who it was:

- **A-10 / D-07.** `POST /api/agent/limits` with `X-SM-Agent` set mode `auto`,
  `human_recent_min` 0, `max_writes_per_plan`, a webhook URL. Only the mode
  change was journaled.
- **D-08 / C-15.** The same agent could move the journal folder. The day files
  stayed behind, so the Calibration log looked empty, and nothing recorded the
  move.
- **A-09.** Identity was a header. A request *without* `X-SM-Agent` was "a
  human", so plain `curl` with `X-SM-Actor: Kim` armed the agent, approved and
  started a plan. `POST /api/agent/event` was open to anyone and turned a forged
  record into a `by_claude` / `by_codex` journal line. With the agent header,
  `/journal/claim` recorded "run by human:Kim".
- **B-04.** `note_set` signed every note `claude-code`, Codex's included.
- **B-01.** A note an agent pinned (`do not touch, fridge warming` on `qA4`) was
  readable by no tool; the next agent said there were no notes.
- **B-10.** The bridge's manual setup had no tool timeout (Codex gives up after
  300 s; `run_node` can wait ~28 min), and the bridge's MCP `instructions` lacked
  the two rules the lab context carries.

## Root causes (lines at 6e8c731e)

| id | where | cause |
|---|---|---|
| A-09 | `web/routes.py:552` `_request_actor` | the one identity function: `X-SM-Agent` -> `by_*`, anything else -> `human[:<X-SM-Actor>]`. Nothing proved a person. |
| A-09 | `web/agent_api.py:1671` `session_arm`, `:1724` `approvals_decide`, `:2207` `plan_start`, `:2175` `plan_mode` | each refuses only `actor.startswith("by_")`, i.e. only a caller that announces itself as an agent. |
| A-09 | `web/agent_api.py:2309` `plan_cancel`, `web/journal_routes.py:140` `journal_claim`, `:188` `journal_adopt` | no caller check at all; the claim takes its author from the payload's `who`. |
| A-09 | `web/agent_api.py:779` `event_post`, `:706` `_author_kind` | any POST is absorbed; the record's own `backend` field names the author (`by_claude`), and its `origin`/`chip` fields are trusted as SM's in-process chat marks. `core/story.py:254` also infers a run's author from these events. |
| A-09 | `web/agent_api.py:523` `journal_append` | with the agent header only `human` is downgraded, so an agent could write `sm` lines ("armed by human:Kim"); without it any kind, `by_claude` included. |
| A-10 / D-07 | `web/agent_api.py:1075` `limits_route` | no caller check; `core/limits.py:147` journals only a mode change. |
| D-08 / C-15 | `core/journal.py:71` `set_root` ("nothing is moved"), `web/agent_api.py:558` `journal_root`, `web/setup_api.py:264` `journal_setup` | the root is a pointer swap; the agent door journals nothing, Setup journals only into the NEW folder. |
| B-04 | `mcp.py:407` (`"author": "claude-code"`), `web/agent_api.py:496` (default `claude-code`) | the author was a constant in the bridge and a default on the server. |
| B-01 | `mcp.py` tools, `web/agent_api.py:213` `state_get`, `:183` `chip` | no read returned `core/entity_notes`; and `note_set` stored a bare `qA4`, which neither the grid's row marks (`entity_notes.row_marks` needs `qubits.qA4`) nor `entity_notes.touches` match. |
| B-10 | `mcp.py:1-4` docstring, `mcp.py:548` `instructions` | the timeout and the two rules were only in Setup's own config block and the lab context file. |

## Who is calling -- the design

`web/callers.py` (new) decides, before any route runs:

**A person's window.** Every HTML page SM serves carries a cookie
`sm_person_<port>` holding a random secret made when this SM process started.
It is `HttpOnly` (no script reads it -- neither SM's nor a page on another local
port, and cookies ignore the port), `SameSite=Strict`, `Path=/`, and named per
port because two SM windows on one machine share one cookie jar. The browser
attaches it to every same-origin `fetch` and htmx request, so no button changed.
The MCP bridge, the hook and a plain `curl` never loaded a page, so they never
have it; a GET carrying `X-SM-Agent` is never given it.

`callers.PERSON_ONLY` (POST only) refuses a request that carries `X-SM-Agent`
(`refused: person_only`, even with the cookie), and one without the cookie
(`refused: no_window_proof`, "reload the page" -- what a person sees after an SM
restart). The set: arm, approve/reject, plan start/cancel/mode, limits, journal
root (both doors), journal claim and adopt, Setup connect/disconnect/context.
Setup's writes are on it because they rewrite the agent CLIs' permission rules
and the lab rules the agents read -- guardrails as much as the limits are.

**SM's hook.** `/api/agent/event` needs `X-SM-Hook-Key`: 64 hex characters SM
writes once into `<instance>/agent_link/hook.key` (`agent_link.ensure_hook_key`,
at `create_app`). The hook already resolves that instance dir to write its event
log, and `agent_link.fire_event` now sends the key. Without it the event is
refused (`unproven_event`) and nothing is recorded: no journal line, no pill
state, no run attribution. The hook's own jsonl is still replayed at the next
start (it lives in the same instance dir, so it is as trusted as the key), so an
old hook that sends no key loses only the live wake. Even a keyed event cannot
carry the in-process chat marks (`origin`, `n`, `chip`, `ask_id`, `readonly`,
`owner`, `who`): those would put its text into the Agent panel's feed as the
agent's words.

**Lines SM cannot vouch for.** `POST /api/agent/journal` signs an agent's line
with the agent's own name (`by_claude` / `by_codex`, from the header), whatever
kind it asked for. Without the header and without a window, the line is
`unverified` (a new journal kind). `note_set` likewise: `by_<cli>`, the person,
or `unverified`.

**Why a cookie and not a header.** Anything a caller can type, a caller can
type: the header-only check stopped honest mistakes and nothing else (A-09).
The cookie is the strongest proof SM can get without a second OS account,
because the browser hands it only to SM's own pages. Its limit, stated rather
than discovered later: every agent here runs as the same OS user as SM. A
terminal agent with a shell can GET a page and replay its cookie, read the hook
key, or edit `instance/agent_limits/*.json` outright. That is a deliberate,
multi-step impersonation, not something the bridge's tools, the hook, or a
`curl` retried after a refusal can do by accident -- and the refusal text does
not explain it. The in-app agents (docs/247) have no shell at all. A real
boundary needs OS separation (the agent CLIs under their own account); that is
out of SM's reach.

Rejected alternatives: a one-time token in the URL the window opens (as
Jupyter does) is a real secret for the desktop window, but every browser-mode
user would have to re-open a printed URL after each restart, and behind a
reverse proxy there is no console to print it to; and the secret would still be
readable by the same OS user. Requiring `Sec-Fetch-*` headers adds nothing
against a forger (curl can send them) and risks a webview that does not.

**The desktop window (pywebview).** `main.py:206` opens
`http://127.0.0.1:<port>` in pywebview 6.2.1, which on Windows is WebView2
(Chromium). `webview.start()` keeps the default `private_mode=True`:
`platforms/edgechromium.py:81` sets `IsInPrivateModeEnabled`, and `:303`
deletes all cookies before the first load. Session cookies then live in the
in-memory jar; the first `GET /` sets `sm_person_<port>`, and every same-origin
`fetch` carries it. `_wait_for_server` (`main.py:121`) also GETs `/`, through
urllib with no jar, and throws its cookie away. Each launch has a new port, so a
new name and secret; closing the window ends both. Measured as well (see the
rig section): the real desktop window, driven over WebView2's own debugging
port, held `sm_person_6067` and its press went through.

## Behaviour now

**Limits (A-10 / D-07).** A person's window changes them; an agent never does,
not even to tighten. A tighter-only rule was considered and dropped: the fields
have quirks where the stricter-looking number is looser (`max_writes_per_plan` 0
means *no* cap; `human_recent_min` 0 becomes 30 at the gate), `stoploss_*` is
not enforced (D-14), and no agent tool needs it -- an agent that wants more care
can take more care. Every change is journaled with who, old and new (the mode
keeps its own line, `mode ask-writes -> auto (set by human:Kim)`, the rest one
`limits max_writes_per_plan 200 -> 50; ... (set by human:Kim)` line). A webhook
URL often embeds its own secret, so the journal shows only its scheme and host
(`https://hooks.slack.com/...`). A refused attempt on a guardrail is journaled
too: `by_claude tried changing the limits (mode -> auto, human_recent_min -> 0,
webhook_url -> https://hooks.slack.com/...) -- refused: only a person can, from
the SM window`.

There was no Limits UI (docs/173 S7 deferred it), and an on-site customer had
lowered `human_recent_min` through this endpoint with a non-browser call. With
the endpoint closed to anything but a window, Setup gains **7. Limits for
<chip>**: mode, max writes per plan, the person-run window, stop by, webhook,
max |Δ| per family; Save posts them, a refusal is said beside the button.

**Journal folder (D-08 / C-15).** Both doors call `journal.move_root`: set the
root, then `carry_over` copies every chip's day files from the old folder (copy,
never move -- the old folder is a person's folder too). A day the new folder
already has gets only the entries it lacks, appended, never rewritten (a vault
file may hold the person's own words), so moving back and forth duplicates
nothing. The move is journaled in both folders, for every chip carried and the
open one: `journal folder moved to <new> by human:Kim -- 2 day file(s) of this
chip carried over` in the old, `journal folder moved here from <old> ...` in the
new. Choosing the same folder again is not a move.

**Notes (B-01, B-04).** `note_set` turns a bare qubit or pair name into its dot
path (`qA4` -> `qubits.qA4`), so the note also lights its grid row. `state_get`
returns `notes`: those on the path, on the entity above it, or under it
(`entity_notes.touches`); every note for the empty path. `GET /api/agent/notes`
lists them; the bridge's `sm_status` adds them (an older SM without the route
leaves them out). A bare-name note written before the fix is still found. The
author is the caller.

**The bridge (B-10).** The docstring gives the manual setup with
`MCP_TOOL_TIMEOUT=1800000` (Claude Code) and `tool_timeout_sec = 1800` (Codex);
`instructions` now say: never edit state.json/wiring.json directly, never run
`python <node>.py`, nodes run only through `run_node` after a plan the human
started, and read the notes `sm_status`/`state_get` carry before changing a
value.

## Changes

- `web/callers.py` (new): `PERSON_ONLY`, `GUARDRAILS`, `HOOK_ONLY`, `enforcing`,
  `cookie_name`, `from_person`, `hook_key_ok`, `_gate` (before_request),
  `_issue` (after_request), `init_app`.
- `web/app.py`: `callers.init_app(app)` after the blueprints (after the CSRF guard).
- `core/agent_link.py`: `HOOK_KEY_HEADER`, `hook_key_path`, `read_hook_key`,
  `ensure_hook_key`; `SMLink.extra_headers`; `fire_event` sends the key.
- `web/agent_api.py` -- functions touched: `state_get` (notes), `note_set`
  (author, subject), new `_note_subject`, `_notes_touching`, `notes_get`
  (`GET /notes`), `journal_append` (kind stamping), `journal_root` (`move_root`),
  `event_post` (+ `_CHAT_ONLY_FIELDS`). No change to `session_arm`,
  `approvals_decide`, `plan_start`, `plan_cancel`, `plan_mode`, `limits_route`:
  the gate runs before them.
- `web/setup_api.py`: `journal_setup` -> `move_root`.
- `core/journal.py`: kind `unverified`; `append(root_dir=)`; `_same_folder`,
  `_blocks`, `carry_over`, `move_root`.
- `core/limits.py`: `_shown`, `describe_patch`; `save` journals every change.
- `mcp.py`: docstring, `instructions`, `t_sm_status`, `t_note_set`, three tool
  descriptions.
- `static/agent-setup.js`, `static/style.css`: Setup's Limits section.

## Pins

`tests/test_agent_guardrails.py` (44 tests, real Flask app on a synthetic chip,
`PROVE_CALLERS` on):
- **The window's cookie.** Its attributes; named per port; never on a JSON
  answer or to a request with the agent header.
- **Person routes.** Curl with a person's name but no window is refused on every
  person route, and nothing changes. The window still presses. Another
  process's or another port's cookie is refused. The agent header is refused
  even with the cookie. Every gated endpoint exists and takes a POST. A
  production app proves by default. Without `PROVE_CALLERS`, the agent header
  is still refused on the routes that had no check of their own.
- **Limits.** An agent's loosening is refused and journaled. Every change is
  journaled with who/old/new. The webhook's path and userinfo are never
  journaled.
- **The journal move.** Day files carried; both folders told; the Calibration
  log shows the old day; moving back and forth duplicates nothing; Setup moves
  it the same way; the same folder is not a move; an agent cannot move it.
- **The journal door.** An agent signs its own lines; no header and no window
  -> `unverified`; an agent cannot claim a run.
- **The hook key.** Made once. An event without it, or with a wrong one,
  records nothing. The hook's own event is recorded. An event cannot speak in
  the Agent panel. `fire_event` sends the key.
- **Notes.** Read back by the next agent through `state_get` and
  `/api/agent/notes`. Bare qubit and pair names are normalised. A bare-name note
  written before the fix is still found. An unrelated entity gets none.
- **The bridge.** `sm_status` carries the notes and survives an SM without
  them; `note_set` claims no author; the `instructions` carry the rules; the
  docstring names the timeout.

`tests/test_agent_api.py::test_a_note_lands_in_the_entity_notes_store` pinned
B-04 itself (`author == "claude-code"`); it now pins the caller as the author.
`tests/agent_setup_selfcheck.cjs` gained six checks for Setup's Limits section.

**Mutation sweep.** Each piece was reverted on its own, the pins run, and the
piece restored.

- **Python: 44 of 44 red.** Two needed a second pass:
  - "no cookie issued" first went red only through a syntax error in the
    mutation. Redone as `if False:`, it is red for the right reason.
  - "cookie handed to the bridge" went GREEN. The pin asked a client that
    already held the cookie, so no answer would have carried one. The pin now
    asks a fresh client, and is red.
- **JS (`agent-setup.js`): 6 of 6 red.**

| reverted piece | caught by |
|---|---|
| the cookie check / `from_person` ignoring the cookie | `test_curl_with_a_persons_name_but_no_window_cannot_press` |
| the agent-header refusal | `test_an_agent_with_the_cookie_is_still_an_agent` |
| no cookie / not HttpOnly / handed to the bridge / not per port | `test_a_page_hands_the_window_its_proof`, `test_the_cookie_is_named_per_port` |
| claim, adopt, cancel, limits, Setup journal, Setup context dropped from `PERSON_ONLY` (each) | the curl test / the agent-with-cookie test |
| production not enforcing | `test_a_production_app_proves_by_default` |
| the hook gate / any key accepted / the hook not sending it / the key rotated | `TestTheHooksKey` |
| chat fields kept on an event | `test_an_event_cannot_speak_in_the_agent_panel` |
| refusal not journaled / not described | `test_an_agent_trying_to_loosen_its_limits_is_refused_and_journaled` |
| only the mode journaled / webhook not redacted / userinfo kept | `TestLimitsAreThePersons` |
| no carry / old or new folder not told / duplicates / same folder moved | `TestTheJournalMovesWithItsHistory` |
| either door back to `set_root` | the two door tests in the same class |
| agent kind not stamped / `unverified` not stamped | `TestTheJournalDoorSignsLines` |
| note author from payload / bare or pair name kept / notes never read / not filtered / old bare notes unmatched / leaf `state_get` without notes | `TestNotesAreReadBack` |
| `sm_status` without notes / bridge claiming an author / instructions / docstring | `TestTheBridge` |

**Related files** (cqt env):
- The 33 test files touching the agent API, the bridge, the hook, the journal
  and its page, limits, setup, notes, story, the actor plumbing, route
  hardening, `test_web` and the multi-instance pins: 1341 passed, 21 skipped,
  0 failed.
- `test_instance_move`, `test_instance_disk` and `test_main` (with
  `TestWaitForServer` deselected, as CLAUDE.md says): 42 passed.
- Every `tests/agent_*selfcheck.cjs` and `journal_page_selfcheck.cjs` is green.

## Verified on the rig

**Setup.**
- Rig: `D:\work\sm_qa_rigs\agent\rP1a`, made by `make_rig.py`, chip network
  `127.0.0.1:1`.
- SM was served from this worktree on 5111 (waitress, a production app), with
  `USERPROFILE`/`HOME`/`CODEX_HOME` sandboxed.
- The in-app session ran the real Claude CLI against a scripted Messages API
  (`rP1a\mock`, from docs/247). No model was called, no node reached
  `run_node`, and Dry run stayed ON.
- Evidence: `rP1a\ev\*.json`, `rP1a\shots\`.

**Outside the window** (`verify.py`, plain HTTP):
- `POST /session/arm` with `X-SM-Actor: Kim`: 403 `no_window_proof`.
- `POST /limits {mode: auto, human_recent_min: 0, webhook_url: ...}` with
  `X-SM-Agent: claude`: 403 `person_only`, and the limits were unchanged. The
  journal says: "by_claude tried changing the limits (mode -> auto,
  human_recent_min -> 0, webhook_url -> https://hooks.slack.com/...) --
  refused ...".
- `/journal/root` and `/journal/claim` with the agent header: 403.
- `POST /event` without the key: 403 `unproven_event`, and no journal line.
- The real hook process (`python -m quam_state_manager.hook --backend codex
  --instance <inst>`) read `inst\agent_link\hook.key` and posted. Its event
  became "`by_codex` ran `05_power_rabi`".
- `POST /api/agent/journal` with no header: kept, as `unverified`.
- Two real bridge processes:
  - Codex (`clientInfo: codex`) pinned "do not touch, fridge warming" on `qA4`.
    It was stored as `qubits.qA4`, author `by_codex`.
  - A Claude bridge then got the note in `sm_status` and in
    `state_get qubits.qA4.T1`; `qubits.qA5.T1` had none. The `initialize`
    instructions carry the rules.

**In headless Chrome** (CDP 9431, real mouse clicks, `rP1a\journey.cjs`):
- The page holds `sm_person_5111` (httpOnly, sameSite Strict, path `/`), and
  `document.cookie` is empty.
- A Task message started the in-app session; **Arm** -> armed (`03_armed`).
- A `/run 11_power_rabi qA1` card: the card's **mode** select set ask-all, and
  **Start** -> RUNNING, with the session told (`05_plan_start`). A second card
  -> **Cancel** -> CANCELLED.
- A held write on `qubits.qA1.T1` -> **Write to chip** -> approved; live went
  1.25e-5 -> 2.5e-5 (`06_`, `07_`).
- Setup **7. Limits**: max writes 200 -> 50 -> **Save limits** -> "Saved". The
  journal says "limits max_writes_per_plan 200 -> 50 (set by human:Kim)"
  (`09_`).
- Setup **4. Journal folder** -> `rP1a\vault\journal` -> **Use this folder**:
  three day files were carried, and both folders got their "moved here from" /
  "moved to" line. The Calibration log for 2026-10-01 and for today reads the
  moved history (`11_*`).
- Calibration log, 2026-09-30: run #1 -> "I ran this" -> Kim -> **Save** ->
  `human:Kim` (`13_claimed`).
- Open the Agent home -> Calibration log -> Back -> reload: intact. The repo's
  `agent_setup_back.cjs` journey on Setup: intact. Console errors: 0 on every
  page.
- After the browser, the same arm press replayed without the cookie: 403.

**The desktop window.**
- `rP1a\desktop_launch.py` ran `main.main()` from this worktree (pywebview
  6.2.1 / WebView2).
- Debugging was opened with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`; pywebview's
  own `REMOTE_DEBUGGING_PORT` setting did not open the port here.
- The window held `sm_person_6067` (httpOnly, Strict, a session cookie). A
  limits press from the page answered 200; the same press from outside got 403
  (`15_desktop_window`).

**One defect of this change**, found in the browser and fixed: the Limits
form's padding hid Pico's select arrow under "ask-writes" (`09_` vs
`14_limits_1366`).

## Not done here (open)

- **The same-user limit** (above). An agent with a shell can still impersonate
  the window on purpose, or edit `instance\agent_limits\*.json`. A real
  boundary means running the agent CLIs under their own OS account.
- **Routes still open to any caller**, by choice or out of scope:
  - `/session/stop` and `/session/disarm`. They only tighten, but their journal
    line names the caller from the header.
  - `/chat/start` and `/chat/send` (the in-app session's composer). Arming is
    still a person's.
  - `/setup/test` (a read-only question) and `/queue/clear`.
  - `/scheduler/settings`, which holds Dry run, a guardrail in all but name.
  - The GUI edit and apply doors still label a curl edit `human`.
  - `GET /api/agent/limits` shows the webhook URL to any caller.
- **An old hook.** A hook running an older SM package sends no key. Its events
  are refused live, and journaled only at SM's next start, from its jsonl.
- **`human_recent_min` 0 becomes 30** at the gate (`agent_api.run_node`, a P3
  from the campaign). Setup's label does not say so.
- **Approver not named** on approval lines (C-30), and `decided_by` is not in
  `approvals.summary`. Left for the approvals branch.
- **Journal labels changed.** The agent's journal lines now read `by_claude` /
  `by_codex` instead of `agent` (A-23), and so do the bridge's own "applied N
  edit(s)" lines.
