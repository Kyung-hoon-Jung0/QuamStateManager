<!-- Moved verbatim from root CLAUDE.md (section: Generate Config + Config Viewer, block 170) on 2026-11-14. -->

After `machine.save()`, a post-save fix-up rewrites each readout MW input port's `downconverter_frequency` as a JSON pointer to its paired MW output port's `upconverter_frequency` (they share one physical LO).
