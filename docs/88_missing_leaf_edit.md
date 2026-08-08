# 88 — a field the entity has not got yet

*Status: shipped 2026-08-09. Branch `feat/always-ask-on-drift`.
Amends docs/56 (typed editing).*

## The report

> 지금 state파일에는 lo_mode라는 field가 있어. 이는 string으로 저장되는 값이거든?
> … "alway_on"으로 입력하든, always_on으로 입력하든
> `Parent at 'qubits.qC4.resonator.opx_input.lo_mode' is str, not dict or list`
> 이런식의 에러메세지를 주면서 에러가 나고 입력이 안되고 있어.

Both spellings failing is the tell: this was never about the value.

## The chain

Reproduced verbatim on a real chip (KRISS_CR):

1. `qubits.<q>.resonator.opx_input` is a **pointer** —
   `"#/wiring/qubits/qA1/rr/opx_input"` → `ports.mw_inputs.con1.3.1`.
2. That port dict does **not carry `lo_mode`**, while sibling ports do — which
   is precisely why the grid derives the column at all (a derived column exists
   when *any* entity has the leaf).
3. `resolve_field_target` dead-ends on the final segment and degrades
   `resolved_path` to the deepest real node — the **parent**, leaf dropped.
4. `edit_policy.resolve_edit_path` saw `resolvable=False` and fell back to the
   **raw** path.
5. `modifier.set_value` walked that path, reached the pointer STRING at
   `opx_input`, and reported it as a structural error.

So the message was true and useless: it described an internal walk, not
anything the user could act on, and the cell was permanently uneditable.

## The fix, in two halves

**The resolver must never hand the modifier a path whose parent is a pointer.**
`resolve_missing_leaf_path` asks one question — does everything up to the last
segment resolve to a real dict, with only the leaf missing? — and returns
`<resolved parent>.<leaf>`. A list parent returns `None`: appending to a list is
not "filling in a missing field" and must never be inferred from a cell edit.
This alone turns the message into `Leaf key 'lo_mode' not found at
'ports.mw_inputs.con1.2.1.lo_mode'`, which names the real target.

**Creation stays declared, never inferred.** The first attempt made the edit
choke points create any absent leaf — and a test caught it:

> *"the flag gates creation, so a generic bulk/plot edit can't silently create a
> mistyped path"* (`test_accept_added_leaf_without_flag_fails`)

That invariant is worth more than the convenience. So the existing per-update
`create` flag is the gate, and the two grids now *declare* the case they know
about: the server marks a cell it rendered as **not set** with `data-missing`,
and the grids turn that mark into `create: true`. The surface that knows the
column is legitimate is the one that says so; a mistyped path still has nobody
to vouch for it.

The batch's pre-existing create fallback also had to be corrected to create at
the **resolved** target rather than the posted alias — creating at
`qubits.q.resonator.opx_input.lo_mode` could only ever have failed, which is why
the flag alone would not have rescued this case.

Creating remains type-safe for free: `create_subtree` runs the same
`check_subtree` policy, so with an env schema loaded a field the class does not
declare is refused there (an invented key is a `Quam.load()` crash, docs/56).

## Verified

Real chip, the reported shape:

```
resolve: resolvable=False  resolved_path=ports.mw_inputs.con1.1.1
/field/edit                  -> 400  "not found at 'ports...lo_mode'"   (names the target)
grid commit create=False     -> 400  applied=False                       (invariant held)
grid commit create=True      -> 200  stored: 'always_on'                 (a str, verbatim)
```

## Pins

`tests/test_missing_leaf_edit.py` (14) — the resolver's three cases; the string
landing verbatim for both spellings; one undoable change; a missing PARENT still
refusing; the same bug class without a pointer (`T1` on a qubit whose sibling
has it); and a whole class, `TestCreationStaysDeclared`, guarding the invariant
the first attempt traded away — including that the error is no longer phrased in
terms of walking into a str.
