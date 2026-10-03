# 269: Pure rules for saved-state changes

S2 of the state-tracking hub. Every run is compared with the state immediately
before it. This module computes facts about the saved documents; it does not
infer who changed them or whether a run used a copied state.

Design source: `D:\work\study\2026-10-03_state-tracking-hub\DESIGN.md`, all of
section 3, read on 2026-10-04. The task's binding user decisions override that
document's pointer resolution and copied-state proposals.

## Rules and provenance

| Rule | Provenance | Implementation |
|---|---|---|
| Run by run, including A → B → A and identical consecutive saves | User decision | `diff` compares only its two inputs; no deduplication or lineage inference. |
| Do not detect or flag copied-state runs | User decision | No flags in S2. The design's `OFF_LIVE` proposal is dropped. |
| Display state is the loader's deep merge, wiring wins | User decision and design 3.1 | `hub_rules.merged` imports the pure function factored from `loader.py` into `state_merge.py`. |
| Holder paths, without pointer resolution | User decision overriding design 3.2 | Dict segments joined with dots; list indices are decimal numbers. |
| Scalar arrays of at most 16 entries flatten per element | User decision | Includes booleans, strings, numbers, and null. |
| Scalar arrays longer than 16 entries are one value | User decision | Fresh marker `{"_array": length, "_hash": sha1}`. |
| Non-scalar lists recurse | User decision | Lists of dicts remain per element; matrices have paths such as `x.0.1`. Each immediate scalar row has its own threshold. |
| Floats compare exactly; NaN agrees with NaN | User decision | No tolerance. Signed zeros agree; equal infinities agree. |
| Booleans never equal numbers; int 1 equals float 1.0 | User decision | No rounding large integers through float. |
| Strings and booleans are recorded | User decision | Canonical JSON text, including string quotes and boolean literals. `__class__` strings are also recorded. |
| Pointer strings are stored raw; changed pointer strings retarget | User decision | Recognize `#/`, `#../`, `#./`. No target lookup. |
| Retarget requires an existing pointer on both sides | Derived operation convention | Pointer-to-ordinary-value transitions are `set`; adding/removing pointers is `add`/`gone`. |
| Changes carry both old and new | Task requirement and design 3.2 | Frozen `Change(path, op, old_num, old_txt, num, txt)`. |
| Rows sort lexically by path | Task requirement; derived choice of lexical order | `x.10` precedes `x.2`; a display may choose natural order separately. |
| Raw state hash is sha1(state bytes + NUL + wiring bytes) | Task requirement and design 3.2 | Original bytes, including whitespace; file order matters. |

The helper modules use only the standard library and have no file, database,
logging, clock, or pointer-resolution calls. Inputs are JSON documents, including
Python JSON's NaN and Infinity extensions. `same` also recursively handles JSON
containers without Python's bool/int container-equality leak.

### Paths and empty values

Ordinary keys keep their existing literal dot-path spelling. Inside a dictionary
key, `\` becomes `\\` and `.` becomes `\.`. An empty key becomes `\e`.
These are derived collision-prevention rules: `{"a.b": 1, "a": {"b": 2}}`
has distinct paths `a\.b` and `a.b`. A literal key `\e` becomes `\\e`.
Consumers must split at unescaped dots and unescape segments before looking up
keys. Numeric object keys and list indices retain the same spelling; the document
structure tells them apart.

Empty dicts and lists contribute no leaves, matching the loader's existing
walker. Null is a recorded scalar. Root scalars and root long arrays use the
empty path; root short lists use `0`, `1`, etc. Nonstring object keys raise
`TypeError`, rather than silently producing ambiguous JSON paths.

Changing from 16 to 17 scalar entries replaces 16 element holder paths with one
array holder path: 16 `gone` rows plus one `add`. The reverse is symmetrical.
This representation change is intentional.

### Encoding and merge compatibility

Canonical text is `json.dumps(sort_keys=True, separators=(",", ":"),
ensure_ascii=False)`: compact, stable key order, UTF-8, no Unicode normalization.
NaN and Infinity use Python's JSON extensions when encountered in containers.
Numeric leaves retain their original int/float value in the numeric fields.
Booleans are text. Stored null is text `null`; an absent side has both fields
`None`, so adding/removing null is unambiguous.

Long-array hashes use the UTF-8 bytes of canonical JSON after finite integral
floats are converted to their exactly equal integer (including -0.0 → 0).
Booleans retain their JSON type; NaNs get the stable token `NaN`. Thus int/float
round trips and repeated NaNs do not change a long-array hash, while distinct
large integers remain distinct. Hash comparison assumes SHA-1 content identity,
as requested; this is a content address, not a security primitive.

The loader's former pure merge body and `_deep_merge` moved to `state_merge.py`.
The loader calls that same body through its existing `merge_state_wiring`
wrapper, preserving its exact warning text and top-level collision conditions.
Neither input is mutated. Unchanged branches still share references with the
inputs, preserving the loader's original shallow-copy behavior. S3 must not
mutate those branches through the merged result.

## Read-only real-archive comparison

`tools/check_hub_rules.py` reads the three archives directly. It does not copy,
write, move, delete, create indexes, or start SM. Each enumeration is frozen
before sampling; new folders wait until the next invocation. Missing/incomplete
state pairs or unusable node timestamps are counted and skipped. Reads check
size and mtime before and after the state/wiring pair and retry a changing pair.

Consecutive means adjacent eligible saved-state runs ordered by offset-aware
`node.json.created_at`, then run id and folder name. A fixed random seed selects
200 distinct adjacent pairs per archive. The live archive may grow between
invocations, so its run and sampled-row counts can change.

The baseline is the actual `Differ.diff` with its default 1e-12 tolerance and
`ignore_keys=set()` so class strings receive the same coverage as other strings.
Both sides are `QuamStore.from_dicts`, using the loader's real deep merge. Passing
tuples to the old differ would use its separate shallow merge and introduce a
merge difference unrelated to S2. Path order and the old `modified` versus hub
`retarget` label are normalized for value comparison. Both old/new payloads,
operations, and completeness of the hub rows are checked.

Differences are counted over the union of row paths, not pairs: an old-only
element row and a new-only array-holder row each count once under `long_arrays`.
`int_float` counts old-only type-change rows; `exact_float` counts new-only rows
hidden by the differ's tolerance. `nan_constant` is another allowed binding-rule
difference. `bool_string` is zero against **Differ**, which already records these
leaves; their addition fixes the older **leaf index**, not this baseline.

Command (all output paths and test temporaries stay in this worktree):

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& D:/miniconda3/envs/cqt/python.exe tools/check_hub_rules.py `
  --archive D:/work/Customer_Codes/dataset/arbel_20260929 `
  --archive D:/work/Customer_Codes/dataset/Novera9Q `
  --archive D:/work/Customer_Codes/dataset/KH_202608_CZ `
  --pairs 200 --seed 269 --focus-archive 1
```

| Archive | Pairs | Differ rows | Hub rows | Int/float | Array row differences | Exact floats | Unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|
| arbel_20260929 | 200 | 2,293 | 1,519 | 0 | 792 | 0 | 0 |
| Novera9Q | 200 | 1,615 | 1,616 | 0 | 0 | 1 | 0 |
| KH_202608_CZ | 200 | 57,798 | 1,097 | 1 | 56,780 | 0 | 0 |

Bool/string and constant-NaN differences are zero in all three sampled archives.
The synthetic NaN-constant check gives **0 rows**. Read skips during sampled pair
comparison are zero. Scan skips and eligible-run counts appear in the exact output.

Exact output from the final check (2026-10-04):

```text
{"archive": "arbel_20260929", "differences_by_reason": {"bool_string": 0, "exact_float": 0, "int_float": 0, "long_arrays": 792, "nan_constant": 0}, "eligible_runs": 1592, "hub_rows": 1519, "matched": 1510, "new_only_by_reason": {"bool_string": 0, "exact_float": 0, "int_float": 0, "long_arrays": 9, "nan_constant": 0}, "old_only_by_reason": {"bool_string": 0, "exact_float": 0, "int_float": 0, "long_arrays": 783, "nan_constant": 0}, "old_rows": 2293, "pairs": 200, "read_skipped": 0, "scan_skipped": 1, "seed": 269, "unexplained": 0}
{"focus": [898, 899], "archive_adjacent": true, "old_rows": 2, "hub_rows": 2, "waveform_spectator_old_rows": 0, "waveform_spectator_hub_rows": 0, "unexplained": 0}
{"focus": [899, 900], "archive_adjacent": true, "old_rows": 108, "hub_rows": 13, "waveform_spectator_old_rows": 98, "waveform_spectator_hub_rows": 3, "unexplained": 0}
{"focus": [900, 901], "archive_adjacent": true, "old_rows": 108, "hub_rows": 13, "waveform_spectator_old_rows": 98, "waveform_spectator_hub_rows": 3, "unexplained": 0}
{"focus": [901, 902], "archive_adjacent": true, "old_rows": 108, "hub_rows": 13, "waveform_spectator_old_rows": 98, "waveform_spectator_hub_rows": 3, "unexplained": 0}
{"focus": [902, 903], "archive_adjacent": true, "old_rows": 108, "hub_rows": 13, "waveform_spectator_old_rows": 98, "waveform_spectator_hub_rows": 3, "unexplained": 0}
{"focus": [903, 905], "archive_adjacent": false, "old_rows": 115, "hub_rows": 20, "waveform_spectator_old_rows": 98, "waveform_spectator_hub_rows": 3, "unexplained": 0}
{"focus": [905, 906], "archive_adjacent": true, "old_rows": 108, "hub_rows": 13, "waveform_spectator_old_rows": 98, "waveform_spectator_hub_rows": 3, "unexplained": 0}
{"focus_pairs": 7}
{"archive": "Novera9Q", "differences_by_reason": {"bool_string": 0, "exact_float": 1, "int_float": 0, "long_arrays": 0, "nan_constant": 0}, "eligible_runs": 2069, "hub_rows": 1616, "matched": 1615, "new_only_by_reason": {"bool_string": 0, "exact_float": 1, "int_float": 0, "long_arrays": 0, "nan_constant": 0}, "old_only_by_reason": {"bool_string": 0, "exact_float": 0, "int_float": 0, "long_arrays": 0, "nan_constant": 0}, "old_rows": 1615, "pairs": 200, "read_skipped": 0, "scan_skipped": 13, "seed": 269, "unexplained": 0}
{"archive": "KH_202608_CZ", "differences_by_reason": {"bool_string": 0, "exact_float": 0, "int_float": 1, "long_arrays": 56780, "nan_constant": 0}, "eligible_runs": 4112, "hub_rows": 1097, "matched": 1057, "new_only_by_reason": {"bool_string": 0, "exact_float": 0, "int_float": 0, "long_arrays": 40, "nan_constant": 0}, "old_only_by_reason": {"bool_string": 0, "exact_float": 0, "int_float": 1, "long_arrays": 56740, "nan_constant": 0}, "old_rows": 57798, "pairs": 200, "read_skipped": 0, "scan_skipped": 9, "seed": 269, "unexplained": 0}
{"nan_constant_rows": 0}
```

### The XEB estimate versus the saved documents

For the adjacent XEB transitions #899→900, #900→901, #901→902,
#902→903, and #905→906, the full saved-state comparison is **108 old rows →
13 hub rows**, with zero unexplained differences. The waveform has 96 elements:
its 96 old rows become one holder row. Together with the two spectator pointer
holders, this is **98 → 3**. Ten other changes remain: six scalar calibration
values, a class string, and three null-valued fields.

#898→899 is **2 → 2**. #904 is a different experiment, so #903→905 is consecutive
within XEB but is **not adjacent in the archive**: it is **115 → 20**, with the
same 98→3 waveform/spectator subset. That focus-only comparison does not replace
either run's comparison to its actual predecessor. The design's approximately
105→3 estimate is therefore not the full saved-state count under the binding
rules. No remaining saved-state changes are hidden to force that estimate.

## Pins and mutations

`tests/test_hub_rules.py` has **31 tests**, all with generic synthetic fixtures:

| Pin | Failure injected for mutation check |
|---|---|
| Numeric leaves and holder paths | Replace scalar values with null. |
| Bool/string/null/class leaves | Replace nonnumeric values with numbers. |
| 16-element boundary | Hash 16-element arrays. |
| 17-element boundary and known SHA-1 | Leave 17-element arrays expanded. |
| Array content and length | Use a constant content hash. |
| Array numeric equality, NaN, large ints | Remove integer normalization. |
| Long arrays with strings/bools/null | Convert bools to ints before hashing. |
| Lists of dicts | Collapse non-scalar lists. |
| Matrices and nested long rows | Lose numeric list indices. |
| Escaped dot/backslash/empty keys | Remove key escaping. |
| Empty containers and root leaves | Drop root scalar leaves. |
| Nonstring keys | Silently stringify keys. |
| Bool versus number | Treat bool as a number. |
| Int versus float | Restore type-strict numeric equality. |
| Exact float equality | Add a float tolerance. |
| Constant NaN | Make two NaNs unequal. |
| Nonfinite numeric payloads | Put nonfinite leaves in text. |
| Exact strings | Compare strings case-insensitively. |
| Recursive equality | Use Python container equality. |
| All three pointer prefixes | Never recognize pointers. |
| Pointer/plain transitions and structural ops | Retarget when only one side is a pointer. |
| Pointer targets remain separate holders | Emit an unchanged pointer holder. |
| Add/gone/set, old/new, null presence | Swap old and new payloads. |
| Canonical JSON text | Stop sorting object keys. |
| Lexical path order | Reverse row order. |
| Array threshold representation change | Drop removed element rows. |
| Shared merge and loader display parity | Replace deep merge with shallow shadowing. |
| Loader warnings | Downgrade collision warnings to debug. |
| Input immutability and no logging | Merge into the input dict. |
| Original-byte pair hash | Omit the NUL separator. |
| Run-by-run reversion | Suppress decreasing-value reverts. |

Six additional mutations cover empty-key escaping, recursion inside a colliding
dict, wiring precedence, reversing the byte pair, a different removal of float
normalization, and making NaN agree with a finite float. Every pin was targeted.

**37/37 mutations RED; all 31/31 pins covered.** Every final RED was an
assertion failure (or the expected missing-exception assertion), not a syntax or
collection error. All three mutated source files were restored byte-for-byte.

Mutations ran sequentially against the corresponding pin using the mandated
Python, `PYTHONUTF8=1`, `pytest -p no:cacheprovider --timeout=900`. Source bytes were
restored in `finally` after every mutation, including failures. Logs and the local
harness are under ignored `.tmp_hub/`, within this worktree. The first sweep found
a missing NaN-versus-finite-float assertion and a numeric-payload assertion that
raised TypeError instead of reporting an assertion failure. Both new pins were
strengthened before the final sweep. No existing tests were edited.

Final targeted regression run:

```text
519 passed, 16 skipped, 1 warning in 11.33s
```

Files: `tests/test_hub_rules.py`, `tests/test_loader.py`,
`tests/test_differ.py`, `tests/test_differ_tree_parity.py`. The 31 new tests pass.
The skips are existing unavailable example-state fixtures. The warning is the
existing invalid-escape warning in `tests/conftest.py`, which was not edited.
No full suite or server was run. A `python -S` import and pure-call smoke also
passes, demonstrating that hub rules need no third-party packages.

## S3 contract and open implementation choices

The ledger builder consumes `merged`, `flatten`, `diff`, and `state_hash`:

- Keep an event for **every** saved run, even if the raw hash repeats or the diff
  is empty. Compare each saved merged document against its immediate ledger
  predecessor. Do not split fit writes from human changes or infer copied states.
- Persist each `Change` directly with its path, operation, old fields, and new
  fields. Preserve the absent-versus-null distinction. No pointer resolution or
  derived flags come from S2.
- Store the raw pair hash separately from array content hashes. Different raw
  bytes can produce zero changes: whitespace, int/float round trips, or empty
  container structure do not necessarily create leaf rows.
- Array markers contain no reconstructable array payload. S3 must retain the
  run reference or collect the original list for blob storage and element
  slicing. Use the documented normalized array encoding for that content hash.
- Decode escaped holder segments before looking up source values. A path's
  segments alone cannot tell numeric dict keys from list indices.
- Keep source refs/checkpoints for exact state replay, including empty containers
  and int/float type-only saves; change rows alone do not preserve these details.

Open choices belong to S3: lossless persistence of NaN, infinities, and integers
outside SQLite's signed 64-bit range (SQLite REAL NaN becomes NULL); array blob
retention and source availability; escaped-path decoding in lookup/query code.
No user decision blocks S2. The full XEB count differing from the design's
estimate is measured evidence, not an unresolved equality rule.
