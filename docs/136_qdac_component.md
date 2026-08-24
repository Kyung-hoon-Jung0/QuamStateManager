# 136 — QDAC-II as a component

*2026-08-23. Customer-directed, after the QDAC/digital audit.*

## Why

docs/119 added QDAC-II bias as an **optional add-on**: one band at the bottom
of the Generate wizard's step 4, a `z` that most read surfaces did not
understand, and no presence in search, in the component pages, or in
Diagnostics. That scoping was deliberate ("TWPA-style, not a resource-pool
integration") and it held until a lab actually ran a chip that way.

The 2026-08-23 audit measured what it cost on the customer's real 20-qubit
chip, 11 of whose qubits are QDAC-biased:

- eleven qubits showed **zero** bias fields on their own inspector page;
- `/flux` listed those eleven as rows with four empty cells;
- four Live-Edit columns collided under one label — two different physical
  ports, same header;
- a class migration rendered as **"No differences"** on every live-facing
  comparison;
- three screens printed "not set" for a port delay the grid printed as a
  number;
- a fresh QDAC build produced a chip that **could not be loaded at all**.

The user's decision: stop treating it as an add-on. **QDAC is a component,
like the LF-FEM.**

## The requirement that changed the data model: bias tee

A qubit may be biased by the QDAC **and** driven by an LF-FEM at the same
time, through a bias tee — the QDAC holds the DC operating point (standing in
for `joint_offset`) while the LF-FEM plays pulses (CZ, flux ramps) on top of
it. SM must recognise this in generation, instrument wiring and diagnostics;
the shared flux port must be visually distinct and one hover must answer for
both instruments.

SM forbade exactly this, in two places — `config_generator.py`'s validation
(a `flux` line for a QDAC qubit was a hard error) and `run_build._apply_qdac`
(a qubit that already had a `z` was skipped, so the QDAC silently lost). The
customer's own builder raises on it too. The root cause is the class shape:
`QdacBiasedFixedFrequencyTransmon` sets `z: QdacBiasLine`, so `z` is *either*
a flux line *or* a QDAC line.

**No class that can hold both exists in the customer's `quam_config` today**,
and SM cannot invent one in the lab's env (quam dataclasses reject unknown
fields at load). So the requirement splits:

| Side | What SM does |
|---|---|
| Read / display / diagnose | Fully implemented and **lab-flexible** — detection is structural, so a bias-tee chip reads correctly the day it appears, under whatever field name its class uses, with no env change. |
| Generate | Offers the mode; probes for a class that can hold both; degrades honestly when there is none, and hands over the snippet. |

## §1 One vocabulary — `core/qdac.py`

Every surface used to guess for itself, and they disagreed. The flatteners
assumed "`z` is a dict ⇒ it is a FluxLine"; `physical_units` defined a channel
as "a dict carrying `opx_output`"; `regen_spec` held the only real classifier
in the codebase (a class-name substring test). Now there is one module and
everything imports it.

`bias_mode(qubit)` returns `opx` / `qdac` / `bias_tee` / `None`.
`bias_line_of(qubit)` returns **`(field_name, node)`** — the name matters
because the bias line is not always at `z`: on a QDAC-only qubit it REPLACES
`z`, on a bias-tee qubit it sits beside it. Callers build dot-paths from the
name they are given; assuming `z` is what made the bias-tee shape unreadable.

Detection is **structural first**: an `opx_output` is disqualifying (checked
before anything else — that guard is what keeps a bias-tee qubit's pulse line
from being read as its DC bias), then the class name corroborates, then the
shape decides. A stripped export with no `__class__` is still recognised; a
lone `channel` key is not enough to claim QDAC.

`trigger_ref` walks the two-hop pointer (`z.opx_trigger_out.digital_outputs.
<name>.opx_output` → `#/wiring/qubits/q1/qt/digital_output` →
`#/ports/digital_outputs/con1/4/1`), with the wiring-level `qt` line as a
fallback for chips whose state channel degraded away.

`ext_groups` returns the real cabling — `{(con, slot, port): {ext, qubits,
conflict}}`. One OPX digital output feeds one QDAC ext input and arms every
channel on it, so several qubits sharing one port is the design, not a
collision. `conflict` catches the case nothing else would: two different exts
on one physical port, which would simply arm the wrong channels at run time.

Verified against the real chip on first run: 11 qdac / 9 opx, and the four
cables reproduced exactly (`con1/4/1 ← q1,q9,q17 (ext1)` …).

## §2 The delay three screens called "not set"

```python
z_port = z.get("opx_output") if isinstance(z, dict) else None   # before
```

`z.opx_output` is a **pointer** on every real chip, so `isinstance(z_port,
dict)` was false always and the branch behind it never ran. `/flux`,
`/couplers` and the qubit inspector printed "not set" while the Live-Edit
grid — resolving the same dot-path through `resolve_field_target` — printed
the number. One app, two answers, for a value that was there the whole time.
The fix resolves the pointer chain first (`_resolve` follows it to the port
dict); an inline dict still passes through untouched. Same dead guard, same
fix, on the coupler twin.

Measured on the real chip: qubit z ports read **78 ns** (9 of 9), coupler
ports **78 ns** ×14 and **141 ns** ×16. Before: `None` everywhere.

## §3 Two ports, one header

`_make_leaf` derived a port column's section from `tmpl_segs[0]` and its label
from the last two segments, folding away everything between. So

```
qubits.{name}.z.opx_output.<leaf>                                        (LF-FEM ANALOG port)
qubits.{name}.z.opx_trigger_out.digital_outputs.trigger.opx_output.<leaf> (FEM DIGITAL port)
```

both rendered as `Z Port+ / out · <leaf>`, and the four fields the two port
classes share — `controller_id`, `fem_id`, `port_id`, `shareable` — appeared
as four pairs of identical headers, side by side, with nothing saying which
was the flux port and which the QDAC trigger. Editing the wrong one silently
re-cables the chip. The nested channel is now part of the section
(`Z Trigger Port+`); duplicate `(section, label)` pairs on the real chip:
**4 → 0**.

QDAC bias fields also get a band of their own, `QDAC bias+`, decided by a rule
rather than a list: a template joins it only when **every** leaf under it is
QDAC-owned. On a mixed chip the same template can be a QdacBiasLine field on
one row and a FluxLine field on another — `settle_time` is exactly that — and
a header claiming QDAC would be false for nine of twenty qubits.

## §4 The word "qdac", on both surfaces

The user asked for the quick-filter chip in Live Edit **and** in the Json Tree
View, "빠짐없이". The trap: the string appears in **no column** on a QDAC chip.
`__class__` is in `_SKIP_KEYS`, and the fields are called `channel`,
`dc_offset`, `trigger_port` — so a hardcoded `("qdac", "QDAC")` term would
score zero hits and be silently dropped by the honesty gate that refuses a
chip matching nothing.

What makes it reachable is §3: the band is named `QDAC bias+` and every
QDAC-owned column carries `qdac` in its search text. The Explorer's gate is
the raw document haystack, which carries the word through `__class__` and the
top-level `qdac` instrument. Both surfaces offer it on a QDAC chip
(18 columns match) and neither offers it on a chip without one — pinned as a
**parity** test, because a chip on one surface and not the other is the bug.

## §5 The inspector section that could not be a map

The static property map is FluxLine-shaped, so a QDAC qubit's Flux props all
read None-and-absent and the empty-section rule dropped the lot. A static map
cannot fix this: the bias line is not always at `z`, so the paths are not
knowable in advance. `_qdac_qubit_section` derives them from the chip's own
bias line, and adds one read-only row the state does not hold as a leaf —
the physical OPX digital output the trigger is cabled to, which otherwise
takes a two-hop walk to learn and appears on no other qubit surface. A
bias-tee qubit shows **both** sections, adjacent: DC point here, pulses there.

## §6 `/flux` says where the bias comes from

A **Source** column (`LF-FEM` / `QDAC-II` / `QDAC + LF-FEM`), plus QDAC
channel, DC offset and trigger columns — rendered **only** on a chip that
mixes sources, judged over the whole scope rather than the current page so
paging never changes the columns under the reader. An all-LF-FEM chip renders
byte-identically to before.

## §7 A class migration is a difference

`Differ` defaults to skipping `__class__`. When a lab's out-of-band edit moved
eleven qubits from `FluxTunableTransmon` to
`QdacBiasedFixedFrequencyTransmon`, SM's banner said the live chip had changed
and the review screen said **"No differences"** — the worst of both answers,
and the reasonable reading was "nothing important happened", for the single
most consequential kind of change a chip can undergo. All six live-facing
comparisons now pass `ignore_keys=set()`: the review, its JSON twin, the drift
count and summary, the auto-pull count, the overwrite-live preflight. docs/128
set the precedent for the two version-compare routes; the live doors were
still blind. The global default is unchanged.

## §8 The bias-tee port on the diagram

The user's own description: *"port 색상도 살짝 다르게 해서 그 flux port는
qdac과 공유하는 flux port라는 것을 알려주고, 마우스 hover했을때, z flux와
qdac 둘 다 정보가 표시되어야 해."*

A bias-tee flux port gets **its own amber fill** (`roleColors.z_qdac`,
`#f1c40f`) with a solid slate outline tying it to the trigger, and a dark
label (white fails on amber). *Revised (r2, customer-directed):* the first
pass marked it with a dashed slate ring over the z blue — "still a z port,
a fourth colour would need a legend" — and the verdict from the real screen
was that the ring is invisible at port size. Amber is the widest free hue gap
in the palette; the only neighbour, `rr_in` gold, is orange-family and lives
on MW-FEM input columns, never beside an LF-FEM z output. The `/flux` Source
chip for "Bias tee" wears the same amber, so one colour means bias tee
app-wide. One hover answers for both instruments: the flux half unchanged, then the QDAC channel, the DC
offset it actually holds, and which ext input steps it — because reading
`joint_offset` alone (or its absence) would describe half the hardware.

A QDAC trigger marker's popup names its **ext input**, which is the only thing
that explains why three qubits legitimately land on one port. Labelled just
"digital", a shared port reads as a wiring collision.

## §9 A page of its own

`/qdac`, on the same terms as the other component pages: a `has_qdac`
structural flag beside `has_resonator`/`has_flux`/`has_coupler`, a nav item
that appears only on a chip that has one, and the shared
`_channel_scoped_qubits_page` shell so filtering, sorting, pagination and the
component map come for free.

What the per-qubit table cannot show, and this page adds: the **instrument**
(address, transport, VISA backend — or an honest line when a biased chip
declares none), and the **trigger cabling** as a table of cables rather than a
list of qubits —

```
QDAC input   OPX digital output   Armed qubits
ext1         con1/fem4/p1         q1  q9  q17
ext2         con1/fem4/p2         q3  q11 q18
```

That grouping is the fact the rest of the app had nowhere to put, and it is
what makes a shared port read as correct instead of as a collision.

It was derived from the chip's own `state.json` + `wiring.json`, and it later
turned out that PJ's `quam_config/qdac_cabling/trigger_cabling.py` **declares**
exactly this — `QDAC_TRIGGER_CABLING` (ext → con/slot/port) and
`QDAC_QUBIT_TRIGGER_PORTS` (qubit → ext), with the shared marker port marked
`shareable`. Independent agreement with the lab's own source of truth, not a
guess that happened to fit. (The CQT tree instead *derives* the ext as
`(channel - 1) % 4 + 1` and declares no sharing — one more reason PJ is the
baseline.)

A cable
whose qubits disagree about their ext is marked in place.

`has_flux` deliberately still counts a QDAC-biased qubit — its `z` IS a bias
line and it belongs on the Flux page, which now says which source each row
uses instead of leaving four cells empty.

On the map, a QDAC-biased qubit gets its own mark. `.cm-flux` keys on
`z_port`, which is `None` for a qubit with no OPX analog output, so the map
drew nothing for eleven of twenty and showed them as having no flux bias at
all. The new `.cm-qdac` bar is dashed on a bias tee, where both sources feed
one line.

## §10 Diagnostics finally lints these qubits

`_iter_channels` defines a channel as "a dict carrying `opx_output`", which a
`QdacBiasLine` never is — so a QDAC-biased qubit contributed **zero** channels
to every check built on it. Eleven of twenty qubits were unlinted.

`_qdac_findings` adds, all in the existing `connectivity` domain and all listed
in `_CHECK_CATALOG` (the source of truth for the "What is checked?" popup):
the instrument exists and carries the address its transport needs; each
channel is an integer in **1–24** and no two qubits share one; `trigger_port`
is one of the four ext inputs; and the cabling is coherent in both directions
— **every qubit on one digital output declares the same ext** (the port and
the ext are two names for one cable, and a disagreement silently arms the
wrong channels at run time), and no ext is fed from two different outputs.

Measured: **0 findings on the correctly-wired real chip**, and one finding
each for five injected faults.

Two rules were considered and **not** shipped, because the evidence did not
support them:

- *`dc_offset` within `output_range`* — it reads like a voltage bound and is
  not one. The driver documents `output_range` as the CURRENT range (low =
  ±200 nA, high = ±10 mA), so it constrains no voltage at all.
- An upper channel bound taken on trust — 1–24 is used because the customer's
  own driver asserts exactly that and says so in the message it raises.

## §11 The generated trigger port is shareable

The customer's builder marks the trigger port shareable in **two** places: on
the `DigitalOutputChannel` and, in a dedicated pass, on the **port object**.
SM marked only the channel. The port object defaults to `shareable=False`, so
`generate_config()` would refuse the second qubit claiming that cable — on the
real chip, seven of the eleven. Now marked on both, with `delay=0`/`buffer=0`
stated rather than left as nulls a later reader has to guess about.

Proven by a real build in `cqt`, not by reading the code: the assertion runs
against the written `state.json`, and reverting the fix fails it.

## §12 The root class, on the fresh path too

`regen_spec` learned to carry the source chip's own `__class__` after the
audit's CRITICAL. A **fresh** build has no source to carry from, and
`generate.js` never sets `quam_class` — so the wizard produced the same
unloadable chip, and would have kept doing so the moment WS5 let anyone pick
QDAC from it.

Measured, not argued. Same spec, same env, twice:

```
without a root class:  build ok: True | warnings: []
                       root __class__ : …flux_tunable_quam.FluxTunableQuam
                       LOAD FAILED: TypeError
                       Path: Quam.qubits["q1"]
                       Required type: FluxTunableTransmon
                       Actual type:   QdacBiasedFixedFrequencyTransmon

with the gate:         root check -> chosen: quam_config.my_quam.Quam
                       build ok: True | warnings: []
                       root __class__ : quam_config.my_quam.Quam
                       LOAD OK -> Quam   (2 qubits, qdac present)
```

Two halves:

**In the env** — `probe_capabilities.qpu_roots()` reports which QPU root
classes import and what each can HOLD. A curated home list, never
`walk_packages`: importing the whole env to find a class is slow and on a
customer tree it executes arbitrary module-level code. The lab's own root is
tried first, because a customer subclass exists precisely when the stock ones
cannot hold that lab's chip. Inspection is **textual** on purpose —
`from __future__ import annotations` leaves annotations as strings and forward
refs may not resolve outside their module, so `get_type_hints` raises on
exactly the classes we care about, while "does this field's declared type
mention a QDAC component?" is answerable from the text and answerable safely.
The same walk reports a **bias-tee** class when one exists, including the
FIELD NAME its bias line sits under — that name is the lab's choice, since no
such class exists upstream.

Against the real customer env: `quam_config.my_quam.Quam` → holds_qdac **True**;
both stock roots → **False**; `FixedFrequencyZZDriveQuam` → not importable
(reported honestly rather than assumed present); bias_tee → **None**, which is
correct today.

**At the door** — `capabilities.qpu_root_check(spec, manifest)`, called by
both build routes beside the existing capability gate. It fills `quam_class`
when the user has not chosen one, and blocks when the env genuinely cannot
root the chip. Three rules earn their place:

- an **unprobed** manifest is never a blocker — unknown is not a negative, the
  rule `assess` already follows;
- a root the user named that the probe knows **cannot** hold the chip is
  **refused**, not warned: the file would be written and would not open;
- a root the probe does **not know** is allowed through — the user may be
  naming a class they are about to write, and the build degrades with a named
  warning if it turns out unimportable.

A capability cache entry written before this probe existed has no `qpu_roots`,
so it counts as a **miss** rather than a hit. Treating it as a hit would have
silently disabled the gate for everyone with a warm cache — exactly the people
least likely to notice.

## §13 The bias tee reaches the build

Two lines forbade it, in two files, and the customer's own builder raises on it
as well. The root cause is a class shape: `QdacBiasedFixedFrequencyTransmon`
types `z` **as** the bias line, so on it a qubit is either flux-tunable or
QDAC-biased, never both.

**The declaration is a flag, not a coincidence.** A `flux` line beside a QDAC
entry is equally the signature of the mistake the old validation error existed
for — a qubit switched to QDAC while its flux line lingered — and the two build
differently. So `spec.qdac.qubits[q].bias_tee` says which happened, and
`validate_spec` checks it **from both ends**: co-presence without the flag is
still refused (with the flag named in the message), and the flag without a flux
line is refused too. Without the second check, declaring the tee and then
removing the flux line builds a plain QDAC qubit while the wizard, the review
step and the emitted recipe all say bias tee.

**No class in the customer's env can hold both**, and SM will not invent one:
quam dataclasses reject unknown fields at load, so a field SM made up produces a
chip that cannot be opened. Instead:

- `probe_capabilities._bias_tee_shape` looks for one **by shape** over a curated
  set of homes — a transmon dataclass whose `z` is not a QDAC bias line and
  which carries a `QdacBiasLine`-annotated field beside it. It reports the
  **field name**, because whoever writes such a class names it.
- `capabilities.bias_tee_check` turns that into a capability row. It is the one
  capability with **no locator behind it**, so it lives in `SYNTHETIC_REGISTRY`
  rather than `REGISTRY` — which stays pinned byte-for-byte against the probe's
  `CATALOG`, precisely so a locator is never claimed without a prober.
  Severity is DEGRADE, never a blocker, and an unprobed env reports
  "unknown", not "missing".
- `run_build._find_bias_tee_class` repeats the shape search inside the build and
  attaches the bias to that class's own field, leaving `z` alone — `z` there is
  the pulse line, and the trigger goes on the **bias** line, which is why the
  attach reads `getattr(qubit, bias_field).opx_trigger_out` rather than
  `qubit.z.opx_trigger_out`.
- With no such class the build **degrades out loud**: the LF-FEM flux line is
  built (it is the half that plays pulses and it is buildable), the DC bias is
  not attached, and a named warning says so per qubit plus once for the chip.
  Silently dropping either half would look like a working chip.

**Measured, all three shapes, in the customer's own env** — each ending in a
real `Quam.load()`, because a build that writes files and reports ok is not
evidence (that is exactly what the root-class CRITICAL did):

```
qdac      QdacBiasedFixedFrequencyTransmon · z=QdacBiasLine · qdac ✓ · LOAD OK -> Quam
opx       FluxTunableTransmon              · z=FluxLine     · qdac ✗ · LOAD OK -> FluxTunableQuam
bias_tee  validate OK · capability warns · 2 named warnings · z=FluxLine, DC bias NOT
          attached (this env has no such class) ·               LOAD OK -> Quam
```

The degraded bias-tee build still writes the top-level `qdac` instrument even
though no qubit ended up biased by it. Deliberate: the lab **does** have that
instrument (they declared it), the warnings say plainly that the bias was not
attached, and once the class exists a re-generate finds the instrument already
in place. An entry with no user is not a lie; a missing one would be a second
thing to fix.

The ~10 lines a lab needs, which the wizard and the README both hand over:

```python
@quam_dataclass
class QdacBiasedFluxTunableTransmon(FluxTunableTransmon):
    qdac_bias: QdacBiasLine = None      # DC operating point; z stays the pulse line
# and widen my_quam.Quam.qubits' Union with it
```

`regen_spec` reads a bias-tee chip back through `qdac.bias_line_of` instead of
`z`'s class name. That was not cosmetic: on a bias-tee chip the bias line is a
**sibling** of `z`, so the old `z`-only test read every such qubit as plain
flux-tunable and a re-generate dropped the DC bias without saying anything.

## §14 Flux source is a choice, made once and overridable

Step 4's line-confirmation row gains a **Flux source** selector — LF-FEM /
QDAC-II / Both (bias tee) / Per qubit… — and the bottom-of-step-4 "QDAC-II bias"
band becomes the QDAC component's own band under it, with a three-way source
picker per qubit where an on/off checkbox used to be.

The chip-level answer is **derived from the per-qubit shapes**, never a mode
field of its own. That is what makes a per-qubit override incapable of
disagreeing with the selector. It also makes "Per qubit…" a *report*: it is
offered only while the chip actually is mixed, because as a command it would
mean "make them differ", which names no particular arrangement.

The whole control hides on a chip with no z line at all. An inert control there
would read as "the answer is LF-FEM".

`deriveLines` now keeps the OPX flux line for a bias-tee qubit — the one line
that turns the flag into a buildable chip. And `prunePopulate` finally reaches
`spec.qdac.qubits`: it was the one qubit-keyed map nothing pruned, so lowering
the qubit count left an orphan entry that `validate_spec` rejects from step 8,
naming a qubit no longer on screen.

## §15 Populate: the QDAC's own section, and the single home

`gen-pop-sec-qdac` renders one row per QDAC-biased qubit (never a "Set all" over
the whole chip — a qubit becomes QDAC-biased through the step-4 source picker,
not by someone typing a dwell time into a table).

The cells write onto **`spec.qdac.qubits[qid]`**, the same entry the step-4 band
edits and the only one `run_build` reads. A `populate.qdac` bucket would have
been a second source of truth for the same eight fields, and the two would have
drifted the first time anyone edited both screens. Three small helpers —
`popBucketRead` / `popBucketWrite` / `popBucketPrune` — are what let one table
renderer serve two different homes; `popBucketWrite` returns `null` for a qubit
with no QDAC entry, which is how the table declines to rewire a chip.

**Populate-protect reaches the second home.** Because the cells write outside
`spec.populate`, `regen_populate.changed_fields` — which walks `spec.populate` —
could not see an edit made there, and on a **re-generate** the tier-1 carry
would have silently reverted every QDAC value to the source chip's. Both sides
of that diff now build the same `"qdac"` pseudo-group:
`regen_populate.populate_view(spec)` on the server, and the wizard's own
hydration snapshot on the client. `protect_paths` expands a changed cell to
`qubits.<q>.<bias_field>.<field>` — the bias field **read off the rebuilt chip**,
because it is `z` on a QDAC-only qubit and a sibling of `z` on a bias-tee one.
A degraded rebuild has no bias line, so nothing is protected, which is right:
there is no value there to defend.

Units are **fixed labels, not the stage-wide unit selectors**: `dwell` is in
seconds and `settle_time` in nanoseconds on the *same component* (confirmed
against the customer's `qdac_components.py`, where `settle_time` feeds
`wait(int(settle_time) // 4)` — a QUA clock-cycle count). One shared `dim` would
have silently converted one of them by 1e9. `dc_offset` is a real voltage and
does ride the volt selector, like every other offset on the step.

## §16 Trigger cabling: auto first, then group

One OPX digital output drives one QDAC **ext trigger input** and arms every
channel on it. Auto-allocation gives each qubit its own dedicated port — correct,
and eleven cables where the customer's bench has four.

So step 5 grows a **Trigger cabling** table under the diagram: one row per ext
input, its OPX digital output, and the qubits armed on it as chips with an
inline ext dropdown. Assigning an ext is what joins a cable; the port follows.

- **Round-robin ext1–4** assigns them cyclically down the qubit list — which is
  exactly how the reference 20-qubit chip is cabled (q1→ext1, q3→ext2, q5→ext3,
  q7→ext4, q9→ext1, …). One press reproduces a real bench.
- **Share one cable per ext input** (default on) pins every member of a group to
  the group's lowest allocated port, and mirrors it into the local allocation so
  the diagram on the same screen shows the cable that will be built rather than
  the pre-grouping one.
- A pin the wizard created is marked `pin_source: "group"` and is the only kind
  it will withdraw. A pin carried in by `regen_spec` records how the bench is
  cabled **today**; dropping it because the wizard would not have chosen it is
  how a re-generate hands a lab a chip expecting cables it does not have.
- The header counts **cables to plug**, not qubits — that is the number someone
  at the bench acts on.

## §17 The long tail, item by item

- **`script_emitter`** had zero QDAC support, so the exported recipe silently
  dropped the instrument. `02_build_machine.py` now carries `QDAC` /
  `QDAC_QUBITS` / `QDAC_PINS` data blocks and calls the same
  `_attach_qdac_bias` / `_inject_qdac_state` / `_inject_qdac_trigger_wiring` the
  wizard ran — the per-qubit loop was split out of `_apply_qdac` for exactly
  that reason, so the recipe runs the code rather than a paraphrase of it. Pins
  are resolved at **emit** time: by the time someone runs a recipe the ports are
  a fact about a bench, not a choice. A chip with no QDAC emits a bundle
  byte-identical to before (the golden test says so).
- **The root class reaches the recipe too.** `02` imports the chip's own root
  as `QuamCls`, and `03_generate_config.py` tries it before the stock list —
  otherwise the emitted verifier could not open the chip the emitted builder had
  just written.
- **`pulse_index`** now enumerates the QDAC trigger marker. It is a real pulse on
  a real OPX digital output, but it lives one level deeper than any other qubit
  pulse (`<bias>.opx_trigger_out.operations.trigger`), so the page printed a
  definite total that excluded eleven of them. The bias field is asked for, not
  assumed, so a bias-tee qubit's trigger is found under its own name.
- **The CLI's `wiring` table** gains `qdac_channel` + `qdac_trigger` — a QDAC
  qubit has no `z` entry in wiring.json at all, so every column was blank for it.
  The columns appear only when a chip has a QDAC, and then on **every** row
  (the CLI derives its column set from `rows[0]`).
- **CSV/MD export** appends `bias_mode` / `qdac_channel` / `qdac_dc_offset` on a
  QDAC chip. The default bias column is `z_joint_offset`, which eleven of twenty
  rows do not have; blank with nothing saying why is the thing being fixed.
- **`click_targets`** offered `qubits.{q}.z.joint_offset` for a flux click. A
  candidate can now name a different path per entity (`path_by_entity`), and the
  bias line's own field is read rather than assumed. The override comes from the
  **open chip**, not the run's snapshot, because the popup writes into the open
  chip.
- **autofit `families.py`** re-routes the flux-offset target to the bias line's
  `dc_offset` on a QDAC-only qubit. `writer.batch_set` is all-or-nothing by
  design, so one impossible flux row discarded the same run's valid resonator
  updates. `min_offset` is **skipped** rather than mapped — a QDAC holds one DC
  level, not a flux-arc parameterisation. A bias-tee qubit is deliberately left
  alone: its `z` is a real FluxLine and `joint_offset` is a real field; which of
  the two a node would write there is a question no node has answered, and
  D-14 forbids inventing the answer.
- **`regen_merge`** gains the same schema gate one level down: the field can be
  legal while its old **value** is an object of a class this build never wrote.
  The case that found it — a QDAC chip re-generated in an env without
  `quam_config` degrades to a qubit with no `z`, and grafting the old
  `QdacBiasLine` back produces a state whose `z` is typed for something else.
  Only under a **tagged** parent: an untagged container (`operations`, `macros`,
  `extras`) is where a lab's own pulse class legitimately lives, and gating there
  would break the tier-2 graft the branch exists for.
- **`compare._instruments`** lists the QDAC (and bias-tee qubits) — two chips of
  the same architecture, one LF-FEM-biased and one QDAC-biased, compared as
  having identical hardware. `_chip_type` is deliberately **not** touched: it
  feeds a `== "fixed_frequency"` gate-inference test in `routes.py`, and a new
  value there would silently take the other branch.
- **Config Viewer's per-qubit slice** now includes `q1_qdac_trigger` — the one
  element a qubit owns that is not dot-joined. Matched by its exact full name; a
  loose `q1_` prefix rule would start pulling arbitrary elements into whichever
  qubit named their prefix.
- **Create-pulse** disables `z` on a QDAC-only qubit (with the reason on the
  option), and the server's refusal now says *what it is* instead of "cannot
  hold pulses yet" — "not yet" is the wrong story for a DC source. A bias-tee
  qubit is unaffected: playing pulses on its `z` is the point.
- **The type-alarm banner** stopped calling a missing **class** a field, and
  stopped reporting the aggregated finding count as a field count — four
  findings can stand for twenty-three places in the state.
- **`probe_state_schema._import_class`** keeps the exception that *explains* the
  failure. Walking the dotted split further left, it reported the shorter
  split's symptom ("`quam_config` has no attribute `qdac_components`") over the
  real cause ("no module named `qdac2_driver`") — two very different things to
  go and fix.

## §18 What the review round caught

Three defects in §14–§17's own work, found by reading it back rather than by a
test failing.

**The selector hid itself on the chip that motivated it.** The Flux source row
was gated on `state.qubitFlux`, which is FALSE for a fixed-frequency
architecture — and the customer's 20-qubit chip is *fixed-frequency qubits
biased by a QDAC* (`QdacBiasedFixedFrequencyTransmon`). The whole component was
unreachable in the wizard for exactly the shape it exists for. A DC bias source
is a question on every architecture; what the architecture decides is which
*sources* are possible. So the row is always shown, LF-FEM and bias tee are
**disabled with the reason on them** when there is no z line to play pulses on,
and the first option relabels itself **"None (no DC bias)"** there — because
with no z line "opx" does not mean "biased from an LF-FEM", it means this qubit
has no QDAC entry.

**A carried pin could be out-voted by an allocation.** The group cable was the
numeric minimum over its members. A member carrying a `regen`-carried pin (how
the bench is cabled *today*) sitting on a higher-numbered port than a fresh
allocation would keep its own pin while the rest were pinned to the lower one —
splitting one ext input across two physical outputs, which is the exact failure
the grouping exists to prevent. A carried pin is **evidence**; an allocation is
a guess, so the carried pin is now the cable. Two members carrying *different*
pins stay split on purpose: that is what the chip says, and Diagnostics reports
it.

**A near-regression that no harness would have caught.** Routing every populate
read through `popBucketRead` tempted `capturePresetSections` into asking for its
rows *by name*. That breaks pairs: a pair bucket may be keyed in the SHORT
second-member form (`q1-2`, r16 0-1) while `presetRowIds` renders the full one,
so the capture finds nothing and a saved preset silently loses its pair values.
It was caught by reasoning, then **confirmed uncovered** — the fix was reverted
and four generate harnesses all stayed green. F7 now pins it.

## §18b Two more from the browser (r3, customer-reported)

**The amber vanished behind "Modify wiring".** /instrument stamps
`qdac_shared` server-side (`query.py`) and the bias-tee port renders amber; the
wizard's step-5 diagram is the SAME renderer fed by `buildInstrumentData`,
which regroups the ALLOCATION client-side — and never stamped it. The same
physical port was amber on one page and plain z blue one click later.
`buildInstrumentData` now stamps `qdac_shared` + the QDAC facts on a bias-tee
qubit's z entry (dual hover included), and `qdac_trigger`/`qdac_ext` on the
trigger entry, matching /instrument.

**Digital ports could not be dragged in the wizard.** Deliberate, once:
docs/135 disabled digital drag because "a QDAC trigger port is not a
spec.lines entry — an edit nothing could carry out". Stale since
`spec.qdac.qubits[q].trigger_pin` exists — that is exactly the home the edit
writes to, and the build honours it verbatim. Now:

- a QDAC trigger circle is draggable; a digital marker with no QDAC home is
  not (cursor stays default);
- the drag moves the whole **cable** — every qubit armed on that ext input —
  because the physical object being moved is one marker cable into one QDAC
  ext input, and peeling a single qubit off its cable would split one ext
  across two ports, the exact mis-wiring Diagnostics flags;
- a valid target is an EMPTY digital port on a declared FEM (both flavors
  carry digital outputs); one already carrying another cable is refused —
  dropping there would merge two ext inputs onto one physical output;
- the drop writes `trigger_pin` for every qubit on the cable as **user
  evidence** (no `pin_source`), so the sharing pass treats it like a
  regen-carried pin and never withdraws it;
- occupancy is answered by `qtElementsAtPort`, which scans the allocation's
  `qt` entries only — a wirer-allocated qt channel can carry `io_type
  "output"` with `signal_type "digital"`, so the generic per-io match would
  misfile it.

Pinned as F8/F9 in `generate_fluxsource_selfcheck.cjs` (**6/6 mutations
caught** — and one of those mutations caught the HARNESS: the occupied-port
assertion originally targeted an undeclared slot and passed vacuously through
the wrong guard).

## §18b Two more from the browser (r3, customer-reported)

**The amber vanished behind "Modify wiring".** /instrument stamps
`qdac_shared` server-side (`query.py`) and the bias-tee port renders amber; the
wizard's step-5 diagram is the SAME renderer fed by `buildInstrumentData`,
which regroups the ALLOCATION client-side — and never stamped it. The same
physical port was amber on one page and plain z blue one click later.
`buildInstrumentData` now stamps `qdac_shared` + the QDAC facts on a bias-tee
qubit's z entry (dual hover included), and `qdac_trigger`/`qdac_ext` on the
trigger entry, matching /instrument.

**Digital ports could not be dragged in the wizard.** Deliberate, once:
docs/135 disabled digital drag because "a QDAC trigger port is not a
spec.lines entry — an edit nothing could carry out". Stale since
`spec.qdac.qubits[q].trigger_pin` exists — that is exactly the home the edit
writes to, and the build honours it verbatim. Now:

- a QDAC trigger circle is draggable; a digital marker with no QDAC home is
  not (cursor stays default);
- the drag moves the whole **cable** — every qubit armed on that ext input —
  because the physical object being moved is one marker cable into one QDAC
  ext input, and peeling a single qubit off its cable would split one ext
  across two ports, the exact mis-wiring Diagnostics flags;
- a valid target is an EMPTY digital port on a declared FEM (both flavors
  carry digital outputs); one already carrying another cable is refused —
  dropping there would merge two ext inputs onto one physical output;
- the drop writes `trigger_pin` for every qubit on the cable as **user
  evidence** (no `pin_source`), so the sharing pass treats it like a
  regen-carried pin and never withdraws it;
- occupancy is answered by `qtElementsAtPort`, which scans the allocation's
  `qt` entries only — a wirer-allocated qt channel can carry `io_type
  "output"` with `signal_type "digital"`, so the generic per-io match would
  misfile it.

Pinned as F8/F9 in `generate_fluxsource_selfcheck.cjs` (**6/6 mutations
caught** — and one of those mutations caught the HARNESS: the occupied-port
assertion originally targeted an undeclared slot and passed vacuously through
the wrong guard).

## §18c The sidebar pill that painted a different box (r4, customer-reported)

The active highlight on rows carrying trailing icons (Chip Status, Datasets,
Live State Edit, ...) visibly over-reached its neighbours. Measured in a real
Chrome rather than eyeballed: a plain active link's pill is x2/w240/h36 (Pico
bleeds every nav ANCHOR over the li's padding with negative margins), while
the icon rows paint a DIV or the LI — which gets no such bleed — so their pill
sat at x13, 45px tall (Pico's nav-button padding beat the bare
`.nav-sub-toggle` rule, and 1.9em of the sidebar font is a ~30px glyph), and a
sub-item's anchor DID bleed inside its padded ul and overlapped the parent
pill by 2px.

The fix gives the containers the anchors' exact bleed — written with the same
Pico vars the anchors use, because the root font is 21px under UI scaling and
any px/rem literal would drift — top and sides only (a bottom bleed pulls the
subnav up under the pill), zeroes the inner anchors so the bleed is not
applied twice, scopes and shrinks the toggle to fit the 36px row, and gives
sub-items half the vertical bleed (full horizontal keeps their right edge on
the shared pill edge; the halving is what removed the overlap).

Verified in Chrome after the patch: all SEVEN `nav-sub-row`s and the floatable
li measure x2/w240/h36 — identical to a plain pill — and the sub-pill gap is
3px. Pinned by `tests/test_sidebar_nav_pill.py` (rule-presence pins, since
jsdom does no layout; **6/6 mutations caught**, and the mutation run also
caught the pin's own first regex matching scoped compound selectors as
false positives).

## §19 The bias tee, built for real — and what that caught

§13 said the first lab to write the ~10-line subclass would be the real test.
It was run instead of waited for: the customer's `quam_config` — the
**`PJ_10082026`** tree, which is the baseline (user-directed) — was **copied**
into a scratch directory (their tree is not written to), the ten lines added
to the copy's own `qdac_components.py`, and the copy put first on `PYTHONPATH`.

*(An env-name trap worth stating once: the conda env `CQT_20Q` resolves
`import quam_config` to **PJ_10082026**, not to the CQT tree; `cqt`, the pytest
env, resolves to CQT. `_find_env_python` looks for `CQT_20Q`, so every real
build and load in this campaign already ran against PJ. The three class files
that carry the argument — `qdac_components.py`, `my_quam.py`, `__init__.py` —
are byte-identical between the two trees, which is why the conclusion does not
depend on the mix-up; the generator files are NOT identical, and PJ's are the
real-bench versions.)*

Three hand-written chips, three real `Quam.load()` calls, before any build:

```
A  both on one qubit, TODAY's classes   LOAD FAILED: AttributeError —
     "Unexpected attribute 'qdac_bias' in Quam.qubits[\"q1\"]
      (quam_config.qdac_components.QdacBiasedFixedFrequencyTransmon)"
B  both, with the 10-line subclass      LOAD OK -> Quam | q1: QdacBiasedFluxTunableTransmon
                                          z = FluxLine · qdac_bias = QdacBiasLine
C  control: QDAC-only, today's classes  LOAD OK -> Quam | z = QdacBiasLine
```

So the blocker is not the hardware and not SM. A bias tee is a passive T on the
bench. `state.json` is a **serialisation of Python dataclasses**, and every key
in it must be a declared field of the class its `__class__` names —
`QdacBiasedFixedFrequencyTransmon` declares `z` and nothing else, so `z` is
*either* a flux line *or* a bias line. Two components on one qubit needs a class
declaring two fields. Ten lines, in a file the lab already owns and maintains.

Then the full path, with that env:

```
validate OK → probe finds it (field `qdac_bias`) → capability: available
→ build ok → q1 = QdacBiasedFluxTunableTransmon
   z = FluxLine (opx_output ✓) · qdac_bias = QdacBiasLine (ch 13, -0.09 V)
   trigger channel attached to qdac_bias, not to z
→ LOAD OK
```

**And it caught a defect the synthetic tests could not.** On the first run the
probe reported *no bias-tee class* while the build, in the same env, found one
and used it. A root reachable by two names is deduplicated to its canonical
one, and the winner is the **re-export** (`quam_config.Quam`) whose module
exports the root but not the transmon classes — so `quam_config.my_quam`, the
module that actually imports them, was never scanned. The probe now looks in
the class's own **defining** module as well as the home it was reached through.
A capability report wrong in the *safe* direction is still wrong: the wizard
would have promised a degrade that never came, and the two halves of one
feature would have disagreed on screen. Pinned by
`TestTheProbeLooksWhereTheClassIsDEFINED` (mutation-checked), which builds a
root re-exported from one module and defined in another.

## What is pinned

`tests/test_qdac_component.py` — the vocabulary (including a **bias-tee**
fixture, the shape the customer's env cannot build yet, and a lab-named
variant), the trigger cabling and its conflict case, the column bands, the
chip parity, the delay, the inspector, `/flux`, the class migration, the QPU
root gate, and §13–§17: the bias-tee validation both ways, the synthetic
capability, the read-back, the emitted recipe, the trigger pulses, the wiring
map, the export columns, the click overrides, the autofit re-route, the config
slice, the compare instrument, the alarm counting, the import-exception
specificity and the regen graft gate. **Every one of those fourteen groups was
mutation-checked — the fix reverted, the group shown to fail, the fix
restored.**

`tests/instrument_qdac_selfcheck.cjs` (20 assertions, **7/7 mutations caught**)
drives the shipped `renderInstrumentWiring` + `_showPortPopup`.
`tests/generate_fluxsource_selfcheck.cjs` (**14/14 mutations caught**) drives
the real wizard under jsdom: the derived chip-level source, the fixed-frequency
case and its relabelled first option, the bias tee keeping its flux line, the
per-qubit picker, `prunePopulate`, the cabling table (round-robin, one cable per
ext, a carried pin as the cable and never withdrawn), the Populate bucket that
refuses to create an entry, and the short-keyed pair preset capture (§18). The duplicate-header and delay
pins were mutation-checked individually.

## Still open

WS0–WS9 are shipped. What remains needs something this machine does not have,
or is a judgement someone else has to make:

**No LAB has a bias-tee class yet.** §19 built one for real in a patched copy
of the customer's `quam_config`, so the success branch is measured rather than
assumed — but until a lab adds those ten lines to their own tree, the shape
exists only in this campaign's scratch directory. Nothing needs to change in SM
for it: the read side already handles whatever field name they choose, and the
probe now reports it.

**`output_range` is a CURRENT range**, not a voltage range (±200 nA / ±10 mA —
`qdac2-driver/qdac_2_driver/channel.py:454`). A "is `dc_offset` inside
`output_range`" diagnostic was written and then **deleted** when the driver was
read: it bounds no voltage, and the check would have been a confident lie.
There is no SM-side bound on `dc_offset` today, and inventing one would need a
number nobody has measured.

**The QDAC is never talked to.** Everything here is state-file work.
`qdac2-driver` is the hardware VISA driver and SM does not import it; whether a
channel is actually at the `dc_offset` the chip claims is not a question SM can
answer, and no surface implies otherwise.

## The measured baseline (CLAUDE.md's is stale)

Full suite in `cqt` at the end of the docs/136 work: **19 failed, 6261 passed,
247 skipped** in 22:27 (`--deselect tests/test_main.py::TestWaitForServer`) — the
same nineteen, name for name, that failed before any of this work started. After
docs/137: **18 failed, 6303 passed**, a strict SUBSET of the same nineteen — the
one that dropped out is `test_safe_io::test_reader_survives_concurrent_os_replace`,
a Windows file-locking timing flake that fails again when run alone. Not a fix,
not a regression: a flake that happened to land the other way.

The same 19 fail on a **pristine worktree at `main`** (92ac48f) — verified
test-by-test, not inferred, so this campaign contributes **zero regressions**.
CLAUDE.md documents 14; the real number at this commit on this machine is 19.
Eleven are the docs/87 OS-behaviour class (loopback/WSL kernel probe, inode
and case identity under `tmp_path`, Windows file-locking timing). The other
eight are ordinary stale pins nobody has swept:

- `test_auto_apply.py` ×3 — the pill was renamed "Auto-apply" → "Auto-Sync"
  in docs/120 item 8; the tests still assert the old label.
- `test_auto_sync.py::test_no_new_poller_was_added` — a distance-based grep
  over `app.js` (`/state/drift` within 4000 chars) that ordinary growth of
  the file trips.
- `test_capabilities_routes.py` ×2 — both die on `{"error": "Output folder:
  not an absolute path"}`, an output-path check that fires BEFORE any
  capability assessment, so neither reaches the code §12/§13 changed. Read
  back deliberately, because a `KeyError: 'capability_blockers'` in a campaign
  that touched both build routes is exactly the failure one must not wave
  through on the strength of a matching count.
- `test_compare_hub_routes.py` ×1, `test_runner_spectral_floor.py[ramsey]` ×1.

Re-derive rather than trusting either number: this is exactly what the
CLAUDE.md instruction to re-derive the baseline is for.
