<!-- Moved verbatim from root CLAUDE.md (section: Generate Config + Config Viewer, block 171) on 2026-11-14. -->

The Populate step is absolute-first: RF frequencies are the primary input, LO auto-derived per MW-FEM port pair by `solveLoWindow` (minimize max|IF| subject to the ±400MHz window + Nyquist coverage + the 5MHz resonator demod hole). A Power input toggle adds an absolute-dBm mode (port `full_scale_power_dbm` auto-allocated; stored spec representation identical in both modes).
