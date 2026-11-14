# 308 — The UI kit (stage 0: the hub)

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

One row height for every control: `--ui-ctl-h` = the small button's `--ui-btn-h-sm` (26px), so a
`ui-select`, a `ui-input`, a `ui-segmented` and a `button.btn-sm` line up in one toolbar row. Text
`--ui-fs-sm`, radius `--ui-r-btn`, border `--ui-ctl-border` (`--ui-border-strong`), background
`--ui-ctl-bg` (the page surface); hover darkens the border, focus = accent border + 1px accent
outline (`--ui-accent`, no offset, so it never clips), disabled = surface-2 + `--ui-text-3`,
`aria-invalid="true"` = danger border. Every colour is a token; the select chevron is two
token gradients (`--ui-chevron`), so it follows light, dark and QM.

| Part | Macro / `UI.` twin | Markup |
|---|---|---|
| select | `ui_select(options, value, label, id, name, block, disabled, invalid)` / `UI.select(spec)` | the class sits **on the native `<select>`**: `<select class="ui-select">`. No wrapper: the chevron is a background, so `label > select`, form serialisation and every existing select handler keep working. As wide as its longest option + the chevron lane (min `--ui-ctl-min-w`, `max-width: 100%`); `ui-select--block` fills a form column. |
| input | `ui_input(value, type, label, id, name, num, unit, placeholder, block, disabled, invalid)` / `UI.input(spec)` | `<input class="ui-input">` (text / search / number); `ui-input--num` right-aligns with tabular numbers and hides the spinner; `ui-input--block` fills. A unit: `<span class="ui-input-unit"><input class="ui-input ui-input--num"><span class="ui-input-unit-text">ns</span></span>` (`ui-input-unit--block` fills). |
| field | `ui_field(label, control, hint, error, source, inline, id)` / `UI.field(spec)` | `ui-field` > `ui-field-head` (`ui-field-label` + an optional `ui-badge` source tag such as "copied" / "default") > the control > `ui-field-hint` > `ui-field-error` (`role=alert`). `ui-field--inline` puts the label left in a `--ui-field-label-w` column (property forms). `ui-field-stack` stacks fields; `ui-field-grid` still lays short fields side by side. The older `<label class="ui-field">` markup keeps working. |
| menu | `ui_menu(id, items, label, kind, value, trigger, placeholder, block, end, up)` / `UI.menu(spec)` | `ui-menu` > `button.ui-menu-trigger` + `ui-menu-list` of `ui-option` rows (`ui-option-title`, optional `ui-option-desc`; a disabled row shows its `reason` there). `kind: "pick"` = `aria-haspopup=listbox`, rows `role=option` + `aria-selected`, a check mark (`ui-option-check`), the trigger looks like a select and shows the picked title. `kind: "action"` = `aria-haspopup=menu`, rows `role=menuitem`, the trigger is a `btn-sm` button. `{sep: true}` = `ui-menu-sep`. |
| toolbar | `ui_toolbar(items, label)` / `UI.toolbar(items, label)` | `<div class="ui-toolbar" role="toolbar">`: inline, small gaps, wraps, nothing stretches; items are specs plus `{part: "button", ...}`, `{part: "toolbar-sep"}` (`ui-toolbar-sep`), `{part: "toolbar-spacer"}` (`ui-toolbar-spacer`, pushes the rest right). |

Menu behaviour (`static/ui.js`, one delegated set of listeners for every `.ui-menu`, server- or
script-rendered; no inline handlers, no `eval`): click / Enter / Space / ArrowDown open on the
chosen row (else the first enabled one), ArrowUp opens on the last; arrows, Home and End move over
the enabled rows (disabled rows are skipped, their reason stays visible); a letter jumps to the
next row starting with it; Enter / Space / click pick; Escape closes and returns focus to the
trigger; Tab or an outside click closes; one menu open at a time; the list opens upward when the
window has no room below (`ui-menu-list--up`, also forced with `up`), aligns right with `end`
(`ui-menu-list--end`) and scrolls inside past `--ui-menu-max-h`. A pick fires one bubbling
`ui-menu-pick` event on the `.ui-menu` root, `detail = {value, title, kind}`; a picking menu also
updates `aria-selected`, the trigger text and `data-value` on the root. `UI.menuOpen(root)` /
`UI.menuClose(root)` drive it from script. Pinned by `tests/ui_menu_selfcheck.cjs`.

**The stretch fix.** Pico styles every `[role=group]` as a full-width bar: `width: 100%`, a 1rem
bottom margin, children `flex: 1 1 auto`, inner corners squared, neighbours pulled -1px, buttons
padded 2rem. The kit's `ui-segmented` and `ui-actions` carry `role=group`, so the kit now resets
those for both (doubled class selectors out-rank Pico's 0,2,1 child rules): a group is as wide as
its content, its buttons keep their own corners and gaps, and a segmented control is one control
row high. `ui-toolbar` uses `role=toolbar`, which Pico does not touch. Places this changes:
`_ui.html` `ui_actions` (so every `ui_head` with actions, `ui_notice` with actions, the `ui_item`
foot and the `ui_dialog` foot) and `ui_segmented`; their `ui.js` twins `UI.actions` /
`UI.segmented`; `_env_upgrade_card.html` (the environment-check action row). Every other
`ui-actions` / `ui-segmented` in templates and scripts has no `role=group` and only gains the
segmented row height.

**Which control when**

| The choice | Use |
|---|---|
| 2 to 4 visible choices | `ui-segmented` |
| many short choices | `ui-select` |
| choices that need a description, or can be unavailable for a reason | `ui-menu` (pick) |
| on / off | `ui-check` |
| a command | a button |
| several commands | `ui-menu` (action) |

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
| 2b | template `<select>` and text / number / search `<input>` with no class that has a CSS rule (Pico's full-width defaults); per file, only down, a new template adds none. 157 over 45 files on 2026-11-14 (71 `<select>` tags in all) |
| 6b | `UI.menu` behaviour (`tests/ui_menu_selfcheck.cjs`, jsdom) |
| 3 | inline `style=` colour / background / font-size (templates), `.style.color/background/fontSize =` (JS) |
| 4 | overlay shells (`<dialog>`, `role=dialog`) that do not wear `ui-dialog` |
| 5 | `window.confirm` / `alert` |
| 6 | macro and `ui.js` build the same tree for every sample (`tests/ui_kit_selfcheck.cjs`, jsdom) |
| 7 | token contrast pairs x 3 themes >= AA (4.5:1 text, 3:1 state dots), computed from the theme variables in pico.min.css / style.css; a known failure is frozen by name |
| 7b | stage-0 boundary: one `:root` block of `--ui-*` names only, no redeclared style.css variable, every other selector starts with `.ui-`, no `:has(` / `[` / `:where(`, no at-rule blocks |
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
