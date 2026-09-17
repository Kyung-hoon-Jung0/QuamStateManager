# 198 — Pressing the write paths: type-fix, Apply to chip, Revert last apply

Date: 2026-09-17/18. Continuing the stress round on the real customer chip
`260907_KRS_5Q` under the standing directive to press every feature, writing
freely. The chip starts and ends this round at **0 differences from pristine**.

---

## 1. Diagnostics, and a repair path that had nothing to repair

The page is healthy on this chip and says so honestly — three real findings:

```
Warning  qubits.q2.resonator.f_01  readout f_01 (6.59021 GHz) and
                                   RF_frequency (6.59071 GHz) differ by -500 kHz.
Warning  qubits.q4.resonator.f_01  ... differ by +100 kHz.
Warning  pulses.const_pulse        pulse is defined but never referenced by any element
```

and `/type-fix/plan` answering *"Nothing to fix — every numeric value on this
chip is stored as a number."*

Which means **the repair path had never been pressed here**. A green verdict is
not evidence that the machinery behind it works, so the condition it exists for
was created deliberately.

## 2. The type-fix repair, end to end on the real chip

Added `qubits.q1.stress_numstr = "1234.5"` — a number stored as TEXT — through
the Json Tree's own ＋, choosing type `str`, and applied it. Deliberately NOT
under `extras`, which docs/81 makes free-form so the alarm correctly ignores it.

Every link in the chain then fired:

| step | what happened |
|---|---|
| `/type-fix/plan` | proposed `qubits.q1.stress_numstr  "1234.5" → 1234.5  real` |
| the alarm | a **`Fix 1 value…`** button appeared |
| Diagnostics card | *"⚠ 1 value stored as TEXT but reads like a number (e.g. qubits.q1.stress_numstr) — usually an external state regeneration."* |
| the dialog | `Convert 1 field(s)` / `Cancel` |
| pressing Convert | toast *"1 value now stored as numbers — review in the tray, then Save / Apply."* |
| **on disk right then** | still `'1234.5'` **(str)** — it STAGED; live untouched, as docs/77 promises |
| after Apply | `1234.5` **(float)** |
| ✕ delete + Apply | key gone, chip back to 0 differences |

Zero console complaints throughout.

## 3. Apply to chip, with both conditions it claims to ignore

docs/126 ⑤ makes this button a deliberate exception: it applies over pending
edits **and** live drift without asking, naming what happened. So it was pressed
with both present — a pending edit on `q1.anharmonicity` in the working copy,
and an outside write to `q2.anharmonicity` on the live chip.

It asked nothing (`window.confirm` recorded no calls), and the result line named
both:

> Run #41's state is now LIVE on 260907_KRS_5Q. The live chip HAD changed since
> it was loaded — those changes were overwritten (the run's state wins).
> **(Replaced 1 unsaved edit.)** Reversible — …

That is the contract, kept exactly.

## 4. The defect: a promise the button does not keep

The same result line ended:

> Reversible — ↺ Revert last apply (top bar) **restores the pre-apply state.**

It does not. The button **stages** the pre-apply state into the working copy;
the chip moves on the *following* Apply — the docs/107 covenant, one explicit
act per live write. The tray's own tooltip has always said this precisely
("stages the pre-apply state for review; nothing touches the live chip until you
Apply"); the line a user reads *immediately after the write* did not.

The consequence is not hypothetical. Driving this path, the press was made, the
chip did not move, and it was recorded as a dead button. It took reading the
tray's tooltip to establish that the button had worked perfectly: the tray said
**"Working state · not applied"**, and a following Apply took the chip from
28,929 differences to exactly 1 — the single leaf that *was* the pre-apply live
state. The revert was correct to the letter and the sentence describing it was
not. docs/120: a press means what the presser could see.

Both spellings now read:

> Reversible — ↺ Revert last apply (top bar) **stages** the pre-apply state;
> **Apply puts it back on the chip.**

## 5. Pins

`TestTheResultLineSaysWhatTheButtonDoes` in `tests/test_dataset_apply_to_chip.py`
(4). **Mutation sweep 5/5 red — after two GREENs with one cause**, and it is a
cause worth recording: the apply response is `msg + _tray_oob()`, and the tray's
own tooltip contains both *"Revert last apply"* and *"stages the pre-apply
state"*. Assertions over the whole response body were therefore reading the
**tray** and passed with the message under test gutted. The pins now slice the
response at `id="pending-tray"` and assert on the message alone.

Verified in real Chrome: the button's tooltip reads *"Reversible — ↺ Revert last
apply stages the pre-apply state, and Apply puts it back on the chip."*, chip at
0 differences before and after.

## 6. Measurement errors this round (running tally: 12)

Same root cause as every previous one — probing before reading:

- three guessed selectors for a sidebar run row before reading the DOM; it is a
  `SPAN.tree-entry-click`, not an anchor, and carries no `#NN` in its innerText.
- the heredoc backslash trap again, on a patch script, despite a memory note
  saying to use a file for backslash content. It made an anchor silently fail to
  match, and the "fixed" script re-ran unchanged.
- **and the one that matters**: reading the CHIP FILE to judge a button that
  stages by design, then recording it as broken. That misreading is what found
  the real defect, but it was luck rather than method — the method is to read
  what the surface itself claims before deciding it lied.

---

## 7. Auto-apply (the push half of docs/117), pressed on the real chip

**The gate refuses honestly.** The first arm came back **409**, and
`/auto-apply/gate` said why in the user's own words:

> `armable: false — "The live chip changed outside SM — resolve that first."`

Correct, and caused by my own file-level restore earlier in the round: writing
`state.json` from a script *is* an outside write, so SM flagged drift like it
would for any experiment. docs/117 refuses to arm on a diverged chip, and it
did. Dropping the pending edit and taking live cleared it (`armable: true`).

**Armed, an edit reaches the chip with no further press.** Pill reads
`⚡ Auto-Sync push ⏻`, tray carries `data-auto-apply="1"`, and
`qubits.q1.anharmonicity` went from 200,607,661.78676715 to the typed value on
disk **within ~4 s** of pressing Enter — nothing else touched. The applied log
recorded it with a Δ:

```
qubits.q1.anharmonicity  200,607,661.78676715 → 203,000,000.0  +2,392,338.21323285
```

**The per-entry ✕ flushes too.** Its tooltip states the CAS contract ("Compare-
and-swap: if the value moved since, this refuses instead of overwriting"), and
the clean run shows `POST /auto-apply/revert` followed immediately by
`POST /state/apply-to-live`, with the chip moving back **by itself in under 3 s**.

### An unreproduced observation, recorded rather than rounded away

One earlier run of that same ✕ staged (tray → 1, toast *"Reverted"*) and the
chip did **not** move while the pill still read armed. Three later attempts did
not reproduce it, and the one clean measurement flushed correctly, so it is NOT
reported as a defect. It is written down because the shape matters: if a revert
can stage while the session claims to be armed, the user reads an armed pill, a
success toast, and a chip that still holds the old value. If it resurfaces, the
place to look is `auto-apply.js`'s `_stopped` latch (docs/187 R-series), which
is released only by a tray rendering *without* the armed attribute — and a
disarm posted by `fetch` rather than htmx swaps no tray at all, which is exactly
what the failing run had done a step earlier.

Two of my own probe errors along the way, both worth naming because they produce
confident-looking nonsense:

- the applied log is **collapsible**, so filtering buttons on visible width
  reports "no ✕" for a control that is simply folded away;
- the forward edit's own flush re-renders the log, so a click measured before
  the swap lands on empty space — no toast, no request, and a reading of "the
  button does nothing". The fix is to confirm the press produced a request
  before believing anything it appears to show.

## 8. The compare-and-swap refusal, reached and read

The applied log's ✕ promises: *"Compare-and-swap: if the value moved since, this
refuses instead of overwriting."* Reached deliberately — apply A, apply B to the
same field, then press A's ✕, whose anchor is now stale — the answer is a model
refusal:

```
POST /auto-apply/revert -> 409
toast: "Not reverted — qubits.q1.anharmonicity has changed since
        (now 2.070000e+08, this change wrote 2.060000e+08). Nothing was written."
chip: unchanged
```

It names the path, the value now, the value this entry wrote, and states
explicitly that nothing was written. Three console lines accompany it, which is
what an HTTP 409 costs and is not a defect.

**This took four attempts to establish, and the three failures were all mine.**
Twice the press never landed (no request at all) and once the toast hook was
installed after the press rather than before — each producing a confident-looking
"the button says nothing". The method that settled it: assert the request fired
and read its status BEFORE judging the interface, and hook the reporting channel
before the action rather than after. A silent-failure claim needs the press
proved first.

## 9. The Generate Config wizard — surveyed, nothing to fix

Eight steps (Environment · Network · Chassis · Qubits · Wiring · Populate ·
Output · Review). Two claims were tested rather than assumed:

- **the env probe is honest.** Ten interpreters are listed with a real verdict
  each — `miniconda3 … ✗ missing: qualang_tools, quam_builder, quam` beside
  `KRISS_CZ … ✓ qualang_tools 0.22.0 · quam_builder 0.4.0 · quam 0.6.0`. (An
  early reading that the customer's own env showed "✗ missing" was mine: the
  selector had matched the *first row's* status span, not the chosen env's.)
- **a refusal is visible from the button that caused it.** Pressing Next with an
  empty Network step answers `"Enter the QOP host IP."` in `#gen-message`. That
  is the element docs/134 ② once called out as out-of-view for a sibling button,
  so it was measured at a 900 px viewport: the message renders at y 825–858 and
  the Next button sits at 900 — directly beneath it, in view. No repeat of that
  defect here.

A wizard walk that pressed Next seven times without advancing looked like a
silent stall and was not: the step was refusing, in `#gen-message`, and the walk
simply never read that element.

## 10. Re-generate: the reconstruction is correct on this chip

The highest-value remaining check, because it is chip-SPECIFIC: docs/118 found
that a modern quam_builder chip's pair reference is a **two-hop** pointer, and
reading it as one hop silently dropped every pair — losing 59% of pair
calibration on a real 10Q chip while the build still reported SUCCESS.

The KRISS chip is a good adversary for that bug, because its pair section looks
empty from three directions at once:

```
state.qubit_pairs            q1-2, q2-3, q3-4, q4-5     (4 pairs)
active_qubit_pair_names      []                          (none active)
wiring.wiring                qubits, twpas only          (NO qubit_pairs)
```

`reconstruct_spec(state, wiring)` nevertheless returns **all four**, resolved to
their `(control, target)` membership:

```
qubits       5
qubit_pairs  [["q1","q2"], ["q2","q3"], ["q3","q4"], ["q4","q5"]]
lines        16
notes / info_notes / warnings   (none)
```

and the silence is correct, which is the part worth checking rather than
assuming. The pairs carry `coupler: null`, `cross_resonance: null`,
`zz_drive: null` — this chip genuinely has **no pair hardware**; its CZ moves
the control qubit's own flux (`moving_qubit: "control"`, the macros driving
`flux_pulse_qubit`). And `wiring.qubits.{q1..q5} × {rr, xy, z}` = 15, plus one
TWPA line = **exactly the 16 lines** reconstruct produced. There is no missing
coupler to report, so reporting none is right — and docs/134's rule ("derive
optional lines only where the SOURCE chip had them") is what makes a rebuild
from this spec reproduce the chip rather than invent couplers for it.

## 11. Compare hub

Renders its honest empty state — *"0 sources · No sources yet — add two or more
below. The same chip can be added several times at different points in time."* —
with the three source pickers (workspace / history / recent) and `+ Current chip
live`. No complaints.

## 12. What three clean rounds mean

Sections 7–11 found **no product defects**. That is worth stating plainly rather
than padding: every surface in them has had a dedicated round already
(docs/117 + 187 + 195 for auto-sync, docs/134–136 + 176 for the generate
wizard, docs/118 for the pair pointers), and they are holding under a deliberate
attempt to break them on a real chip.

The errors in these rounds were mine, and they cluster into one shape worth
carrying forward: **four of them produced a confident "this control does
nothing"** when the control was working — a press that never landed, a message
read from the wrong element, a reporting hook installed after the action, a chip
file consulted about a button that stages. The discipline that settles it is
cheap: prove the request fired and read what the surface itself claims, before
concluding it lied.

---

## 13. The menus that had never been pressed

Answering "what have we actually covered" against the REAL sidebar rather than
memory turned up four menus and a whole sub-nav that no round had touched.
All were driven on the customer chip; **no defects**.

| menu | what it showed |
|---|---|
| **Chip Components → Qubits** | 5 rows × 9 cols, **0 blank cells** |
| **→ Pairs** | 4 rows × 8 cols, 0 blank — columns *adapted to this chip*: `MOVING` (its `moving_qubit`), `CZ FLATTOP AMP`, `DETUNING`, and no coupler columns |
| **→ Resonators** | 5 rows × 9 cols, 0 blank |
| **→ Flux** | 5 rows × 7 cols, 0 blank |
| **Instrument Wiring** | the real rack SVG — con1 OPX1000, FEM 3 (mw-fem) + FEM 5 (lf-fem), q1–q5 + twpa1 |
| **Calibration log** | real journal entries, e.g. *largest Δ `qubits.q4.freq_vs_flux_01_quad_term` 26094182288.42811 → 0.0* |
| **Projects** | 34 QUAlibrate projects, 68 rows, read-only notice |
| **Help** | the guide |
| **Chip Status × 9 sections** | all render and genuinely differ (7/32/26 sections, 1/88/40 SVGs) |

**Couplers is correctly absent from the nav.** Every pair on this chip has
`coupler: null`, and the page gates itself chip-wide on `has_coupler` — so the
nav omitting it is the designed behaviour, not a missing menu.

**A ✓ that looked wrong and is not.** The Pairs page marks all four pairs
ACTIVE while `active_qubit_pair_names` is `[]`. That is QUAM's own semantics,
stated at `query.py:396` and applied identically to qubits at `:131`:
*absent/empty = all active*. Checked rather than assumed, precisely because it
looked like a lie about chip state.

**A warning worth passing on, not a defect.** Projects shows
*"Versions: qualibrate v6 / quam v3 ⚠ unsupported"*. It is explained on hover
("differs from the supported v5/v3 — SM stays read-only") and
`SUPPORTED_QUALIBRATE_VERSION = 5` while this machine runs **v6**. Reading works
fine on v6 (34 projects listed, active project resolved), and docs/55 keeps the
tree read-only for every version anyway — but the version gap is a real fact the
lab should know.

**Two probes reported "no sub-tabs" before this, and both were wrong the same
way**: `/chip-status` is a **404** — a url I invented. The real one is
`/topology?view=<section>`, which the sidebar's own hrefs say plainly. The
page Flask returned for a made-up url was Not Found, and measuring *that* is
how a rich nine-section page reads as empty. Read the href; do not guess the
route.
