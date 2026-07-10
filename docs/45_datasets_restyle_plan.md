# 45 — Dataset panel: function-preserving restyle plan

Output of the `investigate-datasets` workflow (read-only). The Dataset panel is
well-engineered; this plan brings it to the VSCode-clean language WITHOUT breaking
function. Critic verdict: plan-needs-guards (sound if guardrails honored).

All couplings confirmed against the live code. The audit's line numbers, the shared `.exp-chip` reuse (`exp-chip sort-badge param-facet` built in JS), the global `data-table td` bleed, and the colgroup-as-sole-width-source are all exactly as described. I have everything needed to write a buildable, function-first plan.

---

# Dataset Panel — Function-Preserving Restyle Plan

**Verdict:** This surface is well-engineered. The design debt is almost entirely **filled-pill active-states + card framing + raw-hex accents** — all of which are color/border-only paint changes that live *above* the virtual-scroll viewport. ~80% of the visual win ships in Tier A with zero geometry or JS risk. Only three items touch the 32px row box or JS-rendered DOM, and those are isolated and small.

The whole plan is gated on **two confirmed landmines** and a set of hard contracts. Read the guardrails first; every item below references them.

---

## 1. MUST-NOT-BREAK — Guardrails (verified against live code)

These are non-negotiable. Every change is checked against this list before merge.

### G1 — ROW_HEIGHT=32 ↔ CSS row height (THE primary coupling)
- `dataset-virtual.js:23` `var ROW_HEIGHT = 32` **must equal** the rendered `<tbody> <tr>` height, fixed at `style.css:3649-3651` `.datasets-table-virtual tbody tr { height: 32px }`.
- `renderWindow` (744-765, verified) computes `first = floor(scrollTop/32)-OVERSCAN`, `last = ceil((scrollTop+clientHeight)/32)+OVERSCAN`, `topPad = first*32`, `bottomPad = (total-last)*32`. **Every rendered row must occupy exactly 32px** or rows mis-position, the scrollbar lies, and clicks land on the wrong run.
- **Rule:** change row height ⇒ change BOTH constants AND re-run the Tier-B/C verification protocol. Default stance: **do not change row height at all** (Tier A items achieve the look without it).

### G2 — `.datasets-scroll` must stay `contain: content` + bounded resolvable height
- `style.css:3608-3614` (verified, with a load-bearing comment): `contain: strict` bundles `contain: size`, which with only `max-height` (no explicit height) collapses the box to 0 ⇒ blank table, clean console. **Never** `contain: strict` / `contain: size`.
- Keep the height-resolution chain: `.datasets-page{display:flex;flex-direction:column;height:100%;min-height:0}` → `.datasets-page > .datasets-scroll{flex:1 1 auto;min-height:8rem;max-height:calc(100vh-10rem)}` → `.datasets-scroll{max-height:calc(100vh-22rem);overflow-y:auto}`. The `max-height` is a deliberate safety net against ~39000px spacer-ballooning — **do not remove it**.

### G3 — Single width source: `table-layout:fixed` + JS `<colgroup>`
- `buildColgroup` (465-471, verified) is the ONLY width source for header AND body. Widths come from `<col data-col-key style="width:Npx">`. **Never** set `th`/`td` widths in CSS; **never** remove `table-layout:fixed`. Either reintroduces the historical header/body drift bug.

### G4 — `scrollbar-gutter: stable` (3615-3618)
- Reserves the scrollbar width so the sticky header never drifts left of the body. **Keep it.**

### G5 — Sticky header opacity + z-index
- `thead th { position:sticky; top:0; z-index:2; background:var(--pico-background-color) }` (3623-3627, verified). The opaque bg + z-index>tbody MUST survive any restyle or virtual rows scroll visibly under a see-through header.

### G6 — `ds-spacer` rows are zero-box
- `tr.ds-spacer{height:auto}` + `tr.ds-spacer td{padding:0;border:0}` (3652-3657, verified). Sized ONLY by the inline `style.height` JS writes. **Any broad `tbody td` restyle MUST exclude `.ds-spacer`** or it adds px to the spacers and shifts the whole visible window.

### G7 — Global `table.data-table td` bleeds into virtual rows
- The dataset table carries BOTH `data-table` and `datasets-table-virtual`. The global rule `table.data-table td` (1607-1615, verified: `--data-table-td-pad-v`, `--data-table-td-lh`, `--font-mono`, `font-size:var(--data-table-font)`, `vertical-align:middle`) ALSO applies to these cells, held inside 32px only by the explicit `height:32px` + `box-sizing:border-box`. **Never edit those four `--data-table-*` tokens or the global `table.data-table td` rule as part of this restyle** — it silently grows the 32px rows (and also hits `/diff`, `/compare`, `/chip-compare`, Param History, qubit/pair inspectors).

### G8 — `.exp-chip.active` is a SHARED on-state (cross-component contract)
- Verified: sort/fit/param badges are built as `class="exp-chip sort-badge ..."` (JS 1212/1249), and qubit/pair picker summaries reuse it too. `clearDatasetFilters` scopes its "All" reset to `#exp-filter-grid .exp-chip` **precisely because** sort badges share `.exp-chip`. **Any change to `.active` semantics must keep that `#exp-filter-grid` scoping**, and the new on-state must look identical across Experiments/Sort/Folder/Tag/Picker (one `.active` visual language).

### G9 — Shared-global diff classes are off-limits at the global level
- `.diff-badge` / `.diff-row-*` / `.cell-diff` are consumed by `/diff`, `/compare`, `/chip-compare`, Param History. Restyle ONLY via a scoped override under `#ds-compare-root` / `#ds-prevdiff-container` / `#ds-prevdiff-fv-body` — **never the global selector**.

### G10 — Shared inspector primitives must be scoped
- `.dataset-tabs`, `.prop-table`, `.section-header`, `.data-table`, `.tag-badge` are shared with qubit/pair inspectors. Detail-view restyles MUST be scoped under `.dataset-detail` / `#ds-detail-root` / `#ds-tab-combined` / `#ds-compare-root` / `#ds-prevdiff-*`.

### G11 — JS/HTMX/DOM contracts (rename = broken function, not just looks)
- **IDs JS binds on every init:** `#datasets-scroll #datasets-table #datasets-colgroup #datasets-thead #datasets-tbody #datasets-empty #ds-rows-data` (+`data-now/-view/-folder`) `#ds-curated-keys #ds-folders-data #ds-active-date #dataset-search #dataset-filter-count #ds-colvis-menu #ds-select-all #sort-filter-grid` (+sub-ids) `#ds-compare-bar #ds-compare-count #ds-compare-btn #inspector-pane`. `init()` returns early if `#ds-rows-data/#datasets-tbody/#datasets-scroll` are missing.
- **Class hooks the delegated handlers test:** `tr.clickable-row`, `td.col-select/.col-bookmark`, `.ds-check`, `.bookmark-star`, `.tag-badge[data-tag]`, `.tag-add-btn`, `.ds-spacer`, `th.sortable[data-sort][data-type]`, `.ds-resize-handle[data-col-key]`, `.exp-chip(.active)`, `.sort-badge`, `.folder-chip[data-folder-key]`, `.ds-status(+-ok/-run/-err/-unknown)`.
- **Row identity:** `data-id=uid` (`folder_key:run_id`) drives selection / `/dataset/<uid>` / tags / tree highlight. `row.id` (int) is display-only. Never key selection/URLs off `row.id`.
- **Compact-row schema** (`core/dataset.py:_compact_row`: `id,exp,date,time,q,p,oc,metric,bm,tags,status,dur,note,parent,hs,sm,pm,f`) is the render contract — don't rename keys; `/datasets` + `/datasets/changes-since` share it.
- **`th.sortable` click calls `e.stopPropagation()`** to block app.js's generic DOM-sort — any header markup rewrite must preserve this.
- **`#inspector-pane` carries `hx-sync='this:replace'`** — keep the re-issue guard's target intact.
- **Detail onclick handler names are the API:** `switchDatasetTab, switchCompareDatasetTab, switchDatasetStateTab, loadDatasetH5, plotOrSelectQubit, setInteractiveCols, loadPrevDiff/loadPrevDiffInto, applyFitValue, applyAllFitValues, goToFitState, toggleNoteEditor, autoGrowNote, saveDatasetNote, openTagPicker, toggleFigureZoom, copyPropTable, copyWithFeedback, togglePinDataset, closeInspector, compareSelectedDatasets, loadTrendData` — cannot be renamed.
- **Suffix-matched detail IDs** (`[id$=...]`, survive `pinned-`/`current-` prefixing): `ds-tab-combined, ds-tab-prev/-interactive/-data/-state, ds-prevdiff-container, ds-prevdiff-fv(-body), ds-interactive-container, h5-summary-container, h5-plot-container, ds-params-tree, ds-fig-params-tree, ds-compare-root, ds-cmp-tab-<name>`. Don't drop the suffixes or the `.inspector-pinned-col/.inspector-current-col` wrappers.
- **`#ds-tab-combined[data-view]` + `[data-fvsec=overview|results|figures]` `.hidden`-toggle** section machinery, and `.ds-fig-context` CSS-hidden in Full View via `data-view` — preserve attrs + semantics.
- **`#ds-compare-bar[data-state=empty|one|ready|over]`** drives the four `.ds-compare-msg-*` spans + `#ds-compare-btn.disabled` + `#ds-compare-count`. Keep the state machine and all four spans.
- **Fit buttons:** `.fit-apply-btn` + `data-fit-path/-value/-qubit/-label` (`applyAllFitValues` collects every `.fit-apply-btn` in the enclosing `.detail-section`); `.fit-goto-btn` + `data-fit-path`; all header/section buttons call `event.stopPropagation()` so they don't toggle `<summary>`.
- **prop-table copy contract:** `thead th.col-prop/.col-val`, `tbody td.col-prop/.col-val`, `td.col-val[data-copy]` is the haystack for `copyPropTable('tsv')` + click-copy. Keep these classes.
- **HDF5:** `plotOrSelectQubit(this,uid,which,varName,dims)` 5-arg shape (sticky-replay parses dims from the onclick via regex); `loadDatasetH5` re-executes the `window._h5CoordsById` script after innerHTML; `#h5-plot-container`/tiles keep non-zero `min-height` (300/340/360px). **Local** height couplings — preserve.
- **Interactive:** `.ds-interactive-list` `grid repeat(var(--ds-cols,2),minmax(0,1fr))`, tile `min-height` 340/360, `Plotly.Plots.resize` on col change, `#ds-interactive-container` identity persists. Restyle the **button** only.
- **localStorage / sessionStorage / body-class contracts:** `quam_ds_hidden_cols, quam_ds_col_widths, quam_ds_sort_key, quam_ds_sort_desc, quam_ds_sort_agg, quam_sort_banner_collapsed, quam_exp_filter_collapsed, quam_interactive_cols`; `sessionStorage quam_dataset_search_help_shown`; `body.exp-filter-collapsed`, `body.sort-banner-collapsed` (CSS hides sections via these — keep class+selector pairing).
- **`FAVORITE_TAG='favorite'`** stays in sync across `dataset-virtual.js:28` / `core/dataset.py:64` / `app.js` — filtered out of badges, shown as ⭐.
- **Sidebar tree:** `.tree-entry-click[data-uid]` (RUN→GET `/dataset/<uid>`, never activates the chip), `[data-folder-path]` context menu, `hx-post /workspace/select` only when `run_id` is none, `form='compare-form'` checkboxes, `.tree-entry-active` highlight synced by uid, `#sidebar-tree` hx-target. `data-uid` here MUST match the run-list uid.

### G12 — Virtual scroll / sort / delta-poll behaviors must be untouched by CSS
- rAF-coalesced `scheduleRender`, skip-if-unchanged `(lastFirst,lastLast)` guard, in-memory filter pipeline, multi-key sort (nulls sink LAST, stable tiebreak by `row.id`), delta polling + `IDLE_DEFER` buffering, press-vs-click `pressedRowId` capture, compare-Set persistence — these are pure JS. CSS changes must not alter the DOM structure these read (`tr.clickable-row`, `data-id`, spacer rows, column cells).

---

## 2. Prioritized Plan — by RISK tier

### TIER A — `css-only-safe` quick wins (ship FIRST; no layout/JS coupling)
*All items below live ABOVE the scroll viewport (or are paint-only color/border swaps inside it). None changes the 32px row box, the colgroup, or any JS-read DOM. This is the bulk of the value.*

**A1 — Experiments filter banner (de-card + outline on-state)** — *THE loudest violation.*
- **Direction:** Drop the `.exp-filter-section` card-background band → single hairline (or a 2px left accent). Recolor the six category accents (`#3b82f6/#22c55e/#f59e0b/#a855f7/#ec4899/#6b7280`, raw hex @ 1679-1684) to tokenized `*-text` hues. Convert `.exp-chip.active` (1722, solid `--pico-primary-background` fill) to restrained outline/segmented: transparent bg, `--pico-primary` @~45% border, `--pico-primary` text, optional 2px underline.
- **Preserve:** **G8** — `.exp-chip.active` is shared; the chosen on-state becomes the canonical `.active` look for A2/A3/A4. Keep `#exp-filter-grid`, `.exp-chip[data-exp]`, `.exp-section-label`, `#exp-filter-toggle`, `body.exp-filter-collapsed` pairing, `window._selectedExps`. Do not change which selector `.active` applies to — only its appearance.

**A2 — Sort banner (`.sort-badge.active` → outline; de-card sections; borderless inputs)**
- **Direction:** Convert `.sort-badge.active` (1870, solid `--pico-primary` fill) to the **same** outline on-state chosen in A1 (G8 — one `.active` language). Keep `.sort-badge` inactive-state as-is (already a correct outline-when-inactive theme-contrast fix — do not regress). De-card the `.sort-filter-section` 3px left borders → plain hairlines. Make `#sort-key-filter` / `#sort-param-filter` / `.param-range` inputs borderless-until-focus mono+tabular (docs/44 recipe). Leave `.sort-banner-summary` (already canon) and `.param-facet`/`.param-group-head` (already restrained outline) alone.
- **Preserve:** `#sort-filter-grid` + `#sort-col-badges/#sort-fit-badges/#sort-param-badges`, `.sort-badge[data-sort-key][data-sort-fit]`, `[data-sort-agg]/[data-sort-more]`, `#sort-banner-toggle` + `body.sort-banner-collapsed`, `.sec-caret` rotate, picker summaries, `quam_ds_sort_*`.

**A3 — Folder filter chips (8px radius + outline on-state)**
- **Direction:** `#folder-filter-grid .folder-chip` (1733-1759): radius `8px` (not `999px`), active = the A1 outline state (not solid `--pico-primary` fill).
- **Preserve:** `.folder-chip[data-folder-key]`, `toggleFolderFilter→DatasetVirtual.toggleFolder`, `state.folderFilter`, `[data-folder-key='']` clear, single-folder auto-hide. **Scope this to `#folder-filter-grid .folder-chip`** — the per-row `.col-folder .folder-chip` is a separate concern (see B3).

**A4 — Tag filter chips (8px outline; favorite = gold border not gold fill)**
- **Direction:** `#tag-filter-grid .tag-chip` (1778-1804): 8px outline, active = A1 outline state. `.tag-chip-fav` keeps the gold accent as a **colored border + ★ glyph**, never a gold fill (`#fff`-on-gold is the anti-pattern).
- **Preserve:** `.tag-chip[data-tag]`, `toggleTagFilter→window._selectedTags`, `'All'` clear, `FAVORITE_TAG` pinned-first ★. (Distinct from the in-row `.tag-badge` → B4.)

**A5 — Date filter tabs → segmented control**
- **Direction:** `.ds-date-tab` (1939-1951): drop per-pill borders, lay on one bottom hairline, active = 2px primary underline + `--pico-color` text, muted inactive.
- **Preserve:** `.ds-date-tab` class, `hx-get='/datasets?date=…'` `hx-target='#table-pane'`, the onclick that writes `#ds-active-date.value` (read by the delta poller). Keep them flex `<a>` children (`margin-left:auto`).

**A6 — Floating compare bar → neutral surface + one CTA**
- **Direction:** `#ds-compare-bar` (3859-3900): neutral `--pico-card-background-color` bar + hairline + restrained shadow (drop the all-blue fill). Compare = the single filled CTA; Clear = muted/ghost. "N selected" mono+tabular.
- **Preserve:** **G11** — `data-state` machine, all four `.ds-compare-msg-*` spans, `empty→display:none`, `#ds-compare-btn.disabled` (2–5), `#ds-compare-count`, `compareSelectedDatasets`/`clearDatasetCheckboxes`. position:fixed (outside viewport) stays.

**A7 — Borderless-until-focus search box**
- **Direction:** `#dataset-search` (1569-1582): `border-color:transparent` (or 1px bottom hairline) at rest, full `--pico-primary` border on `:focus`, replace hardcoded `rgba(0,123,255,.15)` box-shadow with a tokenized 2px ring. De-fill the `?` help button to ghost-until-hover.
- **Preserve:** `#dataset-search` id, `oninput=filterDatasetTable`, `#dataset-filter-count`, **keep `padding-right:2.5rem`** (or text slides under the `?` glyph). Rescan = single restrained-outline action; Properties summary keeps outline.

**A8 — Search help popover (lighten + ghost example buttons)**
- **Direction:** `.ds-search-help-panel` (3902-3957): single hairline + restrained shadow (trim the 24px blur). De-fill `.ds-help-example` → ghost-until-hover mono chips.
- **Preserve:** `#ds-search-help`, `#ds-search-help-toggle`, `#ds-search-help-close`, `.ds-help-example[data-example]`, `[hidden]` toggle, `sessionStorage quam_dataset_search_help_shown` first-focus open. **Do NOT break the `#sidebar-search-help` `position:static` inline reuse** — shared CSS.

**A9 — Empty-state class**
- **Direction:** Move `#datasets-empty` inline styles → an `.empty-state` class (muted prose, ~1.5rem padding, one restrained Clear CTA).
- **Preserve:** `#datasets-empty` id, `onclick=clearDatasetFilters` (scopes reset to `#exp-filter-grid .exp-chip` — G8). **Do NOT collapse the scroll box** to fit the empty state (G2).

**A10 — Sidebar run-tree (light touch)**
- **Direction:** `.entry-status` `.status-finished/-failed/-standalone` (1230-1233): drop the `*-bg` fills → colored `*-text` only. `.tree-date` divider: tokenize `--tree-date-divider-color` hex → `--pico-muted-border-color` @~45% hairline. `.tree-density-btn[aria-pressed=true]` (1246-1256): align to the A1 outline on-state.
- **Preserve:** `.tree-entry-click[data-uid]`, `[data-folder-path]`, `hx-post /workspace/select` (run_id none only), `form='compare-form'`, `.tree-entry-active`, `body.exp-list-compact`, the `--tree-*` custom props. `data-uid` MUST match run-list uid.

**A11 — Inspector header (drop card shadow; pin type scale)** *(detail; G10-scoped)*
- **Direction:** `.inspector-header-dataset`: drop the `0 2px 6px` box-shadow (keep 1px bottom hairline + sticky opaque bg). Pin title 13px / sub-meta 12px mono-tabular.
- **Preserve:** sticky opaque bg + z-index:5 (same family as G5) — **never remove**. Scope under `.inspector-header-dataset` so qubit/pair headers keep tuning.

**A12 — Detail note textarea (borderless-until-focus)** *(detail; G10-scoped)*
- **Direction:** `.ds-note-textarea` border transparent at rest → 1px `--pico-primary` on `:focus`.
- **Preserve:** `toggleNoteEditor/saveDatasetNote/autoGrowNote`, `.ds-note-input`, hidden-until-open box model (autoGrowNote bails when `ta.hidden`).

**A13 — Detail prop-tables + outcome badges + fit/section buttons** *(detail; G10-scoped)*
- **Direction:** Under `.dataset-detail` only: `.outcome-badge-lg` drop the `rgba(...,.12)` fill → colored word + optional 2px left edge @55%. Pin prop-table values ~12.5px mono-tabular, heads 12px/600 muted. Make per-row `.fit-apply-btn/.fit-goto-btn` ghost-until-hover (opacity .55 → 1 on `tr:hover/:focus-within`); demote `.fit-copy-only` to plain muted text. Make `.section-copy-btn`/`.section-apply-btn` ghost-until-hover text-actions.
- **Preserve:** **G10** scoping; `.outcome-ok/.outcome-fail` hooks (recolor only); copy contract (`td.col-val[data-copy]`); fit button data-attrs + `applyAllFitValues` collection; the `.detail-section > summary.section-header .section-*-btn` higher-specificity scope (beats `.btn-sm` margin — the documented source-order fight); `event.stopPropagation()` on header buttons.

**A14 — Detail HDF5 / Interactive / Compare / Prev-diff (scoped on-states)** *(detail; G9+G10-scoped)*
- **Direction:** `.h5-tab` → segmented control (mirror `.dataset-tabs`); `.h5-qubit-btn.active` + selected-var-row + `.ds-col-btn.active` → outline/2px-left-accent (not solid `--pico-primary` fill). For diff loudness, **scope under `#ds-compare-root` / `#ds-prevdiff-container` / `#ds-prevdiff-fv-body` only**: `.cell-diff` → transparent bg + 2px left edge @55%; `.diff-badge` → outline chips; `.diff-row-*` → 2px left edge.
- **Preserve:** **G9** — never touch the global `.cell-diff/.diff-badge/.diff-row-*`. Keep `plotOrSelectQubit` 5-arg shape, `_h5CoordsById` re-exec, plot `min-height`s, `--ds-cols` grid + `Plotly.Plots.resize`, `#ds-interactive-container` identity, `switchCompareDatasetTab`, `loadPrevDiff` container ids + AbortController, stepper disabled states.

---

### TIER B — `needs-row-height-sync` (changes the 32px row box → check G1)
*These render INSIDE the virtual row. Default plan: restyle via color/border ONLY so the box does NOT grow — then they stay Tier-A-safe in practice. Only if a change genuinely grows the box do you bump ROW_HEIGHT.*

**B1 — Run-list table body rows/cells (table-header pattern, paint-only)**
- **Direction:** Add `font-variant-numeric:tabular-nums` to value columns, `@6/10%` primary row-hover, `60%`-opacity row hairlines, a transparent 2px-left accent slot — **all via color/border, ZERO change to vertical padding, line-height, font-size, or border-width**.
- **Preservation step (G1/G6/G7):** Do NOT touch `td` vertical padding/line-height/font, and do NOT edit the global `table.data-table td` tokens. Any `tbody td` rule MUST be scoped `.datasets-table-virtual tbody tr:not(.ds-spacer) td` (or equivalent) so spacers stay zero-box. `tabular-nums`/color/border have no height effect → row stays 32px, ROW_HEIGHT unchanged. **If any change does grow the row, this becomes Tier C: bump ROW_HEIGHT to the new computed px AND re-run the protocol.**

**B2 — Run-list status chip (`.ds-status`, paint-only recolor)**
- **Direction:** Keep it as colored-text + border (already correct). If aligning to docs/43 H8 (mute the word + colored glyph), prefer a CSS-only recolor that does **not** raise `line-height` (currently `1.5` on `.72rem`, fits in 32px). The pure glyph-via-`::before` variant is CSS-only; muting the word likely needs a JS markup tweak (→ C1).
- **Preservation step (G1):** Do NOT increase `line-height`, padding, or font on `.ds-status` (it sits in the 32px row). Keep the four state classes + the title tooltip + the visible status WORD as escaped text (so `r.status` string-sort is unaffected). Recolor only.

**B3 — Per-row folder chip (`.col-folder .folder-chip`)**
- **Direction:** Recolor only; keep `padding:1px 6px` / inherited font tiny.
- **Preservation step (G1):** Scope strictly to `.col-folder .folder-chip` (NOT the A3 filter chip). Do NOT grow its box. This is the in-row twin of A3 — different selector, frozen geometry.

**B4 — In-row tag badge / bookmark star / outcome badge (paint-only)**
- **Direction:** `.col-tags .tag-badge` → restrained outline chip (transparent bg, border from `--tag-badge-text` @38%, like the detail panel already does), keep hover-× remove. `.bookmark-star` → ghost-until-hover (muted at rest, gold on hover/bookmarked). Outcome badges → keep `*-text` color, optionally 12px mono.
- **Preservation step (G1/G11):** Restyle via color/border/opacity ONLY — `.bookmark-star` is `1.1rem`, the tallest in-row glyph; **do not grow it**. Scope tag-badge changes to `.col-tags .tag-badge` so the BASE `.tag-badge` (shared, G7/G10) is untouched. Keep `.bookmark-star.bookmarked` toggle, `.tag-badge[data-tag]`/`.tag-add-btn` handlers, `FAVORITE_TAG` filtering. Verify all three still fit 32px after the change.

---

### TIER C — `needs-js-edit` (touch JS-rendered DOM or the scroller; do LAST, with care)

**C1 — `statusChip()` markup tweak (only if muting the status word for H8)**
- **Direction:** If docs/43 H8 (colored glyph + muted word) requires more than CSS, edit `statusChip()` (`dataset-virtual.js:355-361`, verified) to wrap the word in a muted `<span>` and prepend the colored glyph (`✓ ok / R run / ✗ err / – unknown` — prefer `R` over `◷` for Windows mono coverage).
- **Preservation step:** Keep the function returning the four state classes, the `title` tooltip, and the escaped status WORD present (string-sort reads `r.status`, not the DOM — but keep the word for the title/copy). The glyph-not-pill change REDUCES the box (smaller fits 32px) — still re-verify the row computes at 32px (it will; height is forced). **Default recommendation: SKIP C1** unless the user explicitly wants the muted-word treatment — B2 (CSS recolor) already gets 90% of H8 with zero JS risk.

**C2 — (Conditional) ROW_HEIGHT bump — only if Tier B genuinely grows a row**
- **Direction:** If B1/B2/B4 cannot hit the target look without growing the row (they can — this is a fallback), measure the new computed row height in DevTools, set `dataset-virtual.js:23 ROW_HEIGHT = <N>` AND `style.css:3650 .datasets-table-virtual tbody tr { height: <N>px }` together, then run the full Tier-B/C protocol below.
- **Preservation step:** Change BOTH in the same commit. Never one without the other (G1). **Strong default: avoid this entirely.**

---

## 3. Explicit "DO NOT TOUCH" list

1. **`dataset-virtual.js:23 ROW_HEIGHT = 32`** — unless paired with the CSS row height in the same commit + full re-verify (G1).
2. **`style.css:3650 .datasets-table-virtual tbody tr { height:32px }`** — same; do not remove the explicit height (rows go auto → desync).
3. **`contain: content` on `.datasets-scroll`** — never `strict`/`size`; never remove the `max-height` (G2).
4. **`scrollbar-gutter: stable`** (G4) and the **sticky `thead th` opaque bg + z-index:2** (G5).
5. **`table-layout: fixed` + the JS `<colgroup>`** — never set `th`/`td` widths in CSS (G3).
6. **The global `table.data-table td` rule + `--data-table-td-pad-v/-lh/-font` tokens** (1607-1615) — bleeds into virtual rows AND `/diff` `/compare` `/chip-compare` Param History (G7).
7. **`ds-spacer` zero-box** — never let a broad `tbody td` rule add padding/border to spacers (G6).
8. **Global `.cell-diff` / `.diff-badge` / `.diff-row-*`** — scope overrides under `#ds-compare-root`/`#ds-prevdiff-*` only (G9).
9. **Global `.dataset-tabs/.prop-table/.section-header/.data-table/.tag-badge`** — scope detail restyles under `.dataset-detail`/`#ds-detail-root`/etc. (G10).
10. **`.exp-chip.active` selector/scope semantics** — change appearance, never which elements get `.active` or the `#exp-filter-grid` reset scoping (G8).
11. **The `.section-*-btn` specificity scope** under `summary.section-header` (the documented `.btn-sm` source-order fight) — keep it winning.
12. **Any DOM id / class / data-attr / onclick handler name / localStorage key in G11** — rename = broken function.
13. **Local plot/grid `min-height`s** (`#h5-plot-container` 300/340/360, interactive tiles, `--ds-cols`) and `Plotly.Plots.resize` calls.
14. **All JS behavior in G12** — CSS must not alter the DOM structure the scroller/sort/poll reads.

---

## 4. Verification Protocol

**Tier A (all items) — paint-only checklist** (run once after the batch):
1. Load `/datasets` with a multi-folder workspace + Collections view → no blank table, no console errors.
2. Light AND dark theme: every recolored accent/chip is AA-legible; no washed-out text (the recurring light-washout trap — use `*-text` tokens + colored border, never `*-bg` as text).
3. Each restyled filter still toggles: click an Experiments/Sort/Folder/Tag chip → `.active` flips, rows filter, count strip updates. Confirm `clearDatasetFilters` ("Clear all") resets Experiments **without** wiping sort badges (G8).
4. Date tabs still `hx-get`; compare bar still cycles `empty→one→ready→over` and Compare enables only at 2–5.
5. Detail panel: open a run, switch all 8 tabs, apply-fit popup opens, copy/copy-JSON pills work, diff badges render (scoped, not global — spot-check `/compare` still looks unchanged).

**Tier B — virtual-scroller integrity (MANDATORY for any row-touching change):**
1. **Scroll math:** With ≥200 runs, scroll top→bottom→top. Rows must not jitter/overlap; the scrollbar thumb length must match total rows; no gap at the bottom.
2. **Click hit-test:** Click row #1, a mid-list row, and the last visible row → the **correct** run opens in `#inspector-pane` each time (mis-position bug manifests as "opened the wrong run"). Repeat after a fast scroll (press-vs-click `pressedRowId` path).
3. **Spacer integrity:** Inspect a `tr.ds-spacer` in DevTools → `padding:0; border:0`, height = inline only. No row-shift.
4. **Row height:** DevTools → computed height of a real `<tr>` = **exactly 32px**. If not, B item grew the box → revert or escalate to C2.
5. **Header/body alignment:** Resize the panel until the vertical scrollbar appears/disappears → header columns stay aligned with body columns (scrollbar-gutter + colgroup).
6. **No blank table:** Resize browser window small and large → table always paints (contain:content + bounded height held).

**Tier C — everything in Tier B, plus:**
7. **C1:** Sort by Status column asc/desc → order is stable and correct (statusChip word still drives sort); title tooltip present; glyph renders on Windows mono (`R` not a tofu box).
8. **C2 (if taken):** Re-run the entire Tier-B checklist; explicitly confirm `ROW_HEIGHT` constant === computed CSS row height; test with 1, 50, and 2000+ rows.
9. **Delta poll:** Leave the page idle 60s (or trigger a Rescan) → new/updated rows merge without yanking scroll; interact within 5s then wait → deferred merge flushes only when idle (IDLE_DEFER).

**Regression sweep (all tiers):** `conda run -n qm_mng python -m pytest tests/ -q` — must stay **1871 pass / 96 skip / 0 fail** (no test asserts CSS, but `_compact_row` schema + routes are covered; a key rename would surface).

---

## 5. Open Questions

1. **Status chip — CSS-only (B2) or JS markup tweak (C1)?** B2 (recolor, keep the bordered word) is zero-risk and ships in Tier A's wake. C1 (docs/43 H8: muted word + leading colored glyph `✓/R/✗/–`) needs a `statusChip()` edit. Default recommendation: **B2 only**, skip C1 unless you specifically want the muted-word/gutter-glyph treatment. Which?

2. **One unified `.active` on-state — outline-border, 2px-underline, or tinted-bg+border?** Because `.exp-chip.active` is shared across Experiments/Sort/Folder/Tag/pickers (G8), whichever you pick becomes the single on-state language everywhere. My recommendation: **transparent bg + `--pico-primary` text + `--pico-primary`@45% border** (segmented underline for the *tab-like* surfaces: date tabs, h5 tabs). Confirm the preference.

3. **De-card the run-list outer wrapper border?** The `.datasets-scroll` 1px border + 4px radius is "card framing" per H7, but it's also the only visual boundary of the scroll region. Keep it as a subtle scroll affordance, or replace with a single bottom header hairline? (Lower priority than the chips — I'd **keep** it.)

4. **Scope of this pass — run-list + filters only, or include the detail panel (A11–A14) now?** The detail-view items are all G10-scoped and low-risk, but they roughly double the surface area. Ship Tier A run-list/filters first and verify in-browser, then a second pass for detail? Or one batch?

---

## 6. Critic refinements (apply these guards)

- **[minor]** Q3 / ROW_HEIGHT coupling — VERIFIED SAFE. No 'css-only-safe' (Tier A) item touches the run-row box. The run rows render only .ds-status, .tag-badge, .outcome-badge, .bookmark-star and the .col-folder .folder-chip — none use .exp-chip. A1-A4 restyle .exp-chip/.sort-badge/.folder-chip(filter)/.tag-chip, all of which live in the filter banners ABOVE the scroll viewport (confirmed: .exp-chip at style.css:1706 is a banner chip, not an in-row class). renderWindow math (dataset-virtual.js:742-765: first=floor(scrollTop/32)-OVERSCAN, topPad=first*32) and CSS height:32px (style.css:3649-3651) match exactly. All in-row paint is correctly quarantined to Tier B (B1-B4) with explicit ROW_HEIGHT/box-growth preservation steps. The recommended unified .active on-state (transparent bg + colored 1px border) is geometry-neutral vs the inactive 1px border (style.css:1710), so no reflow.
  - FIX: No change needed — this is a confirmation that the plan's primary-coupling discipline holds. Keep B1's required scoping `.datasets-table-virtual tbody tr:not(.ds-spacer) td` so any hairline/border on tbody td never lands on the spacer <td> (the spacer is a real <td colspan> emitted at dataset-virtual.js:758/763, so a broad `tbody td{border}` WOULD add px to it).
- **[minor]** A7 (search box) cites lines 1569-1582, which is the SHARED `.table-filter input` rule — used by _compare_diff.html, _pulses.html, _pairs.html, _table.html, AND _qubits.html, not just datasets. Editing border/focus-ring there bleeds into 5 other table-filter inputs across the app. The plan's *direction* names `#dataset-search` (correct scoping intent) but the line citation points at a shared rule and G10/G11 never list `.table-filter`/`.ds-search-wrap` as a shared primitive to scope around. The dataset-specific search styling actually lives at `.ds-search-wrap input[type=search]` (style.css:3919) and `.ds-search-help-btn` (3906).
  - FIX: In A7, add the borderless-until-focus border/box-shadow override to `.ds-search-wrap input` (or `#dataset-search`), NEVER edit `.table-filter input` (style.css:1572) or `.table-filter input:focus` (1579). Add `.table-filter` to the G10 'shared inspector primitives, scope your override' list. The padding-right:2.5rem the plan says to keep is already correctly at `.ds-search-wrap input[type=search]` (3919), so it survives regardless.
- **[minor]** A9 (empty-state) — `#datasets-empty` lives INSIDE `.datasets-scroll` (template _datasets.html:237, between the scroll-div open at 228 and close at 242), so its restyle paints inside the scroll viewport. The plan correctly warns 'Do NOT collapse the scroll box to fit the empty state (G2)', and no existing `.empty-state` class collides (only comments at style.css:4189/6815 mention the phrase). The only residual risk is an `.empty-state` rule that sets height/min-height/contain on an ancestor — but A9 only restyles the <p>, so this is just a note.
  - FIX: Keep A9 scoped to the `<p id=datasets-empty>` only (padding/color/CTA). Do not let the new `.empty-state` class set height, min-height, flex, or contain on `.datasets-scroll`. The introduced class name is collision-free, so it's safe to add.
- **[minor]** Q5 / contain + height-collapse — VERIFIED SAFE. No item in the plan re-introduces contain:strict/contain:size or removes the bounded height. `.datasets-scroll` keeps contain:content (style.css:3614, with the load-bearing comment) and the max-height safety net (3601/3603). A8 restyles `.ds-search-help-panel` (3920) which is shared with `#sidebar-search-help` (style.css:3946, position:static override) — the plan flags this correctly. Q1 (mis-tagged item) and Q2 (ROW_HEIGHT honored everywhere) both come back clean.
  - FIX: No change needed. Keep the explicit 'DO NOT TOUCH #3' guardrail. When restyling .ds-search-help-panel (box-shadow/border) in A8, re-verify the sidebar's #sidebar-search-help inline copy still renders (position:static, no float-clip) — the plan already calls this out.
- **[major]** Q4 / header-body width sync — VERIFIED SAFE, plus a delivery-risk note. The colgroup is the single width source (buildColgroup at dataset-virtual.js:465-471, table-layout:fixed at style.css:3622) and the header is fully JS-built (no template <th> to restyle). th.sortable click calls e.stopPropagation() (dataset-virtual.js:493) to block app.js's DOM-sort — any header MARKUP rewrite must preserve it, but no Tier-A item rewrites header markup, so this contract is not at risk in this plan. The real residual risk is purely process: NO test asserts the ROW_HEIGHT↔CSS coupling, contain:content, or the 32px height (grep of tests/ for ROW_HEIGHT/contain:/height:32/datasets-table-virtual returns only substring false-positives in test names). So the pytest regression sweep CANNOT catch a desync — the manual Tier-B scroll/click/spacer/height protocol is the ONLY safety net.
  - FIX: Treat the Tier-B virtual-scroller integrity checklist (scroll math, click hit-test on first/mid/last visible row, DevTools computed <tr> height === 32px exactly, spacer padding:0/border:0, header/body alignment when the scrollbar toggles) as MANDATORY and blocking for ANY change that touches a tbody td/tr selector — including the 'paint-only' Tier-B items (B1/B2/B4), because the test suite provides zero coverage of this coupling. Do not rely on `pytest 1871 pass` to gate a row-touching merge.
