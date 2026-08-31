# docs/145 — Json Tree inline edit: the literal, and the overlap (2026-08-31, customer)

Two items from a screenshot of the tree editor open on a string value.

## ① The overlap (bug)

While the inline editor is open, the expected-type chip (`str · inferred`,
fetched async from `/field/peek`) painted STRAIGHT UNDER the hover row
actions and the ? help button. Reproduced numerically in CDP: the chip
occupied x 695–792 while the value box ended at x 690 — the chip was
OVERFLOWING the value box (whose flex width had been laid out before the
chip arrived) — and the ⧉/⚙/✕ buttons sat at 706–782 with the ? at
788–810, exactly the collision in the customer's screenshot.

Fix, two halves in `style.css`: `.tree-val-editing` is now `inline-flex`
(the async chip WIDENS the box instead of overflowing it), and
`.tree-row:has(.tree-val-editing)` hides `.tree-row-actions` + the `?` help
for the editing row — mid-edit those buttons are dead weight and were the
other half of the collision.

## ② Strings edit as their JSON literal (feature, user-specified)

The file says `"direct"`; the display shows `"direct"`; but the editor
showed `direct`. Now the editor shows the value **exactly as the JSON file
spells it**: `_makeValueEditable` fills the input with
`JSON.stringify(value)` for string-kind leaves (`tree-val-string` and
`tree-val-pointer` — pointers are file-strings too), escapes included
(`a"b` edits as `"a\"b"`). On commit, a full valid JSON string literal is
unwrapped before POSTing (`"smooth"` → `smooth`), so the server-side type
pipeline is untouched; text typed WITHOUT quotes degrades to the exact old
behavior (sent as typed, type policy decides). Numbers/booleans/null edit
exactly as before. Note: the unwrap runs on the committed text regardless of
the leaf's kind — typing `"5"` into a number field now sends `5` (the ⚙
type picker remains the way to change a field's type).

## Verification

CDP end-to-end on a live server: editor shows `"direct"`, hover
actions/help hidden while editing (no overlap boxes), committing
`"smooth"` stores `smooth` (peeked server-side) and displays `"smooth"`,
number editor unchanged. Pinned by `tests/tree_edit_literal_selfcheck.cjs`
(10 assertions: literal display, quoted-commit unwrap in the actual POSTed
body, inner-quote escaping, number untouched, bare-text passthrough, both
stylesheet rules) — mutations 3/3 red (raw editor, unwrap removed, hover
actions unhidden). A harness note: tree leaves materialize through the
search pipeline, so the fixture searches before clicking (the cap-harness
lesson, again).
