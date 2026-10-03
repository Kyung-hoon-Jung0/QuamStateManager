# docs/260: the MCP bridge never guesses its window

2026-10-03. Findings A-18, A-21, B-06, A-15 and A-23 in the terminal MCP
bridge. All changes are confined to the `fix/mcp-bridge-p2` worktree.

## Root causes

Lines below refer to starting commit
`7af2eac6e687f96f8e469db5fe26750ee52bd75a`.

| Finding | Original location | Cause |
|---|---|---|
| A-18 (P3) | `mcp.py:578`, `handle`; `mcp.py:631`, `main` | Fields were used before shape validation. Arrays were treated as batches, malformed JSON was silently dropped, and `readline()` had no bound. Non-object params and unhashable tool names could escape the tool exception handler. |
| A-21 (P2) | `mcp.py:115`, `_sm`; `core/agent_link.py:114,235`, discovery | Registry entries were sorted newest first and `connect` returned the first responding window, without selecting by chip. The bridge cached that choice. |
| A-21 (Setup) | `core/agent_setup.py:167`; `web/setup_api.py:163,180`; `web/static/agent-setup.js:192,206` | The spec accepted a chip but not a URL; the UI never requested a pin. Default terminal entries therefore contained neither pin. |
| B-06 (P3) | `mcp.py:378`; `web/agent_api.py:454`; `core/autofit/knowledge.py:98` | The bridge forwarded the entire linted manual response, including every case and all sections, with no selectors. |
| A-15 (P2) | `web/agent_api.py:921`; `core/agent_setup.py:40` | Relevance recognized only the in-app server prefix. Setup used a different terminal server name. Hook registration intentionally covered built-in file/shell tools only. |
| A-23 | `mcp.py:28`, protocol documentation and the `TOOLS` table | The module did not have a count checked against the table. The actual table contains 25 tools. |

## Behavior now

**A-18.** The main loop rejects non-JSON and invalid UTF-8 with JSON-RPC
`-32700`. It rejects non-object requests, wrong versions, invalid methods or
IDs, and arrays/batches with `-32600`. Params and `tools/call` argument
shapes are checked before dispatch (`-32602`); unknown methods/tools receive
`-32601`. `initialize` also checks the nested client-info and protocol-version
shapes. Null, string, integer and finite numeric IDs are echoed; booleans,
containers and infinite numeric IDs are rejected. Non-finite JSON constants
are parse errors.

Notifications without an ID are silent and never execute tools. An MCP
`notifications/*` message containing an ID is an invalid request. Empty lines
remain harmless. Lines are capped at 1 MiB; an oversized line is drained in
bounded chunks before the next message. A decoder recursion error is also a
parse error. No batch member is executed.

**A-21.** Bridge discovery probes all distinct candidate URLs through
`/api/agent/chip`. Dead or unsuccessful endpoints are excluded. With no
`SM_URL` or `SM_CHIP`, exactly one live window can be selected. More than one
refuses every available tool other than `sm_status`: the refusal lists each
window's URL and chip facts, including the identity pin, and explains how to
set the environment variables. `sm_status` returns that list without choosing
a window; with one window it reports its URL and chip identity.

`SM_URL` limits discovery to that URL. `SM_CHIP` selects by `pin`/`chip_key`,
declared name, or the existing display-name compatibility rule. Name matches
still latch the first matched chip key (docs/246). A name matching multiple
windows cannot choose one. A URL/chip mismatch refuses calls. Each tool call
rechecks discovery, so a second window opened after an earlier attachment
requires an explicit choice. An ambiguity status must not stamp the last
attached chip onto the list of windows.

Setup offers an unchecked **Pin this window and chip** checkbox beside each
CLI connection preview. When checked, the server gets the open chip's `pin`
from the same function as `/api/agent/chip` and puts it in `SM_CHIP`, together
with the request's window URL in `SM_URL`. Both the Claude JSON entry and
Codex TOML block preserve these environment fields. The checkbox warns that
the port may change after restart. The UI applies the preview's pin choice,
even if the checkbox changes afterward, and passes the previewed chip pin;
an intervening chip switch refuses the write. An unloaded chip cannot be
pinned. The default entry remains unpinned. The existing explicit `chip`
argument remains compatible.

**B-06.** `family_manual(family=...)` keeps its previous response when no
optional selector is supplied. `outline=true` returns case IDs/titles and
available section names; `case_id="C1"` returns one complete case; `section`
returns one of `physics`, `rules`, `closure_rules`, or `signal_map`. Invalid
or conflicting selectors are tool errors. The tool description warns that a
full response can be about 55 KB. Slicing stays in the stdlib-only bridge,
after SM has loaded and linted the manual; the shared API/knowledge loader
is unchanged.

**A-15.** `core/agent_link.py` supplies one server-name tuple and
`is_sm_mcp_tool`. `_relevant` uses it for the exact `mcp__sm__` and
`mcp__quam-state-manager__` prefixes; similarly named foreign servers do not
match. The only production edit to `agent_api.py` is this relevance hunk.
The hook matcher remains `Bash|Edit|Write|MultiEdit`: SM already handles its
own MCP actions through requests carrying `X-SM-Agent`. Adding those tools
to the hook would duplicate recording. The matcher exclusion and actor
header are pinned together; existing in-app hook suppression is retained.

**A-23.** The module explicitly states **25 tools**, with a pin that extracts
the number from its docstring and compares it with `len(TOOLS)`.

## Pins and mutation checks

`tests/test_mcp_bridge_p2.py` is new; no existing test was edited. Each
malformed-input parameter runs the real main loop over an in-memory binary
stream, followed immediately by a valid ping. Discovery uses fake HTTP links;
Setup uses Flask's test client and temporary homes. The UI pin uses Node and
jsdom against the actual Setup JavaScript. No SM web server or real agent
client was started.

| Finding | Collected new pins | Mutations | Result |
|---|---:|---|---|
| A-18 | 59 | Suppress each error-response category; reply to notifications; stop echoing valid IDs. | 59/59 RED, restored GREEN |
| A-21, including Setup | 20 | Disable window-choice refusal; omit status identity; retain cached selection; ignore pin opt-in; pin defaults; remove unloaded/changed-chip checks; drop the UI preview/apply pin. | 20/20 RED, restored GREEN |
| B-06 | 14 | Ignore selectors; change the default response; remove the size hint. | 14/14 RED, restored GREEN |
| A-15 | 7 | Recognize no server or every server; restore the old relevance prefix; add SM tools to the hook matcher. | 7/7 RED, restored GREEN |
| A-23 | 1 | Change the documented count to 24. | 1/1 RED, restored GREEN |

**101/101 collected pins were mutation-checked**, using **29 source
mutations**. Each pin failed under at least one relevant mutation, every
mutation caused assertion failures, and each source file was restored
byte-for-byte after its run. The restored file passed all **101 pins**.
Mutations were sequential; existing test assertions were never changed.
One pytest process stalled before collection and was stopped; the remaining
mutation runs enabled only the timeout plugin, then completed. The repeated
notification run produced all five expected assertion failures.

## Verification and open items

The first required targeted run completed with **459 passed, 12 failed** in
17 test files, including the 101 new pins. Both required Node self-checks
passed (`agent_setup_selfcheck.cjs`: 49 checks). Python was
`D:/miniconda3/envs/cqt/python.exe`, with `PYTHONUTF8=1`,
`-p no:cacheprovider --timeout=900`. Temporary files and substitute CLI homes
were kept inside this worktree. The full suite was not run.

The final regression run after all mutations passed **190 tests** across
`test_mcp_bridge_p2.py`, `test_mcp_bridge.py`, `test_mcp_bridge_safety.py`,
and `test_agent_setup.py`. `git diff --check` passed.

The required Python file search selected these 17 files:

```text
test_actor_plumbing.py             test_agent_api.py
test_agent_backend.py              test_agent_guardrails.py
test_agent_history_restore.py      test_agent_panel.py
test_agent_pill.py                 test_agent_setup.py
test_approval_is_what_was_seen.py   test_arming_scope.py
test_bundles.py                    test_chat_api.py
test_hook.py                       test_main_dispatch.py
test_mcp_bridge.py                 test_mcp_bridge_p2.py
test_mcp_bridge_safety.py
```

The same **12 existing failures** reproduced with all six changed production
files restored to starting HEAD: **91 passed, 12 failed** across the three
failing modules. Source files were then restored byte-for-byte to the patch.
The failures concern approval or arming decisions outside the permitted
`agent_api.py` hunk. Existing tests remain intact; `core/agent_runs.py` is
unchanged. The required all-green targeted gate is therefore still open.

| Existing test file | Failing class/test at starting HEAD and with the patch |
|---|---|
| `test_agent_guardrails.py` | `TestTheWindowsProof::test_the_window_still_presses` |
| `test_approval_is_what_was_seen.py` | `TestAnAllowCoversExactlyOneRun::test_an_allow_for_one_params_set_is_never_spent_on_another` |
| `test_approval_is_what_was_seen.py` | `TestAnAllowCoversExactlyOneRun::test_a_dropped_param_or_another_plan_is_another_run` |
| `test_approval_is_what_was_seen.py` | `TestAnAllowCoversExactlyOneRun::test_a_spent_approval_and_a_held_chain_say_so_in_the_mode_they_are_in` |
| `test_approval_is_what_was_seen.py` | `TestAnAllowCoversExactlyOneRun::test_the_approval_card_and_the_recent_list_carry_the_params` |
| `test_approval_is_what_was_seen.py` | `TestAnAllowCoversExactlyOneRun::test_the_run_card_carries_its_params` |
| `test_approval_is_what_was_seen.py` | `TestAllowTellsTheExactCall::test_allow_run_tells_the_agent_the_exact_arguments` |
| `test_approval_is_what_was_seen.py` | `TestAllowTellsTheExactCall::test_the_journal_names_who_allowed_what` |
| `test_approval_is_what_was_seen.py` | `TestTheRequestIsWhatACardCanShow::test_a_qubit_for_a_pair_node_and_a_pair_for_a_qubit_node` |
| `test_approval_is_what_was_seen.py` | `TestTheRequestIsWhatACardCanShow::test_a_replay_of_another_nodes_run_is_refused` |
| `test_approval_is_what_was_seen.py` | `TestEachAgentPressesForItsOwnRows::test_an_agent_run_whose_door_meets_another_agents_rows_parks_its_writes` |
| `test_arming_scope.py` | `TestTheGrantEndsWithThePlan::test_a_node_outside_the_plan_is_refused_under_its_plan_id` |

The first gate rejects a blank Arm press; approval tests often receive
`not_in_plan` instead of an approval; target-kind validation precedes the
plan-membership refusal in the arming test. One held-write reason is
`mode ask-writes` rather than the asserted ownership reason. These decisions
must be reconciled by the work owning those gates; this patch does not change
them or weaken their assertions.

Remaining operational limits: URL pins can become stale when SM relaunches
on another port; regenerate the pinned entry or use only an identity pin.
Manual slicing reduces inline MCP content, while the bridge still fetches
the complete HTTP manual. Window discovery is a point-in-time probe; existing
chip/tray identity checks remain responsible for intervening chip switches.
The hook's separate newest-window event routing and the other `mcp__sm`
check in `_chat_card` are outside the requested relevance hunk and unchanged.
There are no additional open code items for A-18 or A-23. A-21's launch-port
and probe limits, B-06's full HTTP fetch, and A-15's unchanged external
hook/card paths are the per-finding limits described above; the 12 existing
test failures are the outstanding verification gate.
