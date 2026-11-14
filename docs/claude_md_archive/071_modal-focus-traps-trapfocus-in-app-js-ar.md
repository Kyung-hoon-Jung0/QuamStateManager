<!-- Moved verbatim from root CLAUDE.md (section: Web UI (HTMX), block 71) on 2026-11-14. -->

Modal focus traps (`trapFocus` in app.js) are leak-proof at the source: re-trapping a container releases its previous trap, and a trap whose container went hidden/detached self-heals on the next keydown. Both Live-Edit grids hop Tab/Shift+Tab between edit cells; the topbar calculator hops its visible inputs. Pinned by `tests/tab_focus_selfcheck.cjs` + `tests/test_tab_focus.py`. See `docs/64_tab_focus_calc.md`.
