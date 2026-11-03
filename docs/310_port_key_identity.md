# Port dictionary keys and physical identities

Measured on 2026-11-03. Two installed conda environments were exercised using
copies of existing small states. Public labels A and B omit local environment,
source-folder and device names. Both used QUAM 0.6.0 and quam-builder 0.4.0;
A used qm-qua 1.4.1, B used qm-qua 1.3.1.

Every case started from a fresh temporary copy inside this worktree. Source
copies were read only. Only explicit temporary state.json/wiring.json paths
were written; no `save()` calls, live connections, or original-state edits were
used. A loaded the complete small state. B could not load that state's custom
root class, so used another existing copy with a standard root class. Its full
state had an unavailable optional readout-pulse class and dangling pulse aliases;
the measurement copy was reduced to one actual qubit's z channel, the actual
port entities, and the corresponding wiring. Its pulse operations were emptied.
The port identity, delay and filter data were preserved. Baselines generated
successfully before comparing mutations.

## Measured config behavior

In A the selected LF output was `(con1, 7, 1)` and the declared outputs were
`1,2,3,4`. In B it was `(con1, 5, 1)` with outputs `1,2,3,4,5,6`.
Here `F` means 7 in A and 5 in B. The output column is the actual key set under
`config['controllers']['con1']['fems'][F]['analog_outputs']`; the tuple is
`config['elements'][z_element]['singleInput']['port']`.

| Change from baseline | A output keys | B output keys | Element port, both environments | QUAM result |
| --- | --- | --- | --- | --- |
| None | 1,2,3,4 | 1,2,3,4,5,6 | `(con1,F,1)` | Generates |
| Only `port_id`: 1 to 2 | 2,3,4 | 2,3,4,5,6 | `(con1,F,2)` | Generates; duplicate configuration warnings for delay (A/B), filter (B) |
| Only dict key: 1 to 99; pointer still ends in 1 | No config | No config | No config | Unresolved-reference warnings, then `TypeError` from the LF port constructor |
| Only wiring pointer: existing port 1 to existing port 2 | 1,2,3,4 | 1,2,3,4,5,6 | `(con1,F,2)` | Generates without an identity warning |
| Only `port_id`: 1 to unused 99 | 99,2,3,4 | 99,2,3,4,5,6 | `(con1,F,99)` | Generates without an identity warning |
| Dict key and wiring pointer: 1 to 99; fields still say 1 | 2,3,4,1 | 2,3,4,5,6,1 | `(con1,F,1)` | Generates without an identity warning |

Unrelated pulse deprecation warnings in A and a NumPy/SciPy version warning in
B are excluded from the identity results above. Duplicate configuration warnings
report overwriting config entries, not a dictionary-key/identity disagreement.
The unused port 99 is a config-generation probe, not a hardware-validity claim.

QUAM resolves the pointer to an entity and then uses that entity's identity
fields to generate the config. Reconstructing the generation spec currently
reads addresses from wiring pointer strings. Thus a rebuild can change the
physical destination when those addresses disagree. Reconstruction now reports
both identities and explicitly states that rebuild pins use the wiring pointer
address, while QUAM uses the resolved identity fields.

A pointer-only change to another correctly identified port is a consistent
reroute. An identity check cannot infer whether that reroute was intended.
Existing missing-port diagnostics cover dangling wiring pointers.

## Implementation contract

`core.port_identity.port_identity_mismatches(state)` is pure, imports no hardware
stack, and inspects every kind under `ports`. Results contain `path`, string
`key` tuple, original `fields` tuple, differing field names in `differ`, and
`duplicate_paths`. Present claims compare as strings, so integer fields and
string dictionary keys agree. Absent fields make no claim. Complete identical
field identities on distinct entities are reported on both entities. Duplicate
checks are scoped to the kind: input and output addresses are distinct resources.

Diagnostics emit ERROR findings with both values, direct and chained pointer
users, a Go to field target, and the port key used to pin the wiring diagram.
The check is included in the diagnostics catalogue.

`/field/edit` returns a 409 offer before staging a disagreeing identity field.
The browser offers **Fix other side too**, **Keep as typed**, or **Cancel**.
`port_ack=fix` (the original `port_identity_ack` remains accepted on the
single-field door) moves the port entry and retargets pointers, including
references to its subfields; missing destination parent dictionaries
are created explicitly. Existing destination entries are never overwritten.
The move and pointer edits form one undo group and roll back on failure.
The original request resolves with the resubmitted response so its caller
repaints through the normal success handler; Fix does not reload the page.
`port_ack=keep` stages only the typed field. The MCP state-edit bridge
also exposes these answers. No choice silently rewrites the other side.

The overwrite-live preflight adds identity warnings to its existing confirmation;
the user can still apply. This advisory does not introduce a blocking gate.
The same offer covers `/field/edit-batch`, `/type-fix/apply`, `/field/create`,
the legacy qubit/pair inspector editors, and pulse edits whose aliases reach a
port identity leaf. Whole-object writes are inspected recursively. Batch offers
are an array with each offending row's index, path, typed identity and proposed
move. Any offending row returns **409 before any batch value or type assignment
is written**, including an independent batch. Resubmit with `port_ack=fix|keep`.
Fix groups every move, pointer rewrite and ordinary row into one Ctrl+Z group;
a failure rolls back that whole gesture. Later rows follow earlier moves of the
same port. Keep preserves the ordinary batch write policy. Unusable dictionary
keys still require acknowledgment; Fix refuses them without writes, while Keep
retains the typed value subject to the existing type checks.

The shared browser transport shows every offer in one Fix/Keep/Cancel dialog.
It covers TreeMulti (including Ctrl+H), bulk paste/fill-down and other fetch batch
callers, type conversion, and legacy HTMX inspectors. Requests retain their rows,
undo-group token and headers when resubmitted. Cancel never resubmits. Successful
Fix returns the resubmitted response to the original caller. Concurrent offers
queue one dialog at a time, preserving every unanswered choice.

The route audit also found constrained writes to extras, frequencies, pulse IDs
and links, and historical undo/redo/replay of already accepted changes. These do
not introduce a newly typed port identity. Regen populate remains out of scope.
Diagnostics still detect inconsistencies introduced by external editors.

Pointer users and moves resolve each pointer at its own holder using the
project's `pointer_to_abs` rules: `#/` starts at the root, `#../` starts two
segments above the holder, and `#./` starts at its containing object. Resolution
follows chains and pointers crossed inside a target path. This is the editable
field-target interpretation of `#./`; unresolved runtime aliases remain
unresolved. Relative spellings are retained when they still reach the intended
post-move target; otherwise the rewrite uses an absolute pointer. Move responses
list rewritten holders and pointer values in `port_pointer_retargets` (batch
rows also include this list). Before committing, every pointer must satisfy
`post_landing(moved(holder)) == moved(pre_landing(holder))`, preserving `None`
for unresolved pointers. This checks target identity, including pointers held
inside the moved port that reach external targets. Any changed landing aborts
the move and rolls back new parents, structural changes and pointer edits.

## Verification

The requested pytest selection runs with UTF-8 in environment B. PowerShell does
not expand the `tests/test_diagnostics*.py` argument for Python, so the matching
filenames are expanded with `rg --files` and passed as an argument array:

```powershell
$env:PYTHONUTF8 = '1'
$diagTests = @(rg --files tests -g 'test_diagnostics*.py')
conda run -n <environment-B> python -m pytest tests/test_port_identity.py @diagTests tests/test_regen_spec.py tests/test_fsp_compensation.py -q -p no:cacheprovider
```

Coverage includes every port kind and identity field, integer/string equivalence,
missing claims, duplicates, diagnostics values/users/diagram pin, reconstruct
notes and actual chosen rebuild pin, route offers, both explicit answers,
controller/FEM/port moves, one-step undo, collision refusal, pointer-subfield
retargeting, failure rollback, and the nonblocking apply advisory.

Final run: **458 passed, 2 skipped**, including the requested selection plus
`test_mcp_bridge.py`, `test_mcp_bridge_p2.py`, and `test_mcp_bridge_safety.py`.
The existing NumPy/SciPy version warning remained. Local npm test dependencies
were installed without package-file changes so the DOM checks could execute.
JavaScript syntax checks passed. A separate DOM interaction check exercised Fix,
Keep and Cancel, verified no resend before a choice, preserved the chip header,
and originally refreshed only after Fix (superseded by the review fix below).
The DOM harness stubbed native dialog open/close,
which jsdom does not implement.

Three mutations were applied individually and restored after each run. All
three were killed by test assertion failures:

| Mutation | Detecting test |
| --- | --- |
| Remove string normalization from identity comparison | `test_string_comparison_and_missing_claims` |
| Disable duplicate identity detection | `test_duplicates_report_both_entities` |
| Bypass the edit-time 409 offer | `test_edit_guard_then_explicit_move_and_undo` |

Temporary measurements, raw data and mutation logs are ignored local artifacts
and are not committed.


## Follow-up verification

The follow-up adds batch preflight/answers, mixed rows, multiple port moves,
several identity fields on one port, one-step undo, failed-batch rollback,
whole-object offers, legacy inspector and pulse-alias doors, created identity
leaves, and type-conversion preflight. Pointer cases cover relative users at
depths one and two, absolute/relative/self chains, retained relative spellings,
subfield targets, and preserved external-sibling targets when moving the
holder would otherwise make its relative reference dangling.

The TreeMulti DOM selfcheck runs the shipped identity transport through Ctrl+H
and Apply. It verifies that every row is listed, no resend occurs while the
choice is pending, Cancel makes no second request, and Fix/Keep preserve the
batch rows and group token while adding the explicit acknowledgment.

PowerShell expands the requested diagnostics glob before invoking pytest:

```powershell
$env:PYTHONUTF8 = '1'
$diagTests = @(rg --files tests -g 'test_diagnostics*.py')
conda run -n <test-environment> python -m pytest tests/test_port_identity.py @diagTests tests/test_tree_multi.py -q -p no:cacheprovider
node tests/tree_multi_selfcheck.cjs
node --check quam_state_manager/web/static/port-identity.js
```

Mutation A removes the batch 409 guard: both atomic and independent preflight
tests fail because the route returns 200 and writes without acknowledgment.
Mutation B skips relative pointers: relative-user/chain tests fail. A second B
mutation skips relative retargets only in the move planner: the depth-one and
depth-two successful-move assertions fail because the invariant refuses the
newly dangling pointers (400 instead of 200). Each mutation was restored before
the normal verification run. Mutation logs remain ignored
local artifacts.

Final follow-up run: **340 passed, 1 skipped** for the requested selection.
The existing NumPy/SciPy compatibility warning remains. The standalone
TreeMulti/HTMX DOM selfcheck, JavaScript syntax checks, and `git diff --check`
also passed. The shared transport test additionally verifies that HTMX Keep
retains request headers and values, and HTMX Cancel never resubmits.


## Review fixes

Pointers held inside the moved subtree join the rewrite candidates even when
pointing outside it. The exact regression moves `con1.2.1` to `con1.3.1` with
`delay = "#../2/delay"`, while sibling delays are 20 and 30. Its landing stays
`con1.2.2.delay` and its value stays 20. A separate injected, still-resolving
wrong landing proves the invariant checks equality for every pointer.

Deleted entries record their dictionary position so rollback and undo restore
sibling order. The failure regression compares `list(parent.items())` as well
as the change log. Whole-object and created-leaf Fix tests inject a failure
after the initial write and prove `stage_write` removes every partial change.

Fetch retries support a `Request` without `init`; offers are recognized from
the JSON body even with a text/plain response header. The jsdom selfcheck pins
three queued offers, independent responses, Cancel, preserved headers, and no
immediate or delayed reload. It also executes the real bundled HTMX 2 file,
submits an actual form, observes its FormData-backed parameters proxy, and
checks repeated values survive Fix and the successful response swaps normally.

Node staging checks identity leaves after resolving aliases and recursively
checks container writes. A disagreement holds the complete write group and
puts the reason in its approval card. It requires review through the existing
field editor's Fix/Keep choice; approval staging never silently creates the
mismatch. Tests exercise a node's direct and alias updates, the approval card,
unchanged live state, and an empty pending tray.

The review verification command is:

```powershell
$env:PYTHONUTF8 = '1'
$diagTests = @(rg --files tests -g 'test_diagnostics*.py')
conda run -n <test-environment> python -m pytest tests/test_port_identity.py tests/test_tree_multi.py tests/test_agent_runs.py @diagTests -q -p no:cacheprovider
node tests/port_identity_selfcheck.cjs
node tests/tree_multi_selfcheck.cjs
```


Each review mutation was applied alone and restored before the next run. All
were killed by assertion failures:

| Mutation | Regression |
| --- | --- |
| Omit holders inside the moved subtree from candidates | Exact 20/30 delay repro |
| Replace landing equality with a non-None check | Injected wrong but resolved landing |
| Skip restoring the deleted key's position | Order-sensitive rollback comparison |
| Remove `stage_write` rollback | Whole-object and created-leaf partial writes |
| Open concurrent dialogs immediately | Three pending offers, exactly one dialog |
| Restore delayed reload after Fix | jsdom reload error and no-reload assertion |
| Bypass Request calls without init | Request JSON and multipart retry |
| Require JSON Content-Type on the offer response | text/plain offer body |
| Drop original HTMX form values | Change form while awaiting answer, preserve original values |
| Disable the approval identity guard | Direct and alias node updates held with card reason |

The HTMX mutation initially survived because its source form still contained
the original values. The test now changes the form during the pending choice,
so rereading the form cannot masquerade as preserving the original request.
The strengthened test kills that mutation. Raw logs remain ignored local
artifacts.


Review results: the requested selection passed with **385 passed, 1 skipped**.
The final port/modifier run, including the additional unresolved-pointer
invariant regression, passed with **241 passed, 5 skipped**. Tray restart,
recovery and undo-journal checks passed with **79 passed**. Both touched `.cjs`
selfchecks, JavaScript syntax validation and `git diff --check` passed. The
existing NumPy/SciPy version warning remains.
