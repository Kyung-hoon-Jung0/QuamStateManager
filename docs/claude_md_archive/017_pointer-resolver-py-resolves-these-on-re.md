<!-- Moved verbatim from root CLAUDE.md (section: QUAM Pointer System, block 17) on 2026-11-14. -->

`pointer_resolver.py` resolves these **on-read, never in-place**. The cache is **per-`QuamStore`** (each store owns its own dict + lock), so two chips loaded in the same process with same-named qubits never share resolutions. Unresolvable pointers return raw strings + log warnings (real data has dangling pointers). See `docs/32_red_team_phase_2.md` finding 0.1.
