<!-- Moved verbatim from root CLAUDE.md (section: Typed Editing + State↔Env Validation, block 105) on 2026-11-14. -->

**Validation** rides Diagnostics (domain "Environment match"): aggregated `unknown_field`/`unimportable_class`/`missing_required` errors + type/version warnings. Merge-blockers: a corpus harness (zero blocked re-commits + byte-idempotence over 30 real chips) in `tests/test_type_corpus_idempotence.py`; golden manifests in `tests/golden/state_schema_{modern,fork}.json`. See `docs/56_dynamic_typed_edit.md`.
