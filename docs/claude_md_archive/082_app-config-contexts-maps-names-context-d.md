<!-- Moved verbatim from root CLAUDE.md (section: Multi-Context Registry, block 82) on 2026-11-14. -->

`app.config["contexts"]` maps names → context dicts with a `type` field (currently `"quam"`). Designed to accept HDF5/dataset types without restructuring. Routes use helpers (`_store()`, `_engine()`, `_modifier()`, etc.) to pull from the active context transparently.
