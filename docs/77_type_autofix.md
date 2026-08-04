# 77 — One-click repair for numbers stored as text

Status: **shipped**, 2026-08-04. Branch `feat/type-autofix`.
Builds on r14 (docs/56 amendment), which made the anomaly *visible*.

## The ask

> int, real value들이 string으로 바뀌면 SM이 현재 잘 catch해서 알려주거든? 근데
> 이거 auto-correction 버튼이 있으면 좋겠어 … 왜 유저가 굳이 enter 치고 값
> 수정하고 해야해? 버튼 누르면 SM이 추천값과 type 보여주고 사용자는 확인하고
> okay 누르면 되는거니까.

SM detected the anomaly and told the user about it — then left them to walk to
every field and retype the value. On a chip where an external regeneration
string-ified 25 values, that is 25 round trips.

## The flow

1. The stored-as-text banner now leads with **“Fix N values…”** (the Diagnostics
   row for `value_type_strnum` carries the same button).
2. `GET /type-fix/plan` renders the **proposal**: one row per convertible
   field — the dot-path, what is stored now (quoted, as text), what it would
   become, and the resulting **type** (`int` / `real`) — each with a checkbox,
   all ticked.
3. Under it, a collapsed section lists what SM **refuses** to convert, each
   with a reason.
4. `POST /type-fix/apply` converts the confirmed rows in **one change group**
   into the **working copy**. The tray fills, one Ctrl+Z undoes the whole
   repair, and the live chip is untouched until the usual Save / Apply.

## What SM will and will not convert

A value becomes a candidate only when the text is an unambiguous plain number
**and** nothing says the field is genuinely textual. The proposal can only ever
change the stored **type** — never the number.

| refused | why |
|---|---|
| `id`, `__class__`, `active_*` membership | identity / read-only (`edit_policy.editability_reason`) |
| env schema types it `str` | the environment says this field IS text — converting would fight `Quam.load` |
| `"02"`, `"007"` | leading zero: humans write labels that way, not numbers |
| `"4,8"`, `"1_000"` | a separator — could be a grid location or a grouped number |
| `"joint"`, `"#/qubits/q1/f_01"` | not a number / a pointer (never scanned) |

Everything refused is **listed with its reason**. A repair that silently
skipped half the anomalies would be worse than no repair.

## Why an assignment is persisted

`modifier._type_coerce` faithfully preserves the OLD value's type, so writing
`0.13` into a leaf that currently holds `"0.13"` would just store `"0.13"`
again. The write only sticks when an **enforced** expectation exists, so for
every row that has none the apply step first persists a user type assignment
(`instance/type_assignments/<chip>.json`, `override_env: False` so a later env
schema still wins). Fields the env already types numeric need no assignment —
the plan marks those `needs_assignment: false` and simply writes.

That is also why the fix *lasts*: a later ordinary edit of the same field keeps
the number instead of drifting back to text.

## Safety

* **Re-validated at apply time.** The plan carries a signature over every
  `(path, stored text)` pair; the apply step rebuilds the plan and refuses
  (409 `stale_plan`) if it changed — a fix computed against one chip state can
  never be applied to another. Same doctrine as the diagnostics one-click fix.
* **Nothing outside the plan can be forced.** A posted path that is not a
  current plan row is refused, so `qubits.q1.id` stays `"1"` even if someone
  hand-crafts the request.
* **All-or-nothing.** A write failure rolls back every conversion in the batch
  and says so.
* **Working copy only.** Same review → Save → Apply path as any other edit.

## A bug this found

Browser-testing the fix on a string-ified chip showed the **Qubits list
itself returned 500**: `"%.4f"|format("0.042")` raises, and so does
`"0.991" >= 0.99`. One text value broke the very page a user needs in order to
reach the repair — the same defect class as the r16 `/pairs` fix. `_qubits.html`
and `_resonators.html` now gate on `is number` and render the value honestly
(quoted, muted, "Stored as text — see Diagnostics"), like the bulk grid and
All-values already did. Pinned for `/qubits`, `/resonators`, `/pairs`, `/flux`,
`/couplers` and `/diagnostics`.

## Verification

Real browser (Edge + Playwright) on a copy of the customer 17Q chip with 25
values string-ified: the banner offers **Fix 25 values…**; the preview lists
25 rows with `"5000" → 5000 (int)` shape; unticking a row updates the button
count; confirming stages **25** changes with a toast, empties the anomaly set,
and **one Ctrl+Z reverts all 25**. Plus `tests/test_type_autofix.py` (35 tests)
covering the proposal rules, every refusal reason, the apply contract, the
stale-plan and forced-path refusals, undo grouping, assignment durability, and
the string-ified-chip navigability regression.
