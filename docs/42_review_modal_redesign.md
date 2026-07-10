# 42 — Review modal redesign (VSCode-clean)

The **"Live chip vs. your working copy"** review overlay (`_state_review.html`,
opened by *Review & sync*) lists every param that differs between the live chip
and the working copy, each with an inline-editable live value + a ✓ that pulls
just that field. It went through three iterations driven by customer feedback:

1. **4-column table** (Type · Path · Your copy · Live chip) — a long dot-path
   (`qubits.qA1.resonator.operations.readout.integration_weights_angle`) never fit
   the PATH column; it truncated to `qubit…` and widening the panel never helped
   because the full-precision float values claimed all the width first.
2. **Stacked cards** — path on its own full-width line above the value transition.
   Fixed the truncation, but felt heavy/clunky: oversized font (coupled to the
   Bulk-Edit `--bulk-fs` scale), a loud yellow "modified" pill, a tinted path band.
3. **VSCode Source-Control list** (current) — the clean redesign below.

## Current design (Variant A "Soft-Blue Leaf")

Chosen from a 20-agent design workflow (4 proposals × 3-lens judge panel →
synthesis → adversarial critic). Borderless **hairline list**, not cards:

- **Gutter letter** `M` / `A` / `D` (soft-blue / green / red, *text only* — no
  filled pill) + a **2px left accent** in the same hue. Replaces the loud yellow
  `modified` badge.
- **Path** — parent dirs dimmed (`.dp-dirs`, muted) + the **leaf** segment
  (`.dp-leaf`, last `.`-separated token) emphasised in **soft-blue bold**
  (`--diag-info-text`). No background band. Wraps only if genuinely long.
- **Value line** — `old (dim) → [editable] ✓`, all on a uniform **22px** control
  height. The input is **borderless until focus**; the ✓ is **ghost until row
  hover**, then primary; on accept the row goes green.
- **Fixed px type scale (12.5–13px)**, fully decoupled from `--bulk-fs` — that
  coupling was the source of the "clunky/oversized" complaint.
- **Actions bar** — one filled CTA (*Pull & apply*), the rest outline/muted.

### Why the path splits in the template, not CSS

The leaf emphasis needs two spans, so the split is done in Jinja:

```jinja
{% set _parts = e.dot_path.rsplit('.', 1) %}
<code class="dot-path" title="{{ e.dot_path }}">
  {%- if _parts | length == 2 -%}
    <span class="dp-dirs">{{ _parts[0] }}.</span><span class="dp-leaf">{{ _parts[1] }}</span>
  {%- else -%}
    <span class="dp-leaf">{{ e.dot_path }}</span>   {# single-segment path → whole = leaf #}
  {%- endif -%}
</code>
```

The change glyph is a literal map: `{'modified':'M','added':'A','removed':'D'}.get(...)`.

## Invariants the redesign preserves (the JS contract)

`reviewAccept()` in `app.js` and the sync flow depend on exact hooks — restyled,
never renamed:

- The row matches `.review-row` and carries `diff-row-{modified,added,removed}`
  (+ `diff-row-yours` for the user's own edit); `review-accepted` is added on
  success. `diff-row-added` still drives `create=true` on the ✓.
- Inside: `.review-live-edit > input.review-live-input[data-dot-path][data-yours]`
  + `button.review-accept[onclick="reviewAccept(this)"]`.
- The `.state-review-actions` bar (`.review-sync-edits` / `.review-sync-clean`
  groups, `[hidden]`-toggled by `_reviewRevealEditSync`) is unchanged.

## Scoping

Every rule is scoped under `.state-review` so the **global** `.dot-path` (+ its
blue `:hover` underline) and the filled `.diff-type-*` badges used by `/diff`,
`/compare`, `/chip-compare`, and the live-drift table are untouched. The
`.review-*` / `.btn-sync` / `.diff-yours-tag` classes are used **only** here.
`removed` uses `--color-error-text` (a real text token, AA-readable), not the
badge-fill `--diff-type-removed-bg`.
