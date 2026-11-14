<!-- Moved verbatim from root CLAUDE.md (section: Web UI (HTMX), block 34) on 2026-11-14. -->

Flask routes return either full pages or partial `_*.html` template fragments for `hx-swap`. Partials are named `_*.html`; full pages are named without underscore. No React/Vue — HTMX + ~5,100 lines of vanilla JS + ~3,000 lines for the Generate-Config wizard. Routes in `web/routes.py` (~3,800 lines, ~90 endpoints). Templates: 63 files (17 full pages + 46 partials). All colors are parameterized as ~430 CSS custom properties with light/dark theme support.
