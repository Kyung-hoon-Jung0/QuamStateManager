<!-- Moved verbatim from root CLAUDE.md (section: Param History, block 94) on 2026-11-14. -->

Every editable value carries a 🕘 value-history popover — `GET /field/history?path=` → `HistoryManager.field_history`: change-point timeline (tracked props via SQLite; other leaves via capped scan; pointers resolved per-snapshot) merged with a runs tier, with **Use** (fills the edit input) and **Data** (loads the run's detail).
