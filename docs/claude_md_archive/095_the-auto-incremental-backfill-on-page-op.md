<!-- Moved verbatim from root CLAUDE.md (section: Param History, block 95) on 2026-11-14. -->

The auto-incremental backfill on page open is single-attempt per chip per browser-tab session (sessionStorage marker). Failed entries are captured and surfaced via an amber banner on `/param-history` with a "Retry import" button.
