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
