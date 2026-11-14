<!-- Moved verbatim from root CLAUDE.md (section: Type Coercion Philosophy, block 190) on 2026-11-14. -->

`modifier.py` casts new values to the original field's type but **never validates ranges**. Real QUAM data has coupler amplitudes >1, negative T2, angles outside [-π, π] — trust researcher input.
