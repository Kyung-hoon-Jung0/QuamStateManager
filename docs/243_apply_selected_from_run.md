# docs/243: Apply selected — take only part of a run's state into the chip

2026-10-02. A customer wanted to bring PART of a run's quam_state into the open
chip, for example a few qubits' exponential-filter taps. The State tab offered
two things:
- **Apply to chip:** the whole snapshot.
- **Click a value to copy it:** one scalar at a time, because the snapshot is
  read-only on purpose.

They also asked that edits stay guarded: State History, Trends and every run
record read these snapshots.

## Behaviour

- **Tick boxes on the run's state.json and wiring.json trees.** They use the
  sidebar run box's language: square, soft SM blue, solid blue when ticked,
  and a "−" when only part of a container is ticked. The faint tick shows on
  hover only; an always-on ghost tick read as "ticked" in a dense tree
  (real-Chrome review).
  - **Ticking a container ticks everything under it**, e.g. `qubits.qA4` or
    an `exponential_filter`.
  - **Unticking one child keeps every sibling**, at every level on the way.
  - **The selection is a set of paths over the tree's own data**, so branches
    never expanded are included, and rows rendered later (lazy expand) arrive
    with the right state.
  - **A tick never expands, collapses or copies the row.**
- **The bar above the tree:**

  | Control | What it does |
  |---|---|
  | **N selected** | the count |
  | **Select matches (k)** | ticks what the search found (the shallowest hits) |
  | **Apply selected to chip** | opens the preview, below |
  | **Copy JSON** | one pick copies its value; several copy `{path: value}` |
  | **Clear** | unticks everything |

- **Preview** (`POST /dataset/<uid>/apply-selected/preview`) flattens the
  selection to leaves. **A list is one leaf**: `exponential_filter` is
  `[[a, tau], ...]` and is never written element by element. Each leaf is
  judged against the open chip's working copy:

  | Status | Meaning |
  |---|---|
  | write | the value differs (exact compare: `100` → `100.0` is a change; NaN = NaN, `differ.compare_equal`) |
  | write → target | the open chip holds a pointer there, so the value lands on what it points at. Other fields may share that target, and the preview says so |
  | new | the field is absent in the open chip but its parent exists; it is created |
  | skip | not written, with the reason: a reference (pointer) that differs, a read-only field, or a parent the open chip lacks or holds as a reference |
  | equal | hidden by default ("Show N already equal") |

  The preview names the open chip. A run whose chip does not match the open
  one (by fingerprint), or cannot be verified, needs an explicit "Apply
  anyway" tick first.
- **Apply** posts ONE `/field/edit-batch`: `group: "new"` and the page's chip
  token, with `create` only for new rows.
  - It is atomic: one failing row writes nothing, and the modal names the
    field and the reason.
  - On success the tray shows "N unapplied edits". The live chip is untouched
    until Apply, and **Ctrl+Z undoes it as one step**.
- **Guarded by construction.** The run's snapshot is never written. The only
  write is into the open chip's working copy, through the same door, gates and
  change log as every other edit. An archive context is refused.

## Verified

- **Real Chrome**, on a copy of the customer chip and a copy of a run whose
  snapshot was changed (two filter ports, `qA4.T1`):
  - Tick `qA4`: all 22 fields ticked, `qubits` shows "−".
  - Untick `T1`: `qA4` shows "−", and `f_01` stays ticked.
  - Search `exponential_filter` → **Select matches (21)**.
  - Tick two ports' filters → **Apply** shows 2 writes with old/new.
  - Confirm: the top bar reads "2 unapplied edits · ↑ Apply 2", and Ctrl+Z
    brings it to 0.
  - The run's state.json is byte-identical afterwards.
  - JS errors: 0 (counted with `b.errors()`).
- **Earlier journeys today printed `b.errors` (the function, not a call).**
  Their "no JS errors" was never measured. Re-measured now:
  - Live-Edit dBm (FSP typed + `sm:wc-moved`), the SNZ target slot, and the
    SNZ mirror row: 0 each.
  - Generate Reset step + readout pool: 0 with a host set. The four 400s seen
    without a host were `/generate/allocate` refusing an empty network, which
    is what the panel reports.
- **Pins.**
  - `tests/ds_pick_selfcheck.cjs`: 33 assertions, P1–P8 (model, tree surface,
    lazy rows, bar, Copy JSON, preview + one-batch apply, different-chip ack,
    failure).
  - `tests/test_ds_pick.py`: 9 tests (list as one leaf, flattening, every
    status with its reason, covered picks once, a real edit-batch landing in
    one group with the snapshot unchanged, bad requests, a different chip
    named, the template wiring).
  - Mutations: 21 of 22 went red on the first sweep. "Loose compare" stayed
    green because the fixture had no 100-vs-100.0 or NaN case; both were added
    and that mutation (and a type-blind variant) now goes red.
