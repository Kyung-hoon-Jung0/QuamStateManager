# 98 — Every pulse class, generated and loadable: the SS-branch all-pulse verification

*2026-08-10, part of the 1.0-prep campaign. Question asked: does Generate Config +
the add-pulse path produce EVERY 1Q and 2Q pulse the current customer SS-branch
stack supports, such that the chip still `Quam.load()`s in that env and every key
reaches Live State Edit? Answer: **yes — after two real defects this campaign
found and fixed.** Both were reachable from the UI and both made the whole
state.json unloadable in the very env the user selected.*

## The harness

The docs/95 3×2 tunable-coupler chip (6 qubits / 7 couplers / 2 feedlines),
rebuilt fresh with the **env-head** SS-branch env (quam-builder git HEAD
`71fffe7f` — verified still upstream HEAD at run time), then driven in-process
(Flask test client) through the REAL routes:

1. **Build** → `FluxTunableQuam`, 7 pairs; op inventory recorded: per qubit
   `x180/±x90/±y90/y180` (DragCosine + alias pointers), `saturation`,
   `readout`/`readout_GEF`, z `const`; per pair the four CZ variants'
   qubit-flux + coupler-flux pulses (`SquarePulse`/`_FlatTopGaussianPulse`/
   `_CosineBipolarPulse`/`SNZPulse`); `flattop_erf` skipped with its honest
   per-pair warning (class absent from these builder revisions — docs/95).
2. **Roster** — select the env, probe: 22 classes, 16 creatable specs;
   `ErfSquarePulse` correctly refused by the never-silent env gate (docs/71).
3. **Create ALL of them** — one pulse of every roster-importable class (15)
   through `/api/pulse/create` (readout classes on the resonator channel, the
   rest on xy), PLUS the real pair case: filling the built `cz_SNZ` macro's
   deliberately-empty `coupler_flux_pulse` slot with an `SNZPulse`.
4. **Gates** — `/config/regenerate` = real in-env `Quam.load()` +
   `generate_config()` → 200; `/bulk/all-values` completeness (0 missing leaves
   vs an offline flatten); every created op listed on `/pulses`; edit
   round-trip on 4 of the new pulses (incl. the pair-slot one) → save →
   apply-to-live → regenerate again → 200; finally the **pinned** builder rev
   (`b2056cd9`, env-pinned) loads the finished chip too: 25 elements / 132
   pulses.

## Defect 1 — the legacy-home `__class__` (chip_qclass prefix branch)

On a fresh quam-0.6.0 chip the majority module prefix of the chip's own pulses
is `quam.components.pulses.` (SquarePulse & co. still live there). For a class
with no on-chip evidence, `chip_qclass`'s prefix branch accepted that prefix
because `candidate in _BY_QCLASS` — but `_BY_QCLASS` transcribes ONE stack
generation, and the modern stack moved `GaussianFilteredSquarePulse` (and
friends) out of `quam.components.pulses`. The write produced a `__class__` the
selected env cannot import → **the whole state stopped loading**, and since r15
made the form's derived class read-only, the user could not even correct it.

Fix: with an env roster ACTIVE and the roster KNOWING the class, the env's own
`homes` list is the only acceptable verification for a prefix-derived write; a
rejected prefix falls through to the env-canonical branch (docs/71's doctrine,
now actually enforced). No roster / unknown class ⇒ byte-identical legacy
behavior (pinned).

## Defect 2 — field drift (`post_zero_padding_length` → `padding_length`)

With the home fixed, `Quam.load` still failed: the static catalog's field set
for `GaussianFilteredSquarePulse` writes `post_zero_padding_length`, which the
current stack renamed to `padding_length` (docs/53). quam treats an unknown
attribute as a hard load failure — the docs/56 doctrine ("an invented key is a
`Quam.load()` crash") applies to pulse *creation* too.

Fix: `pulse_catalog.env_field_filter` — with a roster active and the class's
field dump available, any template field the env's model does not know is
dropped BEFORE the write, and the create response says so
(`Created … — omitted post_zero_padding_length (not in the selected
environment's … model)`). No roster / unprobed fields ⇒ no-op, never a guess.

## Defect 3 — None-slot creation left the new keys unsearchable

Filling an explicit-null gate slot (`"coupler_flux_pulse": null`, exactly what
the builder emits for `cz_SNZ`) goes through `set_value`, which indexed only
the slot path itself — the new pulse's own leaves were invisible to search and
later edits logged `update_entry: … not found in index`. The create path now
mirrors `create_subtree`'s per-leaf indexing for that branch.

## Order of discovery (why the driver saw it and a demo would not)

`/config/regenerate` reads the working-copy FILES, and creates live in memory
until `/save` — so the first regenerate after creation trivially passes and the
failure only appears after save/apply. The campaign's gate sequence
(create → regen → edit → save → apply → **regen again** → cross-env load) is
what surfaced both defects; it is the sequence a real user performs across a
session boundary.

## Pinned

`tests/test_pulse_env_canonical_home.py` — env-canonical beats legacy prefix;
roster-verified prefix still wins; roster-unknown + no-overlay stay
byte-identical; reused chip evidence stays first; the field filter drops
unknown fields (and only with a probed field dump); None-slot creation indexes
its leaves. Full pulse suite (266 tests) green. Artifacts:
`D:\work\sm-verify-allpulse\AS\ap_report.json` + screenshots under
`D:\work\sm-screenshots\2026-08-10_1.0-prep\` (all created classes with their
synth sparklines on /pulses).
