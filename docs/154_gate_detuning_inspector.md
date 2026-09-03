# 154 — 2Q gate detuning inspector, and the review that had to precede it

PR #5 (`2q-gate-detuning-inspector`, paulQM) adds a section to the pair detail
inspector that plots the three bare-state detunings relevant to a flux CZ as a
function of the moving qubit's z-line voltage, with click-to-stage and a
"switch moving qubit" rewire.

The idea is right and the plumbing (route, template, Plotly mount, click
contract, review-tray staging) follows the house patterns. The **physics
conventions and two API names were not**, and because the feature shipped with
no route test and fixtures invented to match the code, the suite was green
while the headline action returned 500 on every press.

This document records what was wrong, how each was reproduced, and the two
conventions the module now states in its own docstring so the next person does
not have to re-derive them.

## 1. The two conventions (load-bearing — do not "simplify" them)

### Anharmonicity is stored as a POSITIVE magnitude

Every chip in the corpus stores it positive — the CQT 20Q chip carries
135–229 MHz on all twenty qubits — and the lab's own CZ helper says so:

> `calibration_utils/chevron_cz/cz_branch.py`
> "(anharmonicity A is stored as a positive magnitude in this state.)"
> `"20"`: `detuning = f_control - f_target - A_control`
> `"02"`: `detuning = f_control - f_target + A_target`

`chip_health` agrees (`f₀₁−f₁₂ gives the anharmonicity`). So `f_12 = f_01 - A`,
the second excited level is `2*f_01 - A`, and

```
E|11> = f_c + f_t      E|20> = 2*f_c - A_c      E|02> = 2*f_t - A_t

D(11-20) = f_t - f_c + A_c        zero at  f_c = f_t + A_c
D(11-02) = f_c - f_t + A_t        zero at  f_c = f_t - A_t
D(10-01) = f_c - f_t              zero at  f_c = f_t
```

Those two zeros are exactly `cz_branch`'s two branch conditions. The original
code wrote `f_t - f_c - A_c` and `f_c - f_t - A_t`, i.e. the anharmonicity on
the wrong side, putting each interaction point **2·A ≈ 400–460 MHz** from where
it is — on a plot the user can click to stage a real z voltage.

### `z.flux_point` is a MODE STRING, not a voltage

It is `"joint"` / `"independent"` and names which stored offset the qubit idles
at; the voltage lives in `z.joint_offset` / `z.independent_offset`.
`autofit/families.py` routes on the same field the same way
(`route_when="independent"` → independent offset, `route_when="*"` → joint).

The original code did `float(flux_point)`, got `None`, and fell into a branch
that set the parabola's vertex **and** the operating point to `0 V` while
`joint_offset` — the real bias, read one line earlier — was discarded. Measured
on the real CQT chip, pair `q1-2`, moving qubit `q2`:

| | before | truth |
|---|---|---|
| operating point drawn at | `0.0000 V` | `0.0627 V` |
| parabola vertex | `0 V` | `0.0627 V` |
| interaction markers | none | none (see §2) |
| x-range | `±0.01 V` | centred on the bias |

The model is anchored at the idle bias, where the stored `f_01` was measured:

```
f_01(V) = f_01_idle + quad_term * (V - V_idle)^2
```

which is the inverse of `cz_branch`'s `amp = sqrt(-detuning / quad)`.

## 2. Honest degradation

Three states the original silently blurred, each now stated in the figure's
`notes` (rendered above the plot):

* **No `quad_term`** — x becomes a frequency axis and says so. It used to keep
  a voltage axis and hold it *constant*, so all 500 points shared one x: a
  single vertical line billed as a sweep.
* **A curvature with no offset to anchor it** — same frequency axis, different
  sentence. Click-to-stage is off in both (there is no voltage to write).
* **A crossing on the side flux cannot reach** — the parabola only bends one
  way from the idle point. On the real CQT chip `q2` has *positive* curvature
  (the "lower sweet spot" case node 09 warns about) and all three crossings lie
  below, so none is reachable. That is the honest answer; what was wrong was
  answering it with an empty marker list and a ±10 mV window around the wrong
  centre. The window now spans the voltage that *would* reach the furthest
  crossing, so the plot keeps a physical scale.

## 3. The two crashes

Both reproduced against the real CQT chip through the real routes:

1. **`_chip_token` does not exist** — the helper is `_active_chip_token`. The
   call sat outside the `try`, so `POST …/switch-moving` raised `NameError` →
   500. `app.js` always sends `expect_chip`, so the button was dead 100% of the
   time.
2. **`Modifier.delete_value` does not exist** (`delete_subtree` does), and
   `Modifier.set_value` has no `create=` keyword (`create_subtree` is the door
   for a new key). Even with the token bypassed the route answered
   `500 … 'Modifier' object has no attribute 'delete_value'`, and because the
   deletes run before the edits, nothing at all was staged.

The rewire is now one `group_id`, so the review tray shows one gesture and one
Ctrl+Z takes all of it back — never half a moved pulse.

Also tightened: `_matches_gate` is `<gate>` or `<gate>_pulse` (the naming every
real chip uses) instead of `startswith`, which would drag `cz_flattop_2_pulse`
along when moving `cz_flattop` and break a gate nobody asked about; a pulse
already present on the destination is never overwritten; and when the
destination has no `z.operations` dict at all it is created whole, since
`create_subtree` needs an existing parent.

## 4. Why the suite was green

`tests/test_gate_inspector.py` had 14 tests and **no route test**. Its fixtures
used `anharmonicity=-250e6` (negative) and `flux_point=0.0` (a float) — a
convention no chip in the corpus uses — so the sign test asserted the code's own
mistake back at it. `plan_switch_moving_qubit` was imported and never called.

The rewritten file is 29 tests: the conventions pinned against `cz_branch`'s
branch conditions, the curves read at the marker voltages (not a restatement of
the solver), the three degradation states, the planner, and the routes —
including the exact token the browser sends. **11 of 11 mutations caught.**

One CI failure was the PR's only *new* one against its own base:
`TestThemeContrastGuard::test_no_ghost_button_uses_pico_color_for_text`. It was
right — `.gi-switch-btn` is a transparent `<button>` with
`color: var(--pico-color)`, which Pico v2 rescopes to `--pico-primary-inverse`
(white in both themes) on buttons, so its label was invisible on the light page.
`--pico-contrast` is the readable one. The guard exists because this exact bug
has shipped before; it earned its keep here.

## 5. Not changed

The JS side is sound: `_plotlyRender`, `_attachInteractivePlotClickHandler` and
`_openPlotApplyPopup(updates, expName, qubitName, contextRows, chipExpect)` all
exist with matching signatures, and the click stages through the ordinary popup
with the chip-identity gate. `init()` gained a `data-gi-wired` guard because it
is called from `DOMContentLoaded`, from `htmx:afterSettle` and after a switch.
