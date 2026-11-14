<!-- Moved verbatim from root CLAUDE.md (section: Re-generate Config, block 183) on 2026-11-14. -->

Entity collections are never resurrected. TWPAs build natively via `Connectivity.add_twpa_lines` (legacy 0.2.0 builders fall back to a wiring-carry graft). Pair ids are reconciled by (control, target) membership at merge AND at build-time populate-seed lookup. A **cross-generation schema gate** refuses to graft an old-only key into a class the fresh build's own dataclass schema doesn't recognize (visibly dropped as `schema_dropped`, never a silent `Quam.load()` crash).
