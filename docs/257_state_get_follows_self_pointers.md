# docs/257 -- state_get follows self pointers (A-17)

## Report and reproduction

The agent validation campaign found that `state_get` could not read operation
aliases such as `qubits.q1.xy.operations.x180 = "#./x180_DragCosine"`, even
when the sibling pulse was present with `amplitude = 0.3`.

Before the fix, the new regression file failed **10/10 tests**:

- `QuamStore.resolve_value(...x180)` and `/api/agent/state?path=...x180`
  returned `"#./x180_DragCosine"` as the resolved value.
- `resolve_value(...x180.amplitude)` raised `KeyError`; the API returned 404.
- A `#../` then `#./` chain stopped at the self-pointer string.
- Dangling self pointers and self cycles stayed raw but emitted no warning.

The reproduction uses temporary synthetic state/wiring files and a Flask
test client. It starts no web server and reads no external state files.

## Root cause

Locations at the starting commit `30de21e6`:

| Location | Cause |
|---|---|
| `core/loader.py:587`, `QuamStore.resolve_value` | `if is_pointer(raw) and not is_self_ref(raw)` excluded every `#./` pointer. Its preceding `get_value` also could not cross a pointer in the middle of a path. |
| `core/pointer_resolver.py`, `resolve_pointer` / `_traverse` | The global resolver deliberately returns self pointers raw and excludes them from leaf-chain and intermediate-pointer resolution. Removing only the loader condition would not fix the bug. |
| `web/agent_api.py:228`, `state_get` | The raw `store.get_value(path)` failed before the route could resolve a path through an alias. |
| `mcp.py:168`, `t_state_get` | The MCP tool forwards the API result without performing its own resolution. |

## Why self pointers were excluded

The original contract in `docs/01_pointer_resolver.md` treats `#./` as a
QUAM runtime alias. References such as `#./inferred_RF_frequency` often name
Python properties absent from serialized JSON. SM cannot compute those
properties from a missing JSON key.

The package-wide caller audit found these uses of `is_self_ref`:

- `cr_semantics` and `query` preserve runtime values and derive frequencies
  using their own domain rules.
- `history`, `leaf_index`, and the run-history readers in `web/routes.py`
  preserve runtime fields instead of inventing snapshot values.
- `leaf_classify`, `qubit_columns`, `pair_columns`, `edit_policy`, and the
  inspector renderers in `web/routes.py` classify runtime/read-only fields
  and prevent edits that would break inference links. The inspector
  templates consume that classification.
- `loader` excluded self pointers from `resolve_value`; its load-time
  pointer scan also deliberately excludes them.

The only executable callers of `store.resolve_value` in the package are the
agent state route and the agent update-preview reader in `web/agent_api.py`.
`all_values` and `diagnostics` also mention it in explanatory comments, but
use other resolution functions. The update preview now benefits from the
same alias-aware read.

## Fix and safety

`QuamStore.resolve_value` now uses the existing
`pointer_path.resolve_field_target` follower, exactly as the Live-Edit path
does. Its path rules remain:

| Pointer | Target path from the field holding it |
|---|---|
| `#/X` | root plus `X` |
| `#../X` | holder minus its last two segments, plus `X` |
| `#./X` | holder minus its last segment, plus `X` (a sibling) |

The follower tracks visited holder paths and bounds pointer hops. Cycles
terminate. Missing runtime properties and dangling pointers remain raw
with a warning; missing requested paths still raise `KeyError`. The store
reads the final path with `get_value`, because the edit follower's scalar
result represents containers as `None`. This preserves actual dictionaries,
lists, and JSON null values without mutating the state.
Returned containers keep their nested pointer strings; reading a pointer to
its own container does not recursively expand that container or create an
object cycle.

If the shared follower cannot resolve a directly requested absolute/parent
pointer, the store retains the legacy resolver as a fallback. That resolver
can cross pointers embedded inside a pointer's target path, a case the edit
follower's raw target walk cannot handle. For example, `LO_frequency` pointing
to `#/output/frequency` still resolves when `output` itself points to a port.
Two additional store/API pins caught this regression in the first patch
and verify the preserved behavior. The loader's `is_self_ref` import is also
retained for existing consumers of that module-level name.

The agent route uses the first candidate to retrieve the raw requested leaf
after any intermediate aliases, then `resolve_value` for its resolved value.
It preserves `value`, `resolved`, and `is_pointer`; the last flag now also
describes intermediate pointer traversal. It adds `resolved_path`, which is
null when the shared follower cannot determine the final target. Containers
reached through aliases carry
the same pointer metadata and retain the existing subtree size limit.
`source_file` describes the raw leaf's source, preserving existing direct
pointer behavior. MCP needs no change because it forwards the JSON response.

The global `pointer_resolver`, `QuamStore.resolve_pointer`, its cache, and
`pointer_path` are unchanged. Their existing consumers retain their behavior.
The new behavior is confined to the store read accessor and agent route;
the grids and inspector use the same follower they used before.

## Pins and mutation checks

`tests/test_state_get_self_pointers.py` runs each of five pins against both
the store and Flask API: **10 required regression tests**, plus two legacy
absolute-target compatibility tests. API pins also assert raw
values, pointer provenance, and canonical target paths. Dangling/cycle pins
assert warning-level log records and unresolved-path metadata; the cycle
pin has a ten-second timeout on its read/assertion body, excluding Flask
fixture imports from the cycle-termination check.

Each mutation below was applied to the implementation, tested in a fresh
process, and restored byte-for-byte in a `finally` block:

| Pin | Mutation | Result |
|---|---|---|
| Alias | Make `#./` drop two holder segments instead of one | 2 failed |
| Path through alias | Disable the follower's intermediate-pointer loop | 2 failed |
| Dangling self pointer | Replace the store's unresolved raw fallback with `None` | 2 failed |
| Self cycle | Disable the visited-path guard (the hop bound still terminates the walk) | 2 failed |
| Parent then self chain | Disable the follower's leaf-pointer loop | 2 failed |
| API provenance | Force `is_pointer=False` | 1 failed, 1 passed |
| Legacy absolute target | Disable the legacy resolver's successful fallback | 2 failed |

Result: **5/5 required pin mutations RED, 10/10 store/API pin instances
RED; 7/7 mutations RED including the additional API provenance and legacy
compatibility mutations**.
No mutation remains in the working tree.

## Validation

The initial focused run passed **88 tests, 13 skipped**, covering the new
pins, loader, agent API, and existing pointer-path tests.

The final selection comes from
`rg -l 'resolve_value|is_self_ref|pointer_resolver|state_get|/api/agent/state' tests`:
17 Python test files, plus `test_pointer_path.py`. Tests run with the required
`D:/miniconda3/envs/cqt/python.exe`, `PYTHONUTF8=1`, no pytest cache, and a
600-second timeout. All temporary files and bytecode are confined to this
worktree's ignored `.tmp_a17-validation` directory.

The selected `test_rb_levels.py` probes an external real-data folder during
collection. A local validation wrapper intercepts forbidden filesystem
probes before any access, so that real-data pin skips; its synthetic pins
run normally. Other absent real-data and JS prerequisites retain their
existing skips.

The matching `stress_agent_mcp.cjs` driver hard-codes a different checkout
and external instance. Its **offline `nosm` scenario passed** using an
ignored scratch copy with only those two location constants redirected into
this worktree. The MCP handshake succeeded, 25 tools were listed, and
`sm_status` / `state_get` reported the unavailable SM without a timeout or
stderr. The validation wrapper asserts the tool list and both refusal results.
Live-server scenarios remain unrun under the task's hard rules.

Final selected run: **471 passed, 34 skipped, zero failures**, across all
17 matching Python files plus `test_pointer_path.py` (18 files total).
The regression file contributes **12 passing tests**. The final mutation
sweep is **7/7 RED**, with every required store/API pin RED under its
corresponding mutation. `git diff --check` also passes.
