<!-- Moved verbatim from root CLAUDE.md (section: Typed Editing + State↔Env Validation, block 99) on 2026-11-14. -->

Every leaf of state.json (and wiring.json) is dynamically editable, type-safely. **Path grammar**: list/matrix elements use dot-form numeric segments (`confusion_matrix.0.1`), disambiguated structurally from number-keyed dicts by the actual parent container; strict `^\d+$` index gate.
