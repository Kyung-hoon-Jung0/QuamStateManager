"""Faithful interactive (Plotly) reproductions of experiment figures.

The experiment code (qualibration_graphs / QUAlibrate) renders each run's
figures with matplotlib, baking in the analysis (fits, processed quantities,
panel composition). This package rebuilds those *same* figures as interactive
Plotly figure dicts, entirely in-process, by reading the already-saved
``ds_raw.h5`` / ``ds_fit.h5`` and reconstructing fit-overlay curves from the
stored parameters (the closed-form models live in :mod:`models`).

It deliberately does NOT import the heavy QM stack — only h5py + numpy, both
already required for HDF5 reading.

Public API:
    - :func:`list_interactive_figures` — cheap figure *menu* for a run
    - :func:`build_interactive_figure` — full Plotly JSON for one figure key
"""
from __future__ import annotations

from .registry import build_interactive_figure, list_interactive_figures

__all__ = ["list_interactive_figures", "build_interactive_figure"]
