# 197 — The diff row now reads left to right

Date: 2026-09-17. Customer, on-site, three items in one message:

> 1) 왼쪽에서 2개 이상 실험을 선택했을때, 일반적으로 old가 왼쪽부터, 그리고 new가
>    오른쪽에 정렬되어서 -> 이런식으로 화살표가 old에서 new로 업데이트된 것처럼
>    표현이 되어야 하는데 지금은 그렇게 안나와서 헷갈려 하십니다.
> 2) 특히, default로 보여주는게 table이 아니라 json tree view에서 보여줘서 분간하기
>    어렵다고 합니다. 기본을 table로 합시다.
> 3) 또한, Diff에서 기존에 선택된거 column에 휴지통 모양 두어서 그거 클릭하면 그
>    column은 삭제되게 합시다.

---

## 1. The order was right; nothing said so

This is worth separating carefully, because the first reading — "the sort is
wrong" — is false and fixing a sort that is already correct would have made
things worse.

`routes._oldest_first` (`:20129`, keyed by `_run_age_key` at `:17928`) sorts
the ticked runs oldest-first before assigning slot letters, so the oldest
already lands in `a` = leftmost. That is customer-directed from 2026-09-11 and
pinned in `tests/test_customer_scroll_and_order.py`. Driving the real archive in
real Chrome confirms it: ticking three runs gives **A #39 16:39:39 · B #40
16:42:54 · C #41 16:43:06**.

What the row actually looked like was:

```
A [#39]   vs   B [#40]     C [#41]        D [— optional —]
```

A **"vs"** between the first two and *nothing at all* after the third. So the
one thing the row exists to express — this became that, over time — was stated
between A and B and nowhere else, and "vs" states a contest rather than a
direction anyway. A chip comparison is not two runs against each other; it is
one run becoming the next.

Now every adjacent **filled** pair carries an arrow:

```
A [#39]   →   B [#40]   →   C [#41]        D [— optional —]
```

Identity blue, matching the 2-way direction badge (docs/147) that means the same
thing lower on the same page, with `title="older on the left, newer on the
right"`. The arrow is gated on the slot being filled — drawing one before the
empty optional picker was the first cut, and the sweep caught it.

## 2. The table is the default

`routes.py:18431` decided `view = "tree"` when no `view` was given. The tree is
the better surface for *where does this key live*; the question a comparison
opens with is *what is different*, which is a list of rows. It is now `"list"`.

Three sources and up were already forced to `panes` (docs/141 §4z) — also a
table — so this makes the 2-source case agree with the 3-source one instead of
being the odd one out. An explicit `?view=tree` still wins; nothing was removed.

One existing test had been pinning the old default *implicitly* — it asked for
the tree payload with no `view` parameter. It now says `&view=tree` and pins the
tree's own pruning, with the new default pinned beside it.

## 3. A column can be dropped from its own header

A `🗑` in each filled slot's label, quiet until the row is hovered (it sits
beside a select people aim at all day, and an always-lit delete there is an
accident waiting to happen), `aria-label="Remove column A"`.

**It does not build a URL.** It clears that slot's `<select>` and fires the
form's own `change`, which the picker form already listens for — so the tab,
the view, the baseline and every other slot ride along untouched, and the
server compacts the survivors (`routes.py:18408-18419`) so a dropped middle
column leaves no gap. Hand-building the URL would mean re-spelling five slot
parameters, which is exactly how docs/141 §4ac's Show-more button came to drop
every slot past `a`.

Two details the seam forces:

- **the baseline follows the drop.** `base` indexes the *compacted* list, so
  removing a column to its left would silently re-base the comparison onto a
  different source. The handler steps it back by one first.
- **offered only while more than two sources are loaded.** Below two there is
  nothing to compare, so removing the second is not an action the workbench
  should offer; the select is still there to change.

## 4. Measured in real Chrome, on the customer's own archive

```
three ticked  -> slots a=#39 b=#40 c=#41 d=(optional)
                 arrows 2   drops 3        <- none before the empty slot
state tab     -> view=panes, table=true, tree=false
press B's 🗑  -> slots a=#39 b=#41 c=(optional third)
                 arrows 1   drops 0        <- compacted, no gap, controls retire at two
two ticked    -> view=list                 <- the table, by default
                 "A #40 → B #41 · 0 changed · 1,652 identical"
```

Zero console complaints. (The two-source run shows no table *element* because
those two runs are identical and the page correctly says so in words instead.)

## Pins

`tests/test_diff_three_way.py` — `TestTheRowReadsAsADirection` (4),
`TestAColumnCanBeDropped` (6); `tests/test_diff_workbench.py` — the new default
and that an explicit view still wins. **Mutation sweep 10/10 red.**

Two pins were wrong on the first run and both failures were the same shape: the
class/id name I asserted on also occurs inside a script that *looks it up*
(`diff-tree-payload` inside `getElementById`, `diff-side-drop` inside the
handler's own selector). Both now pin the element — `id="…"`, `class="…"` —
rather than the bare substring.
