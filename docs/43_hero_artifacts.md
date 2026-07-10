# 43 — VSCode-clean hero component artifacts

Ready-to-implement HTML + CSS for the 6 Phase-1 hero components (from the design
workflow). Apply the critic's pre-merge fixes noted in `docs/43_vscode_clean_plan.md`
§5 + the critique before shipping each. Every artifact preserves the named JS contract.


## Inline message banner (warn/error)  (Generate Config wizard chrome (stepper, nav, env-pick, custom-input, message, header, type) — #gen-message in _generate.html, driven by generate.js showMessage()) — avg 7.67/10

_Turn the loud tinted *-bg pill into a quiet VSCode-clean notice: transparent background, a 2px left accent in color-mix(warning/error-text 55%), a small mono severity glyph in a fixed gutter, and the -text token for the message — theme-safe, fixed px, no *-bg fills._

**Preserves:** id="gen-message" (the only DOM hook showMessage() looks up via getElementById). Base class "gen-message" and the two variant classes "gen-message-warn" / "gen-message-error" exactly as the JS rebuilds them in `el.className = "gen-message gen-message-" + (kind || "warn")`. The [hidden] attribute as the visibility switch (`el.hidden = true/false`). textContent stays the single source of the message text — no child elements added (the glyph is a CSS ::before, so JS's `el.textContent = msg` can't clobber it). No JS, no template logic, no behaviour changed.

### Mockup
```
BEFORE  (loud tinted pill — *-bg fill + 1px box border + 0.4rem radius, 0.88em,
         couples to no token discipline; theme-fragile)

  ┌──────────────────────────────────────────────────────────────┐
  │▓▓ Select an environment in step 1 first.                  ▓▓▓ │  ← filled
  └──────────────────────────────────────────────────────────────┘     warning-bg
   (warn = solid gold wash + gold border; error = solid red wash + red border)

  ┌──────────────────────────────────────────────────────────────┐
  │▒▒ Could not load the generated config.                    ▒▒▒ │  ← filled
  └──────────────────────────────────────────────────────────────┘     error-bg


AFTER  (quiet VSCode-clean notice — transparent bg, 2px LEFT accent in
        color-mix(*-text 55%), mono severity glyph in a fixed gutter, -text token)

  ┃ !  Select an environment in step 1 first.
  ^      ^
  │      └ message text in --color-warning-text (AA), UI sans, 12.5px fixed px
  └ 2px left accent = color-mix(--color-warning-text 55%, transparent)
    ( '!' glyph = 11px mono, 1.1em gutter, --color-warning-text )

  ┃ ×  Could not load the generated config.
  ^
  └ 2px left accent = color-mix(--color-error-text 55%, transparent)
    ( '×' glyph = 11px mono, gutter, --color-error-text;  text = --color-error-text )

  No box border, no radius, no fill — just the edge + glyph + token-colored text.
  Hidden state: [hidden] -> display:none (unchanged JS toggle).
```

### HTML
```html
<!-- _generate.html  L223  — drop-in replacement.
     Markup is byte-for-byte the SAME hooks the JS owns: id, base class, hidden attr.
     showMessage() does:  el.textContent = msg
                          el.className = "gen-message gen-message-" + kind
                          el.hidden = true/false
     so #gen-message must stay a single text node with NO child elements (the JS
     blows away any children via textContent). The severity glyph + accent are
     therefore CSS-only (::before + border-left), keyed off the variant class. -->
<div id="gen-message" class="gen-message" hidden></div>
```

### CSS
```css
/* =====================================================================
   Wizard inline message banner — VSCode-clean redesign (warn / error)
   Surface: Generate Config wizard chrome (#gen-message).
   JS contract preserved verbatim:
     - id="gen-message", base class "gen-message", [hidden] toggled by JS.
     - className is rewritten to exactly  "gen-message gen-message-<kind>",
       textContent is plain text  -> NO child markup, glyph is ::before.
   Design language: docs/42_review_modal_redesign.md (state-review / btn-sync).
   Hallmarks applied here:
     - NO *-bg fill surface (the rejected loud banner). Background transparent.
     - Status = a small MONO glyph in a fixed gutter + a 2px LEFT accent in the
       SAME hue mixed to 55% — never a filled pill.
     - Color comes ONLY from the AA-readable *-text tokens + color-mix; no raw hex
       except none here. Theme-safe by construction (dark default + light) because
       every value is a token or a color-mix over one.
     - FIXED px type (12.5px body / 11px glyph), DECOUPLED from --bulk-fs.
   Scope: every selector is rooted at #gen-message (an id — maximally specific and
   unique to this wizard), so nothing here can bleed onto .toast-*, .diag-*-banner,
   .state-review or any other consumer of the *-text tokens.
   ===================================================================== */

/* The [hidden] attribute is the JS visibility switch — honour it over display. */
#gen-message[hidden] { display: none; }

/* Quiet notice base. Transparent surface; a transparent 2px left accent reserved
   for the variant so layout never shifts between warn/error. Fixed px, mono-ish:
   prose in UI sans, but pinned 12.5px so it sits in the dense VSCode-clean scale. */
#gen-message {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    margin-top: 0.75rem;
    padding: 6px 10px 6px 8px;            /* 8px left clears the 2px accent edge */
    background: transparent;              /* NO *-bg fill — the whole point */
    border: 0;
    border-left: 2px solid transparent;   /* status accent, set per variant */
    border-radius: 0;                     /* square — accent edge, not a card */
    font-size: 12.5px;
    line-height: 1.45;
    font-weight: 400;
    color: var(--pico-muted-color);       /* fallback; variants set the -text hue */
}

/* Severity glyph — a fixed ~1.1em mono gutter letter, colored by the variant.
   ::before is safe: textContent only replaces the text node, the pseudo persists.
   We DON'T set content here so an unclassed #gen-message (transient) shows nothing. */
#gen-message::before {
    flex: 0 0 auto;
    width: 1.1em;
    text-align: center;
    font-family: var(--font-mono, var(--pico-font-family-monospace, monospace));
    font-size: 11px;
    font-weight: 700;
    line-height: 1.45;                    /* match the 12.5px body line for baseline */
    font-variant-numeric: tabular-nums;
}

/* WARN — caution gold. Accent + glyph + text all from --color-warning-text,
   accent mixed to 55%. No --color-warning-bg / -border anywhere. */
#gen-message.gen-message-warn {
    border-left-color: color-mix(in srgb, var(--color-warning-text) 55%, transparent);
    color: var(--color-warning-text);
}
#gen-message.gen-message-warn::before {
    content: "!";
    color: var(--color-warning-text);
}

/* ERROR — error red. Same discipline, --color-error-text TEXT token (AA-readable)
   instead of the old --color-error-bg fill. */
#gen-message.gen-message-error {
    border-left-color: color-mix(in srgb, var(--color-error-text) 55%, transparent);
    color: var(--color-error-text);
}
#gen-message.gen-message-error::before {
    content: "\00d7";                     /* × multiplication sign as the error glyph */
    color: var(--color-error-text);
}

/* Dark default + light are both correct automatically: --color-warning-text /
   --color-error-text are defined per-theme (gold/red light, brighter gold/pink
   dark) and color-mix derives the 55% edge over each — no theme override needed.
   The single allowed intensity nudge: lift the left-accent a touch in dark so the
   2px edge stays legible on the darker panel (still the SAME hue, just 65%). */
[data-theme="dark"] #gen-message.gen-message-warn {
    border-left-color: color-mix(in srgb, var(--color-warning-text) 65%, transparent);
}
[data-theme="dark"] #gen-message.gen-message-error {
    border-left-color: color-mix(in srgb, var(--color-error-text) 65%, transparent);
}
```


## Datasets run-list "Status" cell chip — statusChip() (Finished / Running / Error / unknown)  (Datasets → dataset-browse slice (virtual run-list table, Status column). Rendered by dataset-virtual.js statusChip() L355-362, column registry L406-407; styled by style.css .ds-status* L3640-3648.) — avg 7.33/10

_Drop the per-row outline pill (full border + radius + fill-ish token) for a VSCode-clean gutter-status-indicator: a fixed-width colored mono glyph (✓ / ◷ / ✗ / –) + a 2px left color-accent on the span, borderless, AA *-text tokens, pinned 12px type decoupled from --bulk-fs._

**Preserves:** KEEPS (JS contract): the single span hook `.ds-status` and all four state classes `.ds-status-ok` / `.ds-status-run` / `.ds-status-err` / `.ds-status-unknown` (CSS works off these alone, even if the JS edit is skipped); the `title` attribute carrying the raw status (tooltip); the visible status WORD as escaped raw text (so sort via `r.status` and the Sort 'Status' badge — which read `r.status`, never this markup — are 100% unaffected); the regex→class mapping in statusChip() is unchanged. ADDS only an optional `data-ds-state` token + an inner `.ds-status-word` wrapper (both backward-compatible; class-based selectors still match). No change to the column registry (L406-407), `escapeHtml`, sortKey, or applySort.

### Mockup
```
BEFORE  (outline pill: full word, 1px currentColor border + radius, fill-ish token)
────────────────────────────────────────────────────────────────────────────
  Status
  ┌──────────┐
  │ Finished │     ← .ds-status: inline-block, padding .35rem, radius 3px,
  └──────────┘        border 1px solid currentColor, 0.72rem/600,
  ┌──────────┐        color --outcome-ok-color (#1c7a43, fill-ish)
  │ Running  │     ← border + word in --color-warning-text  (reads as a mini-card)
  └──────────┘
  ┌──────────┐
  │ Error    │     ← border + word in --outcome-fail-color (#c0392b)
  └──────────┘
     –             ← unknown: muted dash, transparent border

AFTER  (gutter glyph + 2px left edge, borderless, AA *-text, pinned 12px mono)
────────────────────────────────────────────────────────────────────────────
  Status
  ▎ ✓  Finished    ← ▎ = 2px left edge @55% success ; ✓ + word --color-success-text
  ▎ ◷  Running     ← edge @55% warning ; ◷ + word --color-warning-text
  ▎ ✗  Error       ← edge @55% error   ; ✗ + word --color-error-text
    –  –           ← unknown: no edge, muted glyph + muted dash (benign absence)

  • no box border, no radius, no fill → stops reading as a per-row card
  • glyph in a fixed ~1.1em gutter = instant scan color; word stays for the label
  • type pinned 11px glyph / 12px word, mono+tabular-nums, never × --bulk-fs
```

### HTML
```html
&lt;!--
  DROP-IN for dataset-virtual.js statusChip() (replaces L355-362).
  JS CONTRACT PRESERVED EXACTLY:
    • emits ONE &lt;span class="ds-status ds-status-{ok|run|err|unknown}"&gt;
    • title=raw status (tooltip) unchanged
    • the visible WORD is still the raw escaped status → sort (r.status) and the
      Sort 'Status' badge are untouched (they read r.status, never this markup)
  ONLY ADDITION: data-ds-state="{ok|run|err|unknown}" so the CSS ::before glyph is
  keyed off a clean token (not the localized word). Class names are kept identical,
  so even if this JS edit is skipped the CSS still works off ds-status-* alone.
--&gt;
function statusChip(status) {
    if (!status) {
        return '&lt;span class="ds-status ds-status-unknown" data-ds-state="unknown"'
             + ' title="no status"&gt;&lt;span class="ds-status-word"&gt;–&lt;/span&gt;&lt;/span&gt;';
    }
    var s = String(status).toLowerCase(), cls = 'ds-status-unknown', st = 'unknown';
    if (/(finish|success|done|complet|pass)/.test(s))      { cls = 'ds-status-ok';  st = 'ok';  }
    else if (/(run|progress|pending|queue)/.test(s))       { cls = 'ds-status-run'; st = 'run'; }
    else if (/(error|fail|abort|crash)/.test(s))           { cls = 'ds-status-err'; st = 'err'; }
    return '&lt;span class="ds-status ' + cls + '" data-ds-state="' + st + '"'
         + ' title="' + escapeHtml(status) + '"&gt;'
         + '&lt;span class="ds-status-word"&gt;' + escapeHtml(status) + '&lt;/span&gt;&lt;/span&gt;';
}

&lt;!-- Rendered HTML examples (what lands in the &lt;td class="..status.."&gt;):

  Finished:  &lt;span class="ds-status ds-status-ok"  data-ds-state="ok"  title="Finished"&gt;
                 &lt;span class="ds-status-word"&gt;Finished&lt;/span&gt;&lt;/span&gt;

  Running:   &lt;span class="ds-status ds-status-run" data-ds-state="run" title="Running"&gt;
                 &lt;span class="ds-status-word"&gt;Running&lt;/span&gt;&lt;/span&gt;

  Error:     &lt;span class="ds-status ds-status-err" data-ds-state="err" title="Error"&gt;
                 &lt;span class="ds-status-word"&gt;Error&lt;/span&gt;&lt;/span&gt;

  unknown:   &lt;span class="ds-status ds-status-unknown" data-ds-state="unknown" title="no status"&gt;
                 &lt;span class="ds-status-word"&gt;–&lt;/span&gt;&lt;/span&gt;
--&gt;
```

### CSS
```css
/* ============================================================================
   Datasets run-list "Status" cell — VSCode-clean gutter-status-indicator.
   REPLACES style.css L3640-3648 (.ds-status + .ds-status-ok/run/err/unknown).

   Language (per docs/42): a fixed-width colored MONO glyph in a gutter + a 2px
   left color-accent in the same hue @55%, borderless, color only from AA *-text
   tokens, fixed px type DECOUPLED from --bulk-fs, theme-safe by construction.

   SCOPING: every rule is prefixed `.datasets-table-virtual .ds-status` so it
   can NOT bleed onto any other surface. .ds-status is emitted only by
   statusChip() inside this table, but the table prefix makes that explicit and
   future-proof. No global class is restyled.
   ============================================================================ */

/* The chip itself: no pill. Borderless, no radius, no fill. A flex line that
   holds [glyph gutter][word]. Type pinned in px (NOT --bulk-fs). The 2px left
   accent gives the gutter-status "edge" read within the one span we own. */
.datasets-table-virtual .ds-status {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    max-width: 100%;
    /* 2px left accent (the row-edge analogue), default transparent so layout
       never shifts between states; padding clears it like the shipped rows. */
    border: 0;
    border-left: 2px solid transparent;
    border-radius: 0;
    padding: 0 0 0 6px;
    background: none;                 /* never a fill */
    font-family: var(--font-mono);
    font-size: 12px;                  /* pinned — decoupled from --bulk-fs */
    font-weight: 500;
    line-height: 1.45;
    font-variant-numeric: tabular-nums;
    vertical-align: middle;
    color: var(--pico-muted-color);   /* default/unknown text is muted */
}

/* Gutter GLYPH — fixed ~1.1em centered mono box, color from the state class.
   background:none, no border. Keyed off data-ds-state, with a class-based
   fallback so it still works if the JS edit is skipped. */
.datasets-table-virtual .ds-status::before {
    flex: 0 0 auto;
    width: 1.1em;
    text-align: center;
    font-family: var(--font-mono);
    font-weight: 700;
    font-size: 11px;                  /* gutter glyph scale, pinned */
    line-height: 1.45;
    content: '–';                     /* unknown default */
    color: inherit;
}
.datasets-table-virtual .ds-status[data-ds-state="ok"]::before,
.datasets-table-virtual .ds-status.ds-status-ok::before   { content: '\2713'; } /* ✓ */
.datasets-table-virtual .ds-status[data-ds-state="run"]::before,
.datasets-table-virtual .ds-status.ds-status-run::before  { content: '\25F7'; } /* ◷ quarter-circle ~ running */
.datasets-table-virtual .ds-status[data-ds-state="err"]::before,
.datasets-table-virtual .ds-status.ds-status-err::before  { content: '\2717'; } /* ✗ */
.datasets-table-virtual .ds-status[data-ds-state="unknown"]::before,
.datasets-table-virtual .ds-status.ds-status-unknown::before { content: '\2013'; } /* – */

/* The status WORD — mono, clipped with ellipsis, inherits the chip color.
   This is what the user reads; the glyph is the quick-scan signal. */
.datasets-table-virtual .ds-status .ds-status-word {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* ── State colors: AA-readable *-text tokens, color only (letter + 2px edge).
      Edge = same hue @55% (the shipped accent mix). NEVER the *-bg/-fill or the
      fill-ish --outcome-ok/fail-color tokens. ── */
.datasets-table-virtual .ds-status-ok {
    color: var(--color-success-text);
    border-left-color: color-mix(in srgb, var(--color-success-text) 55%, transparent);
}
.datasets-table-virtual .ds-status-run {
    color: var(--color-warning-text);
    border-left-color: color-mix(in srgb, var(--color-warning-text) 55%, transparent);
}
.datasets-table-virtual .ds-status-err {
    color: var(--color-error-text);
    border-left-color: color-mix(in srgb, var(--color-error-text) 55%, transparent);
}
.datasets-table-virtual .ds-status-unknown {
    color: var(--pico-muted-color);
    border-left-color: transparent;   /* benign absence — no accent, no color shout */
    font-weight: 400;
}

/* THEME-SAFE BY CONSTRUCTION: every value above is a theme token or a color-mix
   over one (the *-text tokens are redefined per-theme at :root / [data-theme=dark]
   — light #155724/#856404/#721c24/#46688f, dark #75d69c/#f0d070/#f5a0a8). No
   literal color is introduced here, so dark (default) and light are both correct
   with zero per-theme overrides. */
```


## Step indicator (8-step stepper) — Generate Config wizard chrome  (Generate Config wizard (#generate-root) — _generate.html stepper header; ol#gen-steps) — avg 7.67/10

_Trade the row of rounded filled pills + circular numbered badges for a VSCode-clean segmented control: one bottom hairline under the whole strip, a 2px primary underline on the active step, an 11px mono number glyph + 13px/500 label, and a green check glyph (not a filled badge) for done steps — all on a fixed px scale, decoupled from --bulk-fs._

**Preserves:** JS contract preserved exactly: the container id #gen-steps; eight li[data-step="1..8"]; the .active class and the .done class (toggled in generate.js L107-108) and the initial class="active" on step 1; the click handler that reads li.dataset.step (generate.js L3082-3084) — li are still direct children, still carry data-step. The .gen-step-num span is kept on every li (it is presentational-only; JS never queries it) and now renders the number glyph (or, on .done, a ✓ via ::before while the digit stays in the DOM). New class .gen-step-label wraps each step's text — purely presentational, not referenced by any JS or other CSS. The ol.gen-steps element and its DOM order/count are unchanged. CSS is fully scoped under .gen-steps; the old .gen-steps block (style.css L5143-5168) is replaced 1:1 with no other selector touched.

### Mockup
```
BEFORE  (rounded filled pills + circular numbered badges; 0.85em; active/done recolor FILL)

  ╭───────────╮ ╭──────────╮ ╭──────────╮ ╭──────────╮ ╭──────────╮
  │ (1) Envir…│ │(2) Netwo…│ │(3) Chass…│ │(4) Qubit…│ │(5) Wiring│  …
  ╰───────────╯ ╰──────────╯ ╰──────────╯ ╰──────────╯ ╰──────────╯
     ▲ active = filled primary pill, ●1 = solid primary circle
     done steps = ●N solid GREEN circle inside a sectioning-bg pill
  (eight rounded chips wrapping, each its own box + bg + 1.4em circle badge)


AFTER  (segmented-control: one bottom hairline; active = 2px primary underline;
        done = soft-green ✓ glyph; 11px mono number + 13px/500 label; fixed px)

   ✓  Environment    ✓  Network     3  Chassis     4  Qubits     5  Wiring   …
  ───────────────  ─────────────  ▔▔▔▔▔▔▔▔▔▔▔▔  ───────────  ───────────  ───
  └ done (muted    └ done          └ ACTIVE: text=--pico-color,
     label, green                    number=primary, 2px primary
     ✓ in gutter)                    underline coincident w/ strip hairline
  ────────────────────────────────────────────────────────────────────────────
   ▲ ONE continuous hairline (muted-border @60%) under the whole strip;
     no boxes, no fills, no circles — status = underline + glyph color only.

  number glyph: 11px mono / tabular-nums, 1.1em gutter   label: 13px/500 (600 active)
```

### HTML
```html
{# Generate Config wizard. Shell built in C1; each step's content is filled
   in by phases C2-C8. Step panels carry data-step; generate.js shows one at
   a time and drives Back/Next navigation. #}
{# Stepper redesigned to the VSCode-clean "segmented-control" language (docs/42):
   one bottom hairline under the strip, a 2px primary underline on the active
   step, an 11px mono number glyph + 13px/500 label, no filled pills/circle
   badges. JS contract preserved EXACTLY: ol#gen-steps, li[data-step=N],
   the .active/.done classes (generate.js L107-108), and the click handler that
   reads li.dataset.step (generate.js L3084). .gen-step-num is presentational
   only and is kept as the number glyph; .gen-step-label is new + JS-untouched. #}
<div id="generate-root" class="generate-root">
  <div class="gen-header">
    <h2 class="gen-title">Generate Configuration Files</h2>
    <button type="button" class="outline btn-sm" id="gen-reset"
            title="Discard everything and start over">Reset wizard</button>
  </div>

  <ol class="gen-steps" id="gen-steps">
    <li data-step="1" class="active"><span class="gen-step-num">1</span><span class="gen-step-label">Environment</span></li>
    <li data-step="2"><span class="gen-step-num">2</span><span class="gen-step-label">Network</span></li>
    <li data-step="3"><span class="gen-step-num">3</span><span class="gen-step-label">Chassis</span></li>
    <li data-step="4"><span class="gen-step-num">4</span><span class="gen-step-label">Qubits</span></li>
    <li data-step="5"><span class="gen-step-num">5</span><span class="gen-step-label">Wiring</span></li>
    <li data-step="6"><span class="gen-step-num">6</span><span class="gen-step-label">Populate</span></li>
    <li data-step="7"><span class="gen-step-num">7</span><span class="gen-step-label">Output</span></li>
    <li data-step="8"><span class="gen-step-num">8</span><span class="gen-step-label">Review</span></li>
  </ol>

  {# ...rest of #generate-root (.gen-panels etc.) unchanged... #}
```

### CSS
```css
/* ── Generate-Config wizard stepper — VSCode-clean "segmented-control" ──────────
   Replaces the old rounded-1rem filled pills + circular numbered badges. The strip
   is now a quiet row of text steps sharing ONE bottom hairline (muted-border @60%);
   the active step carries a 2px primary underline + --pico-color text; done steps
   show a soft-green check glyph (not a filled badge); the rest are muted. Status is
   color (an underline/glyph), never a fill. FIXED px scale, decoupled from --bulk-fs.
   Every selector is scoped under .gen-steps (id #gen-steps is unique to this wizard),
   so nothing here can bleep onto other surfaces.
   JS contract preserved: #gen-steps, li[data-step], .active/.done (generate.js
   L107-108), click → li.dataset.step (generate.js L3084). .gen-step-num kept as the
   number glyph; .gen-step-label is presentational-only. See docs/42_…redesign.md. */
.gen-steps {
    display: flex; flex-wrap: wrap; gap: 0;
    list-style: none; padding: 0; margin: 0 0 1.1rem;
    /* the single hairline under the whole strip — the segmentation cue */
    border-bottom: 1px solid color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent);
}
.gen-steps li {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 12px;
    /* transparent 2px underline at rest so the active step never shifts layout */
    border-bottom: 2px solid transparent;
    /* pull the per-step underline down onto the strip hairline so they coincide */
    margin-bottom: -1px;
    font-size: 13px; font-weight: 500; line-height: 1.45;
    color: var(--pico-muted-color);
    background: none; border-radius: 0;
    cursor: pointer; user-select: none;
    transition: color .1s, border-color .1s;
}
.gen-steps li:hover { color: var(--pico-color); }
/* number glyph — 11px mono, in a fixed gutter so digits align; muted at rest. */
.gen-steps li .gen-step-num {
    flex: 0 0 auto;
    display: inline-flex; align-items: center; justify-content: center;
    width: 1.1em; min-width: 1.1em;
    font-family: var(--font-mono); font-size: 11px; font-weight: 700;
    font-variant-numeric: tabular-nums; line-height: 1.45;
    color: var(--pico-muted-color);
    background: none; border-radius: 0;
}
.gen-steps li .gen-step-label { font-weight: 500; }

/* ACTIVE — 2px primary underline + full-strength text; the number goes primary. */
.gen-steps li.active {
    color: var(--pico-color);
    border-bottom-color: var(--pico-primary);
}
.gen-steps li.active .gen-step-num { color: var(--pico-primary); }
.gen-steps li.active .gen-step-label { font-weight: 600; }

/* DONE — completed steps read as quiet-confirmed: a soft-green check glyph swapped
   in for the number (color only, no filled circle), label slightly dimmed. The
   ::before sits in the same 1.1em gutter so the row never shifts; we hide the digit
   visually but keep it in the DOM (JS/markup untouched). */
.gen-steps li.done { color: var(--pico-muted-color); }
.gen-steps li.done:hover { color: var(--pico-color); }
.gen-steps li.done .gen-step-num {
    position: relative; color: transparent;   /* hide the digit, keep the gutter */
}
.gen-steps li.done .gen-step-num::before {
    content: "\2713";                          /* ✓ */
    position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    color: var(--color-success-text);
    font-family: var(--font-mono); font-size: 11px; font-weight: 700;
}

/* Theme-safe by construction: every value above is an app token or a color-mix over
   one (no raw hex). --color-success-text / --pico-primary / --pico-muted-color /
   --pico-color / --pico-muted-border-color all carry AA-readable light+dark values,
   so the strip is automatically correct in both themes with no per-theme override. */
```


## Env picker rows (Generate Config wizard, Step 1 - Environment)  (Generate Config wizard chrome - #gen-env-list inside .gen-panel[data-step="1"] (templates/_generate.html L30-35); rows injected at runtime by generate.js renderEnvList() L162-201; styled at style.css L5484-5514 (.gen-env-*) + L6556-6560 (custom row).) — avg 6.67/10

_Turn the boxed env cards (border + sectioning fill + radius + selected box-shadow glow + radio dot) into VSCode-clean hairline rows: 2px left accent for selection, 13px/600 name, mono leaf-path, and a color-only outline status chip - all on the fixed 12.5-13px ladder, decoupled from --bulk-fs._

**Preserves:** Every named JS hook is preserved so generate.js needs zero edits: the #gen-env-list mount id; the runtime-injected .gen-env-row[data-python] rows; the inner .gen-env-radio / .gen-env-name / .gen-env-path / .gen-env-status spans (exact class names the innerHTML string in renderEnvList() writes); the .selected class toggled by applySelection(); the .gen-env-status[data-state=checking|ok|bad] attribute set by probeEnv() and its ✓/✗ textContent; #gen-env-empty[hidden] toggled by renderEnvList(); the custom-interpreter ids #gen-env-custom-path / #gen-env-custom-status and the QuamGen.useCustomEnv() onclick. The template-static row markup string lives in JS and is byte-for-byte unchanged — only this template's wrapper scaffold + the CSS changed. The OPTIONAL leaf-split snippet is clearly marked as not required; current single-textContent JS renders correctly.

### Mockup
```
BEFORE  (boxed card grid — the rejected "heavy/clunky" look)
─────────────────────────────────────────────────────────────────────────
 Environment
 Pick a Python environment that has the QM stack …

 ┌───────────────────────────────────────────────────────────────────┐  ← 1px border
 │ (•) LabB        <qm-env>/python   ✓ qualang… · quam …  │   + sectioning
 └───────────────────────────────────────────────────────────────────┘   fill + radius
 ┌═══════════════════════════════════════════════════════════════════┐  ← .selected =
 ║(●) qm_mng    /home/khoon/…/qm_mng/bin/python   ✗ missing: quam     ║   inset box-shadow
 └═══════════════════════════════════════════════════════════════════┘   GLOW (banned)
 ┌───────────────────────────────────────────────────────────────────┐
 │ (•) base      /opt/conda/bin/python            checking…           │
 └───────────────────────────────────────────────────────────────────┘
   ^radio dot   ^name 600  ^path mono 0.8em (no leaf)   ^status raw text
   gap between every card · every row is its own box

AFTER  (VSCode-clean hairline rows)
─────────────────────────────────────────────────────────────────────────
 Environment
 Pick a Python environment that has the QM stack …
 ───────────────────────────────────────────────────────────────  ← top hairline (60%)
   LabB     <qm-env>/python              ✓ quam 0.5.0a3
 ───────────────────────────────────────────────────────────────  ← row hairline (60%)
 ┃• qm_mng  /home/khoon/…/qm_mng/bin/python         (✗ missing: quam)
 ┃                                                   └ outline error chip
 └ 2px PRIMARY left accent + faint primary wash = selected (no glow)
 ───────────────────────────────────────────────────────────────
   base   /opt/conda/bin/python                       checking…
 ───────────────────────────────────────────────────────────────  (last row: no hairline)
   ▲      ▲                                            ▲
   gutter name 13/600   muted mono leaf-path 12.5     status: color-only chip
   dot     (leaf = soft-blue/600 with the optional 1-line JS split)
           ok → success-text outline · bad → error-text outline · checking → plain muted
```

### HTML
```html
{# ───────────────────────────────────────────────────────────────────────────
   STEP 1 — ENVIRONMENT (Generate Config wizard).
   VSCode-clean hairline-list redesign of the env picker.

   JS CONTRACT (generate.js) — UNCHANGED. renderEnvList() still injects each row
   via innerHTML with EXACTLY these hooks, and selectEnv()/applySelection()/
   probeEnv()/checkAnyUsable() still drive them:
     • #gen-env-list ........ mount; rows appended here
     • .gen-env-row[data-python] .... one per env; click → selectEnv()
     • .gen-env-radio ....... selection marker (was a radio dot → now the gutter dot)
     • .gen-env-name ........ env name (textContent)
     • .gen-env-path ........ interpreter path (textContent, mono leaf-path)
     • .gen-env-status[data-state=checking|ok|bad] .. probe result (textContent
                               keeps its ✓ / ✗ prefixes)
     • .selected ............ toggled on the chosen row
     • #gen-env-empty[hidden] ....... shown when zero conda envs
   Only the STATIC scaffold below changed (wrapper class + intro copy); the
   runtime row markup string in generate.js is byte-for-byte preserved, so this
   is a pure CSS reskin of JS-injected nodes plus this template's chrome. #}
<section class="gen-panel active" data-step="1">
  <h3>Environment</h3>
  <p class="muted gen-env-intro">Pick a Python environment that has the Quantum Machines
    stack (<code>qualang_tools</code>, <code>quam_builder</code>, <code>quam</code>)
    installed &mdash; the generator runs there as a subprocess. Discovered conda
    envs are listed; you can also point at any interpreter (a plain
    <code>.venv</code> works too).</p>

  {# Hairline list (VSCode "Select Interpreter" feel): rows separated by a single
     60% muted hairline, a transparent 2px left accent that turns primary on
     .selected (no radio ring, no box-shadow glow, no card boxes). JS fills this. #}
  <div id="gen-env-list" class="gen-env-list">
    <p class="muted gen-env-scanning">Scanning conda environments&hellip;</p>
  </div>

  <p id="gen-env-empty" class="muted gen-env-empty" hidden>
    No conda environments found &mdash; enter a custom interpreter path below.
  </p>

  <div class="gen-env-custom">
    <label for="gen-env-custom-path" class="muted gen-env-custom-label">Custom interpreter (venv / any Python):</label>
    <div class="gen-env-custom-row">
      <input type="text" id="gen-env-custom-path" autocomplete="off"
             placeholder="/path/to/.venv/bin/python  or  C:\Scripts\python.exe">
      <button type="button" class="btn-sync primary gen-env-custom-use" onclick="QuamGen.useCustomEnv()">Use this</button>
    </div>
    <span id="gen-env-custom-status" class="muted"></span>
  </div>
</section>

{# ── OPTIONAL (1-line JS tweak) to get the dim-dirs + soft-blue-LEAF path split ──
   The leaf emphasis in the shipped language needs two spans, but generate.js
   currently sets .gen-env-path via textContent (single text node — CSS alone
   can't bold just the basename). If/when you touch the JS, replacing ONLY the
   path-fill line keeps every other hook identical:

     // was:  row.querySelector(".gen-env-path").textContent = env.python;
     var p = env.python, i = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'));
     var pe = row.querySelector(".gen-env-path");
     pe.textContent = "";
     if (i >= 0) {
       var d = document.createElement("span"); d.className = "dp-dirs";
       d.textContent = p.slice(0, i + 1); pe.appendChild(d);
     }
     var l = document.createElement("span"); l.className = "dp-leaf";
     l.textContent = i >= 0 ? p.slice(i + 1) : p; pe.appendChild(l);

   The CSS below styles BOTH cases: with the split it renders dim-dirs + blue
   leaf; without it (current JS) the whole path renders as the muted mono path.
   No behaviour, no other hook, changes either way. #}
```

### CSS
```css
/* ════════════════════════════════════════════════════════════════════════════
   Generate Config · Step 1 — Environment picker  (VSCode-clean hairline rows)
   Scoped under #gen-env-list / .gen-env-* so it can't bleed onto other surfaces.
   Fixed px type ladder (11 · 12.5 · 13) — NEVER routed through --bulk-fs.
   Theme-safe by construction: every value is an app token or a color-mix over
   one (dark default + light both correct). Replaces L5484-5514 + L6556-6560.
   ════════════════════════════════════════════════════════════════════════════ */

/* Intro / scaffold prose — sans, quiet; the list is the star. */
.gen-env-intro { font-size: 0.85em; }

/* ── hairline-list container: gap:0, the hairline IS the separation ──────────── */
#gen-env-list.gen-env-list {
    display: flex; flex-direction: column; gap: 0;
    margin: 0.4rem 0 0;
    font-size: 13px; line-height: 1.45;          /* list-body anchor size */
    border-top: 1px solid color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent);
}
/* pre-render placeholder line sits inside the hairline frame, reads as quiet prose */
#gen-env-list .gen-env-scanning {
    margin: 0; padding: 0.6rem 10px; font-size: 0.85em;
}

/* ── ROW: borderless, single 60% bottom hairline, transparent 2px left accent ── */
#gen-env-list .gen-env-row {
    display: flex; align-items: baseline; gap: 8px;
    border: 0;
    border-left: 2px solid transparent;          /* reserved for .selected accent */
    border-bottom: 1px solid color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent);
    border-radius: 0;
    background: transparent;                      /* no card / sectioning fill */
    box-shadow: none;                             /* kill the old glow */
    padding: 4px 8px 4px 10px;                    /* 10px left clears the 2px edge */
    cursor: pointer;
    transition: background .1s, border-left-color .1s;
}
#gen-env-list .gen-env-row:last-child { border-bottom: 0; }
#gen-env-list .gen-env-row:hover {
    background: color-mix(in srgb, var(--pico-primary) 6%, transparent);
}
[data-theme="dark"] #gen-env-list .gen-env-row:hover {
    background: color-mix(in srgb, var(--pico-primary) 10%, transparent);
}

/* ── SELECTED: a 2px primary left edge + faint primary wash — NOT a box-shadow. ─ */
#gen-env-list .gen-env-row.selected {
    border-left-color: var(--pico-primary);
    background: color-mix(in srgb, var(--pico-primary) 8%, transparent);
}
[data-theme="dark"] #gen-env-list .gen-env-row.selected {
    background: color-mix(in srgb, var(--pico-primary) 12%, transparent);
}

/* ── GUTTER DOT (was the radio ring): color-only marker in a fixed 1.1em box.
   Hidden at rest (the accent edge already signals selection); on .selected it
   becomes a single primary dot — no fill ring, no border. JS leaves it empty &
   aria-hidden, so the glyph is drawn in CSS via ::before. ───────────────────── */
#gen-env-list .gen-env-radio {
    flex: 0 0 auto; align-self: center;
    width: 1.1em; height: 1.1em; margin: 0;
    border: 0; border-radius: 0; background: none;   /* drop the dot-ring */
    display: inline-flex; align-items: center; justify-content: center;
    font-family: var(--font-mono); font-size: 11px; line-height: 1.45;
    color: var(--pico-primary);
}
#gen-env-list .gen-env-radio::before { content: ""; }
#gen-env-list .gen-env-row.selected .gen-env-radio::before { content: "\2022"; } /* • */

/* ── NAME: 13px / 600 sans — the row's emphasised label. ─────────────────────── */
#gen-env-list .gen-env-name {
    flex: 0 0 auto;
    font-size: 13px; font-weight: 600; line-height: 1.45;
    color: var(--pico-color);
}
#gen-env-list .gen-env-row.selected .gen-env-name { color: var(--pico-color); }

/* ── PATH: mono leaf-path — dim parent dirs + soft-blue bold LEAF.
   Works whether JS injects a single text node (whole path → muted mono) or the
   dp-dirs/dp-leaf split (dim dirs + emphasised leaf). No tinted band. ────────── */
#gen-env-list .gen-env-path {
    flex: 1 1 auto; min-width: 0;
    font-family: var(--font-mono); font-variant-numeric: tabular-nums;
    font-size: 12.5px; font-weight: 400; line-height: 1.45;
    color: var(--pico-muted-color);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    direction: rtl; text-align: left;            /* keep the basename visible on overflow */
}
#gen-env-list .gen-env-path .dp-dirs { color: var(--pico-muted-color); font-weight: 400; }
#gen-env-list .gen-env-path .dp-leaf { color: var(--diag-info-text); font-weight: 600; }

/* ── STATUS: restrained outline CHIP, color-only by data-state — never a fill.
   checking = muted (no border, plain text); ok = success-text; bad = error-text.
   The JS-injected ✓ / ✗ prefixes ride along as plain glyphs. ────────────────── */
#gen-env-list .gen-env-status {
    flex: 0 0 auto; align-self: center; white-space: nowrap;
    font-family: var(--font-mono); font-variant-numeric: tabular-nums;
    font-size: 11px; font-weight: 600; line-height: 1.5;
    padding: 0 5px; border-radius: 8px;
    border: 1px solid transparent; background: none;
}
#gen-env-list .gen-env-status[data-state="checking"] {
    color: var(--pico-muted-color);
    border-color: transparent; padding-left: 0; padding-right: 0;  /* plain text while pending */
}
#gen-env-list .gen-env-status[data-state="ok"] {
    color: var(--color-success-text);
    border-color: color-mix(in srgb, var(--color-success-text) 50%, transparent);
}
#gen-env-list .gen-env-status[data-state="bad"] {
    color: var(--color-error-text);
    border-color: color-mix(in srgb, var(--color-error-text) 50%, transparent);
}

/* ── EMPTY state: muted prose, generous padding so absence reads intentional. ── */
.gen-env-empty {
    padding: 1.25rem 10px; margin: 0;
    font-size: 0.85em; color: var(--pico-muted-color);
}

/* ── CUSTOM interpreter row (unchanged behaviour; brought onto the same scale).
   The "Use this" button adopts .btn-sync.primary (the one filled CTA here). ─── */
.gen-env-custom { margin-top: 0.75rem; }
.gen-env-custom-label { font-size: 0.85em; }
.gen-env-custom-row { display: flex; gap: 0.4rem; margin: 0.2rem 0; align-items: center; }
.gen-env-custom-row input {
    flex: 1 1 auto; min-width: 0; margin: 0;
    font-family: var(--font-mono); font-variant-numeric: tabular-nums;
    font-size: 12.5px;
}
.gen-env-custom-row .gen-env-custom-use { margin: 0; white-space: nowrap; }
#gen-env-custom-status {
    font-family: var(--font-mono); font-size: 11px; line-height: 1.5;
    color: var(--pico-muted-color);
}
```


## Config status banner (Regenerate CTA + stale note + version meta + warnings/error block)  (Generate Config / Config Viewer — gen-config-forms slice: _config_status.html (the banner; included by _config.html:12 into #config-status.config-status-host) + the embedded per-qubit/pair "Generated config" headers in _qubit_config.html / _pair_config.html. Drop-in redesign of _config_status.html plus a scoped CSS block under .config-status-host.) — avg 7.67/10

_De-card the boxed banner into a hairline-capped action region: one restrained Regenerate CTA, a warning gutter-glyph + muted stale/warnings notes (color-only, no filled pills), 12px mono+tabular meta, and a theme-safe sectioning-bg traceback — all on the fixed VSCode-clean px ladder, scoped so it can't touch other surfaces._

**Preserves:** IDs/classes kept verbatim for the JS + HTMX + app.js contracts: outer .config-status-banner and the .config-status-error modifier class; the swap host .config-status-host (matched by _config.html:12 and by app.js:295 which sets shouldSwap on >=400 to render the error body — still works because the class is unchanged); the Regenerate button's hx-post="/config/regenerate", hx-target="closest .config-status-host", hx-swap="innerHTML", hx-indicator="closest .config-status-host", hx-disabled-elt="this", and title; the .htmx-indicator class on the running-spinner span (still driven by the global .htmx-request show/hide at style.css:664-666 — I only restyle the inner layout, scoped); the .config-stale-note, .config-status-error-msg, .config-status-meta, .config-warnings, .config-trace class names (re-skinned, not renamed); the <details>/<summary> Traceback + Warnings disclosures; every Jinja contract — config_stale, meta, meta.at, meta.versions(.quam/.quam_builder/.qm), meta.qubits, meta.qubit_pairs, meta.unsaved_at_generate, meta.warnings, error, traceback. The embedded per-qubit/pair empty states keep .config-inline-empty + .config-status-host + the .add-gate-submit button untouched (the new .config-inline-empty rule only de-cards layout; if you also swap that button to .btn-sync the CTA styling carries, but no markup change is required). NEW classes added (presentation-only, no JS reads them): .config-regen-btn, .config-status-spinner/.config-status-spin, .config-note-glyph(-warn/-err), .config-note-body/-lead/-text, .config-error-head/-lead/-pre, .config-meta-line/-item/-strong/-sep, .config-disclosure.

### Mockup
```
BEFORE  (boxed card · filled blue CTA · filled warn pill · rem type · dark hardcoded <pre>)
┌──────────────────────────────────────────────────────────────────────┐  ← 1px #888 box
│  ┌───────────────────────────────┐                                    │    +6px radius
│  │  Regenerate from loaded chip  │   running generate_config()…       │    +elevated fill
│  └───────────────────────────────┘     (filled blue, white text)      │
│                                                                        │
│  ▟ config may be stale ▙  The state changed after this config was…     │  ← filled warn pill
│   (999px warning-bg pill)                                              │
│                                                                        │
│  Last good: 2026-06-17 14:02 · quam 0.5.0a3 · quam_builder 0.4 · qm…   │  ← 0.78rem #888
│  ▸ 2 warning(s)                                                        │
└──────────────────────────────────────────────────────────────────────┘

  (error variant: whole box turns red-fill; <pre> is a hardcoded #1c1f24 dark
   slab with #ddd text — unreadable/odd in LIGHT theme)


AFTER  (hairline region · one outline CTA · warn gutter-glyph · 12px mono meta · theme-safe <pre>)

   ⟳ Regenerate from loaded chip      ◜ running generate_config()…        ← outline .btn-sync
   └ outline, restrained ┘              (muted ghost spinner, ring)          (the ONE CTA)

   ⚠  Config may be stale.                                               ← warn TEXT-token glyph
      The state changed after this config was generated — Regenerate…       (no pill, muted detail)

   Last good 2026-06-17 14:02 · quam 0.5.0a3 · quam_builder 0.4 · qm 1.2  ← 12px mono tabular-nums
   ▸ ⚠ 2 warnings                                                            (timestamp bold only)
  ──────────────────────────────────────────────────────────────────────  ← single bottom hairline
                                                                              (caps region, no box)

  ERROR variant:
  ╷
  ╎  ● Couldn't generate config                                          ← 2px LEFT error accent
  ╎  ┌────────────────────────────────────────────────────────────┐        + error-text glyph
  ╎  │ RuntimeError: octave LO out of range for element q3.xy       │        (no full red box)
  ╎  └────────────────────────────────────────────────────────────┘     ← <pre> = sectioning-bg
  ╎  ▸ Traceback                                                            + --pico-color (light-safe)
  ╵
```

### HTML
```html
<div class="config-status-banner{% if error %} config-status-error{% endif %}">
    {# ── Action region: one restrained CTA + ghost running-spinner. The banner is
       no longer a boxed card — it is a region capped by a single bottom hairline
       (see .config-status-banner CSS); on error it gains a 2px left accent + an
       error gutter glyph, not a full red box. Regenerate is a refresh, not a
       destructive action, so it is an OUTLINE .btn-sync (the single CTA), not a
       loud filled blue button. All hx-* hooks + .config-status-host targeting +
       hx-indicator/hx-disabled-elt preserved verbatim from the original. #}
    <div class="config-status-actions">
        <button type="button"
                class="btn-sync config-regen-btn"
                hx-post="/config/regenerate"
                hx-target="closest .config-status-host"
                hx-swap="innerHTML"
                hx-indicator="closest .config-status-host"
                hx-disabled-elt="this"
                title="Run machine.generate_config() on the currently loaded chip">
            {% if meta %}&#x21bb; Regenerate from loaded chip{% else %}&#x21bb; Generate config from loaded chip{% endif %}
        </button>
        <span class="htmx-indicator config-status-spinner">
            <span class="config-status-spin" aria-hidden="true"></span>running <code>generate_config()</code>&hellip;
        </span>
    </div>

    {% if config_stale is defined and config_stale %}
    {# Staleness = a warning GUTTER GLYPH + muted note (color-only on the glyph via
       --color-warning-text), NOT the old filled .waveform-warn-chip pill. #}
    <div class="config-stale-note">
        <span class="config-note-glyph config-note-glyph-warn" aria-hidden="true">&#9888;</span>
        <span class="config-note-body">
            <span class="config-note-lead">Config may be stale.</span>
            <small class="config-note-text">
                {% if meta and meta.unsaved_at_generate %}
                This preview predates the unsaved edits you had at generate time — Regenerate to include them.
                {% else %}
                The state changed after this config was generated — Regenerate to refresh.
                {% endif %}
            </small>
        </span>
    </div>
    {% endif %}

    {% if error %}
    {# Error = a region accent (2px left edge on .config-status-error) + an error
       gutter glyph + muted traceback, NOT a full red filled box. The <pre> uses
       the sectioning surface + --pico-color so it is theme-safe in light too. #}
    <div class="config-status-error-msg">
        <div class="config-error-head">
            <span class="config-note-glyph config-note-glyph-err" aria-hidden="true">&#9679;</span>
            <strong class="config-error-lead">Couldn't generate config</strong>
        </div>
        <pre class="config-error-pre">{{ error }}</pre>
        {% if traceback %}
        <details class="config-disclosure">
            <summary>Traceback</summary>
            <pre class="config-trace">{{ traceback }}</pre>
        </details>
        {% endif %}
    </div>
    {% endif %}

    {% if meta %}
    {# Version/count META — 12px mono + tabular-nums, muted, segmented by a thin
       mono separator. Each stat is a quiet token; the "Last good" timestamp is
       emphasised only by weight, not color. #}
    <div class="config-status-meta">
        <span class="config-meta-line">
            <span class="config-meta-item">Last&nbsp;good <b class="config-meta-strong">{{ meta.at }}</b></span>
            {% if meta.versions %}
            <span class="config-meta-sep" aria-hidden="true">·</span>
            <span class="config-meta-item">quam&nbsp;{{ meta.versions.quam or '?' }}</span>
            <span class="config-meta-sep" aria-hidden="true">·</span>
            <span class="config-meta-item">quam_builder&nbsp;{{ meta.versions.quam_builder or '?' }}</span>
            <span class="config-meta-sep" aria-hidden="true">·</span>
            <span class="config-meta-item">qm&nbsp;{{ meta.versions.qm or '?' }}</span>
            {% endif %}
            {% if meta.qubits %}
            <span class="config-meta-sep" aria-hidden="true">·</span>
            <span class="config-meta-item">{{ meta.qubits | length }}&nbsp;qubits, {{ meta.qubit_pairs | length }}&nbsp;pairs</span>
            {% endif %}
        </span>
        {% if meta.warnings %}
        {# Warnings disclosure — a warning gutter glyph + muted count summary;
           the list itself is plain muted rows, color-only on the glyph. #}
        <details class="config-disclosure config-warnings">
            <summary>
                <span class="config-note-glyph config-note-glyph-warn" aria-hidden="true">&#9888;</span>
                {{ meta.warnings | length }} warning{{ 's' if (meta.warnings | length) != 1 }}
            </summary>
            <ul>
                {% for w in meta.warnings %}
                <li>{{ w }}</li>
                {% endfor %}
            </ul>
        </details>
        {% endif %}
    </div>
    {% endif %}
</div>
```

### CSS
```css
/* ===================================================================
   Config status banner — VSCode-clean redesign (see docs/42).
   De-carded: a hairline-capped action region, not a boxed card. One
   restrained CTA, warning/error as gutter-glyph + muted note (color-only,
   never a filled pill or box), 12px mono+tabular meta, theme-safe traceback.
   EVERY rule scoped under .config-status-host so it can't bleed onto other
   surfaces; the embedded per-qubit/pair empty state shares the host class.
   No --bulk-fs anywhere — pinned px on the 10.5/11/12/12.5/13 ladder.
   Replaces the old .config-status-* / .config-stale-note / .config-trace /
   .waveform-warn-chip(-in-banner) rules at style.css 5641-5677,5728-5733.
   =================================================================== */

/* REGION, not a card: drop the 1px box border + 6px radius + elevated fill;
   separate from the config body below with a single bottom hairline. */
.config-status-host .config-status-banner {
    border: 0;
    border-left: 2px solid transparent;            /* status accent only (error) */
    border-bottom: 1px solid color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent);
    border-radius: 0;
    background: none;
    padding: 0.5rem 0.25rem 0.7rem 0.5rem;         /* left clears the 2px accent */
    margin: 0 0 0.9rem;
    display: flex; flex-direction: column; gap: 0.45rem;
    font-size: 13px; line-height: 1.45;
}
/* Error: a 2px LEFT accent (error TEXT token @55%), NOT a full red box. */
.config-status-host .config-status-banner.config-status-error {
    border-left-color: color-mix(in srgb, var(--color-error-text) 55%, transparent);
    background: color-mix(in srgb, var(--color-error-text) 6%, transparent);
}

/* ── Action region: ONE restrained CTA + ghost spinner ────────────── */
.config-status-host .config-status-actions {
    display: flex; align-items: center; gap: 0.7rem; flex-wrap: wrap;
}
/* The single CTA. Regenerate is a refresh (not destructive) → OUTLINE .btn-sync
   tokens, not the old loud filled blue .add-gate-submit. .btn-sync is already a
   global VSCode-clean class; .config-regen-btn just pins the height/label here. */
.config-status-host .config-regen-btn {
    font-size: 13px; font-weight: 500; line-height: 1.2;
    padding: 0.4rem 0.85rem;
}
.config-status-host .config-regen-btn:disabled { opacity: 0.55; cursor: default; }
/* Ghost running-spinner — muted, quiet; shown by the global .htmx-indicator
   show/hide. Code-style mono for the function name; small spinning ring. */
.config-status-host .config-status-spinner {
    align-items: center; gap: 0.4rem;
    color: var(--pico-muted-color);
    font-size: 12px; font-family: var(--font-mono);
}
.htmx-request .config-status-host .config-status-spinner,
.config-status-host.htmx-request .config-status-spinner { display: inline-flex; }
.config-status-host .config-status-spinner code {
    font-family: var(--font-mono); background: none; padding: 0; color: inherit;
}
.config-status-host .config-status-spin {
    width: 11px; height: 11px; border-radius: 50%;
    border: 2px solid color-mix(in srgb, var(--pico-muted-color) 35%, transparent);
    border-top-color: var(--pico-primary);
    display: inline-block; animation: config-status-spin 0.7s linear infinite;
}
@keyframes config-status-spin { to { transform: rotate(360deg); } }

/* ── NOTE GLYPHS (gutter, color-only) — replace the filled pills ──────
   A fixed ~1.1em centered glyph, colored by a *-text token; the row body is
   muted prose. Warning = --color-warning-text, error = --color-error-text. */
.config-status-host .config-note-glyph {
    flex: 0 0 auto; display: inline-block;
    width: 1.1em; text-align: center;
    font-family: var(--font-mono); font-size: 12px; line-height: 1.45;
}
.config-status-host .config-note-glyph-warn { color: var(--color-warning-text); }
.config-status-host .config-note-glyph-err  { color: var(--color-error-text); font-size: 9px; }

/* ── Stale note — warning glyph + lead + muted detail (no pill) ──────── */
.config-status-host .config-stale-note {
    display: flex; align-items: baseline; gap: 6px;
    margin: 0; padding: 0; background: none; border: 0;
}
.config-status-host .config-note-body { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.config-status-host .config-note-lead {
    color: var(--color-warning-text); font-weight: 600; font-size: 12.5px;
}
.config-status-host .config-note-text {
    color: var(--pico-muted-color); font-size: 0.85em; line-height: 1.4;
}

/* ── Error block — glyph head + muted prose + theme-safe traceback ───── */
.config-status-host .config-status-error-msg {
    display: flex; flex-direction: column; gap: 0.35rem;
    color: var(--pico-muted-color); font-size: 12.5px;
}
.config-status-host .config-error-head { display: flex; align-items: baseline; gap: 6px; }
.config-status-host .config-error-lead { color: var(--color-error-text); font-weight: 600; }
/* Traceback / error <pre>: sectioning surface + --pico-color, NOT the hardcoded
   dark #1c1f24/#ddd that was theme-broken in light. Mono + tabular. */
.config-status-host .config-error-pre,
.config-status-host .config-trace {
    margin: 0;
    background: var(--pico-card-sectioning-background-color);
    color: var(--pico-color);
    border: 1px solid color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent);
    border-radius: 4px;
    padding: 0.45rem 0.6rem;
    font-family: var(--font-mono); font-size: 11.5px; line-height: 1.4;
    font-variant-numeric: tabular-nums;
    overflow: auto; max-height: 12rem; white-space: pre-wrap; word-break: break-word;
}
.config-status-host .config-trace { font-size: 11px; max-height: 16rem; }

/* ── META line — 12px mono + tabular, muted; timestamp bold-only ────── */
.config-status-host .config-status-meta {
    display: flex; flex-direction: column; gap: 0.25rem;
    color: var(--pico-muted-color);
}
.config-status-host .config-meta-line {
    display: flex; align-items: baseline; flex-wrap: wrap; gap: 0 6px;
    font-family: var(--font-mono); font-size: 12px; line-height: 1.5;
    font-variant-numeric: tabular-nums;
}
.config-status-host .config-meta-item { white-space: nowrap; }
.config-status-host .config-meta-strong { color: var(--pico-color); font-weight: 600; }
.config-status-host .config-meta-sep { color: color-mix(in srgb, var(--pico-muted-color) 60%, transparent); }

/* ── Disclosures (Traceback / Warnings) — quiet summaries ───────────── */
.config-status-host .config-disclosure { margin: 0; }
.config-status-host .config-disclosure > summary {
    cursor: pointer; list-style: revert;
    font-size: 12px; font-weight: 500; color: var(--pico-muted-color);
    padding: 0.15rem 0;
}
.config-status-host .config-disclosure > summary:hover { color: var(--pico-color); }
.config-status-host .config-warnings > summary {
    display: flex; align-items: baseline; gap: 4px;
}
.config-status-host .config-warnings ul {
    margin: 0.2rem 0 0; padding: 0 0 0 1.4em; list-style: disc;
    font-family: var(--font-mono); font-size: 11.5px; line-height: 1.5;
    color: var(--pico-muted-color);
}
.config-status-host .config-warnings li { margin: 0; }

/* ── Embedded per-qubit/pair empty state (_qubit_config / _pair_config):
   shares .config-status-host, so it inherits the CTA + spinner styling above.
   Lay it out as a quiet stack, not a boxed card. ────────────────────── */
.config-status-host.config-inline-empty {
    display: flex; flex-direction: column; align-items: flex-start; gap: 0.4rem;
    border: 0; background: none; padding: 0;
}
.config-status-host.config-inline-empty p { margin: 0; color: var(--pico-muted-color); font-size: 12.5px; }

/* Reduced-motion: stop the spinner ring from animating. */
@media (prefers-reduced-motion: reduce) {
    .config-status-host .config-status-spin { animation: none; }
}
```


## Operations table (Op / Element / Pulse / Waveform) with per-row "view waveform" button — Generate Config gen-config-forms slice  (Generate Config embeds + Config Viewer: _qubit_config.html / _pair_config.html (the per-qubit/pair "Generated config" Operations section), with re-skinned waveform-plot states shared by the /config Config Viewer page (_config.html / _config_status.html). New rules scoped under .gen-config-ops (the operations &lt;details&gt;) and .qubit-config-pane so the /config top-level-key sections and the pair-page .add-gate-btn are untouched.) — avg 8/10

_Re-skins the Operations table into the shipped VSCode-clean table-header pattern — bandless weight-600 summary, single 60% hairline under the header, plain mono+tabular-nums value cells (no code-badges), primary-tint row hover, and a ghost-until-hover "view waveform" action instead of the dashed-outline mongrel — all px-pinned and theme-safe, with every JS hook preserved._

**Preserves:** IDs/classes kept exactly: button keeps class="add-gate-btn" (plus new cosmetic-only .wf-view-btn) and all three JS hooks app.js:636-645 reads — data-target-prefix="{{ target_prefix }}", data-op-name="{{ op.op_name }}", onclick="window.showWaveformPlot(this)". Render node keeps id="waveform-plot-area" class="waveform-plot-area" inside .qubit-config-pane (app.js:642 scope.querySelector('.waveform-plot-area')). The app.js-written classes .waveform-plot-loading / .waveform-plot-err / .waveform-plot-caption are preserved as targetable names (re-skinned, not renamed). Jinja contract preserved: {% for op in operations %}, op.op_name / op.element / op.pulse, {{ target_prefix }}, operations|length. Outer .detail-section + summary.section-header classes kept (so the &lt;details&gt; open/close + chevron + any :has() rules still work); .prop-table kept on the table (new .ops-table is the scoping hook). No handler rebind needed — the inline onclick is untouched.

### Mockup
```
BEFORE  (tinted-band summary · code-badge cells · uppercase th · dashed-outline button)
┌──────────────────────────────────────────────────────────────────────┐
│▓▓ Operations (4) ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  ← tinted band band │
├──────────────────────────────────────────────────────────────────────┤
│  OP        ELEMENT      PULSE        WAVEFORM      ← uppercase 0.8em th │
│ ┌──────┐ ┌──────────┐ ┌──────────┐                                     │
│ │ x180 │ │ qA1.xy   │ │ x180_pls │   ┌- - - - - - - - -┐  ← 1px DASHED │
│ └──────┘ └──────────┘ └──────────┘   ¦  view waveform  ¦    #888 0.9rem │
│  (Pico code-badge fills, em-scaled)  └- - - - - - - - -┘               │
│ ┌──────┐ ┌──────────┐ ┌──────────┐   ┌- - - - - - - - -┐               │
│ │ y90  │ │ qA1.xy   │ │ y90_pls  │   ¦  view waveform  ¦               │
│ └──────┘ └──────────┘ └──────────┘   └- - - - - - - - -┘               │
│ (default Pico borders under/around every cell)                         │
└──────────────────────────────────────────────────────────────────────┘

AFTER  (bandless weight-600 head · plain mono cells · ONE header hairline · ghost action)
  Operations (4)                                  ← 13px/600, no band
 ─────────────────────────────────────────────────────────────────────  60% hairline
  Op       Element     Pulse                        Waveform   ← 12px/600 muted sans
 ─────────────────────────────────────────────────────────────────────  60% hairline
  x180     qA1.xy      x180_pls                     ⌁ view waveform     ← .55 ghost
 ·····················································  (row sep = 60% hairline)
  y90      qA1.xy      y90_pls                       ⌁ view waveform
 ·····················································
  readout  qA1.res     ro_pls                        ⌁ view waveform
 ·····················································
  ▏x180    qA1.xy      x180_pls         ◀hover: primary@6/10% wash  ⌁ view waveform ◀ opacity 1 + primary
   (mono + tabular-nums values, no badge fill; last row drops the hairline)

  ⌁ x180 · I  +  Q   waveform               ← .waveform-plot-caption (mono 12px muted)
  ┌───────────────────────────────────────┐
  │              (Plotly trace)            │  ← render area, app.js-owned, unchanged
  └───────────────────────────────────────┘
  on error:  ┌ color-error-text text · 8% wash · 45% edge ┐
             │ no integration weights for this element     │  ← re-skinned .waveform-plot-err
             └─────────────────────────────────────────────┘
```

### HTML
```html
&lt;!-- ============================================================= --&gt;
&lt;!-- _qubit_config.html  lines 26-54  (drop-in replacement)         --&gt;
&lt;!-- _pair_config.html   lines 26-54  is BYTE-IDENTICAL except the  --&gt;
&lt;!-- surrounding {{ qubit_name }} vs {{ pair_name }} header above.  --&gt;
&lt;!-- Only the operations block changes; paste the same block into   --&gt;
&lt;!-- both files.                                                    --&gt;
&lt;!-- ============================================================= --&gt;

{% if operations %}
&lt;details open class="detail-section gen-config-ops"&gt;
    &lt;summary class="section-header"&gt;Operations &lt;small class="muted"&gt;({{ operations | length }})&lt;/small&gt;&lt;/summary&gt;

    &lt;table class="prop-table ops-table"&gt;
        &lt;thead&gt;
            &lt;tr&gt;
                &lt;th&gt;Op&lt;/th&gt;
                &lt;th&gt;Element&lt;/th&gt;
                &lt;th&gt;Pulse&lt;/th&gt;
                &lt;th class="ops-wf-col"&gt;Waveform&lt;/th&gt;
            &lt;/tr&gt;
        &lt;/thead&gt;
        &lt;tbody&gt;
        {% for op in operations %}
            &lt;tr&gt;
                &lt;td class="ops-val"&gt;{{ op.op_name }}&lt;/td&gt;
                &lt;td class="ops-val"&gt;{{ op.element }}&lt;/td&gt;
                &lt;td class="ops-val ops-val-dim"&gt;{{ op.pulse or '—' }}&lt;/td&gt;
                &lt;td class="ops-wf-col"&gt;
                    {# PRESERVED JS CONTRACT: .add-gate-btn class kept; the three
                       hooks app.js:636-645 reads (data-target-prefix, data-op-name,
                       onclick=window.showWaveformPlot(this)) are byte-for-byte the
                       same. .wf-view-btn only re-skins it inside .gen-config-ops. #}
                    &lt;button type="button"
                            class="add-gate-btn wf-view-btn"
                            data-target-prefix="{{ target_prefix }}"
                            data-op-name="{{ op.op_name }}"
                            onclick="window.showWaveformPlot(this)"&gt;
                        &lt;span class="wf-view-glyph" aria-hidden="true"&gt;⌁&lt;/span&gt;
                        &lt;span class="wf-view-label"&gt;view waveform&lt;/span&gt;
                    &lt;/button&gt;
                &lt;/td&gt;
            &lt;/tr&gt;
        {% endfor %}
        &lt;/tbody&gt;
    &lt;/table&gt;

    {# PRESERVED: id + .waveform-plot-area class (app.js:642 scopes via
       .qubit-config-pane → .waveform-plot-area). app.js writes
       .waveform-plot-loading / .waveform-plot-err / .waveform-plot-caption
       into this node — all three re-skinned in CSS below. #}
    &lt;div id="waveform-plot-area" class="waveform-plot-area"&gt;&lt;/div&gt;
&lt;/details&gt;
{% endif %}
```

### CSS
```css
/* ================================================================== */
/* Generate-Config "Operations" table — VSCode-clean table-header      */
/* pattern. SCOPED under .gen-config-ops (the operations <details>) and */
/* .qubit-config-pane so it can't bleed onto:                          */
/*   • the /config Config Viewer top-level-key .section-header sections */
/*   • the pair-page .add-gate-btn (real add-gate control)             */
/*   • any other .prop-table (dataset Property/Parameter tables)        */
/* Everything is px-pinned (NOT --prop-table-* / --bulk-fs) and built   */
/* only from --pico-* / *-text tokens + color-mix → theme-safe by       */
/* construction (dark default + light). Mirrors the shipped            */
/* .state-review language (style.css 4529-4683).                       */
/* ================================================================== */

/* --- Section summary: drop the tinted band; weight-600 13px on        */
/* --- transparent. Scoped to the ops details only — the global         */
/* --- .section-header band on /config + other detail-sections stays.   */
.gen-config-ops > summary.section-header {
    background: none;
    border-radius: 0;
    padding: 4px 0 6px;
    font-size: 13px;
    font-weight: 600;
    color: var(--pico-color);
    border-bottom: 1px solid
        color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent);
    margin-bottom: 0.4rem;
}
.gen-config-ops > summary.section-header .muted { color: var(--pico-muted-color); }

/* --- Table shell: no outer border; px type decoupled from            */
/* --- --prop-table-font / --bulk-fs.                                   */
.gen-config-ops .ops-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
    border: 0;
    background: none;
    table-layout: auto;
}

/* --- Header row: single 60% bottom hairline, 12px/600 muted sans.     */
.gen-config-ops .ops-table thead th {
    font-family: inherit;                /* UI sans, not mono */
    font-size: 12px;
    font-weight: 600;
    line-height: 1.45;
    text-transform: none;                /* drop the loud uppercase */
    letter-spacing: 0;
    color: var(--pico-muted-color);
    text-align: left;
    white-space: nowrap;
    padding: 0 10px 5px;
    background: none;
    border: 0;
    border-bottom: 1px solid
        color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent);
}

/* --- Body rows: separated by the SAME 60% hairline, last drops it,    */
/* --- primary-tint hover (6% light / 10% dark). No code-badges.        */
.gen-config-ops .ops-table tbody td {
    padding: 4px 10px;
    line-height: 1.45;
    vertical-align: middle;
    border: 0;
    border-bottom: 1px solid
        color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent);
    background: none;                    /* kill any inherited cell bg */
}
.gen-config-ops .ops-table tbody tr:last-child td { border-bottom: 0; }
.gen-config-ops .ops-table tbody tr:hover td {
    background: color-mix(in srgb, var(--pico-primary) 6%, transparent);
}
[data-theme="dark"] .gen-config-ops .ops-table tbody tr:hover td {
    background: color-mix(in srgb, var(--pico-primary) 10%, transparent);
}

/* --- Value columns: plain mono + tabular-nums, NOT Pico code-badges.  */
.gen-config-ops .ops-table td.ops-val {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 12.5px;
    color: var(--pico-color);
    white-space: nowrap;
    cursor: default;                     /* override .prop-table copy-cursor */
}
.gen-config-ops .ops-table td.ops-val-dim { color: var(--pico-muted-color); }

/* Waveform action column hugs the button (right edge of the row). */
.gen-config-ops .ops-table .ops-wf-col {
    width: 1%;
    white-space: nowrap;
    text-align: right;
}

/* --- "view waveform" → ghost-row-action. Borderless transparent;      */
/* --- muted + opacity .55 at rest; opacity 1 + primary on row-hover /   */
/* --- focus. The dashed border is gone. Scoped so the real .add-gate-   */
/* --- btn on the pair page keeps its existing dashed look.             */
.gen-config-ops .wf-view-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    margin: 0;
    padding: 2px 6px;
    border: 0;                           /* drop the 1px dashed mongrel */
    border-radius: 4px;
    background: transparent;
    color: var(--pico-muted-color);
    opacity: 0.55;
    font-size: 12px;
    font-weight: 500;
    line-height: 1.45;
    cursor: pointer;
    transition: opacity .1s, color .1s, background .1s;
}
.gen-config-ops .wf-view-glyph {
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1;
}
/* Reveal on row hover or keyboard focus (matches .review-accept). */
.gen-config-ops .ops-table tbody tr:hover .wf-view-btn,
.gen-config-ops .wf-view-btn:focus-visible {
    opacity: 1;
    color: var(--pico-primary);
}
.gen-config-ops .wf-view-btn:hover {
    opacity: 1;
    color: var(--pico-primary);
    background: color-mix(in srgb, var(--pico-primary) 14%, transparent);
}
.gen-config-ops .wf-view-btn:focus-visible {
    outline: none;
    box-shadow: 0 0 0 1px var(--pico-primary);
}

/* ================================================================== */
/* Waveform plot render area — app.js writes .waveform-plot-loading /   */
/* .waveform-plot-err / .waveform-plot-caption into here. Re-skin to    */
/* --pico-* tokens (the shipped block at 5699 uses legacy --color-*     */
/* fallbacks). Scoped to .qubit-config-pane so only the embeds change.  */
/* These class names are PRESERVED — JS targets them by name.          */
/* ================================================================== */
.qubit-config-pane .waveform-plot-area { margin-top: 0.6rem; }

.qubit-config-pane .waveform-plot-caption {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    font-size: 12px;
    color: var(--pico-muted-color);
    margin-bottom: 0.35rem;
}

.qubit-config-pane .waveform-plot-loading,
.qubit-config-pane .waveform-plot-err {
    padding: 6px 10px;
    border-radius: 4px;
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.45;
    color: var(--pico-muted-color);
    background: var(--pico-card-sectioning-background-color);
    border: 1px solid
        color-mix(in srgb, var(--pico-muted-border-color) 60%, transparent);
}
/* Error: text token (AA-readable both themes), faint same-hue edge —    */
/* color-only, never the *-bg fill as text (the washout rule).          */
.qubit-config-pane .waveform-plot-err {
    color: var(--color-error-text);
    border-color: color-mix(in srgb, var(--color-error-text) 45%, transparent);
    background: color-mix(in srgb, var(--color-error-text) 8%, transparent);
}
```
