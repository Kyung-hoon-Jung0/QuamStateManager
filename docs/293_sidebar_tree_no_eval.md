# 293 — The sidebar run tree no longer asks the browser to eval (5 console errors per page load)

**Date:** 2026-10-06 · **Found by:** two separate verification agents (docs/291, docs/284)
reporting the same console errors on every full page load.

## Cause

A lazy date group in the sidebar run tree (more than 200 runs in the tree, docs/142)
declared `hx-trigger="toggle[this.open] once"`.
- An htmx trigger **filter** (`event[expr]`) is compiled with `Function()`.
- The app's Content-Security-Policy is `script-src 'self' 'unsafe-inline'`, with no
  `unsafe-eval`. So every lazy group logged `EvalError: Evaluating a string as JavaScript
  violates the following Content Security Policy directive...` when htmx processed it,
  and htmx dropped the filter.
- The group still loaded its runs, by accident: its first `toggle` is the opening.

## Change

- The markup says `hx-trigger="lazy-open once"`: no filter, nothing to compile.
- One delegated capture-phase `toggle` listener in `app.js` rings the group's
  `lazy-open` when a lazy group OPENS while still showing its placeholder.
- It marks `__lazyAsked`, the same mark the docs/164 restore path uses, so a group is
  never asked twice.
- The CSP is unchanged.

## Verified

- **Real headless Chrome**, the same page and data, 5 lazy groups (`csp_probe.cjs`
  records every console message, exception and log entry through a page load and
  opening one group):
  - current main: 5 × `console.error EvalError ...` on load; the opened group loads its 3 runs;
  - this change: 0 messages; the opened group loads its 3 runs.
- **Pins:**
  - `tests/sidebar_lazy_group_selfcheck.cjs` (+5): opening a waiting group rings
    `lazy-open` once, re-opening rings nothing more, a filled group rings nothing, and a
    restored group is asked once in all.
  - `tests/test_sidebar_lazy_group.py::test_no_template_hx_trigger_carries_a_filter`.
  - Mutation sweep 4/4 red.
