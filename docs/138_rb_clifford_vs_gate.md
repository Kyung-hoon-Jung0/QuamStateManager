# 138 — a 2Q RB number is per-Clifford or per-gate, and SM was showing the wrong one

Reported from the real chip: the topology's headline said the q19-20 CZ was
**97.1%**, while scrolling down to the per-pulse sections on the same page gave
**99.09%** for `cz_gaussian_bipolar` and **99.34%** for `cz_flattop`. Same pair,
same page, two different numbers, and the smaller one on top.

## What the two stored fields actually are

`state.json` keeps both under one `fidelity` dict, and they measure different
things:

```
qubit_pairs.q19-20.macros.cz_flattop.fidelity
    StandardRB           0.970608   = 1 - EPC   per CLIFFORD
    StandardRB_alpha     0.960811   = the RB decay base. Not a fidelity.
    InterleavedRB        0.993363   = 1 - EPG   per GATE
    InterleavedRB_alpha  0.951566   = the RB decay base. Not a fidelity.
```

Established three independent ways, because a labelling change is only worth
making if the labels are right:

1. **Arithmetically.** `(d-1)/d · (1-α)` with d=4 reproduces `StandardRB` to
   1e-12; `(d-1)/d · (1 - α_int/α_ref)` reproduces `InterleavedRB` the same way.
2. **From the lab's own node docstrings.** `37a` writes "fitted average
   **Clifford** fidelity"; `37b` writes "fitted **CZ-gate** fidelity".
3. **From the lab's own code.** `two_qubit_rb/fidelity.py:87` computes
   `epc = 1 - clifford_fidelity`, then
   `epg = epc / average_gates_per_clifford`, then
   `average_gate_fidelity = 1 - epg` — and the node stores only the Clifford one.

On this chip `average_gates_per_clifford = 5.370984` (from
`#2271/data.json`). A Clifford is over five two-qubit gates, so the two numbers
are nowhere near interchangeable.

## Three defects, one cause

The cause is that `_extract_pair_gate_fidelities` emitted every numeric key in
`fidelity` as an undifferentiated "fidelity row".

**① The map opened on the Clifford number.** `EDGE_METRICS` is ordered, and the
first surviving entry is the default. `cz_fidelity` came only from
`Bell_State`, which this chip does not have, so it was filtered out — and the
next entry was the Standard (Clifford) metric. Hence 97.1% on top of a page
whose own per-pulse sections said 99.3%.

**② The decay parameters were rendered as fidelities.** `StandardRB_alpha` and
`InterleavedRB_alpha` appeared as `95.62%` and `93.80%` under a heading reading
**Gate fidelity**. α is the base of the RB exponential. It is not a percentage
of anything.

**③ The edge was grey on a chip with a measured CZ fidelity.** `cz_fidelity`
consulted `Bell_State` and the CR channel and nothing else, so a pair
characterised by interleaved RB — the better measurement — coloured nothing.

## The fix

`query._RB_LEVEL` classifies each row: `gate` / `clifford` / `state` / `decay`,
and the row carries it. Then:

- **The edge takes the interleaved number** when there is no `Bell_State`
  (`fidelity_source: "interleaved_rb"`), and `best_gate` becomes the pulse that
  won it. `StandardRB` is deliberately **not** a fallback: colouring an edge
  with a per-Clifford number understates every gate on the chip by whatever
  `average_gates_per_clifford` happens to be.
- **The gate metric is offered before the Clifford metric**, so the default on
  an RB chip is 99.3, not 97.1. The Clifford number stays available, last.
- **The patches say which is which** — `2Q gate (IRB)` and
  `2Q Clifford (SRB)`, where both used to read `2Q RB`.
- **α gets its own popup section**, `RB fit (decay α)`, printed as a bare
  number.
- **`2Q Bell` is renamed `2Q gate fidelity`** — that tile reads `cz_fidelity`,
  whose source is Bell / interleaved RB / the CR channel depending on the chip.
  It was already wrong on a CR chip; this campaign would have made it wrong on
  an RB chip too.

**The stored metric choice is versioned** (`quam_topo_hero_metric_v2`). Nobody
picked the Clifford number on purpose — they got it, as a default, under a
label that did not say what it was. Leaving the old key would pin the wrong
number forever for exactly the people who never chose it. Same precedent as
docs/85's `_cols_v2` visibility flip; a choice made *after* seeing the honest
label survives from then on.

## Recovering the per-gate number the run threw away

Offered, then approved, so it is done.

The lab's node computes `average_gate_fidelity = 1 - epc/average_gates_per_clifford`
and stores only the Clifford one — but the chip records **which run** produced
it, in `fidelity["StandardRB_load_id"]`. `core/rb_gate_fidelity.py` follows that
id to the run's `data.json` and **reads** the answer the node already worked out.
It never recomputes: the divisor is measured, not assumed, and on this chip it is
**5.371**, not the 1.5 one might guess for a CZ-based Clifford.

`routes._topology_with_derived_rb` wires it into both topology entry points. Two
things it is careful about:

- `get_topology`'s result is **cached**, so the enrichment deep-copies before
  touching it, and skips the copy entirely when no Clifford row exists.
- `_rb_run_folder` does the same bare-run-id lookup `/dataset/by-run` does, but
  **without** its rescan-and-retry. That route is a user pressing a link; this
  one runs while rendering a page, and a run that is not loaded is a blank
  field, not a reason to sweep the filesystem.

Shown **beside** the Clifford value, never instead of it — `97.06% → 99.44% per
gate ÷5.37` in the pair popup, plus a `2Q gate (SRB÷)` hero patch that is only
offered when some edge actually has one.

Measured on the demo chip, and the third row is the point:

```
cz_unipolar          Clifford 0.967162  →  gate 0.993886  (÷5.371)   IRB 0.985697
cz_gaussian_bipolar  Clifford 0.970461  →  gate 0.994499  (÷5.370)   IRB 0.990935
cz_flattop           Clifford 0.970608  →  (blank)                   IRB 0.993363
```

`cz_flattop`'s run (#2655) is not on this machine, so there is no derived number
and none is shown. The other two give a per-gate figure that agrees with the
independent interleaved measurement to 0.4-0.8% — a cross-check the page could
not previously offer, because it had only one of the two.

*A correction worth recording: I first reported that this divisor was "in
neither state.json nor the saved run". That was wrong — I had checked one
unrelated run (`#2259`) and generalised. It is in `#2271/data.json`, and it is
5.371, not the 1.5 one might guess for a CZ-based Clifford. Which is the reason
for not guessing.*

## Measured

Before, on `#3424`'s q19-20:

```
hero "best"           97.1%    (max StandardRB)
per-pulse sections    99.09% / 99.34%   (InterleavedRB)
edge                  grey
popup                 four rows, all percentages, one heading
```

After:

```
edge         0.993363   source=interleaved_rb   best_gate=cz_flattop
hero best    99.3%      per pulse: flattop 99.34 / gaussian_bipolar 99.09 / unipolar 98.57
Clifford     still one click away, and says "per Clifford"
α            its own section, bare numbers
```

## What is pinned

`tests/test_rb_levels.py` (37) — the arithmetic that proves the two are
different quantities, the classification including `IRB` and the unknown-metric
case, the edge precedence (Bell_State still wins; interleaved colours it;
Standard never does), and the guards a dangling pointer and an unphysical value
must still hit (**5/5 mutations**); plus the derivation: both pair spellings, the
epg-only fallback for an older run, a broken fit refused, absent/unreadable/None
folders, only Clifford rows enriched, the Clifford value left untouched, one read
per load_id on a 30-pair chip, and a resolver that raises (**7/7 mutations**).
One test runs against the customer's real run and checks the read number really
is `epc ÷ 5.371`.

And the class those unit tests could NOT catch, caught live and then pinned:
`routes._rb_run_folder` used `getattr(run, "folder_path", None)` — but
`DatasetStore.get_run` returns a **dict** with a stringified `folder_path`, not
the `RunInfo` object, so every lookup yielded None and every derived value came
back blank while the whole suite stayed green (the unit tests hand
`derive_for_edges` their own resolver). `TestTheRouteHelperThatResolvesTheRun`
now drives the real helper with the dict shape `get_run` actually returns, plus
the copy-before-enrich guarantee on the cached topology (**3/3 mutations**).

`tests/chip_status_hero_selfcheck.cjs` — the real hero under jsdom, with the
three-pulse fixture carrying both RB kinds: the gate metric is offered before
the Clifford one, "best" prints the best interleaved value, clicking a pulse
prints that pulse's interleaved value (the number the section below it shows),
the Clifford patch still shows the Clifford value and names itself, and the old
un-versioned key is no longer written. **3/3 mutations caught.**
