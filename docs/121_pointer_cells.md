# 121 — A reference is a value

## The report

> In Live Edit, `control` / `target` are not shown in the cell — it just says
> **"not set"**. So the customer always has to go over to Json Tree View to
> check. Is it because it's a string pointer? Is there a reason? Is it a bug?
> It should be visible AND editable, like the tree. Fix every case of this.

It was a bug, it was one line, and it was never about pointers being
unsupported.

## Root cause

`_build_bulk_cell` — the ONE cell builder both grids share — did:

```python
val = ft.get("resolved_value") if resolvable else None
...
"missing": (not resolvable) or val is None
```

`resolved_value` is **scalar-nulled for containers** and absent entirely for a
pointer that resolves to nothing. So *"this pointer does not reach a scalar"*
was being read as *"this field is not set"*. Those are different questions.

Measured on the customer's 20-qubit chip, three real populations rendered blank
while the tree showed their value right there:

| population | example | cells |
|---|---|---|
| pointer → entity dict | `qubit_control = "#/qubits/q1"` | 60 |
| pointer → pulse dict (operation alias) | `xy.operations.x180 = "#./x180_DragCosine"` | 120 |
| dangling pointer | resolves to nothing on this chip | 200 |
| resolvable list | `mutual_flux_bias = [0, 0]` | 30 |

The list case already reached the ✎ list-cell swap — but it was **also** flagged
missing, and that flag is not cosmetic: docs/88 made `missing` mean *genuinely
absent* precisely because the grids turn it into `create: true`.

## What was actually dangerous

Display was the visible half. The write path was the other:

`edit_policy.resolve_edit_path` follows a leaf pointer to its target, on the
strength of a promise its own docstring states — *"the generic edit surfaces
render these as the resolved NUMBER"*. That promise does not hold when the
pointer reaches a **container**. For `qubit_pairs.q1-2.qubit_control` it
returned **`qubits.q1`**, so a write aimed at one cell was aimed at the entire
qubit object. Reproduced: only the type judge (`Expected dict, got str`) stood
between a typed qubit name and a chip whose `q1` became a string.

And with the cell visible but unguarded, two silent corruptions were reachable
— both returned **200**:

- typing `q3` stored the literal `"q3"` — a `Quam.load()` failure met days later
- typing `6.1e9` over a dangling pointer stored the **string** `"6100000000.0"`

## The fix

**Display** — in the same `val is None` branch that already existed (so a cell
holding a scalar pays nothing), ask the alias what *it* holds. A pointer with
no scalar behind it renders **the pointer**, exactly as `_pair_detail.html`'s
pointer-badge and the tree already do, and carries `ptr_kind` ∈
`dict | list | dangling`. `missing` now means what docs/88 says it means:

```python
"missing": (not raw_present) and val is None
```

**Write** — `resolve_edit_path` follows a pointer only to a **scalar**. A
container target means the cell *is* the pointer, so that is where the write
lands. Value-mode is byte-unchanged for every pointer that reaches a number.

**Guard** — `edit_policy.pointer_cell_refusal` refuses plain text on a cell
that has no scalar behind its pointer, naming the current link and the two ways
forward (type a pointer to re-point; the Pulses page's 3-mode editor to break
the link on purpose). It is narrow by construction: a pointer reaching a real
number is never refused. Wired into all **four** generic value-edit surfaces —
`/field/edit`, `/field/edit-batch` and the two legacy inspector routes — because
the older audits describe a side door around a shared rule as exactly this class
of hole.

**Honesty in the tooltip.** `— pointer alias (edits the resolved target)` is
false on these cells, so a reference now says it is a reference, and a dangling
one says it resolves to nothing (docs/114's vocabulary, which the grid was not
applying).

## Not changed, deliberately

`LO_frequency` also holds an unresolvable `#./upconverter_frequency`, and the
grid renders it read-only as *"computed at runtime"*. That is **accurate** —
quam derives it from the port at config-generation time — so it stays. Checking
before "fixing" is the difference between the two.

## Result on the real chip

Falsely-"not set" cells: **410 → 0**. The 279 that still say "not set" are
genuinely absent (`T1`, `T2echo`, `T2ramsey`, `chi` — the not-yet-calibrated
fields of an early bring-up chip), which is the correct answer.

Pinned by `tests/test_pointer_cells.py`.

---

## The audit's correction (same day)

Three parallel audits — speed, red-team, customer-roles — ran over the whole
campaign. Two of their findings were in THIS change, and both were the same
mistake: a verdict that was easy to compute standing in for the one that is
true.

### `dangling` must mean the resolution FAILED

`-x90_DragCosine.digital_marker = "#../x180_DragCosine/digital_marker"`
resolves **perfectly** — to a target that holds `null`. The first cut derived
`dangling` from *"no container behind it"*, so it badged that cell **"resolves
to NOTHING (dangling) — type a pointer to re-point it"** while
`resolve_edit_path` still ran value-mode underneath. Typing `ON` was accepted,
returned 200, and wrote to the **shared x180 pulse** — a path the user never
named, feeding six aliases. The screen and the behaviour said opposite things,
which is worse than the blank cell this doc set out to fix. 100 cells.

A pointer that reaches a scalar — `null` included — is in value-mode and keeps
its pre-docs/121 rendering exactly. `missing` is now derived from *what the
cell is showing*, not from whether the alias holds bytes.

### A `#./` self-ref is `runtime`, and SM already knew that

`#./upconverter_frequency` and `#./inferred_intermediate_frequency` do not
resolve statically because the **component computes them**. `qubit_columns`
has classified that shape as `runtime` since it was written — *"#./ self-ref →
runtime … editing breaks the link"* — which is why the DERIVED `LO_frequency`
column read "computed at runtime" while the CURATED sibling read "dangling".
One shape, two verdicts, one of them false. `_build_bulk_cell` now reuses the
one rule.

### On the real chip, after

| badge | cells |
|---|---|
| dangling (false) | **200 → 0** |
| computed at runtime (honest) | 20 |
| reference to a dict | 90 |
| "not set" | 379 — genuinely absent, plus the null-valued pointers back in value-mode |

### And the door this opened

`resolve_edit_path` no longer follows a container pointer, so the write lands
on the pointer cell — where the type judge used to refuse it. The four web
routes gained `pointer_cell_refusal` in the same change; **`cli.py` did not**,
and it is the surface with no working copy. `cli set qubit_pairs.q1-2.
qubit_control q3 --save` wrote `"q3"` over `#/qubits/q1` in the **live**
`state.json` and exited 0. Fixed by the three lines that make the comment above
it ("the same hardening as /field/edit — they MUST NOT diverge") true.
