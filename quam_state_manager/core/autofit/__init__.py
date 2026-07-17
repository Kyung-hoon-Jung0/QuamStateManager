"""Autofit — the one-button automatic fitting scheduler (docs/56).

Package layout:
    plan.py        Plan/Step models + shipped presets
    families.py    per-family gates, bands, update ops, adaptation rules
    gates.py       deterministic verdict pipeline
    auditor.py     LLM verdict layer (judge-only; never emits numbers)
    writer.py      staged-write orchestrator + deterministic revert
    engine.py      the plan state machine driving the scheduler chassis
    synth.py       synthetic run generator (ground truth + corruption)
    simbackend.py  hardware-free backend over synth
"""
