# 137 — QDAC and LF-FEM on the same qubit, through the lab's own builder

docs/136 made SM read, display, diagnose and *build* a bias-tee qubit — a qubit
whose DC operating point is held by a QDAC-II while an LF-FEM port plays flux
pulses on top of it. That covered SM's own build path, which drives
`quam_builder` directly.

It did not cover the path the lab actually runs. Their calibration nodes load a
chip produced by **their** `quam_config` builder, and that builder cannot make
this shape. This is the file that fixes it.

## The one `if`

```python
# quam_config/build_quam_qdac.py:93
qubit_class = QdacBiasedFixedFrequencyTransmon if is_qdac_biased else machine.qubit_type
```

Per qubit id, one branch or the other. A bias-tee qubit is both, so it has no
branch to land in. And the two guards on either side of it are **exact
complements**:

- `:107-113` — a `flux` line on a QDAC-biased qubit raises.
- `:119-124` — a `qt` trigger line on a **non**-QDAC-biased qubit raises.

So the qubit is unreachable from either direction. Whichever way it is
declared, one of those two fires.

The worst part is not a raise, though. It is `:131`:

```python
if is_qdac_biased:
    transmon.z = QdacBiasLine(channel=channel)     # unconditional
```

That runs *after* the line loop. On a qubit that did get a `FluxLine` at `:118`,
this **overwrites it** — no exception, a chip that builds and saves, and a flux
line that quietly stopped existing. A silent physics error that looks like a
working chip.

## Why the lab has to change one file, and SM cannot do it for them

`state.json` is a serialisation of Python dataclasses. Every key in it must be a
**declared field** of the class its `__class__` names.
`QdacBiasedFixedFrequencyTransmon` declares `z` and nothing else, so `z` is a
`FluxLine` *or* a `QdacBiasLine`, never both. Measured, not argued (docs/136 §19):

```
both components, today's classes   LOAD FAILED: AttributeError
  Unexpected attribute 'qdac_bias' in Quam.qubits["q1"]
  (quam_config.qdac_components.QdacBiasedFixedFrequencyTransmon)
```

The fix is ten lines in a file the lab already owns and maintains:

```python
@quam_dataclass
class QdacBiasedFluxTunableTransmon(FluxTunableTransmon):
    qdac_bias: QdacBiasLine = None      # z stays the pulse line
# …and widen my_quam.Quam.qubits' Union with it
```

SM emits the text and refuses to run without it. **SM does not write into the
lab's tree** — that is their source, and a generated edit to it is not something
to make on their behalf.

## What SM emits

`core/qdac_lf_recipe.py`, folded into the existing recipe bundle
(`script_emitter.emit_bundle`) and produced **only** for a spec that declares a
bias-tee qubit. A chip without one gets a bundle byte-identical to before.

`build_quam_qdac_lf.py`
: The combined builder, to be dropped into `quam_config/` beside
  `build_quam_qdac.py`. A transcription of their own
  `_add_transmons_with_qdac` with four named divergences:

  - **D1** the class pick is three-way, not two.
  - **D2** the flux guard raises only for QDAC-**only** qubits, so a bias-tee
    qubit falls through to `add_transmon_flux_component`.
  - **D3** the `qt` guard raises only for qubits with no QDAC bias at all.
  - **D4** the bias lands on a field chosen by mode — `z` for a QDAC-only
    qubit, the sibling for a bias tee. This is `:131`, the silent-overwrite
    line, and it is the reason the shape could not be built.

  Everything structural — octaves, mixers, ports, pairs, TWPAs, pulses, the
  shareable trigger ports, the cabling validation — is **imported from their
  own modules** and runs unchanged. `_add_pulses` needs no change and is
  reused: it hides a `QdacBiasLine` `z` from `add_default_transmon_pulses`, and
  a bias-tee qubit's `z` is a real `FluxLine`, so it correctly falls through and
  does get its `z.operations["const"]`.

`generate_qdac_LF_combined.py`
: The top-level script: three qubit sets, the declared trigger cabling, and
  every flux line explicitly pinned. Takes the output folder as its one
  argument, sets `QUAM_STATE_PATH` itself, and runs unattended (their own
  `generate_quam.py` has an `input()` prompt and a matplotlib window).

### Transcribed, not wrapped — and why

The tempting alternative is to delegate to their `_add_transmons_with_qdac` and
just remove the `qt` entries from `machine.wiring` first so the stock pass does
not raise on them. That trades a hundred readable lines for a mutation hazard:
`machine.wiring` is a `QuamDict` whose nested writes do not reliably stick
(`build_quam_wiring_qdac.py:16-22` exists because of exactly that). The
transcription is longer and the lab can read it side by side with the file they
already have.

`build_quam_wiring_qdac.py` needs **no** changes and is called unmodified.
`set_nested_value_with_path` is a `setdefault` chain, so the `qt` entry lands as
a **sibling** of `z` rather than replacing it — the one part of this that
already worked.

## Every flux line is pinned, and that is not tidiness

Their `generate_quam.py:128-133` cables the coupler flux lines by **allocation
order**, pinning only the last seven:

```python
connectivity.add_qubit_pair_flux_lines(qubit_pairs=qubit_pairs[:-n_slot8])
```

Adding one qubit flux line shifts every unpinned coupler one port along. No
exception. No warning. Nothing visibly wrong until a CZ misbehaves on hardware.
A bias-tee qubit adds exactly such a line — it is the only qubit that is
QDAC-biased *and* wants an OPX flux port.

So the emitted generator pins every flux and coupler line from the allocation
the spec was built with, and **raises** on a missing pin rather than letting the
allocator choose. Same doctrine `script_emitter._qdac_pins` already applies to
trigger ports: by the time someone runs a recipe, the ports are a fact about a
bench, not a choice.

## Three facts that only a real run found

None of these came from reading the code. Each came from running the emitted
script and looking at what landed:

- **A pair is `q1-q2` in a spec and `q1-2` in an allocation** — the target drops
  its leading `q`. Looking the element up verbatim missed **every** coupler, and
  the miss was silent in the worst way: no pin was emitted, so no coupler line
  was added to the connectivity, so the finished chip had **no qubit pairs at
  all** — while the generator printed `OK: 3 qubits, 1 through a bias tee,
  reloaded clean`. Two fixes, because the resolution and the silence are
  separate defects: `_alloc_keys` now tries every spelling, and the emitted
  script carries a `PAIR_COUPLERS` list it checks `FLUX_PINS` against and
  **raises** on a gap. (This is the second time this spelling has bitten in two
  days — docs/136 §18 caught it in preset capture.)

  Verified after the fix, pin against reality:

  ```
  emitted   q1-q2 -> con1 slot5 port4     landed  #/ports/analog_outputs/con1/5/4
  emitted   q2-q3 -> con1 slot5 port5     landed  #/ports/analog_outputs/con1/5/5
  pairs in state: ['q1-2', 'q2-3']
  ```

- **The wirer indexes qubits by NUMBER.** `generate_quam.py:61` is
  `qubits = list(range(1, 21))`; `add_qubit_flux_lines(qubits=["q1"])` is
  accepted and allocates nothing recognisable. The emitted script carries an
  `_idx()` helper and the test pins it.
- **The trigger cabling must fall back to the allocation.** The first version
  filled the ext→port table only from a spec `trigger_pin`, so a spec that
  declared `trigger_port: "ext1"` and nothing else emitted an empty table and
  died at `build_quam_wiring_qdac.py:75` with `KeyError: 'ext1'`. A spec pin
  still wins — it records how the bench is cabled today — with the allocation
  behind it.

## Refusing to half-build

Both emitted files raise at **import** if the subclass is absent, before
anything is written. That is deliberate, and it is the opposite of what SM does
on its own path (docs/136 §13: keep the flux line, warn, continue).

The difference is who is at the keyboard. SM's wizard drives many chips and a
named warning is the right degrade. A hand-run generator produces one chip, and
a chip whose DC bias silently did not attach looks exactly like a working one.

The generator also **loads back what it wrote** before it exits. If the `Union`
in `my_quam.py` was not widened, the build still succeeds and `machine.save()`
has already overwritten `state.json` — the failure would otherwise appear only
in the *next* process, after the good file is gone.

## Measured

The end-to-end chain, run for real:

```
spec (q1 bias-tee, q2 ordinary) → SM allocates → emit_files
  → generate_qdac_LF_combined.py RUNS in the lab's env
  → Quam.load() in a FRESH process
```

against a **copy** of the `PJ_10082026` `quam_config` with the ten lines added
(their tree untouched):

```
OK: 2 qubits, 1 through a bias tee, reloaded clean

q1 __class__ : QdacBiasedFluxTunableTransmon
q1 children  : extras gate_fidelity macros qdac_bias resonator xy z
q1.z         : FluxLine     opx_output ✓   operations ['const']
q1.qdac_bias : QdacBiasLine ch 13  ext1  trigger ✓
wiring q1    : qt rr xy z        ← both entries, one qubit dict
  z.opx_output : #/ports/analog_outputs/con1/5/2
  qt.digital   : #/ports/digital_outputs/con1/5/1
q2 (control) : FluxTunableTransmon | z: FluxLine     ← untouched

fresh-process load: LOAD OK -> Quam | generate_config elements: 7
```

`generate_config()` contains `q1_qdac_trigger`, and `q1.z.operations` contains
`const` — the pulse line survived, which is the whole point.

## Also fixed here (docs/136 follow-ups)

Three honesty defects in SM's own QDAC path, found by reading the review's
claims back against the code rather than accepting them:

- The two `quam_config not importable` degrade strings said the biased qubits
  "have no z/flux component at all". False for a bias-tee qubit, which keeps its
  LF-FEM flux line and loses only the DC bias. The first of them returns before
  `_attach_qdac_bias` runs, so it is the **only** thing that user is told — it
  was sending them to look for a wiring fault that is not there. Both now count
  the two shapes separately.
- The build result reported `qdac_qubits` as the spec's **intent** whenever
  nothing got wired — i.e. it read as a full success exactly when the QDAC path
  had failed completely. It now reports what was wired, with `qdac_requested`
  beside it.
- **One claim was rejected.** The review said the `want_tee and not has_z`
  degrade "re-introduces the docs/136 CRITICAL shape". Checked: that path builds
  a valid `QdacBiasedFixedFrequencyTransmon` with `z = QdacBiasLine`, the root
  can hold it, and it loads. It is a correct degrade with a clear warning. Left
  alone.

## What is pinned

`tests/test_qdac_lf_combined.py` — when it fires, the four divergences, the
pinning (checked **structurally**: asserting the message wording alone passes
with the guard deleted, which mutation testing caught), the integer indices, the
multiplexed feedline, the cabling precedence and sharing, the import gate, the
load-back, and the bundle/README wiring. Plus one live end-to-end build against
the lab's own stack, ending in a fresh-process `Quam.load()`.

**10/10 mutations caught**, and five of them — D4, the trigger target, the
cabling fallback, the integer indices and the pair-id spelling — broke the
**live build**, not just a string assertion. The live test is parametrized over
a chip with tunable-coupler pairs and one without, because the coupler path is
the one where a wrong pin is silent and physical.

## Still open

- **No lab has added the ten lines to their own tree.** Everything here runs
  against a patched copy. Placing them is the lab's call, not SM's.
- **`populate_quam_qdac.py` is untouched.** It keys on
  `isinstance(qubit, QdacBiasedFixedFrequencyTransmon)` in six places, so a
  bias-tee qubit would be skipped by its QDAC pass and mis-handled by its flux
  pass. The emitted bundle populates through SM's own `apply_populate` instead,
  so this only matters if the lab runs their populate script on a tee chip.
  Scoped, not done.
- **The trigger element is not `align()`-aware.** `Qubit.channels` collects only
  top-level `Channel` attributes, so neither `qdac_bias` nor its nested
  `opx_trigger_out` is enumerated and the pair macro never time-orders the
  trigger against the gate. This is **already true of every QDAC qubit today**;
  the bias tee inherits it. The element still reaches `generate_config()`, so it
  is a timing gap rather than a missing element — and fixing it is a QUA-timing
  change with its own blast radius.
- Nothing here talks to the QDAC. It is all state-file work.
