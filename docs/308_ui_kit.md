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
| Server templates | `web/templates/_ui.html` | Jinja macros `ui_head`, `ui_notice`, `ui_callout`, `ui_verdict`, `ui_item`, `ui_table`, `ui_actions`, `ui_badge`, `ui_chip`, `ui_count`, `ui_note`, `ui_check`, `ui_segmented`, `ui_disclosure`, `ui_dialog`, and the dispatcher `ui_render(spec)` |
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
