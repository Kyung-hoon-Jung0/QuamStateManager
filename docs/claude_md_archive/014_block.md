<!-- Moved verbatim from root CLAUDE.md (section: Core Pipeline, block 14) on 2026-11-14. -->

```
state.json + wiring.json
    → safe_io.read_state_wiring (FILE_SHARE_DELETE + mtime-bracketed pair read)
        → loader.py (QuamStore — owns per-instance pointer cache)
            → pointer_resolver.py (resolves #/, #../, #./ references via store cache)
            → search_index.py (prefix map + trigram index)
            → query.py (flattens nested JSON → qubit/pair dicts)
            → modifier.py (type-coerced edits, undo, rollback)
            → saver.py (atomic writes via safe_io.atomic_write_json, .bak rotation)
            → differ.py (2-way diffs, N-way trends)
            → history.py (Param History: SQLite property index, snapshots)
            → config_view.py (Config Viewer — preview generate_config())
            → config_generator.py (Generate wizard subprocess driver)
```
