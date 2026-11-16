# 308 — The UI kit (stage 0: the hub)

> **Removed (2026-11-15):** the Gaussian CZ builder (`/pulse/gaussian-cz`, `/api/pulse/gaussian-cz*`, the "Gaussian CZ from cz_flattop" kind of + New pulse, the several-pairs dialog) and the pair page's experimental "+ Add gate" form (`/pair/<name>/gate/new`, `POST /pair/<name>/gate`) are gone. A CZ gate is created only in the CZ gate dialog (+ New pulse -> "CZ gate", the gate list on a pair page, or the CZ plan). What follows about them is history.

> **Contract:** new or changed UI uses only the UI kit. A class with no CSS rule, a new visual
> rule outside `ui-kit.css`, or a raw value in place of a `--ui-*` token fails
> `tests/test_ui_contract.py`.

Stage 0 adds the kit and its sample page. **No existing page changes**; the only visible
difference anywhere is one line at the bottom of Help that links to `/ui-kit`. Later stages move
existing screens onto the kit one part at a time.

## 1. Reading principles

| # | Principle | Shape in QSM | Part |
|---|---|---|---|
| P1 | What it is, at a glance | Document head = eyebrow, title, one-line gist; data head = title + count + actions on one line | head |
| P2 | A paragraph starts with a bold key word | "**Load.** ..." (`<strong>`, 600) | text |
| P3 | Names in the code face | classes, fields, paths, pulses, qubit lines = mono chip | chip |
| P4 | One box per role | warning / danger / note / comparison (table) / result summary (verdict) | notice, callout, verdict, table |
| P5 | A repeated item = one card | only when it repeats (2+) and each item has its own action; head (identifier + badge), body, foot | item |
| P6 | Side information small, muted, in one place | safety sentences = one muted line in the note/foot slot | note |
| P7 | Buttons are QSM buttons | one primary per area + secondary + quiet, right-aligned, same height, never a bare Pico button | actions |
| P8 | Short sentences, action last | a sentence <= ~90 characters; a line of prose <= 85ch | text |
| P9 | Numbers aligned, meaning visible | a count = muted `ui-count`, before/after = the existing delta renderer, a state = dot + a VISIBLE word | verdict, table |

Global rules: colours only from the theme variables (light / dark / qm); a new colour constant is
allowed only in the `ui-kit.css` token block. Warning = `--color-warning-*`, danger =
`--color-error-*`, success = `--color-success-*`; there is no info colour (a note uses the
accent), and success green is used for dots and checks, never as a filled surface. At most one
filled button per area, at most one badge per row or card, no bordered box inside a bordered
box, no `text-transform: uppercase`, screen text >= 11px, muted text >= 4.5:1. Evidence and
lists fold; conclusions and actions stay open. Buttons are `<button>`, groups are
`role=group` with a name.

## 2. Where it lives (the four sources, nothing else defines style)

| Source | File | Content |
|---|---|---|
| Tokens | `web/static/ui-kit.css`, the one `:root` block at the top | aliases of existing variables, or `color-mix()` of them; no theme blocks, nothing existing redeclared |
| Part CSS | `web/static/ui-kit.css`, the rest | selectors that start with `.ui-` only (stage 0) |
| Server templates | `web/templates/_ui.html` | Jinja macros `ui_head`, `ui_notice`, `ui_callout`, `ui_verdict`, `ui_item`, `ui_table`, `ui_actions`, `ui_badge`, `ui_chip`, `ui_count`, `ui_note`, `ui_check`, `ui_segmented`, `ui_disclosure`, `ui_dialog`, `ui_select`, `ui_input`, `ui_field`, `ui_menu`, `ui_toolbar` (§4b), and the dispatcher `ui_render(spec)` |
| Script templates | `web/static/ui.js` | `window.UI.<part>(...)` and `UI.render(spec)` -- the same tag / class / role / text tree as the macro, built with `textContent` |

`ui-kit.css` is linked right after `style.css` in `base.html`, `calc_window.html` and
`chip_report.html` (both the `<link>` and the downloaded-report CSS list). `ui.js` is a page
bundle (docs/141 §4l) -- today only the `ui_kit` page carries it; a screen that starts building
parts in script adds the `uikit` bundle to its own page entry.

A spec is a plain dict, `{"part": "item", "chip": "q1", "badge": {"text": "ready", "tone": "ok"},
"body": [...], "note": "...", "actions": [button, ...]}`; a button is `{label, cls, disabled?,
title?, aria_label?}` where `cls` is an existing button class (`btn-sync primary`,
`btn-sync`, `btn-sync outline`, `btn-sm`, `btn-sm secondary`, ...) or `ui-btn-icon`.
Containers (`item`, `disclosure`, `dialog`) take lists of child specs.

## 3. Tokens

| Token | Definition |
|---|---|
| `--ui-text`, `--ui-text-2`, `--ui-text-3` | `--pico-color` / `--color-text-muted` / muted 70% over the page (disabled only) |
| `--ui-surface-0/1/2` | page / card / `--pico-color` 6% over the page |
| `--ui-border`, `--ui-border-strong` | `--pico-muted-border-color` / `--pico-color` 28% over the page |
| `--ui-accent`, `--ui-accent-fill`, `--ui-on-accent` | `--pico-primary` / `--pico-primary-background` / `--pico-primary-inverse` |
| `--ui-accent-border`, `--ui-accent-bg` | accent 30% / 12% |
| `--ui-{warn,danger,ok}-{text,bg,border}` | `--color-{warning,error,success}-*` |
| `--ui-fs-xs/sm/md/base/lg/xl` | from `--font-size-base` (never rem): base, x15/17, x21/17, x28/17, max(12px, x13/17), max(11px, x12/17) -- 12/13/15/17/21/28 px at M |
| `--ui-fw`, `--ui-fw-normal` | 600 / 400 (the only two weights) |
| `--ui-lh-dense`, `--ui-lh-read`, `--ui-measure` | 1.35 / 1.55 / 85ch |
| `--ui-r-sm/md/lg` | 4 / 6 / 12 px |
| `--ui-gap-1..4` | .25 / .5 / .75 / 1.25 rem |
| `--ui-bw`, `--ui-bw-strip` | 1px border / 3px left strip |
| `--ui-dot`, `--ui-icon-btn` | 8px state dot / 28px icon button |
| `--ui-shadow`, `--ui-dialog-s/m/l`, `--ui-dialog-max-h` | dialog shadow / 560, 760, 920 px / 82vh |
| `--ui-disclosure-marker` | the fold marker |

Sizes: xs = footnotes, table side text, badges (the minimum) · sm = table heads, cells, labels,
eyebrow · md = dialog and card body · base = page body · lg = dialog and panel title · xl = page
title.

## 4. Parts (stage 0)

- **head** `ui-head` (document: eyebrow, title, gist, actions) / `ui-head ui-head--data` (title +
  count + actions on one line). The title keeps the page's own h1/h2 and adds `ui-head-title`;
  the count is a muted `ui-count`, never a badge.
- **actions** `ui-actions` -- right-aligned button row, `role=group`. Order: [danger] ... [cancel]
  [secondary] [primary]; a single-action confirm puts the danger button last and has no filled
  primary. Icon button: `ui-btn-icon` (borderless 28px).
- **notice** `ui-notice ui-notice--{warn|danger|note}` -- page-top line: icon, bold key word, short
  sentence, at most two small buttons (one primary). Long text wraps, never ellipsized.
- **verdict** `ui-verdict ui-verdict--{ok|warn|danger|neutral}` -- state dot, bold conclusion,
  muted reason. Green only on the dot. No separate number badge; the number is in the sentence.
- **item** `ui-item` -- head (identifier chip, at most one badge, muted count on the right), body
  (sentence or a small `ui-table`), muted note, foot (`ui-actions`). No bordered box inside.
- **dialog** `ui-dialog ui-dialog--{s|m|l}` -- radius lg, shadow, max-height 82vh, a head (S and M
  use the data head, L the document head), a scrolling body and a foot that stays at the bottom.
  Real dialogs keep their `<dialog>` / overlay element, their hook classes, `smModalOpen` and
  `trapFocus`; `ui-dialog` is added beside them. No new overlay shell.
- Small pieces the parts need: `ui-chip` (code face), `ui-badge ui-badge--{neutral|accent|warn|danger|ok}`
  (state only), `ui-count`, `ui-note`, `ui-check`, `ui-callout ui-callout--note`, and minimal
  `ui-table` (+ `ui-table--kv`), `ui-disclosure`, `ui-segmented` -- these three are full parts in
  later stages; stage 0 ships only what the worked examples use.

## 4b. Choosing parts (select, input, field, menu, toolbar)

One row height for every control: `--ui-ctl-h` = 28px (the design study's row; a toolbar lifts its `btn-sm` to it), so a
`ui-select`, a `ui-input`, a `ui-segmented` and a `button.btn-sm` line up in one toolbar row. Text
`--ui-fs-sm`, radius `--ui-r-btn`, border `--ui-ctl-border` (`--ui-border-strong`), background
`--ui-ctl-bg` (the page surface); hover darkens the border, focus = accent border + a soft 2px ring (`--ui-ctl-ring`)
that never clips, disabled = surface-2 + `--ui-text-3`,
`aria-invalid="true"` = danger border. Every colour is a token; the select chevron is two
token gradients (`--ui-chevron`), so it follows light, dark and QM.

| Part | Macro / `UI.` twin | Markup |
|---|---|---|
| select | `ui_select(options, value, label, id, name, block, disabled, invalid)` / `UI.select(spec)` | the class sits **on the native `<select>`**: `<select class="ui-select">`. No wrapper: the chevron is a background, so `label > select`, form serialisation and every existing select handler keep working. As wide as its longest option + the chevron lane (min `--ui-ctl-min-w`, `max-width: 100%`); `ui-select--block` fills a form column. |
| input | `ui_input(value, type, label, id, name, num, unit, placeholder, block, disabled, invalid)` / `UI.input(spec)` | `<input class="ui-input">` (text / search / number); `ui-input--num` right-aligns with tabular numbers and hides the spinner; `ui-input--block` fills. A unit sits inside the box: `<span class="ui-input-unit"><input class="ui-input ui-input--num"><span class="ui-input-unit-text">ns</span></span>`; the wrapper also takes `--block`, `--invalid`, `--disabled` (the macro sets them). Numbers are mono and tabular. |
| field | `ui_field(label, control, hint, error, source, inline, id)` / `UI.field(spec)` | `ui-field` > `ui-field-head` (`ui-field-label` + an optional `ui-badge` source tag such as "copied" / "default") > the control > `ui-field-hint` > `ui-field-error` (`role=alert`). `ui-field--inline` puts the label left in a `--ui-field-label-w` column (property forms). `ui-field-stack` stacks fields; `ui-field-grid` still lays short fields side by side. The older `<label class="ui-field">` markup keeps working. Every field has an id: the control's `id`, else a generated one (`ui_uid()` in Jinja, a counter in `ui.js`). An input or select is named by `<label for>` and described by the hint and the error (`aria-describedby` = `<id>-hint <id>-error`), so they are never part of its name. A group control (segmented, menu, toolbar) is named by the label TEXT (`<span class="ui-field-label" id="<id>-flabel">` + `aria-labelledby`); clicking that text does nothing. |
| menu | `ui_menu(id, items, label, kind, value, trigger, placeholder, block, end, up)` / `UI.menu(spec)` | `ui-menu` > `button.ui-menu-trigger` + `ui-menu-list` of `ui-option` rows (`ui-option-title`, optional `ui-option-desc`; a disabled row shows its `reason` there; an item `note` adds a second `ui-option-desc` line, also under the title). `kind: "pick"` = `aria-haspopup=listbox`, rows `role=option` + `aria-selected`, a check mark (`ui-option-check`), the trigger looks like a select and shows the picked title; it is named by `aria-labelledby` = a hidden label span (`<id>-label`) + the value span (`<id>-value`), so a screen reader hears "Pair, q1-q2". An item without `value` carries no `data-value`. The menu `id` must be a non-empty string without whitespace (it is a token in `aria-labelledby`); the macro and `UI.menu` refuse any other with the same error. `kind: "action"` = `aria-haspopup=menu`, rows `role=menuitem`, the trigger is a `btn-sm` button. `{sep: true}` = `ui-menu-sep`. |
| toolbar | `ui_toolbar(items, label)` / `UI.toolbar(items, label)` | `<div class="ui-toolbar" role="toolbar">`: inline, small gaps, wraps, nothing stretches; ArrowLeft / ArrowRight / Home / End move focus between its controls (a text field, a select or a radio keeps its own arrow keys; every control stays a Tab stop); items are specs plus `{part: "button", ...}`, `{part: "toolbar-sep"}` (`ui-toolbar-sep`), `{part: "toolbar-spacer"}` (`ui-toolbar-spacer`, pushes the rest right). |

**Where the behaviour lives.** `static/ui-menu.js` is a **core script** of `base.html` (every page,
like `app.js`); it is one delegated set of document listeners, guarded against a second load. So a
`.ui-menu` / `.ui-toolbar` works by construction wherever it is rendered: any page extending
`base.html`, any htmx partial swapped in later, any `<dialog>`, without the `uikit` bundle.
`static/ui.js` (the page bundle) only BUILDS parts in script. A standalone page with its own
`<html>` that renders a menu must load `ui-menu.js` itself
(`tests/test_bundles.py::test_every_page_that_can_render_a_kit_menu_has_its_behaviour`).

Menu behaviour (no inline handlers, no `eval`): click / Enter / Space / ArrowDown open on the
chosen row (else the first enabled one), ArrowUp opens on the last; arrows, Home and End move over
the enabled rows (disabled rows are skipped, their reason stays visible); a letter jumps to the
next row starting with it; Enter / Space / click pick; Escape closes and returns focus to the
trigger; Tab or an outside click closes; one menu open at a time; the list opens upward when the
window has no room below (`ui-menu-list--up`, also forced with `up`), aligns right with `end`
(`ui-menu-list--end`) and scrolls inside past `--ui-menu-max-h`. A pick fires one bubbling
`ui-menu-pick` event on the `.ui-menu` root, `detail = {value, title, kind}`; a picking menu also
updates `aria-selected`, the trigger text and `data-value` on the root. `UI.menuOpen(root)` /
`UI.menuClose(root)` (or `window.UIMenu.open/close`) drive it from script. Keyboard focus on a row is an inset accent ring (`--ui-row-focus`); hover is the tint only. Pinned by `tests/ui_menu_selfcheck.cjs`.

**The stretch fix.** Pico styles every `[role=group]` as a full-width bar: `width: 100%`, a 1rem
bottom margin, children `flex: 1 1 auto`, inner corners squared, neighbours pulled -1px, buttons
padded 2rem. The kit's `ui-segmented` and `ui-actions` carry `role=group`, so the kit now resets
those for both (doubled class selectors out-rank Pico's 0,2,1 child rules): a group is as wide as
its content, its buttons keep their own corners and gaps, and a segmented control is one control
row high. `ui-toolbar` uses `role=toolbar`, which Pico does not touch. Places this changes:
`_ui.html` `ui_actions` (so every `ui_head` with actions, `ui_notice` with actions, the `ui_item`
foot and the `ui_dialog` foot) and `ui_segmented`; their `ui.js` twins `UI.actions` /
`UI.segmented`; `_env_upgrade_card.html` (the environment-check action row).

**Listed behaviour change (look only), every `ui-segmented` and `ui-actions`, with or without
`role=group`:** a segmented control is one control row high (`--ui-ctl-h`, 28px, inline-flex, no
vertical padding, tight line height) and its focus ring is drawn inside (`--ui-focus-inset`) so the
clipped box never hides it; a button directly in a `ui-actions` row keeps the kit button radius.
Uses: `static/chip-status.js` (the Chip Status metric segmented control), `static/gen-rules.js`
(Generate rule chips and their segmented rows, and its `ui-actions` rows),
`templates/_pulse_gaussian_cz.html` (the CZ pulse shape choice and its action row),
`static/port-play.js` and the `ui-actions` rows of the diagram, diagnostics, explorer, history and
bulk-edit templates. One control height across the app is intended.

`ui-segmented-bar` is the one wrapper for a segmented control that sits directly above a list or table (the
Pulses channel tabs): it keeps the space the older tab rows left below them (`margin: 0 0 var(--ui-gap-3)`, 0.75rem).
The segmented control itself stays flush (`margin-bottom: 0`), so every other use keeps its spacing.

**Which control when**

| The choice | Use |
|---|---|
| 2 to 4 visible choices | `ui-segmented` |
| many short choices | `ui-select` |
| choices that need a description, or can be unavailable for a reason | `ui-menu` (pick) |
| on / off | `ui-check` |
| a command | a button |
| several commands | `ui-menu` (action) |

## 4c. Reading order

A screen tells the eye where to look first. Six rules, each with its part:

| # | Rule | Part |
|---|---|---|
| 1 | The conclusion first, one banner per region | `ui-verdict ui-verdict--strong ui-verdict--{ok,warn,danger,note}` (`ui_verdict(..., strong=True)`): icon disc, key phrase in the tone colour, one sentence (reason + what to do). The existing verdict is the conclusion part, so the banner is its strong size, not a fourth look-alike of notice / callout / verdict. |
| 2 | At most four key numbers as tiles; colour only on the abnormal one | `ui-stats` > `ui-stat [ui-stat--warn,--danger,--quiet]` > `ui-stat-label` / `ui-stat-value` / `ui-stat-unit`, an optional quiet `ui-stat-note` line under the value (`ui_stats(tiles)`, tile `{label, value, unit?, note?, tone?, title?}`). `ui-stat--quiet` is an unknown value: grey, never a state colour. |
| 1b | A banner may carry one quiet meta line under its reason (freshness, source) | `ui-verdict-meta` (`ui_verdict(..., meta=...)`) |
| 3 | Every group in a panel with a title and a one-phrase summary; details folded unless something is wrong | `ui-panel` (`ui_panel(title, body, step, hint, summary, fold, open, flush)`): `ui-panel-head` (`ui-panel-step`, small-caps `ui-panel-title`, quiet `ui-panel-hint`, right `ui-panel-sum`), `ui-panel-body`; `fold` = `<details class="ui-panel ui-panel--fold">`, the summary stays visible when folded |
| 4 | Three text levels; an explanation is never louder than the content; prose longer than one line goes into a hint or a disclosure | `ui-text-strong` (names, values), normal, `ui-text-quiet` (smaller, `--ui-text-3`); `ui-field-hint`, `ui-panel-hint`, `ui-disclosure` |
| 5 | Colour only with a meaning: a state, the identity of a line, or "can be pressed" | state tones; `ui-line-1..6` on `ui-slot` (left bar + tinted `ui-slot-chip`) and `ui-line-dot`; accent on controls |
| 6 | Passes are counted, failures are spelled out; a cell that differs or is over a limit says so in place | `ui-checks` > `ui-checks-row` of `ui-result [--fail,--skip]` + one `ui-fail-row` per failure; the count goes in the panel summary. Table cells: `ui-cell--warn` (differs), `ui-cell--danger` (over a limit), with a quiet `ui-cell-note` ("limit 2.5 V"); a cell spec `{text, tone, note, line}` |
| 7 | Facts are label / value pairs, not one run-on sentence | `ui-facts` > `ui-facts-row` > `ui-facts-label` (normal) / `ui-facts-value` (strong) [+ quiet `ui-facts-note`] (`ui_facts(items)`, part `facts`) |
| 8 | A question about one table row sits directly under that row | `tr.ui-table-sub > td.ui-table-cell[colspan]` holding a part (a `ui_table` row given as `{sub: [specs]}`); a callout takes a body (`ui-callout-body`, e.g. facts) and its buttons (`ui-actions`) at the foot: `ui_callout(tone, title, text, body=(), actions=None)` |

Line colours: `--ui-line-1..6` alias the chart palette variables in the order
`--plot-colorway-1, 4, 6, 3, 5, 2` (blue, magenta, teal, green, yellow, amber), the same variables
`PlotTheme.palette()` reads. The order is not the palette order on purpose: amber, green and rose
read as warn / ok / danger, so a gate's usual two or three lines get the hues that carry no state.
A chart that draws line N uses the colour of `--ui-line-N` (palette index 1,4,6,3,5,2 minus one),
not series N. `tests/test_ui_contract.py` (`test_7h`) pins that no line resolves to, or lies within RGB distance 40 of, a state tone; the known exceptions are lines 5 and 6 near the warning border (light, dark) and line 5 equal to the warning colour in QM. They are classes, not an inline `--ui-line`, because
inline styles are not allowed.

A line legend button is `btn-sm secondary ui-legend-btn` holding a `ui-line-dot`, the label and a `ui-note`; a hidden line adds
`ui-legend-btn--off` to the button (dashed border, quiet text) and `ui-line-dot--off` to its dot (hollow), keeps
`aria-pressed="false"` and its focus ring. A pair list row is `ui-pair-row` (hover tint, `--ui-row-focus` ring). A class a
component uses only as a script hook is a `data-*` attribute, never a class: every class on an element has a kit rule.

Small caps titles use `font-variant-caps: all-small-caps` (never `text-transform: uppercase`).
`ui-placeholder` marks where a component renders (dashed, quiet text, `--ui-placeholder-h`). The sample page rebuilds a dialog body from these parts only, passed and failed, with a 372 px
form column and an evidence column.

## 4d. Zones: the level above a panel

Hierarchy, outermost first:
1. Page or dialog: the ground (`--ui-surface-0`, or `--ui-surface-2` for a result column).
2. Zone (`ui_zone` / `UI.zone`): a main region the eye must find first. At most three or four per screen
   (a dialog's form steps and its result). Coloured boundary, tinted head band, bold title, filled step badge.
3. Panel (`ui_panel` / `UI.panel`): a lighter grouping inside a zone or a page (waveform, checks, a table).
   Grey border, small-caps quiet title. Never as loud as a zone.
4. Field: one label and its control.

Use a zone when the region is one of the few things the user scans for on arrival; use a panel for
everything that groups content inside it. Never nest a zone in a zone.

Parts: `ui-zone`, `ui-zone-head`, `ui-zone-step` (optional), `ui-zone-title`, `ui-zone-sum` (right, quiet),
`ui-zone-body`; `ui-zone--fill` fills its column's height; `ui-zone--quiet` is a zone with nothing to show yet:
it keeps its head and shows one centred `ui-zone-empty` line (macro argument `quiet="..."`), so the region stays
visible before a check. `ui-zone--danger` (tone='danger') marks a zone whose actions cannot be undone (danger border,
tinted head, danger title); `ui-zone--fold` (fold=True) is a `<details>` zone whose head is the summary, folded unless
`open`. Signature: `ui_zone(title, step=None, summary=None, body=(), fill=False, quiet=None, tone=None, fold=False, open=False)`.

Tokens (all derived, one formula serves light, dark and qm):
- `--ui-zone-border`: the accent 30 % over a 40 % text-on-ground grey. A cool boundary, deliberately
  under the state colours: a red banner inside a zone still dominates it.
- `--ui-zone-head-bg`: the accent 10 % over `--ui-surface-1`; `--ui-zone-bg`: `--ui-surface-1`.
- `--ui-zone-title`: `--ui-text`; `--ui-fs-zone`: 14/17 of the base size (one step above the panel's small caps).

Measured (`test_7h`, contrast ratios): zone title on its head 9.65 / 9.97 / 11.85 (light / dark / qm);
boundary against the ground 2.85 / 3.75 / 3.74, against `--ui-surface-2` 2.57 / 3.32 / 3.34, where the panel
border was 1.67 / 2.09 / 2.19; head band vs body luminance step 0.137 / 0.0099 / 0.0081.

## 4e. Scroll region: a region that visibly continues

Use `ui-scroll` on any region that scrolls inside a dialog or a pane and can end mid-content (a dialog's
form or result column, a long list in a side pane). Without it, a zone cut by the scroll edge has no bottom
border and reads as broken. One class, no JS:
- a thin scrollbar, always visible while the region can scroll (`scrollbar-width: thin`, thumb
  `--ui-scroll-thumb`, transparent track, `scrollbar-gutter: stable` so the width does not jump when
  content starts to overflow); `::-webkit-scrollbar` equivalents at `--ui-scroll-bar-w`;
- a bottom fade, `::after`, `position: sticky; bottom: 0`, `--ui-scroll-fade-h` tall, painted with
  `--ui-scroll-fade`: transparent to `--ui-scroll-bg` (`--ui-surface-1`, the dialog and pane ground). The
  gradient is computed in the token block, so a region on another ground needs its own fade token there.
  It takes its own space after the last child, so at the scroll end nothing is under it, and it has
  `pointer-events: none`, so a click on a field under it reaches the field. Over the same ground it is invisible
  when the content fits.
Put the class on the element that really scrolls. When a layout moves the scroll to a parent (the CZ gate
dialog stacked), hide the inner `::after` and drop its gutter, so there is one scrollbar and one fade.

## 5. Writing rules (UI text, English)

1. Conclusion first, reason next, action last. A sentence <= ~90 characters; two thoughts = two sentences.
2. Lift the key word into a short bold head; reasons go muted.
3. Names (field, class, pulse, path, env) are chips, not part of the sentence.
4. Number + unit is one piece ("32 objects", "903 KB"); counts are muted numbers.
5. Never delete a safety / scope sentence ("Nothing is written until you choose", "not measured
   hardware timing", "Not checked: ...", "live only via Apply"); only move it to a muted note.
6. Same meaning, same word: Apply / Keep as is / Cancel / Close / Remove / Delete. Remove = take
   out of the chip (reversible), Delete = delete a file or record. Dismiss = close without a
   record, Acknowledge = store the acknowledgement.
7. Never: "safe", "compatible", exclamation marks, apologies, "please". English only.
8. A tooltip is extra; deciding information is never only in a tooltip.
9. A changed sentence updates its wording pin in the same commit.

## 6. Do not

`:has()`, attribute-selector rules or new per-grid-cell rules (style recalc, docs/141 §4, docs/223);
`:where()`; `hx-on` or htmx trigger filters (CSP, docs/141, docs/125); changing
`attributesToSettle`; theme blocks in `ui-kit.css`; inline `style=` colours or sizes; a new
`<dialog>` shell without `ui-dialog`; `window.confirm` / `alert`; a class with no rule.

Classes are for the kit's look: every class name has a rule in `ui-kit.css` (or `style.css`).
Script hooks are `data-*` attributes, never classes (check 9b counts class names without a rule per file).

## 7. The sample page `/ui-kit`

Every part in each state (ok / warn / danger / note / neutral, disabled, long text, empty),
rendered by the macro (left) and by `ui.js` (right) from one spec list
(`web/ui_kit_fixtures.py`), with Light / Dark / QM buttons, plus worked examples built from
fixtures that mirror real data: the rule review dialog (2 swapped pairs), the apply-to-other-pairs
dialog, and the environment-upgrade card head with its verdict summary. Not behind
`SM_EXPERIMENTAL`, no sidebar entry; linked from one line at the bottom of `/help`.

## 8. The contract test (`tests/test_ui_contract.py`)

Debt is frozen as `{file: max count}` in `tests/ui_contract_baseline.json`; a count may only go
down (the test warns with the new maximum). Re-measure with
`python tests/test_ui_contract.py --write-baseline`, only when lowering.

| # | Check |
|---|---|
| 1 | colour literals (`#hex`, `rgb(`, `hsl(`) per file; style.css counted outside its `:root` / theme variable blocks |
| 2 | `<button>` with no class, or only classes without a CSS rule |
| 2d | the same for selects / inputs / textareas BUILT in `static/*.js` (`createElement('select'|'input'|'textarea')`, `<select` / `<input` / `<textarea` literals): 101 over 18 files on 2026-11-14, only down |
| 2b | template `<select>` and text / number / search `<input>` with no class that has a CSS rule (Pico's full-width defaults); per file, only down, a new template adds none. 157 over 45 files on 2026-11-14 (71 `<select>` tags in all) |
| 6b | `UI.menu` behaviour (`tests/ui_menu_selfcheck.cjs`, jsdom), toolbar arrows, one pick event after a second load |
| 7g | small read-me text of the new parts (quiet text, hints, stat labels and units, unit suffixes, placeholders, option descriptions, cell notes) is one kit token that reaches 4.5:1 on surface 0/1/2 in light, dark and QM |
| 3 | inline `style=` colour / background / font-size (templates), `.style.color/background/fontSize =` (JS) |
| 4 | overlay shells (`<dialog>`, `role=dialog`) that do not wear `ui-dialog` |
| 5 | `window.confirm` / `alert` |
| 6 | macro and `ui.js` build the same tree for every sample (`tests/ui_kit_selfcheck.cjs`, jsdom) |
| 7 | token contrast pairs x 3 themes >= AA (4.5:1 text, 3:1 state dots), computed from the theme variables in pico.min.css / style.css; a known failure is frozen by name |
| priority | `ui-col-secondary` is the sole narrow-column media rule; header and row carry it; values remain in detail (`tests/test_reading_order_small.py`) |
| 7b | stage-0 boundary: one `:root` block of `--ui-*` names only, no redeclared style.css variable, every other selector starts with `.ui-`, no `:has(` / `[` / `:where(`, only the priority-column media rule |
| 7c | CSP: `hx-on` and trigger filters per file do not grow; the `attributesToSettle` meta is unchanged; the kit files hold no `hx-on`, `eval(`, `new Function`, `innerHTML` |
| 9 | dead classes: an element whose literal classes ALL lack a CSS rule; frozen as an explicit class-name list plus a per-file count (no prefix allowances) |
| 10 | style.css rule and line counts only go down |
| 11 | ui-kit.css values outside the token block are `var(--ui-*)` (or a keyword); no `--pico-*`, no raw colour |
| 12 | every `.ui-*` class in ui-kit.css appears on the rendered `/ui-kit` |

Start values (2026-10-31): colour literals 1,100 over 19 files; bare / unstyled buttons 25 over
17 files; inline visual styles 42 over 19 files; overlay shells 28 over 18 files; confirm/alert 77 over 16
files; dead classes 330 names on 380 elements in 118 files; style.css 5,943 rules / 16,279 lines; `hx-on` 5
(app.js 4, auto-apply.js 1), trigger filters 4; one known contrast failure -- light theme,
`--ui-warn-border` on a card is 1.63:1 against the 3:1 non-text minimum (the warning dot on
white), to be fixed in the theme variables in a later stage, not here.

### Priority columns

`ui-col-secondary` on the matching header and data cells hides secondary evidence at
viewport widths of 900 px or less. Primary names keep the released space. Keep the
values reachable in row detail. Spacer and spanning rows retain their logical colspan.
Navigation options in `ui_segmented` / `UI.segmented` accept `href` and `attrs`;
`ui-menu.js` supplies arrow keys, Home, End and selection; a key with Alt, Ctrl or Meta is left to the
browser. A link option is never underlined and carries `aria-current` when selected; a button option
carries `aria-pressed`. The group wraps onto further lines at a narrow width instead of clipping.
Wrapped lines of a segmented control keep `row-gap: var(--ui-gap-1)` between them; a one-line control is unchanged.
A pick menu's trigger may carry a `ui-menu-note` span after the value: the trigger then wraps, the picked name keeps
the first line and the note takes the line under it (never the name or the note alone). An open list is never cut by
its `ui-zone` (the zone shows overflow while a trigger is expanded), and a block menu's list is as wide as its trigger,
so a long reason wraps; a list past `--ui-menu-max-h` scrolls with a visible thin scrollbar.
The CZ plan chip layout card sits beside the frequency map while the row holds the card plus `--ui-czp-map-min-w`;
otherwise it takes the full row above the map. At the top bar fit floor (`tb-fit-11`) the Auto-Sync pill wraps its words
inside the window instead of widening the page.
A gate row's name chip (`.gate-status-row > .ui-chip`) may shrink and wraps a long name inside the row; the delete
offer's pointer lines (`.gate-delete-list code`) break after each path dot (`<wbr>`) and, as a last resort, anywhere.
Segmented controls built from buttons (`.ui-segmented-opt`) move focus with ArrowLeft / ArrowRight / Home / End through
one handler in `ui-menu.js`; the arrow only moves focus, never presses, so no selection and no write follows a key.
`.ui-field-head` wraps its label and badges onto a second line instead of breaking a label word. The pulse class field
grid keeps the Name column at least `--ui-pce-name-min` so a name like "amplitude" stays whole beside its Type.
The Pulses table drops Amp and Used by with the other `ui-col-secondary` columns below 900 px; its headers and alias
badges break only between words. The gate timeline stacks each lane title above its lane when titles beside the lanes
would leave the plot under 200 px, and then keeps one time tick per 70 px.
