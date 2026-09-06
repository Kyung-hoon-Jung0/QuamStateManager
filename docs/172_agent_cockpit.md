# 172 — SM 1.0: the agent's eyes and hands (part 1: the gate, the door, the bridge)

**Date:** 2026-09-06 · **Branch:** `feat/physics-daily-flow` · **On top of:** `5f1b3d5` (docs/169)

## 0. The customer's finding, and the decision

Users do not use Experiment Runner, Fit Replay or Auto Calibrate. In their words the
menus are unintuitive, and more to the point: they run **Claude Code in a terminal**
with the qualibrate repo and `state.json` as context and let it calibrate — often
overnight. "Why use SM for that?"

The product owner's decision (2026-09-06), in three parts:

1. The trio goes off the menus. Code kept behind `SM_EXPERIMENTAL=1`; deletion after
   the customer confirms (never the night before a release).
2. SM becomes the **assistant to the terminal agent**: its eyes (figures, tables,
   diffs, history — what a terminal cannot show) and its **hands** (the one safe door
   to `state.json`: working copy → Review tray → apply-to-live, with Ctrl+Z and
   Versions knowing every agent write). That is an MCP server.
3. Of Auto Calibrate, the parts a terminal agent does NOT have survive as tools:
   the deterministic fit gates, the per-family case manuals (docs/129–133), and the
   run/figure plumbing. The closed loop and the figure judge (docs/169) do not —
   a terminal Claude calling SM which calls `claude -p` again is a 30 s loop for a
   picture the outer Claude can Read itself.

A same-day red team (3 agents, docs/172 §4) shaped the details below; a UX round
(4 personas; 3 red-teamers + synthesis lost to a session limit) is part 2.

## 1. Shipped in part 1

### The gate (`SM_EXPERIMENTAL`)

A `context_processor` in `app.py` exposes `experimental` per render (a test can
flip it with `monkeypatch.setenv`). Gated: the sidebar trio + the running-run badge
(`base.html`), the **Ctrl-K palette** entries and the **landing/help getting-started
bullets** (the release red team found those two — a sidebar-only gate would have
left a palette that opens hidden pages and a help page teaching a menu that is not
there), and the dataset page's "Autofit diagnose" button. **Routes untouched**:
`/scheduler/status` is every page's 2.5 s heartbeat, a blueprint-wide
`before_request` guard imports the engine, `/qualibrate/open` and app startup call
scheduler code — a route-level 404 breaks normal pages and ~9 test files. Hidden,
not removed: a typed URL still renders. Pinned by `tests/test_experimental_gate.py`
on the RENDERED page (the r15 sidebar pins read template source and stay green
either way).

### The JSON door — `web/agent_api.py`, mounted at `/api/agent`

| route | what |
|---|---|
| `GET chip` | loaded?, path, name, `chip_token`, qubits/pairs, pending count, `live_diverged`, plus `now` |
| `GET state?path=` | one leaf (raw + pointer-resolved + source file) or a bounded subtree (60 kB cap, keys always) |
| `GET tray` | the staged edits + `seen_changes` (= count; docs/120's token) |
| `GET versions`, `GET field-history?path=` | the Versions list and one path's change points |
| `GET runs`, `GET run/<id>` | run rows; one run with parameters, outcomes, fit results and **absolute paths** of figures / node.json / data.json / ds_raw.h5 |
| `GET diagnostics` | findings as JSON + summary |
| `GET families`, `GET manual/<family>`, `GET check-fit/<run_id>` | the surviving autofit knowledge: family table, case manual (Clause-B linted), deterministic gates over a saved run |
| `POST note` | an entity note (docs/167 store), author `claude-code` |
| `GET/POST journal`, `journal/root` | the calibration journal (part 2 renders it); `reason` is REQUIRED for an agent entry |
| `POST event`, `GET now`, `GET events` | the live strip: fed by the hook, "running" = an unmatched `PreToolUse` under an hour old |

Behind the app-wide CSRF guard: an out-of-process caller sends `Origin:
http://<host:port>` equal to what it connects to (`localhost` ≠ `127.0.0.1`; verified
403 → 200 live).

### The bridge — `quam_state_manager/mcp.py`

```
claude mcp add sm -- python -m quam_state_manager.mcp
```

A stdio JSON-RPC server, **stdlib only** (the customer env has no `mcp` package),
newline-delimited, surface `initialize` / `notifications/initialized` / `ping` /
`tools/list` / `tools/call`. A THIN CLIENT of the running window: it finds the port
in `instance/instances/<pid>.json` (dead PIDs and null ports skipped, newest live
entry, confirmed by a GET; `SM_URL` overrides; `SM_INSTANCE` names the instance
dir). 18 tools: `sm_status`, `state_get`, `state_search`, `state_edit` (staged),
`tray`, `undo`, `apply_to_live`, `versions`, `field_history`, `runs`, `run`,
`diagnostics`, `check_fit`, `families`, `family_manual`, `journal_append`
(`reason` required by schema), `journal_read`, `note_set`.

**`state_edit` uses `/field/edit`** — the same door as a cell edit, with
`expect_chip`. Its 409 offers (numeric-text type fix, FSP amplitude compensation)
are handed back to the agent as `needs_answer` with the offer verbatim; the agent
answers with `type_fix` / `fsp_ack`, never SM on its behalf.

**`apply_to_live` declares what the agent SAW.** The first cut re-read the tray
right before applying and so wrote a human's edit the agent had never looked at
(reproduced on a chip copy: the human staged `q2.f_01` in the window, the agent
applied both). Now the bridge remembers the tray count from the agent's last
`tray` / `state_edit` / `undo` and declares that as `seen_changes`; SM's docs/120
gate refuses with the paths, the bridge forgets what it saw, and the agent must
`tray` again before it can press. Never `force`, never `ack_unseen` (pinned as a
source assertion).

### The hook — `quam_state_manager/hook.py`

Registered in the user's Claude Code settings for `PreToolUse` / `PostToolUse` /
`Stop`. **Disk first**: every event is appended to
`<instance>/agent_events/<date>.jsonl` before anything else, then POSTed to a
running SM with a 1 s timeout (SM replays today's file on start, so the
morning-after view knows last night). Journal lines only for what matters: a
`Bash` completion ("ran `05_power_rabi`"), an `Edit`/`Write`, and the turn's final
message ("Claude: …"); `Read`/`Grep`/`Pre` events feed the strip, never the journal.
With SM closed the journal line goes straight to the `.md`. Exit 0 whatever happens.

### The journal — `core/journal.py`

One `.md` per chip per day under a folder the user picks (`instance/journal.json`,
an Obsidian vault is the intended target; default `<instance>/journal`). SM only
APPENDS and RENDERS. The renderer is a whitelist over a markdown subset (headings,
bullets nested by indent, ordered lists, fenced code, blockquote, pipe tables, rule,
bold/italic/links (http(s) and relative only)/`[[wikilinks]]`); everything else is
escaped, nothing the agent writes becomes markup. Two SM tokens: `#1234` → the run
(`/dataset/by-run/1234`, htmx into the pane), a backticked dot path →
`<code class="jr-path" data-path>` for the field-history popover. Rendering surface
is part 2.

## 2. Verified

- **Real `claude` on the subscription login → the MCP → real SM with the real PJ 20Q
  chip**: `claude -p "Use sm_status, then state_get on qubits.q1.f_01" --mcp-config
  … --allowedTools mcp__sm__sm_status,mcp__sm__state_get` → `PJ_10082026, 20 qubits,
  4312.4052 GHz` in 18 s, `is_error: false`. (The unit is the model's arithmetic
  slip — the tool returned `4312405235.7 Hz`.)
- **Stdio round trip** of all 18 tools against the real window: reads, search,
  runs (real archive), manual, journal append/read, tray, apply (nothing staged).
- **Write path on a COPY of the chip** (never the customer's live files): stage →
  tray → a human edits in the window → `apply_to_live` refused naming `qubits.q2.f_01`
  → `tray` → apply → the live file holds the new amplitude → `undo`.
- **Hook end to end**: one simulated `PreToolUse` → jsonl on disk → `/api/agent/now`
  says `running: python 05_power_rabi.py`.

Pins: `test_experimental_gate.py` (6), `test_agent_api.py` (16), `test_journal.py`
(19), `test_mcp_bridge.py` (8, incl. a real stdio subprocess), `test_hook.py` (8, the
real module as a subprocess). Mutation sweep **19/20 red**: gate always-on, palette
ungated, journal without reason, tray seen=0, stale Pre as running, no replay,
pointer not resolved, HTML unescaped, any-scheme links, `#` inside a word, command
as path, repeated title, apply with a fresh read, apply before looking, 409 swallowed,
refusal not resetting, disk write skipped, Read journaled, Stop dropped. Regression:
`test_web` sidebar/onboarding pins green.

## 3. What part 2 is (not built here)

The UX round's four personas agree on more than they disagree: **never render the
transcript** (12 MB / 4.5k lines seen); the unit is a **run card** (family · target ·
verdict · run # · parameters that changed · "because:" · figure thumbnail · next Δ),
grouped by target/day, built by joining journal lines (`#run`) with `run/<id>`; the
PI needs actor badges and before→after with units; the terminal postdoc must never
see a second writer on the chip; the field engineer needs a Stop that owns the
process and zero terminal setup for persona B. The open decision is the chat inside
SM for users who will not use a terminal — the product owner's call, with the
mechanics/safety/product red team still to run. See the session notes.

## 4. The part-1 red team (3 agents), what it changed

- user lens: "why" is not in any file SM can read → `journal_append(reason
  REQUIRED)`; do not build a fourth "what happened" page → part 2 renders INTO
  existing surfaces; overnight is when live surfaces are off → a durable page with a
  since-last-visit marker; markdown must be a whitelist → done; run ids are
  folder-scoped → `#123` resolves through `/dataset/by-run`; the judge tool is a loop
  → dropped, deterministic layers exposed instead.
- mechanics: hooks carry tool name/input/result, only `Stop` carries text → the strip
  is tool-level, the reason is the tool; transcript is tail-able but private format →
  not relied on; discovery via the registry + PID probe + GET confirm; `Origin`
  exactness; disk-first hook; no `mcp` package → hand-rolled protocol.
- release: palette + help bullets + dataset button; keep `/scheduler/status`; gate at
  template level; a rendered-HTML pin; typed URLs still open the pages (accepted).
