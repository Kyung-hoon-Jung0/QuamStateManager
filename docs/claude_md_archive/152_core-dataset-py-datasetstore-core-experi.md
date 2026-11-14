<!-- Moved verbatim from root CLAUDE.md (section: Dataset Support, block 152) on 2026-11-14. -->

`core/dataset.py` (DatasetStore) + `core/experiment_data.py` load experiment runs from `node.json` + `data.json` + HDF5 files, routed through `safe_io` so a still-active experiment's writeback isn't blocked. A separate `_h5_lock` guards h5py reads. Tag/bookmark/note mutations roll back on disk-write failure. `_data_json_cache` is an LRU bounded at 200.
