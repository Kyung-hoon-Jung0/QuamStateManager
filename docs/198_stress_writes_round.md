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
