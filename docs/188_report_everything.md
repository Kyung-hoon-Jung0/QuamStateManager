# docs/188 — the printable report holds every value SM can extract

**2026-09-16, customer, on-site.** Two items came in one message; this is the
second.

> 현재 printing 기능으로 기본적인 characterization은 잘 나오는데, 추출할 수 있는
> 모든 정보를 담았으면 좋겠다고 함. T2, Echo, RB 등의 데이터도.

(The first item — Left/Right collapsing the Datasets folders — shipped as
`770dda5`.)

## 1. What the report actually printed

`GET /chip-status/report` (docs/126 #21) rendered five tables. The Qubits one
carried **nine columns**:

```
id · is_active · f_01 · T1 · T2ramsey · readout_amplitude · P(RO) ·
gate_fidelity_avg · grid_location
```

Measured against what the same `QueryEngine` hands every live page, that is
roughly a third of the chip. Absent from the printout, all of it already
resolved and sitting in the same dicts:

| where | never printed |
|---|---|
| frequencies | `f_12`, `anharmonicity`, `chi`, `xy_RF_frequency`, `xy_intermediate_frequency` |
| coherence | **`T2echo`** — the customer named it |
| 1Q gates | `x180_amplitude/length/alpha`, `x90_amplitude`, `saturation_amplitude`, `gate_fidelity_x180/x90` |
| readout | `readout_threshold`, `readout_iw_angle`, `readout_RF_frequency` |
| readout fidelity | `assignment_fidelity`, `ro_fidelity_g/e`, `assignment_fidelity_gef`, `ro_fidelity_gef_g/e/f` — every one of them |
| flux | `z_delay_ns`, `phi0_voltage`, `phi0_current`, `freq_vs_flux_01_quad_term`, `bias_mode` |
| QDAC | the whole component (docs/136) |
| wiring | which OPX port each channel is cabled to |
| pairs | `detuning`, `mutual_flux_bias`, `moving_qubit`, `fidelity_source`, `best_gate` |
| 2Q gates | **every RB number** — SRB, IRB, the decay α, the Bell state, the run that produced them |
| 2Q gates | every gate's amplitude / coupler amplitude / length / flat length / smoothing / phase shifts |
| pairs | the pair's own readout confusion matrix |

The pairs table printed exactly one number, `pair_fidelity`.

## 2. What it prints now

Thirteen sections. The one Qubits table became six, grouped by family so a
column header stays one short word and each table is readable on its own page
in print:

```
Topology (the map, unchanged)
Qubits — frequencies        f_01 · f_12 · anharmonicity · chi · xy RF · xy IF · last cal.
Qubits — coherence          T1 · T2 Ramsey · T2 echo · T2 Ramsey / 2·T1
Qubits — single-qubit gates x180 amp/length/alpha · x90 amp · saturation · F avg/x180/x90
Readout                     f · RF · amp · P(RO) dBm · length · threshold · IW angle · ToF
Readout fidelity            assignment GE · F|g> · F|e> · assignment GEF · F|g> · F|e> · F|f>
Flux lines                  joint · independent · flux point · delay · Phi0 V · Phi0 A · df01/dPhi^2
QDAC-II bias                channel · dc offset · trigger · dwell · slew · range · filter · settle
Wiring — ports              drive · readout · flux · bias source · QDAC channel
Qubit pairs                 control/target · moving · best 2Q fidelity · source · best gate · detuning · mutual flux bias
2Q randomized benchmarking  one row per stored number, with what it measures
2Q gate parameters          amplitude · coupler amp · length · flat · smoothing · phase shifts
Pair readout confusion      diagonal + mean diagonal
Couplers                    decouple · interaction · delay
```

`T2 Ramsey / 2·T1` is the one derived column: the `T2 ≤ 2·T1` ceiling as a
ratio, a cross-check the chip implies and stores nowhere. It renders only when
BOTH are real numbers.

## 3. Three rules the rewrite is built on

### 3a. A column is never dropped because THIS chip does not fill it

docs/94 and docs/148's rule, and the customer's own earlier *"왜 GEF는
없는거니"*. A silently-absent field reads as *this chip has no such thing*,
which is the opposite of what an empty cell means. So every column renders on
every chip, an absent value is `-`, and a block that is empty for **every**
entity says which leaf it would have filled from:

> GE columns fill from `resonator.confusion_matrix`; no qubit on this chip
> carries a usable one.

A whole absent **instrument** is the one exception: the QDAC section renders
only on a chip that has one, because an instrument that is not on the bench is
not a value this chip failed to record. Both halves are pinned, in both
directions.

### 3b. The report reads the app's own derivations, never its own

The readout fidelities are derived from the confusion matrices, and only
`get_topology` derives them (`query._assignment_fidelity` / `_cm_diag`). The
RB rows' **level** (docs/138) is stamped there. So is the per-gate fidelity
read out of a Standard-RB run. The route now calls
`_topology_with_derived_rb(engine)` — the same function `/api/topology` and
Chip Status call — and the report cannot disagree with the page.

The enrichment is wrapped: a topology that raises leaves the report's own
tables intact rather than 500-ing the printout.

### 3c. One number renderer

Everything dimensional goes through the `qty` filter. The template owns exactly
one piece of formatting, `pct`, because a fidelity as a percentage is something
`qty` has no opinion about.

**A second renderer was written and caught the same day.** A `raw()` macro
doing `"%.4f"` printed a `4.4588e-04` readout threshold as `0.0004` — four
significant figures thrown away in a document whose whole purpose is
extraction. `raw()` now delegates to `qty`, whose unknown-field branch is the
plain-number rule every other surface already uses. Pinned by
`test_a_small_number_keeps_its_precision`, which asserts both that
`4.4588e-04` is there and that `>0.0004<` is not.

## 4. Two things only a real chip showed

### 4a. A gate's pulse is often a POINTER, and the two readers disagree

The topology's `_extract_gate_details` reads the raw macro. `get_pair`
DEREFERENCES the macro's `flux_pulse_qubit` JSON reference (`_deref_pulse_ref`)
— wizard-built chips and the customer's own `pair_gates` recipe store the pulse
as a reference to the op on the moving qubit's `z` line, which is its single
calibration home.

On the customer's 5Q chip, `gate_details` therefore reports **the phase shifts
and nothing else** for every one of the 20 gate variants. The amplitude, the
length, the flat length, the smoothing length — all of them are behind the
pointer.

So `_report_gate_param_rows` reads the flat pair dict. It discovers the gate
NAMES from that same dict (`get_pair` writes `<gate>_phase_shift_control` for
every macro it judged CZ-shaped), so nothing in the report re-implements which
macros count as gates. Pinned with a fixture whose macro is a pointer, which
is the only shape on which the two readers differ.

### 4b. An RB number is per-gate or per-Clifford, and a decay α is neither

docs/138 was a customer report about exactly this confusion, on the topology
headline. A table that printed `StandardRB 0.658` and `InterleavedRB 0.957`
side by side under a column headed "fidelity" would reproduce it in a document
the customer prints and sends on. The RB table therefore carries a **What it
measures** column:

| metric | what it measures |
|---|---|
| `StandardRB` | 2Q Clifford (SRB) |
| `InterleavedRB` / `IRB` | 2Q gate (IRB) |
| `Bell_State` | 2Q Bell state |
| `*_alpha` | RB fit (decay α) |

A decay α renders as a bare number, never as a percentage — it is the RB
exponential's base, a fit parameter. An **unphysical** value (the real donor
chip carrying `IRB = 1.5345`, which once made the Overview claim a 107% 2Q
gate fidelity) is SHOWN, because it is in the file, with its raw number and the
word `unphysical` — never as `153.450%` and never silently dropped.

The `Per gate ÷N` column is the per-gate fidelity the Standard-RB run itself
computed, read from that run and never recomputed here (docs/138's rule). It is
blank where that run is not in this workspace's dataset folders, and the
caption says so.

## 5. Measured on the real chip

Rendered against a COPY of the customer's `260907_KRS_5Q` (their folder read
only, never written):

- 13 sections, 5 qubits, 4 pairs
- **12 RB rows** where the old report printed zero — SRB 65.834% / 93.188%,
  IRB 95.696% / 98.587% / 97.612%, plus both decay α per pair, each with its
  run id (1477, 2857)
- **20 gate-parameter rows** across five CZ variants per pair, amplitudes and
  flat lengths resolved through the pointer
- GE readout fidelity for all five qubits (87.8%–92.3%), each from its own
  confusion matrix; GEF honestly blank with the leaf named
- T2 echo 15.79–47.57 µs — the value the customer asked for
- port labels `con1/fem3/p2` / `con1/fem3/p1` / `con1/fem5/p1` for every qubit
- document 4.0 KB → 35.6 KB

## 6. Pins

`tests/test_chip_report.py`, 19 tests (was 6). The new `TestEverythingExtractable`
class runs against a second fixture built to be awkward in five ways the
original could not reach: a GEF confusion matrix, a QDAC-biased qubit, a CZ
macro whose pulse is a pointer, an `InterleavedRB` of 1.5345, and the real
two-hop `state → wiring → ports` cabling.

**Mutation sweep: 19 of 19 RED.** Every one breaks a rule the section above
claims — the T2 echo column dropped, the ceiling ratio dropped, the GEF note
firing on a chip that HAS the matrices, the absent-derivation note losing its
leaf name, a decay α rendered as a fidelity, SRB and IRB labelled identically,
an unphysical number silently dropped, the gate parameters no longer read from
the flat pair dict, the QDAC section fed nothing, the QDAC section rendered on
every chip, a `%.4f` second renderer, `topo_nodes` emptied, `topo_edges`
emptied, the detuning column removed, a section heading removed, the drive port
not printed, the flux port not printed, the ports section removed, and the
readout power not derived.

## 7. Verified in real headless Chrome

Served on 127.0.0.1:5077 against a COPY of the customer chip and screenshotted
at 1280x7600. The map draws, all thirteen sections render, and the RB table
reads as intended:

```
q1-2  cz_flattop  StandardRB         2Q Clifford (SRB)  65.834%  1477
q1-2  cz_flattop  StandardRB_alpha   RB fit (decay a)   0.5445   1477
q1-2  cz_flattop  InterleavedRB      2Q gate (IRB)      95.696%  1477
```

It found one layout defect the HTML could not show: in the 9-column
gate-parameter table a `#./inferred_total_length` pointer took the width and
squeezed the first column until `q1-2` rendered as `q1-` over `2`. An entity id
and a gate name are NAMES and now carry `.rep-id` (`white-space: nowrap`); the
pointer is the cell that gives way (`overflow-wrap: anywhere`).

## 8. The Download HTML path now RUNS, instead of being grepped for

`ChipReport.buildStandalone()` is the only part of the report that is CODE
rather than markup, and the pins only ever asserted that its SOURCE was on the
page - the shape docs/187 named as this project's recurring failure (a handler
that never runs). It cannot be driven under jsdom honestly: it waits for the
ComponentMap's SVG and then FETCHES both stylesheets, so a harness would be
testing its own stubs.

`tests/report_download_probe.cjs` drives it over CDP in real headless Chrome
against a running server (a round-verification tool, like the `cdp_*` drivers -
not wired into pytest, which has neither a server nor a browser). 14 checks,
all passing on the customer chip copy: the saved file is a whole document of
866,126 characters, every `<script>` stripped, no stylesheet `<link>` left to
404 offline, the CSS inlined as `<style>`, the drawn map baked in, the Download
button removed (it is dead in a saved file) and Print still working - plus the
docs/188 sections themselves, since a printout the customer archives must carry
them and not just the live page.

**Its own first sweep scored 4 of 5, and the miss was the probe's.** "The
download no longer waits for the map" went GREEN because the probe let the SVG
draw before calling, so deleting `whenDrawn` changed nothing it could observe.
The wait is made observable instead: take the drawn SVG away, start the
download, and put an SVG back after 800 ms. A serializer that waits returns
late and carries the map; one that does not returns at once and archives the
"Loading chip layout..." placeholder. **Re-swept: 5 of 5 RED.**

## 9. A silent no-op, recorded because it nearly shipped

Section 7 above was first appended with a `str.replace` whose anchor spanned a
line break the file did not have. The replacement matched nothing, the script
wrote the file back unchanged, and printed `ok`. Nothing was lost only because
the next command read the tail. Every patch script in this round asserts its
anchor count BEFORE writing; the one that did not is the one that quietly did
nothing.

