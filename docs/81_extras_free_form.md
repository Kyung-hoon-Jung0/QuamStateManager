# 81 — `extras` is the user's own corner of the state

*Status: shipped 2026-08-06. Branch `feat/multi-instance-safety`.
Amends docs/56 (typed editing) and docs/77 (one-click repair).*

## The report

A CZ branch label on a real chip, stored as

```json
"qubit_pairs": { "q0-q1": { "extras": { "cz_branch": "02" } } }
```

Three things went wrong with it, and they turned out to be one thing.

**It was reported as a mistake.** The stored-as-text alarm flagged
`cz_branch: "02"` as a number that had been string-ified. But a branch label
*is* text, and `"02"` is exactly how a label is written. On the reporting chip
those two labels were **100 % of the alarm** — the feature was crying wolf and
nothing else.

**It could not be edited.** Changing it to `"03"` came back 409:

> `qubit_pairs.q0-q1.extras.cz_branch is stored as TEXT ("02"), not a int —
> convert the type to store the number, or keep text (wrap the value in
> quotes "…" to keep text without asking)`

The escape hatch works, but the Explorer editor opens on a bare `02` with no
quotes, so nothing on screen suggests that typing `"03"` is the way through.
From the user's side the field was simply uneditable.

**And past that, the value changed.** Typing `03` stored `"3"`. The legacy
pipeline parses the input to the number 3, then the coercer casts it back to
the field's original type — leading zero gone, on an ordinary label edit.

## The cause

`extras` has no schema and never did. QUAM does not model it; it is where a lab
puts its own keys (`extras.chip_name` and `extras.data_folder` are SM's own use
of exactly that property). So SM must not form opinions about the **type** of
what lives there — above all it must not read a numeric-looking string as a
number that got string-ified by mistake.

The detector already said so. `numeric_string_leaves`'s docstring read *"Skips
`extras` (user-declared free-form)"* — but the implementation only skipped the
**root** `extras`, and on real chips `extras` lives at
`qubit_pairs.<pair>.extras.<key>`. Documented intent, unimplemented at depth.

## The fix

One predicate, `edit_policy.is_free_form_path`, matched on a whole path
segment at any depth — so `extras_backup` is a normal key and
`a.b.extras.c` is not. Three call sites share it, which is the point: the
warning and the repair previously disagreed about what counts as text, and a
single definition is what stops that recurring.

| where | change |
|---|---|
| `diagnostics.numeric_string_leaves` | `extras` skipped at any depth — no warning, and (since it feeds the candidate list) no repair-plan row either |
| `routes._type_fix_offer` | never fires inside `extras` — ordinary label edits stop being intercepted |
| `routes._parse_for_target` | a TEXT leaf inside `extras` keeps what the user typed, verbatim |

The parse carve-out is deliberately keyed on the **current** value being text.
A lab that genuinely stores a number under `extras` keeps a number and keeps
numeric editing; only a field that is already text is treated as text. That
also keeps the change away from the empty-policy golden, which pins
byte-identical legacy `_type_coerce` behaviour for schema-free fields.

## What did NOT change

The carve-out must not blunt the feature it lives beside, so this is pinned
explicitly: a genuinely string-ified number outside `extras`
(`qubits.qA1.T1 = "1.5e-5"`) is **still** warned about, **still** offered the
409 repair, and **still** appears in the repair plan.

## Pins

`tests/test_extras_free_form.py` — the predicate (depth, segment equality),
the warning (label silent, real anomaly still reported, repair plan does not
even list extras as a refused row), and editing (no interception, leading zero
survives, bare numbers stay text, non-numeric text still works, a numeric
extras value still edits as a number).

Verified on the reporting chip: 2 warnings → 0, `03` stored as `"03"`.

---

# Band-edge headroom: SM's guideline, said as one

*Same session, same root cause in a different place: SM stating an opinion
with more force than it has evidence for.*

## The report

A lab retuned a real LO because of this:

> LO (upconverter_frequency) 7.48 GHz sits only 20 MHz from the band 2 edge
> [4.5, 7.5] GHz; band 3 ([6.5, 10.5] GHz) would place it 980 MHz from its
> nearest edge. The bands partially overlap, so this LO works in band 2;
> placing the coupled pair (this port + in1) in band 3 would leave more
> headroom from the band edge (a more comfortable LO range margin — this does
> not guarantee better signal quality). Optional, not required. Note: …

Then they asked the right question: **is that in the QM documentation?**

It is not. The official guide (`Guides/opx1000_fems.md`) states the bands, that
they "partially overlap to provide greater flexibility in frequency
allocation", the settable range per band, and one binary rule:

> Values outside the band's specified range will not meet the performance
> specification.

7.48 GHz is inside band 2, so by the official specification it is simply in
spec. A search of the whole docs repo for band-edge / edge-of-band /
centre-of-band guidance returns **nothing**. The 50 MHz margin was SM's own
number, and no QM document asks for headroom at all.

## What was wrong with how we said it

The finding already carried `advisory=True` and its own text already said
"Optional, not required". Two things drowned that out:

* **The badge counted it.** `summarize()` correctly kept advisories out of the
  `warning` tier, but the badge leads with `total` — and `total` included
  them. The number a user actually reads said "⚠ 7 issues".
* **The message buried the point.** 489 characters, four hedging clauses, with
  the word "Optional" at the very end.

## What changed

* `BAND_EDGE_MARGIN_HZ` 50 MHz → **5 MHz**. The reported LO (20 MHz of margin,
  comfortably in spec) is now silent; only an LO practically sitting on the
  boundary is mentioned.
* `summarize()` gains **`issues`** = error + warning + info. The four badge
  headlines use it, so an optional recommendation never inflates the count a
  user reads. `total` keeps its meaning for other callers, and the Diagnostics
  page's "No structural issues found" still keys on `total` — with a
  recommendation present, claiming nothing was found would be a lie.
* The message is **210 characters**: the two margins, that it is optional and
  in spec, and the coupled-mate consequence when there is one.

Deliberately NOT done: adding "this is SM's own guideline, not a QM
requirement" to the message. It is accurate but it makes a short message long
again, and a recommendation that no longer shouts does not need the disclaimer.

## Pins

`tests/test_diagnostics_tier2.py::TestBandEdge` — the reported 20 MHz LO is
silent, a 2 MHz LO still recommends, and the message stays under 260
characters with "Optional" in it. The existing coupled-mate feasibility cases
moved to 7.498 GHz so they still exercise the gate at the new threshold.
