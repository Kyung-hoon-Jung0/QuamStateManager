<!-- Moved verbatim from root CLAUDE.md (section: Param History, block 84) on 2026-11-14. -->

`core/history.py` (`HistoryManager`, ~2,300 lines) keeps timestamped snapshots of each chip under `instance/history/<chip>/` plus a per-chip SQLite property index (`index.sqlite`) for fast trend queries. Content-hash dedup, fingerprint-aware multi-chip routing, alignment scan against the workspace, chip-decisions persistence, v1+v2 migrations. Triggers: `save` / `manual` / `auto` / `experiment`. See `docs/20_param_history.md`, `docs/21_multi_chip_support.md`, `docs/23_param_history_performance.md`.
