<!-- Moved verbatim from root CLAUDE.md (section: Dependencies, block 196) on 2026-11-14. -->

Core: Flask, Jinja2, Typer, Rich. Desktop: pywebview. Data: h5py (HDF5 reading), numpy + scipy (Pulses-page waveform synthesis — scipy gives bit-exact window/filter parity with qualang_tools). Frontend: HTMX, Pico CSS, Split.js, Plotly.js — all bundled in `web/static/` (no CDN). The Generate-Config wizard shells out to a user-selected conda env that has the heavy QM stack (`qualang_tools`, `quam_builder`, `quam`, `qm`) installed; the State Manager process itself never imports them.
