# 194 — Writing to the real chip: Json Tree CRUD, State History, and one late refusal

Date: 2026-09-17. The round after docs/193, under a changed rule: the customer
chip `260907_KRS_5Q` (`state.json` + `wiring.json`) became **writable** on the
user's instruction — *"마음껏 수정하고/지우고/갱신하고 다 해봐. 반드시 해봐.
너는 user야. 그것도 '가능한 모든 기능들을' 전부 써보고 싶은 user라고."*

So every surface below was pressed against the real chip with real mouse and
keyboard over CDP, and every write was read back off disk. A pristine copy sits
in the scratchpad (`PRISTINE_KRS_5Q/`) and the chip was diffed against it after
each phase; it ends this round at **0 differences**.

---

## 1. Json Tree CRUD — ＋ / ⚙ / ✕ end to end

| step | what happened | read back from `state.json` |
|---|---|---|
| ＋ Add a key under `q1` | typed `stress_note`, tabbed to the type select, tabbed to the value box, typed a value, pressed **Add** | absent until Apply, then **present** with the typed value |
| ⚙ Expected type | panel reported `expected: str · inferred — str (from value)`, picked `str`, **Assign** | `inst_kriss/type_assignments/quam_states-622b9527.json` gained `qubits.q1.stress_note → str` |
| ✕ Delete | confirm read `delete stress_note (1 leaves, 0 pointer refs)?` | key gone, `q1` back to 22 keys |

Zero console complaints across the whole lifecycle.

**A deliberate non-fix.** The delete leaves the type assignment behind. That
looks like a leak until you remember delete is undoable through the tray —
clearing the declaration on delete would restore the key without its type on
the undo. The orphan is the safer side, so it stays.

## 2. State History — both modes, on the real chip

`View changes vs current` on the 2026-09-07 baseline reported 29,061 changes
(+28,724 / −55 / ~282) and rendered the per-path table. `Pin` flipped the badge
and reached `meta.json` on disk.

**Mode 2 `Restore to live` was executed for real**, twice, and the reversibility
claim is the thing that was actually under test:

1. restored the chip to the 2026-09-07 baseline → the live `state.json` became
   **28,941 leaves** different from pristine;
2. the pre-restore backup it took (`20260917_083519_9083`) proved
   **leaf-identical to pristine**;
3. restoring that backup through the same button brought the chip back to
   **0 differences**.

So "the prior state was snapshotted first, so this is reversible" is true on a
1.6 MB real chip, not just in the message.

## 3. The defect: a press that could not see the refusal

Clicking the value of `active_qubit_names.0` in the Json Tree **opened an edit
box**, accepted typing, and only on Enter did `/field/edit` answer:

```
✗ chip-membership array — edit via the chip add/remove controls, not here
```

That contradicts the policy's own stated intent, recorded beside the vocabulary
in `leaf_classify.py` (user decision, 2026-06-19):

> Chip-membership arrays — read-only with a warning badge […] **The most
> dangerous leaves to fat-finger, so they are visible (counted) but never
> editable here.**

The All-values tab honours it with a badge. The tree did not — because the tree
builds every row in the browser from raw JSON and had no way to ask. Same for
identity keys (`__class__`, `id`). This is docs/120's rule: *a press means what
the presser could see.*

### The fix, and the trap avoided

The tempting fix is three strings in `app.js`. That would be a **second spelling
of a vocabulary `leaf_classify.py` already owns** — the recurring trap in this
codebase. Instead the server publishes it:

- `leaf_classify.readonly_policy()` emits the sets **and** the two reason
  strings; `MEMBERSHIP_REASON` / `SKIP_REASON` are now defined there and
  `edit_policy.editability_reason` + `routes._crud_policy_reason` consume them
  (the same sentence had been duplicated in both files);
- `/explorer` ships it as `window._treeReadOnly`, beside the existing
  `window._treePortOwners`;
- `app.js` gains `_policyReadOnly(path)`, applied in two places: the value cell
  takes a read-only branch (dotted underline, the reason in the `title`, click
  copies) and the hover action group withholds ＋/⚙/✕.

The action group already had a literal `__class__`/`id` check; it stays as the
**floor** for trees rendered without the payload (the selfcheck harnesses pass
`crud: true` and no global), so the change can only refuse more, never less.

## 4. What the mutation sweep changed about this write-up

The first version of the action-group pin used a membership **array** — the
real shape — and it stayed **GREEN with the fix reverted**: for a list, the
pre-existing `inList`/`isDict`/`topLevel` rules already suppress every action,
so the pin asserted nothing (docs/141 §4af — a GREEN means the fixture cannot
reach that state).

Rather than assume the branch was dead, a reachability probe rendered all five
shapes a membership top could take. The gate **does** fire, on a **dict-shaped**
membership top and anything under it: without it, `active_qubit_names.q1` is
offered ⚙ and ✕ that `/field/delete` then refuses. So the code is live and the
*pin* was wrong; C10 was re-pinned on the malformed shape, with the reason
written into the test.

Final sweep, all RED:

| mutation | pin that caught it |
|---|---|
| value-cell gate never taken | C7 marked read-only |
| action-group gate dropped | C10 no ＋ on a membership top |
| `_policyReadOnly` always editable | C7 marked read-only |
| `membership_reason` ignored | C7 title carries the reason |
| `skip_leaves` ignored | C8 identity key marked read-only |
| payload drops a membership top | `test_the_payload_carries_the_real_sets` |
| payload invents a skip leaf | same |
| payload reason drifts from the door's | `test_the_reasons_are_the_doors_own_words` |
| payload over-refuses (`qubits`) | `test_a_path_the_doors_allow_is_not_in_the_payload` |
| door stops speaking the shared reason | same |

## 5. Verified in real Chrome

```
policy reached the page: {"membership_tops":["active_qubit_names", …],
                          "skip_leaves":["__class__","id"], …}
active_qubit_names.0 : ro=true  cursor=copy
                       title="chip-membership array — edit via the chip
                              add/remove controls, not here — click to copy"
                       editor: none (correct)   copied: "Copied: q1"
                       posted: ["/scheduler/status"]      ← nothing to /field/edit
__class__            : ro=true  title="identity / type key — read-only — click to copy"
extras.…_pre_settle_ns : ro=false → opened, committed (tray 1), Ctrl+Z reverted (tray 0)
```

Zero console complaints. Chip left at 0 differences from pristine.

---

## Pins

- `tests/explorer_crud_selfcheck.cjs` — C7–C10 (19 new assertions)
- `tests/test_leaf_classify.py` — `TestTheClientGetsTheSameVocabulary`

## Measurement errors this round (running tally: 10)

All the same root cause — **probing before reading the markup**:

- `tree-crud-key` read as an **id**; they are **classes**, built by `_openAddKey`
  into a `.tree-crud-panel` inserted after the row. There is no `.tree-add-form`.
- the type assignment was looked for in the repo's `instance/`; the rig's
  instance is `inst_kriss/`. Reported "(no folder)" for a file that existed.
- the dialog/toast recorders were installed once and then survived two
  navigations in name only — `window.__toast` read back `null`, which was read
  as "no toast fired" rather than "the hook is gone".

Each was corrected in place rather than reported as a defect.
