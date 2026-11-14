<!-- Moved verbatim from root CLAUDE.md (section: Scanner + LRU Cache + Sidebar Tree v2, block 29) on 2026-11-14. -->

`core/scanner.py` discovers experiment folders lazily. `QuamStore` instances are LRU-cached (max 10, ~40MB total) — only loaded when selected in the UI. `node.json` reads also go through `safe_io.read_json`.
