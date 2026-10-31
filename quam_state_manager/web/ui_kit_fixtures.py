"""Static fixtures for the /ui-kit sample page (docs/308).

Every entry is ``(section, label, spec)``; the page renders each spec through
the Jinja macros AND through ``static/ui.js`` side by side, and
``tests/ui_kit_selfcheck.cjs`` compares the two trees. The worked examples
mirror the shapes of real data (a rule review, the apply-to-other-pairs
dialog, an environment upgrade card head); every name in them is generic.
"""
from __future__ import annotations

LONG = ("A long sentence that keeps going to show how the part wraps when the text is longer than one line; "
        "it is never cut with an ellipsis, it wraps onto the next line and keeps its left edge.")

P_PRIMARY = {"label": "Apply", "cls": "btn-sync primary"}
P_SECOND = {"label": "Review", "cls": "btn-sync"}
P_QUIET = {"label": "Cancel", "cls": "btn-sync outline"}


def _parts():
    out = []
    # K3 heads
    out += [
        ("Head", "Document head", {"part": "head", "eyebrow": "Diagnostics", "title": "Environment match",
                                   "gist": "What changed between the environment the chip was built in and this one.",
                                   "actions": [P_SECOND, P_PRIMARY]}),
        ("Head", "Data head with a count", {"part": "head", "data": True, "title": "Pulses", "count": "48",
                                            "actions": [{"label": "New pulse", "cls": "btn-sm"}]}),
        ("Head", "Long title, no actions", {"part": "head", "title": LONG, "gist": LONG}),
        ("Head", "Empty count", {"part": "head", "data": True, "title": "Runs", "count": "0"}),
    ]
    # K2 action rows
    out += [
        ("Actions", "Primary, secondary, quiet", {"part": "actions", "label": "Choices",
                                                  "buttons": [P_QUIET, P_SECOND, P_PRIMARY]}),
        ("Actions", "Small buttons, one disabled", {"part": "actions", "buttons": [
            {"label": "Keep as is", "cls": "btn-sm secondary"}, {"label": "Apply", "cls": "btn-sm", "disabled": True}]}),
        ("Actions", "Icon buttons", {"part": "actions", "buttons": [
            {"label": "⋯", "cls": "ui-btn-icon", "aria_label": "More"},
            {"label": "✕", "cls": "ui-btn-icon", "aria_label": "Remove", "disabled": True}]}),
        ("Actions", "Empty row", {"part": "actions", "buttons": []}),
    ]
    # K4 notices
    for tone, key, text in (("warn", "Environment changed.", "The selected environment is not the one the chip was built in."),
                            ("danger", "Live file unreadable.", "The chip file could not be read; nothing was changed."),
                            ("note", "Read only.", "This folder is an archive."),
                            ("warn", "Long notice.", LONG)):
        out.append(("Notice", tone, {"part": "notice", "tone": tone, "key": key, "text": text,
                                     "actions": [{"label": "Review", "cls": "btn-sm"},
                                                 {"label": "Acknowledge", "cls": "btn-sm secondary"}] if tone == "warn" else None}))
    # K6 verdicts
    out += [
        ("Verdict", "ok", {"part": "verdict", "tone": "ok", "key": "All 1218 waveforms are bit-identical", "why": "to the reference"}),
        ("Verdict", "warn", {"part": "verdict", "tone": "warn", "key": "2 need a look", "why": "control and target swapped"}),
        ("Verdict", "danger", {"part": "verdict", "tone": "danger", "key": "Stops loading 2 found"}),
        ("Verdict", "neutral", {"part": "verdict", "tone": "neutral", "key": "May fail at run time", "why": "none found"}),
        ("Verdict", "Long", {"part": "verdict", "tone": "warn", "key": "Long conclusion", "why": LONG}),
    ]
    # K7 items
    out += [
        ("Item", "Item with badge, count, note and actions", {
            "part": "item", "chip": "FluxTunableTransmonPair.cross_resonance", "badge": {"text": "field removed", "tone": "danger"},
            "count": "32 objects", "body": ["Was declared in the previous version. No rename candidate."],
            "note": "Live changes only through Apply.",
            "actions": [{"label": "Keep as is", "cls": "btn-sync sm outline"}, {"label": "Remove", "cls": "btn-sync sm"}]}),
        ("Item", "Item, empty body", {"part": "item", "chip": "q1", "title": "no changes"}),
        ("Item", "Item, ok badge, disabled action", {"part": "item", "chip": "q3", "badge": {"text": "ready", "tone": "ok"},
                                                    "actions": [{"label": "Apply", "cls": "btn-sync sm", "disabled": True}]}),
    ]
    # small pieces
    out += [
        ("Pieces", "Chip, badges, count", {"part": "item", "title": "pieces", "body": [
            {"part": "chip", "text": "qubits.q1.xy.operations.x180.amplitude"},
            {"part": "badge", "text": "neutral"}, {"part": "badge", "text": "accent", "tone": "accent"},
            {"part": "badge", "text": "warn", "tone": "warn"}, {"part": "badge", "text": "danger", "tone": "danger"},
            {"part": "badge", "text": "ok", "tone": "ok"}, {"part": "count", "text": "3 of 4"}]}),
        ("Pieces", "Callout note", {"part": "callout", "tone": "note", "title": "Not checked",
                                    "text": "Timing on hardware; only the samples written to the config are compared."}),
        ("Pieces", "Callout warn", {"part": "callout", "tone": "warn", "title": "Will be skipped",
                                    "text": "The selected environment has no class for this line; the build keeps going without it."}),
        ("Pieces", "Callout danger", {"part": "callout", "tone": "danger", "title": "Cannot build",
                                      "text": "No root class in this environment can hold these qubits."}),
        ("Pieces", "Table", {"part": "table", "columns": ["Pair", "Status", "Length (ns)"], "numeric": [2], "rows": [
            [{"chip": "q1-2"}, {"dot": "ok", "text": "ready"}, "40"],
            [{"chip": "q2-3"}, {"dot": "warn", "text": "look"}, "1200"]]}),
        ("Pieces", "Key / value table", {"part": "table", "kv": True, "columns": [], "rows": [
            ["Generated from", {"chip": "state.json"}], ["Recorded", "2026-10-31 01:21"]]}),
        ("Pieces", "Segmented", {"part": "segmented", "label": "Select", "options": [
            {"label": "All ready", "command": True}, {"label": "Feedline 1", "selected": True}, {"label": "None", "command": True}]}),
        ("Pieces", "Disclosure", {"part": "disclosure", "summary": "Details", "count": "3", "body": [
            {"part": "note", "text": "Raw field lines would be listed here."}]}),
        ("Pieces", "Check", {"part": "check", "text": "Also add to pairs created later"}),
    ]
    # K13 dialog shells
    out += [
        ("Dialog", "Small", {"part": "dialog", "size": "s", "head": {"title": "Remove this rule?"},
                             "body": [{"part": "note", "text": "The pulses it added stay on the chip."}],
                             "foot_actions": [{"label": "Cancel", "cls": "btn-sync outline"},
                                              {"label": "Remove", "cls": "btn-sync danger"}]}),
    ]
    return out


def _review_dialog():
    """A1: the rule review after a frequency populate swapped two pairs."""
    def pair(pid, ctl, tgt):
        return {"part": "item", "chip": pid, "badge": {"text": "control ↔ target swapped", "tone": "warn"},
                "title": "by Populate frequency",
                "body": [{"part": "table", "columns": ["Pulse", "Plays on now", "After"], "rows": [
                    ["control", {"chip": ctl + ".z"}, {"chip": tgt + ".z"}],
                    ["target", {"chip": tgt + ".z"}, {"chip": ctl + ".z"}]]},
                    {"part": "note", "text": "Pulse names stay the same; only the channel changes."},
                    {"part": "disclosure", "summary": "Details", "body": [
                        {"part": "note", "text": "qubit_pairs." + pid + ".macros.cz.flux_pulse_control: " + ctl + ".z → " + tgt + ".z"}]}],
                "actions": [{"label": "Keep as is", "cls": "btn-sync sm outline"}, {"label": "Apply", "cls": "btn-sync sm"}]}
    return {"part": "dialog", "size": "m",
            "head": {"eyebrow": "Rule review · cz_rule", "title": "2 pairs would change"},
            "body": [{"part": "note", "text": "Recalculated with each pair's current frequencies. Nothing is written until you choose."},
                     pair("q2-1", "q1", "q2"), pair("q4-3", "q3", "q4")],
            "foot_actions": [{"label": "Keep all as is", "cls": "btn-sync outline"},
                             {"label": "Apply all 2 changes", "cls": "btn-sync primary"}]}


def _apply_dialog():
    """A2: add a pair rule to other pairs."""
    def rows(items):
        return {"part": "table", "columns": ["Pair", "Status", "Control plays on", "Target plays on", "Pulse names", "Why"],
                "rows": [[{"chip": p}, {"dot": d, "text": w}, {"chip": c}, {"chip": t},
                          {"text": n, "muted": True, "title": n}, {"text": y, "muted": True}] for p, d, w, c, t, n, y in items]}
    return {"part": "dialog", "size": "l",
            "head": {"eyebrow": "Apply to other pairs", "title": "Add cz_rule to other pairs",
                     "gist": "From q2-1 · CZGateCustom (lab class)"},
            "body": [
                {"part": "table", "columns": ["Pulse", "amplitude", "flat_length"], "numeric": [1, 2], "rows": [
                    ["control", "0.142", "36"], ["target", "0.087", "36"]]},
                {"part": "note", "text": "Copied from q2-1 as starting values — recalibrate each pair."},
                {"part": "segmented", "label": "Select pairs", "options": [
                    {"label": "All ready", "command": True}, {"label": "Feedline 1"}, {"label": "None", "command": True}]},
                {"part": "disclosure", "summary": "Ready", "count": "3 of 4", "open": True, "body": [rows([
                    ("q3-4", "ok", "ready", "q3.z", "q4.z", "cz_control, cz_target", ""),
                    ("q5-6", "ok", "ready", "q5.z", "q6.z", "cz_control, cz_target", ""),
                    ("q7-8", "ok", "ready", "q7.z", "q8.z", "cz_control, cz_target", "")])]},
                {"part": "disclosure", "summary": "Needs a look", "count": "1", "body": [rows([
                    ("q9-10", "warn", "look", "q10.z", "q9.z", "cz_control, cz_target", "control has the lower frequency")])]},
                {"part": "disclosure", "summary": "Can't", "count": "0", "body": [{"part": "note", "text": "None."}]},
                {"part": "disclosure", "summary": "Already has it", "count": "1", "body": [{"part": "note", "text": "q2-1"}]},
                {"part": "callout", "tone": "note", "title": "Ready",
                 "text": "Both qubits have a flux line and the frequencies give the same control and target."},
            ],
            "foot": [{"part": "check", "text": "Also add to pairs created later"}],
            "foot_actions": [{"label": "Cancel", "cls": "btn-sync outline"},
                             {"label": "Add to 3 pairs", "cls": "btn-sync primary"}]}


def _env_card():
    """E1 + E2: the environment upgrade card head and its verdict summary."""
    return {"part": "item",
            "body": [
                {"part": "head", "eyebrow": "Environment match", "title": "quam-builder 0.4.0 → 0.6.0", "level": 3,
                 "badge": {"text": "upgrade", "tone": "accent"},
                 "gist": "Checked in env_new · quam 0.6.0 · qm 1.4.1 · numpy 2.5.3",
                 "actions": [{"label": "Check this env", "cls": "btn-sync sm primary"},
                             {"label": "Record reference", "cls": "btn-sync sm"},
                             {"label": "References", "cls": "btn-sync sm"},
                             {"label": "Copy as text", "cls": "btn-sync sm"},
                             {"label": "Re-probe environment", "cls": "btn-sync sm outline"},
                             {"label": "Validate deeply (Quam.load)", "cls": "btn-sync sm outline"}]},
                {"part": "verdict", "tone": "danger", "key": "Stops loading", "why": "2 found"},
                {"part": "verdict", "tone": "ok", "key": "Changes output", "why": "0 of 1218 waveforms differ"},
                {"part": "verdict", "tone": "neutral", "key": "FYI", "why": "1 note"},
                {"part": "verdict", "tone": "neutral", "key": "May fail at run time", "why": "none found"},
                {"part": "callout", "tone": "note", "title": "Not checked",
                 "text": "Hardware timing; samples written to the QM config are compared, not measured hardware timing."},
            ]}


def specs():
    """[(section, label, spec)] in page order."""
    out = _parts()
    out += [("Worked example: rule review (A1)", "Review dialog", _review_dialog()),
            ("Worked example: apply to other pairs (A2)", "Apply dialog", _apply_dialog()),
            ("Worked example: environment card (E1, E2)", "Card head and verdicts", _env_card())]
    return out
