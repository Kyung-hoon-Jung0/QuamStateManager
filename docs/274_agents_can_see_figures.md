# docs/274 -- Agents can see figures; the pill describes the open chip

2026-10-04. Findings B-07, C-10 and C-11. Production changes are confined
to `mcp.py` and the read surfaces in `web/agent_api.py`. The write doors
and existing tests are untouched.

## Causes and behavior

| Finding | Cause | Behavior now |
|---|---|---|
| B-07 | `run` returned figure paths and told the agent to read those files itself. The files can be outside the CLI's allowed directory, and the agent has no image tool. | `run` still lists the figures, with names, zero-based indices and instructions to call `run_figure`. The new MCP tool fetches a declared PNG through SM and returns an MCP image content block. |
| C-10 | `_now_state` counted every failed hook event of every session. A tool error (one 404) turned the pill red, a failed `run_node` run did not count, and another chip's failures counted. | The count is today's FAILED NODE RUNS on the open chip, from two sources: (1) `run_node`'s own runs whose failure class is one of docs/249's five, keyed by the chip's machine key; (2) a node a terminal agent ran through its shell (`python NN_name.py`) that failed, attributed by the journal's own chip rule (`_chip_for_event`), the same event the journal writes as "✗ `node` failed". A failed shell command that ran no node, any MCP tool error, a refused call and a successful run do not count. |
| C-11 | The pill's `mode` always came from the chip's limits. | The pill shows the mode the next run obeys, through the run's own function `agent_runs.run_mode` (one rule, not a second copy): the armed, RUNNING plan's mode, else the armed session's, else the chip limit. A plan that is armed but no longer running does not set it. |

The pill JavaScript already renders `mode` and `failures_today` from the
API. It needs no change. A failure can show even without hook activity.
An old failure remains in today's count, but a later tool event does not
make that old failure recent again. The existing one-hour red-state
window is measured from the failed run's completion.

## Figure read contract

`GET /api/agent/run/<run_id>/figure` takes exactly one `name` or `index`
query argument. The MCP `run_figure` tool takes the same selector plus
`run_id`. Indices start at zero and refer to the order returned by `run`.

- The run must exist in the open dataset. Unknown runs return HTTP 404.
- The selector must identify a declared figure. Names containing `..`,
  directory separators or a drive separator are refused with HTTP 400.
- Resolve the file and the known run folder, then require containment.
  This rejects a file in a sibling folder or a symlink resolving outside
  the run folder. There is no arbitrary file-path argument.
- Only regular `.png` files with a PNG signature are accepted. Other
  content returns HTTP 415; missing files return HTTP 404.
- Refuse files larger than 2,097,152 bytes with HTTP 413 and a readable
  `figure exceeds the 2 MB image limit` error. Check size before opening
  and read at most the limit plus one byte, checking again after the read
  to catch a growing file.
- HTTP returns JSON with an `image` object. MCP returns that object
  directly inside its `content` list, with `type: image`, base64 `data`
  and `mimeType: image/png`. It is image content rather than JSON text.
- `SM_MCP_MODE=readonly` lists and allows `run_figure`. The bridge stays
  a stdlib HTTP client and reads no dataset file itself.

## Failure records

`_failed_runs_today` reads the persisted run metadata and overlays the
current process's records by run key. That counts each run once and does
not truncate today's count to the registry's 50 recent cards. It requires
an exact chip key and an `ended` timestamp in the local day, plus one of
`host_unreachable`, `hardware_contention`, `timeout`, `node_error` or
`interrupted`. A `node_error` refusal is excluded because no node ran.

The pill polls, so the persisted scan is memoized on the runs folder's
signature (mtime + entry count): it is re-read only when a run folder is
added or removed. A meta rewritten in place belongs to a run this process
started, and the in-process registry already holds its current record.

A terminal agent's failed node run arrives as a hook event, not as a
`run_node` record, so it is counted from the events of today's relevant
sessions; `run_node` itself is an MCP tool, so the two sources never
count the same run twice.

## Pins and mutation checks

`tests/test_agent_figures_pill.py` adds 39 cases over Flask test clients,
a real synthetic DatasetStore, temporary run metadata, and an MCP stdio
subprocess. The initial GREEN run passed all 39 after correcting the new
protocol fixtures to include JSON-RPC's required version field.

The pins cover selector names and indices, path rejection, resolved
containment, unknown runs and figures, ambiguous selectors, both size
checks and the bounded read, PNG content, missing images, listing help,
MCP image content and readonly visibility. The pill pins cover every
failure class, tool errors, chip switches, completion-day boundaries,
refused and successful calls, complete history, deduplication, old
failures and plan/session/chip mode precedence.

The mutation runner was a scratch script (not committed). Each trial compiles a broken source
variant, runs the selected new pins, requires failing test executions
and restores the original source bytes in `finally`. Evidence is kept
beside it in `mutations.json` and per-trial logs.

All **39/39 pins** were mutation-checked: **38/38 source mutations** turned
RED, totaling **56 failing test executions**. Every source variant compiled
and the original source bytes were restored after each trial. Two initially
surviving mutations exposed weak new assertions: the unknown-name case
only checked the status, and a start-day error could substitute one run
for another while preserving the total. The new pins now assert the
unknown-name refusal and check the day count after each record is added.
Both mutations then turned RED. The final GREEN run passed all 39 cases.

All **13/13** requested `tests/agent_*selfcheck.cjs` scripts passed,
including the existing pill selfcheck's 27 assertions. The JavaScript and
existing tests remain unchanged.

## Regression selection and existing expectation

The requested Python selection contains 28 files, including the new pins,
and is the literal search
`rg -l 'mcp|agent_api|agent_pill|agent_panel|run_figure' tests -g 'test_*.py'`.
Run those files with the specified Python environment, `PYTHONUTF8=1`,
`pytest -p no:cacheprovider --timeout=900`, and stop on the first existing
test failure. Run all `tests/agent_*selfcheck.cjs` separately. This is a
subset of the suite.

Codex's first version counted only `run_node` records, and two existing
tests (`test_agent_api.py::...::test_a_failure_is_journaled_with_its_error`,
`test_agent_pill.py::TestPrecedence::...`) went red: both drive a node a
terminal agent ran and that failed. Those tests were right -- that IS a
failed node run -- so the counter now takes the terminal source too, and
both pass unedited. The coordinator's review added seven mutation-checked
pins (terminal node failure counts, a failed non-node shell command does
not, another chip's does not, yesterday's does not, the scan is reused
until a run is added, a non-running plan does not set the mode, the mode
comes from an armed session only): 7/7 RED.

## One existing pin superseded

`test_chat_api.py::TestLimits::test_a_failed_tool_counts_as_a_failure`
pinned exactly the behavior C-10 reports as the bug (an MCP tool error,
here "no chip", counted as a failure). It is replaced by
`test_a_failed_tool_is_in_the_feed_not_the_failure_count`: the failed
event is still in the feed with its error, and `failures_today` stays 0.
The MCP module docstring's tool count moved from 25 to 26
(`test_mcp_bridge_p2.py::test_module_docstring_tool_count`).
