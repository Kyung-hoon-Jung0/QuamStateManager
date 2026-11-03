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
The page refreshes after a successful move so trees show the new key.
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
Fix reloads the displayed trees/grids after their response is processed.

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
rows also include this list). Before committing, every pointer that resolved
before the move must still resolve at its relocated holder. A newly dangling
pointer aborts the move and rolls back its new parents, structural changes and
pointer edits. Already unresolved pointers do not independently block a move.

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
and refreshed only after Fix. The DOM harness stubbed native dialog open/close,
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
subfield targets, and rollback when a relative reference to an external sibling
would become dangling.

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
