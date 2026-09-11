# docs/176 — The chip's root class, and three things the review would not say

2026-09-11. Found while answering a direct question about a real chip: can a
user generate **KRISS 5Q** (`260907_KRS_5Q`) from SM's Generate wizard, in the
environment that lab actually runs?

The chip was copied out of `D:\work\Customer_Codes\quam_states\260907_KRS_5Q`
and never touched. The environment is the existing conda env `KRISS_CZ`, which
imports the lab's own `quam_config`. Everything below was measured in real
headless Chrome against a rig serving that env.

## 0. The answer to the question

**No — not as it stands, and the wizard now says so instead of promising
otherwise.** The chip's `state.json` names five classes that live in the lab's
own `quam_config`, and the wizard had no way to say "root this chip at
`quam_config.my_quam.Quam`". SM *can* build the non-QDAC variant of it
(verified: the built chip loads and `generate_config()` yields 15 elements) —
but it rooted it at the stock `FluxTunableQuam`, so the lab's own fields and
overrides were simply absent from the result.

That is the fourth defect below. The first three are what the wizard did on the
way to telling me.

## 1. A sentence rendered through an object template

`/generate/build` answers a refusal in `capability_blockers`. **Two shapes
arrive under that one key**: `capabilities.assess` sends objects
`{label, package, symbol, fix}`, and `capabilities.qpu_root_check` sends one
finished sentence. The page rendered every entry through the object template, so
the root refusal — a carefully worded paragraph naming exactly what to add to
your `quam_config` — reached the user as:

```
• undefined — needs ? · (missing). Fix:
```

The only text that said what was wrong was thrown away by the renderer that was
supposed to show it. `showBuildResult` now branches on `typeof b === "string"`
first, and the object shape is untouched.

## 2. The verdict contradicted the list underneath it

`report.buildable` means **nothing BLOCKS the build**. The page rendered it as
"✓ This environment can build everything this chip needs." — directly above a
list headed "Will be skipped / downgraded", one of whose entries loses a
qubit's flux component entirely. Two adjacent lines of the same panel, in
disagreement.

The verdict now says what is true: `✓ Nothing blocks the build — but N things
will be skipped:`. With nothing skipped, "everything" is honest and stays.

## 3. The two doors read two different manifests

`/generate/capabilities` (the review) built its manifest from `capabilities` +
`versions`. `qpu_root_check` reads `qpu_roots`. With the key absent it saw no
roots at all, and **a check that sees no candidates cannot refuse** — so the
review answered "can build everything" for a spec that `/generate/build`, which
does pass `qpu_roots`, rejected outright with a 400.

Both doors read the same manifest now, and the review carries `root` (the
check's own verdict) and `roots` (every importable root this env offers).

Measured on the KRISS env with a QDAC-biased spec, after the fix:

```
status   400
error    This environment can't root a chip with QDAC-biased qubits.
blockers ["No QPU root class in this environment can hold QDAC-biased qubits. …"]
files written: none
```

— and the review now refuses it for the same reason, before anything is
written. The refusal is honest, not a gap: the KRISS lab's own
`quam_config.my_quam.Quam` genuinely declares no `qdac` field. That lab does not
run a QDAC; a lab that does adds the subclass docs/136 describes and it is
offered here.

## 4. Nothing could name the root class

The root decides what the chip can CONTAIN and which fields it carries. `docs/136`
gave the *build* a root check, and `regen_spec` carries the source chip's own
`__class__` on a re-generate — but a **fresh** build derived a stock root from
the line types and there was no way to say otherwise. Every lab that drives this
wizard has its own `Quam` subclass; the probe lists it first; the build wrote
the stock one.

Step "Review" now carries a **Chip root class** select, built from the probed
roots, each option stating what its `qubits` field can hold. `spec.quam_class`
was already honoured end to end (`run_build.py`, `script_emitter`,
`qpu_root_check`) — the wizard simply never set it.

**"Automatic" is the default and is byte-for-byte the old behaviour**: the key
reaches the spec only when a person picks one, and picking Automatic again
*deletes* it rather than writing `""`.

## 5. Verification

- `tests/stress_generate_root.cjs` — real headless Chrome against the KRISS_CZ
  rig: 12/12, zero console errors.
- `tests/generate_root_selfcheck.cjs` — 21 assertions, executing the shipped
  renderers under jsdom rather than grepping them.
- `tests/test_generate_root_class.py` — 9, the route half.

### Two traps this round re-paid

**A check that passes for the wrong reason.** My first driver posted `out_dir`
to `/generate/build`. That is not the key the route reads — it is `output_path`
— so the 400 I recorded as "the build refuses the root" was really *"No output
folder given."* The check was green and proved nothing. It now posts
`output_path` pointed at a real empty folder and asserts both the reason and
that **nothing was written** before the refusal.

**A mock that answers everything is a noisier harness, not a quieter one.** The
selfcheck's first `fetch` stub returned the capability body for every URL; the
wizard's env handlers read that body too and cleared `state.env`, so the second
render bailed at its first line with an empty box and no error anywhere. The
stub routes by URL now. Related: jsdom fires `DOMContentLoaded`
asynchronously and the wizard's `init()` resets the draft on it — the other
generate harnesses are synchronous and finish before it ever runs, this one
awaits, so it lets `init()` go first.
