# docs/273 -- Small fixes in the agent HTTP API

2026-10-04. Findings C-28, A-19, D-17 and the read-endpoint parts of A-23.
Changes are confined to `fix/agent-api-p3`, starting at `c7f21c10`.
Production edits are only in `web/agent_api.py`; the write doors are untouched.

## Verified causes and behavior

| Finding | Verified cause | Behavior now |
|---|---|---|
| C-28 | `_journal_line` replaced every newline in a Stop answer with a space, then sliced to 600 characters. | Preserve newlines. Answers of at most 600 characters are unchanged. Longer answers reserve space for `\n[truncated]` inside the cap, end at the last complete line that fits when possible, and otherwise cut the long line. The existing journal continuation escaping and `agent_says` setting still apply. |
| A-19 | `runs` delegated the experiment filter to `DatasetStore.list_runs`, which requires exact spelling. | Use `core/search_query.py`: case-insensitive substring tokens, spaces for AND, standalone `|` for OR. Filter before applying `n`, preserving newest-first ordering and the date/qubit filters. If the experiment query matches no archive name, return an empty success with up to five unique `closest` names ranked by case-insensitive similarity and a readable `hint`. A valid experiment with no rows on the requested date does not produce a false unknown-name hint. |
| D-17 | `_chat_card`, `_journal_line`, `_relevant` and `_human_ran_recently` recognized only `Bash` as a shell. | Recognize `Bash`, `PowerShell`, `exec_command`, `shell_command` and `shell`, including dotted function names such as `functions.exec_command`, in all four filters. Keep the original tool name in the card. PowerShell calibration runs become feed cards, journal entries and relevant activity, and are not mistaken for a person's run. |
| A-23: versions | The HTTP implementation clamps `n=0` to **one**. The finding's reported 30 comes from `mcp.t_versions`, which rewrites zero to 30 before the request. Negative HTTP counts also clamp to one; non-integers raise. | Refuse zero, negative, empty and non-integer HTTP counts with JSON HTTP 400, `n must be a positive integer`. The omitted default remains 30 and the upper bound remains 500. |
| A-23: field history | The history manager can return an empty timeline for a path that does not exist. | Verify the path in the open store first, using the same pointer-aware lookup as the state endpoint. A missing path returns HTTP 404 with `no such path`; an existing path can still have an empty history. |
| A-23: dates | `runs` accepts arbitrary date text. `journal_get` checks only the date's shape, allowing impossible calendar days. | Both read endpoints require a real calendar day in the exact `YYYY-MM-DD` format and return JSON HTTP 400 for invalid days. Leap days are accepted. The journal's `day` alias follows the same validation. Runs validate dates even with no dataset open. |
| A-23: subjects / nodes | `/notes?path=` and `/runs?qubit=` accept unknown subjects as empty successes. | Notes normalize a bare qubit/pair name and validate the requested path, returning 404 for a missing subject. Runs validate qubits against the open dataset, returning 404 with the available names. Unknown experiment/node queries have the A-19 empty result and closest-name hint. `/manual/<family>` already refuses an unknown family with 404. |
| A-23: session | `session_get` already loads by `_chip_key()` after docs/253. | No production change. A new pin creates distinct chip-key and display-name records and proves the correct session is returned. A chip without a session record can still truthfully return null. |
| A-23: simulated | The runner writes the flag in `result.simulated`, while `_run_view` reads `m.simulated`, so a dry run can show `simulated: false` beside `result.simulated: true`. | Use the recorded result flag when present, including explicit false. Fall back to legacy top-level metadata when the result has no flag. `/run/<key>`, `/runs/agent` and cards share `_run_view`; current Runner settings are not consulted. |

The backend verification found Claude's native `PowerShell` name, Codex's
`command_execution` normalization to `Bash`, and Codex function-call names
passed through unchanged. The shell filters accept those function-call forms.

## Scope exclusions

- Unknown nodes/targets in `run_node`, plan proposals and write approvals
  belong to the write doors and docs/253--254. No hunks touch them here.
- `mcp.t_versions` still coerces zero to 30 before sending the HTTP request.
  Changing the bridge function is outside this task's permitted
  `agent_api.py` read/feed function hunks. The new HTTP behavior is pinned;
  a bridge follow-up must preserve zero to receive that refusal.
- The runner's simulation injection, staging and application behavior is
  unchanged. This fix reads the runner's already recorded result.
- Dataset list/detail responses do not previously expose a `simulated`
  field; this change corrects the contradictory agent-run views rather than
  inventing a simulation status for archived runs without evidence.
- `core/journal.py` already stores multiline entries and is unchanged.

## Pins and mutation checks

`tests/test_agent_api_p3.py` contains 49 cases. All use Flask test clients,
temporary synthetic chips/datasets, or read-helper fixtures. No hardware,
real chip server, CLI agent, or user configuration is used.

Mutation checks alter only the guarded function in `agent_api.py`, run only
the selected new pins, require the expected RED assertion/test count, and
restore the original source bytes after every trial. The session lookup is
mutated too, despite being an existing fix. The shell relevance and journal
guards are broken separately so a combined pin cannot mask either guard.

All **49/49 pins** were mutation-checked: **24/24 source mutations** produced
the expected RED, totaling **55 failing test executions** (the six shell
pins were run against both the relevance and journal mutations). Each
trial restored the original bytes. The initial GREEN run passed all 49 cases.

| Mutation group | Trials | RED cases |
|---|---:|---:|
| Multiline storage, exact cap, complete-line cut, marker, setting | 5 | 6 |
| Search grammar, filter-before-limit, closest names, empty date slice | 4 | 8 |
| Shell cards, relevance, journal, human attribution | 4 | 19 |
| Versions count validation | 1 | 3 |
| Missing field and empty existing history | 2 | 2 |
| Invalid dates and valid leap days | 2 | 10 |
| Missing note subject, bare name, missing run subject | 3 | 3 |
| Existing chip-key session lookup | 1 | 1 |
| Result simulation precedence and legacy fallback | 2 | 3 |
| **Total** | **24** | **55** |

The regression selection is the literal requested grep over Python pytest
files (231 files, including the new pins):

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = (Resolve-Path tmp_cr_audit/agent_api_p3).Path
$env:TMP = $env:TEMP
$p3TestFiles = @(rg -l 'agent_api|mcp_bridge|journal|agent_runs|runs' tests -g 'test_*.py')
& D:/miniconda3/envs/cqt/python.exe -m pytest -p no:cacheprovider --timeout=900 `
  --basetemp=tmp_cr_audit/agent_api_p3/regression -x -q --tb=short @p3TestFiles
```

This is a subset of the repository's 568 existing pytest files, not the
full suite. Python wrappers exercise their Node selfchecks where available.
Standalone browser/stress scripts that need a running SM window are not
launched; the API work uses Flask clients and temporary folders.

The final regression result will be recorded here when the run completes.

## Review at integration (coordinator)

- The shell-tool names appeared as four hand-copied tuples in `agent_api.py`, and two of them were multi-line. They are now one constant, `_SHELL_TOOLS`, read by the journal, the feed and the strip. Dropping `PowerShell` from it turns 2 pins red.
- Codex's regression run stopped on `test_runner_b3::...test_a_plain_folder_is_unversioned_not_live`. That test passes alone both on main and with this change, so it is order-dependent and unrelated. Related files re-run: 350 + 176 passed.

