# docs/181 — One chip object, several names; Live Edit's search follows

2026-09-11, customer, on-site:

> 제발 동일한 컨택스트의 키워드는 live edit에서 같이 검색되게 해주세요!! 예를
> 들어, ro, readout 만 입력해도 resonator 도 당연히 함께 나와야하는데, 지금은
> resonator를 입력하면 readout이 검색 안되고 그 vice versa 임.

## The chip is the evidence

On their 5Q chip, the qubit's readout object is literally named `resonator`, its
pulses are named `readout` and `readout_GEF`, and SM's own metric labels read
**"Readout frequency"** over the blurb *"Resonator frequency used to read the
qubit out."* (`chip_health.py`). One object, three spellings — and `resonator`
and `readout` share **no substring**, so no substring search can cross between
them. `ro` reaches neither.

## Two halves, and only one of them is a dictionary

**① The template never entered the haystack.** `_colHay` reads
label + key + section + search. The curated column `Readout freq` addresses
`qubits.{name}.resonator.f_01` — the chip's own word for it is right there in
the path, and the search could not see it. Adding the template's words fixes
half the report with no vocabulary at all: it is the chip telling us what it
calls things. `Depletion time` is the pure case — label, key and section contain
no synonym; the *only* place `resonator` appears for that column is the path it
addresses.

**② `core/search_synonyms.py`** is the curated dictionary for names that share
no substring with each other:

| group | why |
|---|---|
| `readout` `resonator` `ro` `rr` | the reported one — the object, what it does, and how the labels abbreviate it (`RO amp`, `f_ro`) |
| `xy` `drive` | `qubit.xy` **is** the drive line; quam names the channel, people say drive |
| `z` `flux` `bias` | `qubit.z` **is** the flux line; on a QDAC chip the DC bias sits elsewhere (docs/136), so a user looking for "flux" there is looking for the bias line |
| `coupler` `cz` | a CZ is played on the tunable coupler, and the wizard, the pair grid and the node names use both words for one surface |

### Whole words, never substrings

Membership is decided on **tokens**. `ro` occurs inside `control`, `crosstalk`,
`cross_resonance` and `phase_shift_control` on a real chip, so a substring test
would quietly pull every control column into the readout group — the search
would get *vaguer*, which is the opposite of the request. Tokenising also keeps
`cross_resonance` (a two-qubit drive) out of the resonator group, where a prefix
test would not.

### What is deliberately NOT in it

`amp`/`amplitude` and `freq`/`frequency` are **not** groups: one is a prefix of
the other, so ordinary substring matching already finds them and an entry would
be pure noise in the haystack. The groups above are exactly the ones whose
members share no substring — which is what makes them unreachable today.

This is a small, explainable dictionary, not a fuzzy matcher. docs/175 drew that
line for the typeahead (*a suggestion may be approximate; a query must not be*)
and it holds here: every entry is checkable against a real `state.json`.

## Where it is applied

Both additions land in the column's existing `search` field — the haystack
`_colHay` already reads — via one helper, `routes._search_text`. So both grids,
the cold-column value map (docs/141 §4n/§4ad) and the Properties menu get it
with **no client change** and no JS↔PY parity to maintain.

## Measured on the customer's own chip

Through SM's own column builders, against the real 5Q working copy (237
columns):

```
ro          before  28   after  51   (+23)
readout     before  16   after  37   (+21)
resonator   before  21   after  37   (+16)
drive       before   3   after  65   (+62)
flux        before 111   after 124   (+13)
```

and the property that is actually the report:

```
readout-only after fix:   []
resonator-only after fix: []
```

The two searches now return the **identical set**.

## Verification

`tests/test_search_synonyms.py` — 16 pins: the dictionary (both directions, the
`control`/`crosstalk` false positives, `cross_resonance`, the deliberately
absent prefix pairs, and a lint that every entry is a single lowercase word so a
dead entry cannot hide), plus the grid's real payload through `/bulk` on a chip
shaped like the customer's — `readout` and `resonator` returning the same set,
stated as a property rather than a count.
