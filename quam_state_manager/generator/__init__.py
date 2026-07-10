"""Standalone QUAM config-generation scripts.

Files in this package are executed by an *external* Python interpreter (a
conda env that has the Quantum Machines stack installed) as a subprocess —
they are NOT imported by the ``quam_state_manager`` package itself. Keep
this package import-light: ``run_build.py`` must depend only on the QM
libraries and the standard library.
"""
