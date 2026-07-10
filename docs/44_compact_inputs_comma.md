# 44 — Compact data-entry inputs + thousands-comma number input

Design from the `compact-inputs-comma` workflow (22 agents). Decisions: on-blur comma
grouping; app-wide scope (wizard + Bulk Edit + inspector); exponents (1.2e9) left verbatim;
auto-grow uncapped + drag-to-narrow. Critic: go-with-changes (purpose-built resize, not the
lazy enhanceColumnResize; bounded-scroll for sticky; select carve-out; regroup on Set-all).

## Synthesis

Confirmed both facts. `enhanceColumnResize` flips `table-layout:fixed` on first call (line 535-540) — so the auto-grow/manual-resize conflict is real and table-wide. And `_groupDigits` (line 2569) returns `String(v)` early for exponential and rejects non-finite. This lets me design the coexistence correctly: **don't enhance on render — enhance lazily on first handle-grab**, so auto-grow lives by default and only the table the user actually drags goes fixed. That's the clean reconciliation every proposal missed.

Here is the final design.

---

# Final Recommended Design — Compact wizard tables + thousands-comma input

## 1) Recommendation

**(A) Compact + flexible-width + auto-grow tables.** Port the shipped, loved **`.bulk-cell` recipe** onto the wizard's two JS-rendered tables (`.gen-wiring-pin`, `.gen-pop-in`): delete the fixed `width:7rem/8rem`, kill Pico's tall control via `--pico-form-element-spacing-vertical:0.02rem` + `content-box` + `min-height:0` (gives the shipped ~22px VSCode-clean row), and use the HTML **`size` attribute** as the auto-grow primitive (re-fit on every `input` event — exact because `var(--font-mono)`+`tabular-nums` makes 1 glyph = 1ch). For per-column drag, reuse `window.enhanceColumnResize` — but with **one critical change from every reviewed proposal: call it lazily on first handle-grab, not on render.** As shipped, `enhanceColumnResize` flips the whole table to `table-layout:fixed` on its first call (app.js:535-540), which would kill auto-grow table-wide from paint #1. So we render the handles only (a tiny "arm" pass that adds `.col-resize-handle` spans but does **not** flip layout), and defer the layout-freeze to the first `mousedown` on a handle. Result: auto-grow is live by default; the moment a user drags any column, *that table* freezes to fixed-px and the dragged column pins (manual wins, exactly the user's mental model); double-click a handle reverts that column to value-fit. This is the only reconciliation that delivers "auto by default, drag to override" without the table-wide auto-grow death the red-team flagged as fatal.

**(B) Comma-grouped number input.** Accept `100,000,000` everywhere and **format on blur, never per-keystroke** (matches the rest of the app — Bulk Edit/inspector group at commit, never mid-type — and sidesteps caret-reanchoring entirely for these scientific fields). Ship one shared `window.NumberInput` helper in `app.js` (next to `_groupDigits`) holding: `stripGroupingCommas(s)` (byte-for-byte mirror of `cli.py:546/566` `_GROUPED_NUMBER` + `s.replace(/,/g,'')`, so genuine strings like `MW,FEM`/pointers/`con/slot/port` are untouched), an on-blur regrouper via `window._groupDigits`, and an `attach()` that also wires auto-grow. In the wizard the **hard prerequisite** is flipping numeric inputs from `type="number"` → `type="text" inputmode="decimal"` (a number input physically drops commas and reports `value===""`), then strip-before-`parseFloat` at the three read sites (`toBaseValue`, `ampToBase`, `setPopValue`'s bare branch) **and** the standalone freq parses (`~1952/2035`, which feed LO/band-edge diagnostics — easy to miss, must be covered). The build payload is byte-identical to today: `setPopValue` stores a stripped float into `state.spec.populate`, so `100,000,000` reaches the subprocess as the same float `100000000` does now. Server `_parse_value` already accepts commas for `/field/edit` — that path needs nothing; only the wizard (which never routes through it) needs the JS strip.

---

## 2) Concrete mechanisms

### Compact height
Replace the `.gen-pop-in` / `.gen-wiring-pin` CSS blocks (style.css:5345-5348, 5405-5409) with the `.bulk-cell` recipe (style.css:6307-6321). Load-bearing keys: `--pico-form-element-spacing-vertical:0.02rem` (the wizard never sets this today → inherits Pico's ~0.75rem → tall) + `box-sizing:content-box` + `min-height:0` + `line-height:1.3` + `padding:0.05rem 0.25rem`. Swap stale `em` font + generic `monospace` → `var(--font-mono)` + `font-variant-numeric:tabular-nums`. Tighten table chrome: `table.gen-pop-table th,td` → `padding:1px 3px; white-space:nowrap`; `table.gen-wiring th,td` → `padding:1px 4px`. `td input{margin-bottom:0}` (style.css:1631) already kills the Pico 1rem margin, so no margin fight. **Plus** (the half every proposal missed for *many-qubit scan*): make the qubit-id first column and the header sticky so they don't scroll away at 30 rows — copy `.bulk-corner`/`.bulk-rowhead` `position:sticky` onto the populate table's first `<th>`/row-label cell + sticky `thead`. This is the difference between "rows are short" and "actually scannable at 30 qubits."

### Per-column draggable resize (reuse `enhanceColumnResize`, lazily)
Two-phase to avoid the `table-layout:fixed`-on-render trap:

- **Arm phase (on every render):** a new tiny `armColumnResize(tableId)` adds the `.col-resize-handle` span to each `<th>` (idempotent — skip if present) and sets `th.position:relative`, but does **not** touch `table-layout` and does **not** freeze widths. Auto-grow stays live.
- **Activate phase (first `mousedown` on any handle):** the handle's listener calls `window.enhanceColumnResize(tableId, storageKey)` once (it's idempotent; it freezes current auto widths → px, flips to `table-layout:fixed`, restores saved widths, and binds the drag), then immediately re-dispatches/continues the drag. From then on that table is pinned-px and the dragged column persists per-index in localStorage. Double-click a handle clears that column (`th.style.width=''; delete saved[i]`).

Distinct storage keys per table: `quam_gen_wiring_col_widths`, `quam_gen_pop_<group>_col_widths`. Index-keyed persistence is safe because each table's column set is fixed per group (`POP_*_COLS` constants). Give the inner `<table>`s stable ids in their builders: `gen-wiring-tbl` (renderWiringTable) and `gen-pop-tbl-<group>` (set in **`buildPopTable`** where `group` is in scope — *not* setPopHost, which is group-agnostic).

### Auto-grow on typing-overflow + coexistence with manual resize
Auto-grow = the **`size` attribute**, re-applied on `input`:
```
fitInput(el){ var n=(el.value||el.placeholder||'').length; el.size = Math.max(4, Math.min(40, n+1)); }
```
CSS `width:auto !important; min-width:4ch` lets `size` drive width; cap `max-width:none` (let it grow; horizontal scroll on the host handles extremes — capping at 22ch + comma-widening was judged to risk unreadable internal scroll in a 22px box). Call `fitInput` at: create (in `buildPopCell`/`buildBulkCell`/wiring builder), on the `input` listener, and inside `refreshColumnCells`/`refreshAmpCells` after they reassign `.value` (those bypass the typed event, so the explicit call is the only thing keeping width correct after Set-all / unit-toggle).

**Coexistence** is solved by the lazy-arm design above, *not* by the "they layer" hand-wave the reviews correctly demolished: while a table is un-dragged it stays `table-layout:auto` and `size` drives every column live (type → box grows). The instant the user grabs a handle, `enhanceColumnResize` freezes *that table* to fixed-px (auto-grow becomes the frozen baseline; the dragged column pins). Per-column reconciliation within a fixed table is intentionally **not** attempted — once you've chosen to manually size a table, manual wins table-wide until you double-click a column back to its frozen width. This is predictable and matches "I dragged, so now I'm in control."

### Comma input — shared enhancer + wizard build-payload strip
- **Accept (server, already done):** `/field/edit` + `/field/edit-batch` route through `cli._parse_value` (cli.py:549-566) which strips commas guarded by `_GROUPED_NUMBER`. **No server change.**
- **Accept (wizard, new):** flip numeric inputs `type="number"`→`"text" inputmode="decimal"` (generate.js:1704 buildPopCell, 1764 buildBulkCell). Wrap `stripGroupingCommas` around `parseFloat` at **all** read sites: `toBaseValue` (1534), `ampToBase` (1577), `setPopValue` bare branch (1685), **and** freq parses `~1952/2035`. Missing one site = silent 100MHz→100Hz (and a missed freq-parse site = spurious band-edge diagnostic false-positive). The `_GROUPED_NUMBER` guard makes the strip a no-op on `MW,FEM`/pointers/the slash-delimited wiring pin, so it's safe to apply broadly. **Do not** attach comma logic to `.gen-wiring-pin`.
- **Format — ON BLUR (decided):** on `blur`/`change`, after the SI base is already stored, regroup the **display** string only: `var d=stripGroupingCommas(el.value); if(d!=='' && isFinite(+d)) el.value=window._groupDigits(+d); fitInput(el);`. Operates on the unit-converted display value, never the stored base → no double-scaling; `_groupDigits` guards sign+fraction+exponential. **Caret handling: none needed** — on-blur means the caret is already gone. (If a user later insists on live grouping, the same `stripGroupingCommas` core upgrades to digit-count-before-caret reanchoring — deliberately deferred.)
- **Build payload:** unchanged contract — `state.spec.populate` already holds stripped floats; `/generate/build` ships the same numbers. Round-trip is lossless because `_groupDigits` is the exact JS mirror of `units.group_digits` and `/,/g` strip is its exact inverse (never `toLocaleString` — it rounds/locale-drifts).

**One honest exponent caveat to surface:** a user who types `1.2e9` and blurs gets `1,200,000,000` (because `String(1.2e9)==="1200000000"`, no `e`). Value is byte-identical, but their compact notation is rewritten. Acceptable; noted in Open Questions.

---

## 3) Drop-in artifacts

**CSS** (style.css — replace the `.gen-pop-in` / `.gen-wiring-pin` blocks):
```css
.gen-pop-in, .gen-wiring-pin {
  width: auto !important; min-width: 4ch; margin: 0;
  padding: 0.05rem 0.25rem; line-height: 1.3;
  height: auto; min-height: 0; box-sizing: content-box;
  --pico-form-element-spacing-vertical: 0.02rem;
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: calc(0.92rem * var(--bulk-fs, 1)); font-weight: 500;
}
.gen-pop-in:focus, .gen-wiring-pin:focus { position: relative; z-index: 4; } /* never resize on focus */
select.gen-pop-in { width: auto !important; min-width: 7ch; }                /* size attr N/A to selects */
.gen-wiring-pin { min-width: 8ch; }
table.gen-pop-table th, table.gen-pop-table td { padding: 1px 3px; white-space: nowrap; }
table.gen-wiring   th, table.gen-wiring   td { padding: 1px 4px; white-space: nowrap; }
/* many-qubit scan: sticky row-head + header (copy from .bulk-corner/.bulk-rowhead) */
table.gen-pop-table thead th { position: sticky; top: 0; z-index: 3; }
table.gen-pop-table td:first-child, table.gen-pop-table th:first-child { position: sticky; left: 0; z-index: 2; }
/* .col-resize-handle already global at style.css:6376 — nothing to add */
```

**JS — shared helper** (app.js, beside `window._groupDigits` ~2581):
```js
window.NumberInput = (function () {
  var GROUPED = /^[+-]?\d[\d,]*(\.\d+)?$/;                 // mirror cli.py:546
  function strip(s){ s = String(s == null ? "" : s).trim();
    return (s.indexOf(",") >= 0 && GROUPED.test(s)) ? s.replace(/,/g, "") : s; }
  function sizeFor(s){ return Math.max(4, Math.min(40, (s || "").length + 1)); }
  function fit(el){ el.size = sizeFor(el.value || el.placeholder || ""); }
  function format(el){ var d = strip(el.value);
    if (d !== "" && isFinite(+d)) el.value = window._groupDigits(+d); fit(el); }
  function attach(el){ el.type = "text"; el.inputMode = "decimal"; el.autocomplete = "off";
    el.addEventListener("input", function(){ fit(el); });
    el.addEventListener("blur",  function(){ format(el); }); fit(el); }
  return { strip: strip, sizeFor: sizeFor, fit: fit, format: format, attach: attach };
})();

// Lazy column-resize arm — adds handles WITHOUT flipping table-layout:fixed,
// so size-attr auto-grow stays live until the user actually drags.
window.armColumnResize = function (tableId, storageKey) {
  var table = document.getElementById(tableId); if (!table) return;
  table.querySelectorAll("thead th").forEach(function (th) {
    if (th.querySelector(".col-resize-handle")) return;
    th.style.position = th.style.position || "relative";
    var h = document.createElement("span");
    h.className = "col-resize-handle"; h.title = "Drag to resize (double-click = auto-fit)";
    h.addEventListener("mousedown", function () {              // first grab → activate
      window.enhanceColumnResize(tableId, storageKey);        // idempotent: freezes + table-layout:fixed
    }, { once: true });                                       // enhanceColumnResize rebinds the real drag
    th.appendChild(h);
  });
};
```

**JS — generate.js edits:**
- `buildPopCell` (~1704) / `buildBulkCell` (~1764): for numeric/`dim` cols `window.NumberInput.attach(input)` (replaces the `type='number'` branch); leave `kind==='text'`/`select` and the wiring pin alone (but call `NumberInput.fit` on the wiring pin for auto-grow without comma logic).
- `toBaseValue` (1534), `ampToBase` (1577): `parseFloat(window.NumberInput.strip(displayVal))`.
- `setPopValue` bare branch (1685): `parseFloat(window.NumberInput.strip(raw))`.
- Freq parses `~1952`, `~2035`: wrap with `window.NumberInput.strip(...)` before `parseFloat`.
- `buildPopTable` (~1828): `table.id = 'gen-pop-tbl-' + group;`
- `setPopHost` tail: `if (node && node.id) window.armColumnResize(node.id, 'quam_gen_' + node.id + '_col_widths');`
- `renderWiringTable`: inner `<table id="gen-wiring-tbl">`; after `host.innerHTML=...` → `window.armColumnResize('gen-wiring-tbl', 'quam_gen_wiring_col_widths'); host.querySelectorAll('.gen-wiring-pin').forEach(window.NumberInput.fit);`
- `refreshColumnCells` (1795) / `refreshAmpCells` (1813): after each `input.value=...` → `window.NumberInput.fit(input);`

Reuses unchanged: `window._groupDigits` (app.js:2569), `window.enhanceColumnResize` (app.js:522), `.bulk-cell` (style.css:6307), `.col-resize-handle` (style.css:6376), `cli._parse_value`/`_GROUPED_NUMBER` (cli.py:546/566).

---

## 4) Scope & rollout order

**Wizard-first, then promote the helper app-wide.** The comma *acceptance* is already done server-side for the inspector/Bulk Edit; the only correctness gap is the wizard, so fix that first. Ship in this order, each independently mergeable:

1. **CSS height + value-fit width** (style.css only, no JS) — instant ~22px compact rows + small/wide auto value-fit. Lowest risk, biggest visible win. Includes sticky row-head/header for the 30-qubit scan.
2. **`window.NumberInput` shared helper** in app.js (auto-grow `fit` + comma `strip`/`format`/`attach`). No call sites yet — pure addition.
3. **Wizard comma wiring** — `type` flip + strip at all 5 read sites + on-blur format. This is the top user ask; gate it behind a quick check that the freq-parse sites feeding diagnostics still pass band-edge tests.
4. **Lazy `armColumnResize` + per-table ids** — drag-resize last (smallest UX delta, and the lazy-arm pattern is the novel bit worth isolating for review).
5. **Promote app-wide (optional, later):** attach `NumberInput` to inspector/Bulk Edit inputs for *live on-blur grouping consistency* and `armColumnResize` to other plain tables. Not required for the user's ask — the server already accepts their commas — so this is polish, not correctness.

---

## 5) Open Questions (please decide before build)

1. **Comma format: on-blur (recommended) vs live-as-you-type?** I'm building **on-blur** — you type raw `100000000`, it snaps to `100,000,000` when you leave the cell (matches the rest of the app, zero caret risk). Live grouping mid-type is possible but needs digit-count caret reanchoring. **OK with on-blur, or do you want to *see* commas appearing as you type?**
2. **Auto-grow max width?** I'm leaving it **uncapped** (`size` grows freely; the host scrolls horizontally for extreme values). Alternative is a cap (~22ch) that internally-scrolls a long value — but in a 22px-tall box that's nearly unreadable, especially once commas add width. **Uncapped + rely on drag-resize to narrow, or hard cap?**
3. **`1.2e9` → `1,200,000,000` on blur:** scientific notation a user types gets expanded to grouped decimal (value identical, notation changed). **Accept this rewrite, or leave any string containing `e`/`E` verbatim (don't regroup it)?**
4. **Apply the comma enhancer app-wide now, or wizard-first?** The inspector/Bulk Edit already *accept* commas server-side; promoting `NumberInput` to them only adds *live on-blur regrouping* in the box. **Wizard-only this round (recommended), or wire it into Bulk Edit + inspector in the same PR?**
