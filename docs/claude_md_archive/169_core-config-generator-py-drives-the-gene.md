<!-- Moved verbatim from root CLAUDE.md (section: Generate Config + Config Viewer, block 169) on 2026-11-14. -->

`core/config_generator.py` drives the **Generate Config** wizard: discovers conda envs, probes for the QM stack, spawns `generator/run_build.py` in a user-selected env, and reads the resulting `_result.json`. The State Manager process never imports the heavy QM libraries. Both runners share `_run_script_outcome()`; a Preview config button stashes a result transplanted onto the store on load (seed-on-load).
