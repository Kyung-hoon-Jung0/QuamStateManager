# docs/179 — The unseen-edit gate was off on the button people actually press

2026-09-11. Found by the write-path stress round, lane 2 (`/state/apply-to-live`
— the one door), driving **two real browser tabs** against a writable copy of
the PJ 20Q chip. 93 checks, zero console errors, nothing wrote without a press —
and this.

## The gate, and why it was off

docs/120 item 22 built the gate and made it explicit that an **absent**
parameter means "no opinion":

> Absent parameter ⇒ None ⇒ byte-identical to before, so no caller that has not
> opted in can be refused.

That is the right default. Its consequence is that **a door which never opts in
has no gate at all** — and nothing was checking which doors opted in. Two of the
three UI buttons that POST `/state/apply-to-live` declared nothing:

- the tray's **↑ Apply to live chip** (the `working_dirty` branch)
- both **Keep mine — overwrite live** buttons in the conflict tray

Reproduced twice, once with two real tabs:

1. `/bulk`, type into a cell, blur — tray = 1.
2. Press **Save to working state** — tray goes to `data-change-count="0"
   data-working-dirty="1"`, which is the branch that renders the undeclared
   button.
3. In a second window, edit a different qubit. One server context, one change
   log: the server holds 1. Window A's tray still says 0 and its grid still
   shows the old value.
4. In window A press **↑ Apply to live chip**. The confirm reads "Apply the
   working state to the live chip's state.json / wiring.json now?" and names
   nothing about the other window. **Both edits land on state.json.**

## The second half: a count is not a change set

The gate compared counts, and the lane found the sequence that beats that with
no double-click at all:

```
A stages alpha     log = 1; A's tray shows 1 — that tray is A's consent record
B applies          alpha written, log = 0; A's tray does not refresh (docs/120)
B stages beta      log = 1
A presses Apply    declares the 1 it still shows
                   have(1) <= seen(1)  ⇒  200, and beta is written
```

docs/120 chose counting deliberately, and its reason was good:

> a refusal that fires when the change set is identical would be noise on the
> one button that must stay trustworthy.

But the count was never what the presser saw — the **paths** were. A signature
of the ordered dot paths gets the same property *exactly* (an identical set
hashes identically, so it is still never a refusal) while telling two
same-sized, different sets apart.

## The fix

- `_change_log_sig(store)` — 12 hex chars of sha1 over the ordered dot paths.
- The tray publishes `data-change-sig`; the conflict tray and the review modal
  get `change_sig` in their render context.
- All four doors declare `seen_changes` **and** `seen_sig`:
  the tray's own button, both conflict force buttons, the review modal, and
  `doStateSync` (which already sent the count).
- The gate checks the **set** first; a `seen_sig` that matches returns None
  outright, and one that differs refuses with the paths currently pending. A
  caller that sends only a count is judged exactly as before, and a caller that
  sends neither is still "no opinion".
- `?force=1` still does **not** wave it through. It answers the *staleness*
  question, against a different screen; one token never collapses two gates
  (docs/41). So the conflict buttons declare their own signature rather than
  being exempt.

An empty `change_sig` would render as `""` and read as "no opinion" — i.e. a
missing template variable would silently switch the fix back off. The pins
assert the published attribute is **non-empty** for exactly that reason.

## Verification

`tests/test_unseen_gate_doors.py` — 13 pins, including the lane's own sequence B
end to end through the real door, asserting the refused press **did not write**
and that the same press against a current screen does. The review-modal pin
drives `/state/review` rather than hand-building a template context: the actions
only render with `total > 0` on a `working_dirty` chip, and an assertion against
an empty review would have passed for the wrong reason.


## Mutation sweep: 12 of 13, and the thirteenth is a measured no-op

The sweep was left unfinished at 10/13 — one anchor that never matched, and two
GREENs I had reasoned were harmless without measuring either. Both readings were
wrong in opposite directions.

**#4 "the primary force button declares nothing" — the anchor was mine, the gap
was real.** The mutation never applied (an `hx-confirm` line sits between the
`hx-vals` and the text I anchored on), so it proved nothing. With a correct
anchor it went GREEN — and that pin was vacuous:

```jinja
{% if _staged %}   ... force button A ...
{% else %}         ... force button B ...
```

Two force buttons in **mutually exclusive branches**, and the pin rendered one
(`staged_conflict=False`) then asserted `html.count("seen_sig") == n` where `n`
came from *that same render*. An assertion about itself, blind to the other
branch by construction. Deleting button A's whole declaration — an ungated live
door, the exact defect this round exists to fix — left every pin green. The pin
renders **both** branches now and asserts per branch, including that the
signature each declares is this screen's own.

**#8 "a matching signature still falls through to the count rule" — a real gap.**
`if seen_sig: return None` is what makes the signature *authoritative* rather
than advisory, and the symmetric half of the back-compat rule was pinned only
one way round: there was a pin for a **count-only** caller and none for a
**signature-only** one. With no count, `seen` is `None` and the count rule below
evaluates `have <= None` — a `TypeError`, i.e. a 500 on a live-write door, not a
verdict. Pinned now, both the matching and the mismatching case.

**#11 "declaring nothing starts being refused" — genuinely nothing.** The guard

```python
if (seen_raw is None or seen_raw == "") and not seen_sig:
    return None
```

is a fast-path duplicate of the one twelve lines below it (`if seen is None and
not seen_sig: return None`), and nothing between them has an effect a caller can
observe. **Measured rather than argued**: the real gate driven over all 120
input shapes — `seen_changes` ∈ {absent, "", "0", "1", "2", "abc", "-1", " 1 "}
× `seen_sig` ∈ {absent, "", "   ", wrong, right} × `ack_unseen` ∈ {absent, "1",
"0"} — with the guard and without it, comparing verdicts one by one, including
raised exceptions as verdicts. **0 of 120 differ.** A pin cannot fail on a
mutation that does not mutate (docs/141 §4ad), so the guard stays as the
readable early-out it is, and this is the record that it was checked.

Final: **12 of 13 caught**, the thirteenth measured inert.
