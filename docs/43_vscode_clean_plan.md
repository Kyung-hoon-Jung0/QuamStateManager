# Final Plan — VSCode-Clean Language for Generate-Config + Dataset Surfaces

## 1. Design System Summary (the canon to reuse everywhere)

The shipped `.state-review`/`.btn-sync` block (`style.css` 4529–4678) is the reference implementation. Every new surface reuses these **patterns** and **tokens** — never invents.

**Core principles (non-negotiable):**
- **Hairline, not cards.** Sibling rows = `gap:0` stack, each `border-bottom:1px solid color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent)`, `border-radius:0`, `:last-child` drops it. Outer card shell only for true modals/floating panels.
- **Status = letter + 2px edge, not a filled pill.** A mono glyph in a `1.1em` gutter colored by a `*-text` token + a `2px` left border in that hue `@55%`. `background:none`. Map: modified→`--diag-info-text`/`M`, added→`--color-success-text`/`A`/`✓`, removed→`--color-error-text`/`D`/`✗`, warn→`--color-warning-text`.
- **Leaf-path emphasis, no band.** Mono 12.5px: dim parent dirs (`--pico-muted-color`, wt 400) + soft-blue bold leaf (`--diag-info-text`, wt 600). Split in Jinja (`rsplit('.',1)` / `rsplit('/',1)`), not CSS. Override global `.dot-path` hover **only inside the surface root** so it stops masquerading as a link.
- **Borderless-until-focus inputs.** Transparent border+bg at rest → `:focus` reveals `1px var(--pico-primary)` + `var(--pico-card-background-color)`. `width:auto; min-width:6ch; max-width:22ch`.
- **Ghost-until-hover actions.** Per-row affordances at `opacity:.55` muted → `opacity:1` + primary on row-hover/focus.
- **One filled CTA.** At most one `.btn-sync.primary` (filled `--pico-primary-background`/`-inverse`); siblings outline-tinted (`--pico-primary @45%` border, transparent) or fully muted.
- **Color only from `*-text` tokens + color-mix.** Never raw hex (only the `rgba(0,0,0,.45)` backdrop), never a `*-bg`/`diff-type-*-bg` fill token as text or accent.
- **Fixed px type, decoupled from `--bulk-fs`.** Pin everything; only legacy table cells in the same modal may still scale.
- **Uniform control height** on a value line: `--review-ctl-h:22px` shared by old-value, arrow, input, ✓.
- **Mono + tabular-nums** for all values/paths/ids/timestamps; sans for prose.
- **Scope new rules** under a surface root (`.generate-root`, `.config-browser`, `.datasets-table-virtual`); override shared classes only inside it.

**Type ladder (px, pinned):** 10.5 micro-chip · 11 gutter glyph · 12 caret/meta/count · 12.5 path/value/input · 13 body/header/button. Nothing exceeds 13px except the page/modal title (keep its existing size — restraint is in the body).

**Reusable component recipes:** `hairline-list-row`, `gutter-status-indicator`, `leaf-path`, `inline-edit-field`, `ghost-row-action`, `restrained-chip` (outline-only, 8px radius, 10.5px/600, one word), `one-cta-action-bar`, `card-to-panel`, `value-transition-line`, `table-header` (no outer border, one header hairline, body-row hairlines, primary `@6%/10%` hover), `segmented-control` (text buttons, one bottom hairline, active = 2px primary underline), `empty-state` (muted prose, `1.5rem` padding, ≤1 restrained CTA), `toolbar-summary`.

---

## 2. Prioritized Roadmap

Effort: S = CSS-only / minutes, M = scoped CSS + small template edit, L = template restructure or JS-coupled. Ordered impact-then-quick-win within each phase.

### Phase 1 / HERO (highest leverage — ship first)
These are the loudest, most-seen, lowest-risk wins; four already have scored mockups.

| # | Surface · Component | Impact | Effort | Direction |
|---|---|---|---|---|
| H1 | **Generate** · Step indicator (8-step stepper) | high | M | segmented-control: one bottom hairline, active = 2px primary underline, done = green ✓ glyph (**keep the digit visible**, tint it — don't `color:transparent`), 11px mono number + 13px/500 label. |
| H2 | **Generate** · Env picker rows | high | M | hairline rows, 2px primary left accent + `@10%` wash on `.selected` (no glow); name 13/600, path = muted mono **leaf-path** (do the JS split — don't ship `direction:rtl`); status = color-only text via tokens; keep `bad`→`--color-warning-text` (not error). |
| H3 | **Config Viewer** · Status/Regenerate banner | high | M | de-card → borderless region capped by one bottom hairline; error = 2px left `--color-error-text @55%` + glyph (no red box); stale = warn gutter-glyph + muted note (no fill pill); meta 12px mono tabular; **Regenerate = the one CTA** (`.btn-sync.primary`); re-skin `<pre>` to `--pico-card-sectioning-background-color`+`--pico-color`. |
| H4 | **Config Viewer** · Operations table + view-waveform | high | M | table-header pattern: drop borders, one header hairline, 12/600 muted sans heads, mono+tabular value cells (no code-badge fill), `@6/10%` row hover; view-waveform = ghost-row-action; **add `text-overflow:ellipsis`+width cap, not `nowrap`-only**. |
| H5 | **Config Viewer** · JSON section accordions (`.config-browser`) | high | M | strip `--section-header-bg` band (transparent, 13/600 sans header); `<pre>` → sectioning-bg + `--pico-color` + mono 12px; between-section separator = 60% hairline. |
| H6 | **Generate** · Inline message banner (warn/error) | high | S | quiet notice: transparent bg, 2px left `*-text @55%` accent + mono gutter glyph; **also fix the real bug — failure paths pass `"warn"`; route genuine errors through `"error"`** so they read red not gold. |
| H7 | **Datasets** · Run-list table header + body rows | high | M | table-header + hairline rows: 12/600 muted heads, one header hairline, body `@6/10%` hover + 60% row hairlines, mono+tabular value cols, transparent 2px left accent slot. **If row height changes, update `ROW_HEIGHT` in `dataset-virtual.js`.** |
| H8 | **Datasets** · Status cell chip | high | S | gutter-status-indicator: colored mono glyph (`✓/◷/✗/–`) via `*-text` tokens + 2px left row accent; **keep the word muted-default, not recolored** (avoid the 3× color-signal restraint violation); prefer an `R` over `◷` for glyph-coverage safety. |

### Phase 2 (high-value, slightly heavier)
| # | Surface · Component | Impact | Effort | Direction |
|---|---|---|---|---|
| P2-1 | **Datasets** · Experiments multi-select filter banner | high | L | hairline-separated category groups (13/600 label, no card band); active chips = segmented/outline (not solid blue). |
| P2-2 | **Datasets** · Sort banner (Columns/Fit/Params) | high | L | de-card sections; active sort = underline/outline; param facets = restrained chips; range inputs = borderless-until-focus mono; match Experiments banner language. |
| P2-3 | **Generate** · Nav footer (Back/progress/Next) | med | S | one-cta-action-bar: Next=`.btn-sync.primary`, Back=`.btn-sync`, progress 12.5 mono. |
| P2-4 | **Config Viewer** · Config Viewer empty state | med | S | empty-state class: muted prose, 1.5rem padding, Generate=filled / Choose-env=outline. |
| P2-5 | **Config** · Embedded per-qubit/pair "Generated config" header + inline empty | med | S | pin px ladder (no 0.85rem pane scale), name = soft-blue leaf, stale = restrained-chip; **swap inline-empty button to `.btn-sync` so it matches the banner** (closes the H3 inconsistency). |
| P2-6 | **Datasets** · Tag cell badges + (+) button | med | S | `.tag-badge` → restrained outline chip; `+` = ghost-until-hover. |
| P2-7 | **Datasets** · Floating compare bar | med | S | one-cta-action-bar on neutral `--pico-card-background-color`; Compare=filled, Clear=muted; `N selected` mono. |
| P2-8 | **Datasets** · Date filter tabs | med | S | textbook segmented-control. |
| P2-9 | **Datasets** · Page header (search/count/Properties/Rescan) | med | M | toolbar-summary + borderless-until-focus search; count mono muted; Rescan = single outline action. |
| P2-10 | **Datasets** · Folder-filter chips | med | S | align active state to the chosen outline/underline language; radius 8px. |

### Phase 3 (polish / lower-impact)
| # | Surface · Component | Impact | Effort | Direction |
|---|---|---|---|---|
| P3-1 | **Generate** · Sub-headers + field labels (`.generate-root`) | med | M | pin headers 13/600, labels 12.5–13/600 muted, machine text mono 12 tabular. |
| P3-2 | **Config** · Page header strip + subtitle `<code>` | low | S | keep h2 size; subtitle muted sans 13, strip code-badge fill. |
| P3-3 | **Generate** · Custom-interpreter input row | low | S | mono input, restrained outline "Use this", token status. |
| P3-4 | **Generate** · Header (title + Reset) | low | S | keep h2; Reset = fully-muted restrained button. |
| P3-5 | **Datasets** · Left sidebar nav tree | med | M | light touch: status pills → colored `*-text`, date-divider → tokenized hairline. |
| P3-6 | **Datasets** · Qubits/Pairs/Properties dropdowns | low | M | keep panel shell; chips → outline; one-cta-action-bar inside. |
| P3-7 | **Datasets** · Outcome cell badges (per-qubit ✓/✗) | low | S | recolor to `*-text`, 12px mono. |
| P3-8 | **Datasets** · Bookmark star, Collections chips, empty/help panel | low | S | ghost-until-hover star, unified chips, empty-state class. |
| P3-9 | **Config** · "experimental" badge + de-nest add-gate form | low | M | restrained-chip; de-nest inner card → hairline + sub-header. |
| P3-10 | **Config Viewer** · Drag-drop preview banner + issue badge | high | M | borderless toolbar-summary; Clear = muted ×; issue badge → outline chip (don't touch global `.diag-badge`). |
| — | **Config Viewer** · Embedded diagnostics findings list | med | L | **Defer** to the diagnostics-surface audit; apply once at source, propagates here. Note dependency only. |

---

## 3. Hero Components (ready-to-implement)

**H1 — Step indicator (avg 7.7).** Replaces eight wrapping filled pills + circular number badges with a single segmented-control strip: one continuous 60% hairline under the row, the active step marked by a 2px primary underline (coincident with the strip) and `--pico-color` text, done steps showing a soft-green `✓` with a muted label. JS contract is fully intact (`#gen-steps`, `li[data-step]`, `.active`/`.done`, the `data-step` click read, `.gen-step-num` kept). **Two fixes before merge:** keep the done-step digit visible (tint green, don't `color:transparent` — the ordinal carries "go back to step 3" meaning), and since 8 steps wrap, render the hairline per-row or accept that wrap shows the underline mid-strip. Ready to implement as a 1:1 replacement of `style.css` 5143–5168.

**H3 — Config status/Regenerate banner (avg 7.7).** De-cards the boxed banner (drops 1px border + radius + elevated fill) into a borderless region capped by one bottom hairline; the error state becomes a 2px left `--color-error-text @55%` accent + glyph + faint `@10%` wash instead of a full red box; the stale pill becomes a warn gutter-glyph + muted note; meta goes 12px mono tabular-nums; the `<pre>` traceback is re-skinned to `--pico-card-sectioning-background-color`+`--pico-color` (fixes the light-theme-unreadable dark slab). All IDs/HTMX hooks preserved (`.config-status-banner`, `.config-status-host`, `hx-post="/config/regenerate"`, the `.htmx-indicator`). **Verified hazard to fix:** `generate.js:2691` writes `<pre class="config-trace">` inside `.gen-preview-result` (not `.config-status-host`) — so **keep a global `.config-trace` rule or add `.gen-preview-result .config-trace`**; do not delete the global block. Make Regenerate the one CTA (filled `.btn-sync.primary`).

**H4 — Operations table + view-waveform (avg 8.0, top score).** Brings the table into the table-header pattern: removes outer/Pico cell borders, puts one 60% hairline under a bandless 13/600 header, renders Op/Element/Pulse as plain mono+tabular (strips the code-badge fill), gives rows a primary `@6/10%` hover + 60% separators, and turns "view waveform" into a ghost-row-action (no dashed border). JS hooks intact (`add-gate-btn`, `data-target-prefix`/`data-op-name`/`onclick`, `waveform-plot-area`, the re-skinned `.waveform-plot-*` classes). **Two fixes:** replace bare `white-space:nowrap` with `text-overflow:ellipsis` + a width cap (real pulse names like `cz_unipolar_square_DragCosine.pulse` overflow narrow inspector panes), and scope the summary override with `.qubit-config-pane` ancestor (its specificity ties the global `.detail-section > summary.section-header` at `2100`, so it relies on source order). **Verified:** `_waveform_plot.html:14` consumes `.waveform-plot-area` outside `.qubit-config-pane` — do not delete the legacy `.waveform-plot-*` block.

**H8 — Datasets Status cell (avg 7.3).** Converts the outline word-pill (1px currentColor border + radius) to a gutter-status-indicator: a fixed-width colored mono glyph (`✓`/`◷→R`/`✗`/`–`) via AA `*-text` tokens + a 2px left row accent `@55%`. JS contract preserved (`.ds-status` + the four state classes, the `title` tooltip, the visible word as escaped text so `r.status` sort is unaffected). **One fix:** keep the status **word muted/default**, not recolored to the state hue — the mockup colored glyph + word + edge (3× signal), violating the restraint budget; mute the word so only the glyph + edge carry color. Prefer `R` over `◷` (U+25F7 has spotty mono coverage on Windows, where this data lives).

---

## 4. Sequencing Recommendation

**Branch topology — one stacked feature branch per surface, small PRs:**

1. **`feat/vscode-clean-generate`** — H1, H2, H6, then P2-3, P3-1/3/4. Generate-Config wizard chrome is self-contained (`.generate-root` / `#gen-*`), CSS-mostly, zero data-path risk. Land first to prove the language on a low-stakes surface.
2. **`feat/vscode-clean-config`** — H3, H4, H5, then P2-4/5, P3-2/9/10. **Order matters:** ship H3+H4+H5 together with their two cross-surface guards (keep global `.config-trace`; scope `.section-header` override under `.qubit-config-pane`/`.config-browser`), and include P2-5 in the same PR so the embedded per-qubit/pair Regenerate button doesn't read loud-filled next to the new outline banner.
3. **`feat/vscode-clean-datasets`** — H7, H8 first (table is the star), then P2-1/2/6/7/8/9/10, then P3-5/7/8. H7 is the only item with a **JS coupling**: if computed row height changes, bump `ROW_HEIGHT` in `dataset-virtual.js` and re-test the virtual scroller (recall the contain:strict zero-height bug class — verify the viewport renders, not just that CSS looks right).

**Per-PR guardrails (each PR, no exceptions):**
- Scope every override under the surface root; grep for other consumers before deleting any global rule (the `.config-trace` and `.waveform-plot-*` hazards are confirmed-real — both have a second consumer).
- **Test in light AND dark** — a `*-bg` token used as text vanishes in light (the recurring washout bug). This is the single most common regression.
- Verify each named JS/HTMX hook is untouched (IDs, `data-*`, `hx-*`, class hooks the JS queries). The mockups preserve these; keep it that way.
- Run the suite (`conda run -n qm_mng python -m pytest tests/ -q`) — these are CSS/template changes so tests should stay green (1871 pass / 96 skip); a failure means a template hook moved.
- Browser-verify the interactive bits the language touches: forms still editable, table still scannable + sortable, plots still render, virtual scroll still paints.

**Don't:** restyle shared globals (`.dot-path`, `.diff-type-badge`, `.section-header`, `.diag-badge`, `.add-gate-submit`) outside the scoped root — `/diff`, `/compare`, `/chip-compare`, `/diagnostics`, and live-drift consume them and must stay on the legacy look.

---

## 5. Open Questions (greenlight choices)

1. **Which surface first?** Recommend **Generate-Config wizard** (lowest data-path risk, self-contained chrome, proves the language) → then Config Viewer → then Datasets. Or do you want **Datasets first** because it's the most-trafficked screen day-to-day? (Datasets carries the one JS coupling, so it's slightly riskier as opener.)

2. **How aggressive on the status-word + glyph?** For Datasets Status and similar: **glyph-only colored + muted word** (strict canon, quietest) vs **keep the colored word** (louder but more legible at a glance for a column users sort by)? I recommend muted word; confirm.

3. **One filled CTA vs outline for refresh actions?** Regenerate is the *only* action on the Config banner. Make it the **filled `.btn-sync.primary`** (maximizes "press this to generate" discoverability) or the **restrained outline** (treats it as informational chrome)? The hero authors split on this; I lean filled for discoverability.

4. **Fix the latent bugs the audit surfaced, in-scope or separate?** Two are real, not cosmetic: (a) genuine Generate-Config failures currently render in caution-gold because all `showMessage()` calls pass `"warn"` never `"error"`; (b) the `done`-step digit disappears. Fold these into the redesign PRs (recommended — they're one-liners and the redesign touches the exact code), or keep the redesign purely visual and file them separately?

Relevant files: `quam_state_manager/web/static/style.css` (4529–4678 reference block; 2100 `.section-header`; 5143–5168 stepper; 5641–5677 `.config-status-*`; 5699+ `.waveform-plot-*`), `quam_state_manager/web/static/generate.js` (2691 `.config-trace`), `quam_state_manager/web/static/app.js` (294 `.config-status-host` swap; 636–645 waveform), `quam_state_manager/web/static/dataset-virtual.js` (`ROW_HEIGHT`, 406 status col), templates `generate.html`/`_generate.html`, `config.html`/`_config.html`/`_config_status.html`/`_config_preview.html`, `_qubit_config.html`/`_pair_config.html`, `_waveform_plot.html` (14, second `.waveform-plot-area` consumer), `datasets.html`/`_datasets.html`.
