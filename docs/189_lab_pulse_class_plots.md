# docs/189 — a pulse whose class the LAB wrote still plots

**2026-09-16, customer, on-site, CRITICAL.**

> 지금 KRISS_CZ라고 state manager 열려있는것 확인해봐. pulses 메뉴에서 snz 는
> plotting이 안돼. 왜 그래?

## 1. What was open, and what it was

`instances/31324.json` and `34604.json` — project **KRISS_CZ**, chip
`D:\work\Customer_Codes\quam_states\260907_KRS_5Q`, ports 5050 and 5052. Their
SM was not touched; everything below ran against a copy.

That chip's CZ flux pulse is:

```json
"flux_pulse_qubit": {
  "id": "cz_SNZ_flux_pulse_q1_q2",
  "amplitude": 0.3317294199169741, "flat_length": 78, "t_phi": 0,
  "b_over_a": 0.25591004288204816, "neg_offset_v": -9.546e-05, "padding": 4,
  "__class__": "quam_config.two_flux_gate.SNZTwoFluxPulse"
}
```

**`quam_config.two_flux_gate.SNZTwoFluxPulse` is a class that lab wrote.**
`core/waveform_synth.py` is an exact in-process mirror of *quam's* pulse
classes, so it answered:

```
unrecognized pulse class 'quam_config.two_flux_gate.SNZTwoFluxPulse'
```

and the page drew nothing.

It is not one pulse, and it is not only SNZ. Measured on that chip:

| class | objects | in SM's catalog |
|---|---|---|
| `quam_builder…DragCosinePulse` | 40 | yes |
| `quam.components.pulses.SquarePulse` | 19 | yes |
| **`quam_config.two_flux_gate.SNZTwoFluxPulse`** | **16** | **no** |
| `quam.components.pulses._FlatTopGaussianPulse` | 6 | yes |
| `quam.components.pulses._CosineBipolarPulse` | 6 | yes |
| `quam_builder…ErfSquarePulse` | 6 | yes |
| **`quam_config.complex_weights_pulse.ComplexWeightsReadoutPulse`** | **5** | **no** |
| **`quam_config.gef_weights_pulse.GefWeightsReadoutPulse`** | **5** | **no** |
| `quam_builder.common.pulses.GaussianFilteredSquarePulse` | 5 | yes |
| **`quam_config.two_flux_gate.GaussianNZTwoFluxPulse`** | **4** | **no** |

**30 pulse objects across four lab-owned classes.** 15 of the 50 Pulses rows
carried no sparkline either.

### A correction to my own first reading

I first reported that the detail page said nothing about why. It does — the
server has always rendered `synth_error` into `.pulse-synth-err`, and it read
`unrecognized pulse class 'quam_config.two_flux_gate.SNZTwoFluxPulse'`. My
probe searched for the words "no synthesizer" / "unsupported" and missed
"unrecognized". The real defect is not silence, it is that **the sentence had
no remedy attached and no waveform beside it.**

## 2. Why SM does not transcribe the lab's algorithm

The class is fully specified in the lab's own module — `core_samples()` builds
`[A1]*half + [B1] + [0]*t_phi + [B2] + [A2]*half`, `inferred_length` rounds
`2·padding + flat_length + 2 + t_phi` up to a multiple of 4 — so transcribing
it into `waveform_synth.py` was possible, and there is precedent
(`core/gaussian_cz.py`, docs/126).

It was rejected. A transcription is a **copy**, and it goes stale the day the
lab edits `two_flux_gate.py` — silently, because a stale copy still draws a
plausible curve. **A wrong waveform is worse than no waveform**, and this one
would be wrong exactly when the lab is changing the gate, which is the moment
they are looking at it.

## 3. What ships instead: the lab's own code drew it

SM already runs `generate_config()` in the lab's selected env and caches the
result (the Config Viewer; the Pulses page's "Verify vs config" button reads
it). That output **is** the lab's own class's waveform — it cannot disagree
with what the instrument will play.

So when the class has no in-process synthesizer, the detail render takes the
waveform from the generated config:

- `waveform_synth.synth_for_operation` stamps a **machine-readable**
  `reason: "unknown_class"` (with `qclass`) on that one payload. "this class is
  not in the catalog" and "this class is, but a parameter is wrong" have
  different remedies, and the only thing that told them apart was the wording
  of an English sentence.
- `/api/pulse/synth` forwards `reason` / `qclass` / `schema_known`.
- `_pulse_truth_lookup(store, path)` is **ONE lookup with two callers** —
  `/api/pulse/ground-truth` (which adds staleness and a synth-vs-truth
  comparison) and `_pulse_section_ctx`. Extracted rather than duplicated.
- `_pulse_section_ctx` calls it only when `reason == "unknown_class"`, and on
  success sets `plot_source="config"` and clears the error.

**The page never passes one off as the other.** The plot label becomes
*"waveform from the generated config — this pulse class is the lab's own, so
its own code drew it (2026-09-16T01:52:34+00:00)"*, and a config older than the
user's edits says so.

**The banner had to change too.** `_pulse_detail.html` said *"fields are shown
raw; the synthesized preview is unavailable"* — the second half became FALSE
the moment the waveform appeared under it, and a banner that contradicts the
plot beneath it is worse than no banner. It now reads *"…is this lab's own
pulse class, so SM cannot synthesize it and shows its fields raw. The waveform
below is the real one, taken from this chip's generated config."*

**No config yet is not a dead end.** The bar names the class and offers the one
action that fixes it — `Generate now (~10–30 s)`, the button that already
existed for Verify — and a pulse newer than the cached config offers
`Regenerate`.

**And the silent branch.** `refreshCommittedPlot` in `pulses.js` — the path that
redraws after an edit, an undo or a re-link — had an **empty failure branch**:
the server answered with a finished sentence and the client dropped it, so any
later failure left the previous curve on screen with nothing said. It now
publishes the reason and, for `unknown_class`, takes the same fallback.

## 4. Measured

On a COPY of the customer's chip, in real headless Chrome:

| | before | after |
|---|---|---|
| `cz_SNZ_flux_pulse_q1_q2` | no plot | **88 samples drawn** |
| `readout` (lab weights class) | no plot | **1,200 samples** |
| `readout_GEF` | no plot | **1,488 samples** |
| what the page says | `unrecognized pulse class …` | names the source and the date |

The drawn SNZ curve is the shape the class documents: a positive lobe, the
boundary samples, the mirrored negative lobe, centred in 88 samples.

## 5. Pins

`tests/test_pulse_unknown_class.py` (8) with a fixture chip carrying a made-up
lab class beside a class SM does know, and a hand-built config in the shape QM
emits. **Mutation sweep 12 of 12 RED.**

`tests/snz_plot_probe.cjs` drives the REAL page in REAL Chrome over CDP: only
the browser runs the render path end to end, and a jsdom harness would be
testing stubs of the two fetches that ARE the mechanism. `SHOT=<file>` writes
the screenshot the round was reviewed from.

### The sweep's own finding

The first sweep scored **11 of 12**. The miss was
*"the fallback also fires for a class SM DOES know"* — set `unknown_class = True`
unconditionally and nothing failed, because the fixture's config carried no
entry for the known pulse, so the lookup answered `not-found` and the page
looked identical either way. **The FIXTURE could not reach the state**
(docs/141 §4af). The config now carries that pulse too, under a marker
amplitude (`0.777`) the synth could never produce, so a fallback that fired
there would be visible rather than merely unobserved. Re-swept: **12 of 12**.

## 6. Open, and recorded

- The **row sparklines** are still blank for these classes (15 of 50 rows). A
  sparkline has no room for a provenance label, and filling it from the config
  would put an unlabelled lab-drawn curve in a column of SM-drawn ones. Worth
  doing, deliberately not done in a CRITICAL fix.
- A chip whose env has never generated a config still shows no waveform for
  these classes until the user presses the button once. That is the honest
  state: SM has no other source for them.

---

# Part 2 — GaussianNZ, the cost, and generating it in advance

Three follow-up questions from the customer, in their words:

> 그러면 GaussianNZ도 그려지는거니? 그리고 "랩 자신의 generate_config()을 쓴다"고
> 했는데... 이거 원리가?? SM이 느려질텐데?? […] 이거 미리할수는 없나?

## 7. Yes — and a correction to Part 1's numbers

**GaussianNZ plots: 96 samples**, verified in real Chrome. So do both readout
weight classes (1,200 and 1,488 samples).

Part 1 said "15 of 50 rows". That was **one paginated page**, not the table.
Re-measured through the index itself:

| | |
|---|---|
| pulse rows on this chip | **151** |
| do not plot in-process | **25** |
| — `SNZTwoFluxPulse` | 12 |
| — `ComplexWeightsReadoutPulse` | 5 |
| — `GefWeightsReadoutPulse` | 5 |
| — `GaussianNZTwoFluxPulse` | **3** |

(The "30 objects" in Part 1 was the raw `__class__` count in `state.json`,
which includes objects the Pulses page does not enumerate as rows. 25 is the
number that matters.)

## 8. The mechanism, and what it actually costs

`generate_config()` runs **once**, in a subprocess in the lab's own env, and
the result is **one dict cached in RAM on the store**. Nothing re-runs it per
pulse. Measured on the customer's chip:

| | |
|---|---|
| the subprocess | **13.0–13.3 s**, once per chip |
| the cached config | **0.3 MB** — 17 elements, 103 pulses, 161 waveforms |
| reading one pulse out of it | **~0 ms** |

It is read-only: `generator/run_generate_config.py` loads the machine and calls
`generate_config()`. There is no `machine.save()`; the one write it makes is
into a temp scratch dir, for the shim that drops empty root keys. **It never
writes the chip.**

### The 33 ms that was real, and is gone

The customer's instinct ("SM이 느려질텐데") was right about something, just not
the subprocess. A lab-class pulse's detail render measured **33 ms** against
**2 ms** for one SM synthesizes itself — and profiling put **all 33 ms in
`_config_stale`**, which serialises the whole chip to canonical JSON and hashes
it. That was fine while the only caller was a button press. It is not fine when
every render of every CZ flux pulse asks for it.

`_config_state_hash` is now memoized on `(store.mutation_seq,
len(store.change_log))` — the pair `_bulk_grid_key` already trusts to decide
which grid CELLS are current, so a hash can never outlive a change to the thing
it hashes. **33 ms → 4 ms.**

## 9. "미리할수는 없나?" — yes, and it does

The best moment to pay 13 s is while the user is still looking at the chip
list, not the moment they click the pulse they wanted to see. So
`_activate_quam` now starts a daemon thread that decides and, if needed,
generates:

- **Gated on the chip actually needing it.** `_chip_needs_generated_config`
  reads the pulse index's own `known` flag — the same fact the unrecognized-class
  banner is gated on — so a chip made entirely of quam's classes starts nothing
  and pays nothing. That is the difference between a warm that helps one lab
  and a warm that taxes every other user.
- **Off the request path entirely.** Even the decision runs on the thread: the
  request pays for `Thread(...).start()` and nothing else. Measured: chip open
  **1,021 ms → 759 ms–1,021 ms**, i.e. unchanged.
- **Single-flight.** Two activations in quick succession — a reload, a second
  window, the LRU handing the context back — cost ONE subprocess.
- **Never in a loop.** A failure is latched against the chip's own content
  hash, so a broken env costs one subprocess, not one per page view, and
  fixing the env re-arms it.
- **Never without an env the user picked.** SM does not choose the
  interpreter; a missing one is said, not guessed.
- **Idempotent.** A config that is provably fresh answers `already-fresh` and
  starts nothing.

Measured end to end on a real server, with **no click anywhere but opening the
chip**:

```
open the chip (request)     0.76 s
config ready                15 s later, in the background
SNZ detail render           4 ms  — plots, with no button ever pressed
```

### The bug that only a real server showed

The first cut hooked the warm onto the end of `_activate_quam` — and nothing
ever happened on a real server. `_activate_quam` **returns early for a chip
already in the LRU**, and re-opening a chip is the commonest way to reach it,
so a warm on the cold-build path alone almost never runs. Both paths call it
now, and `test_re_opening_a_cached_chip_still_warms` is that measurement.

## 10. Pins

`tests/test_pulse_unknown_class.py` grew to **16**, with
`TestTheConfigIsWarmedInAdvance` covering the gate in both directions, the
no-env refusal, the fresh-config skip, the failure latch, single-flight, the
cached activation path, and the memo's expiry. **Mutation sweep 11 of 11 RED.**

### The sweep's own second finding

The first run of this sweep reported `ANCHOR x0` for four anchors that are
demonstrably in the file, because it matched whole indented blocks and one
`\n` had been eaten passing the script through a shell. **A sweep that cannot
find the code it is mutating is scoring itself, not the product** — the same
shape as docs/141 §4ad's `tail -1`. It matches unique substrings line by line
now, and deliberately mutates BOTH sites of the failure latch (it is written in
two branches; mutating one leaves the other honest and the pin would pass for
the wrong reason).

### The suite was the second measurement

Running the pins beside `test_web.py` gave **4 failures** where the same file
alone gives the 2 that fail identically at `efef882` (measured, not inferred).
The extra two were this round's own daemon threads: the suite does not spawn
subprocesses, and a thread walking the pulse index while a test mutates the
store is interference, not coverage. `_maybe_warm_generated_config` returns
early under `TESTING` (docs/135's conda probe is the precedent) unless a test
sets `SM_CONFIG_WARM_IN_TESTS`. Back to **2 failed, 524 passed** — and the warm
is re-verified on a real, non-testing server: **18 s after opening the chip,
zero clicks**.

## 11. Still open

- The **row sparklines** remain blank for these 25 rows. Unchanged from Part 1,
  and for the same reason.
- The warm needs an env selected. On a chip whose lab env has never been
  picked, the detail page still shows the honest line and the `Generate now`
  button — which is correct: SM has no other source for these waveforms.

---

## Part 3 — the table's sparkline column (2026-09-17, docs/195 round)

**The customer reported the blank column a second time**, with the right
question attached: *"generate config가 가장 좋긴한데, 이걸 매번실행하면 느려질텐데
가능한가요?"*

Both halves have answers, and one of them was mine to fix.

**The speed half was already solved by part 2** and is worth restating, because
the customer's worry is exactly the right one: `generate_config()` does NOT run
per render. It runs once, in a background daemon thread, when a chip that
actually carries a class SM cannot draw is opened (13–18 s), and the result is
one ~0.3 MB dict in RAM. Reading one pulse out of it is a dictionary lookup.

**The blank column was mine.** Part 1 left it deliberately:

> the row sparklines are still blank for these classes, because a sparkline has
> no room for a provenance label and an unlabelled lab-drawn curve in a column
> of SM-drawn ones is the one thing this fix refuses to do.

The principle is right and I still hold it. The conclusion was wrong: a
sparkline *does* carry a `title`, and it can be *drawn differently*. Two
reports is enough evidence that a blank cell reads as a broken feature, which
is a worse lie than a labelled curve.

So the row draws it, from the same `_pulse_truth_lookup` the detail view uses,
through `_pulse_truth_spark` → the existing `sparkline_svg`. It is wrapped in
`.pulse-spark-lab`, whose polylines are **dashed** (`stroke-dasharray: 3 2`),
and whose title reads *"Waveform from the generated config — this pulse class is
the lab's own, so its own code drew it (‹when›)"*. An SM-drawn sparkline is
untouched and solid.

Measured in real Chrome on a copy of the customer's `260907_KRS_5Q`:
`cz_SNZ_flux_pulse_q1_q2` renders its 88-sample SNZ shape dashed, `readout`
(1,200) and `readout_GEF` (1,488) likewise, while `ErfSquarePulse` and
`GaussianFilteredSquarePulse` two rows above stay solid and unmarked.

Pinned by `TestTheTableRowGetsASparklineToo` (6) in
`tests/test_pulse_unknown_class.py`; **mutation sweep 7/7 red**, after one
GREEN that was the useful kind: every non-`ok` status `_pulse_truth_lookup` can
currently return *also* has empty traces, so the status check is shadowed by
the emptiness guard and no fixture can reach the state (docs/141 §4af). The
status is the documented contract, so it stays as the primary guard and is
pinned directly, by handing the renderer a payload that breaks that contract
(not-ok, yet carrying traces).

### Part 3 self-review: what it cost the page

Part 2's argument was that the customer's *"SM이 느려질텐데"* worry was answered —
so adding a per-row lookup to the table render without measuring it would be
careless. `_pulse_truth_spark` runs per unknown-class row, each doing a config
lookup plus `decimate_minmax` over up to 1,488 samples.

Measured on the real chip, warm, six requests:

```
/pulses                     median 23 ms   (194 KB, 3 lab sparklines, 51 polylines)
/pulses?channel=xy          median 28 ms   (no lab classes at all)
/pulse/row (a lab class)            17 ms
```

The tab with **no** lab rows is the slower of the two, so the lab path is not
distinguishable from run-to-run noise. The config is a cached dict (part 2) and
`_config_stale` is memoized on `(mutation_seq, len(change_log))`, which is what
keeps the per-row lookup to a dictionary read. The worry stays answered.
