<!-- Moved verbatim from root CLAUDE.md (section: QuamStore (Thread-Safe), block 19) on 2026-11-14. -->

`core/loader.py` holds merged state+wiring dicts, change log, search index, and per-store pointer cache in a single `QuamStore`. All mutations are guarded by `threading.RLock` (Flask workers + pywebview UI thread share state). Loads route through `safe_io.read_state_wiring` so the read never blocks an experiment's atomic save on Windows.
