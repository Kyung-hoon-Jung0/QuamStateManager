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
        ("Head", "Page head with the fold button (D-103)", {"part": "head", "page": True, "title": "Diagnostics",
                                                            "gist": "Every check on the open chip, grouped by what it would break.",
                                                            "actions": [P_SECOND]}),
        ("Head", "Data page head with the fold button", {"part": "head", "page": True, "data": True, "title": "Pairs",
                                                         "count": "32", "actions": [{"label": "Add gate", "cls": "btn-sm"}]}),
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
        ("Pieces", "Pills: human labels (D-44)", {"part": "item", "title": "pills", "body": [
            {"part": "pill", "text": "T1"}, {"part": "pill", "text": "selected", "tone": "accent"},
            {"part": "pill", "text": "stale", "tone": "warn"}, {"part": "pill", "text": "failed", "tone": "danger"},
            {"part": "pill", "text": "in sync", "tone": "ok"}]}),
        ("Pieces", "Paths: the middle gives way (D-44)", {"part": "item", "title": "paths", "body": [
            {"part": "path", "text": "qubit_pairs.qD3-qC4.macros.cz_bipolar.flux_pulse_qubit.amplitude"},
            {"part": "path", "text": "C:/data/lab/2026-10-31/#1234_ramsey/node.json"},
            {"part": "path", "text": "short"}]}),
        ("Pieces", "Checkboxes (D-105)", {"part": "item", "title": "checkboxes", "body": [
            {"part": "check", "text": "Diagnosis"}, {"part": "check", "text": "Ticked", "checked": True},
            {"part": "check", "text": "Disabled", "disabled": True},
            {"part": "check", "text": "Disabled, ticked", "checked": True, "disabled": True}]}),
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
        ("Dialog", "Extra large (a form beside a chart)", {
            "part": "dialog", "size": "xl", "head": {"eyebrow": "New gate", "title": "New CZ gate", "gist": "Pair q1-2"},
            "body": [{"part": "note", "text": "The form on the left, the evidence on the right."}],
            "foot_actions": [{"label": "Cancel", "cls": "btn-sync outline"},
                             {"label": "Check", "cls": "btn-sync primary"}]}),
    ]
    return out


PAIRS = [
    {"value": "q1-q2", "title": "q1-q2", "desc": "Coupler c12, 40 ns pulse"},
    {"value": "q2-q3", "title": "q2-q3", "desc": "Coupler c23, 48 ns pulse"},
    {"value": "q3-q4", "title": "q3-q4", "disabled": True, "reason": "No flux line on the control qubit"},
    {"value": "q4-q5", "title": "q4-q5", "desc": "Coupler c45, not calibrated yet"},
]
CLASSES = ["CZGate", "FluxPulseCZ", "SquarePulse"]


def _choosing():
    """K18: select, input, field, menu, toolbar in every state (docs/308 section 4b)."""
    sec = "Choosing"
    return [
        (sec, "Select", {"part": "select", "label": "Pulse class", "options": CLASSES, "value": "CZGate"}),
        (sec, "Select, invalid", {"part": "select", "label": "Pulse class", "options": CLASSES, "value": "SquarePulse",
                                  "invalid": True}),
        (sec, "Select, disabled", {"part": "select", "label": "Pulse class", "options": CLASSES, "disabled": True}),
        (sec, "Input", {"part": "input", "label": "Gate name", "value": "cz_unipolar", "placeholder": "Gate name"}),
        (sec, "Input, invalid", {"part": "input", "label": "Gate name", "value": "cz unipolar", "invalid": True}),
        (sec, "Input, empty with placeholder", {"part": "input", "label": "Search", "type": "search",
                                                "placeholder": "Search pulses"}),
        (sec, "Number with unit", {"part": "input", "label": "Length", "type": "number", "num": True, "value": "40",
                                   "unit": "ns"}),
        (sec, "Number with unit, disabled", {"part": "input", "label": "Amplitude", "num": True, "value": "0.125",
                                             "unit": "V", "disabled": True}),
        (sec, "Field with hint and source", {"part": "field", "label": "Length", "hint": "Multiple of 4 ns.",
                                             "source": {"text": "copied"},
                                             "control": {"part": "input", "id": "uk-f-len", "num": True, "value": "40",
                                                         "unit": "ns"}}),
        (sec, "Field with an error", {"part": "field", "label": "Gate name",
                                      "error": "A name has no spaces; use cz_unipolar.",
                                      "control": {"part": "input", "id": "uk-f-name", "value": "cz unipolar",
                                                  "invalid": True, "block": True}}),
        (sec, "Inline field", {"part": "field", "label": "Pulse class", "inline": True, "source": {"text": "default"},
                               "control": {"part": "select", "id": "uk-f-cls", "options": CLASSES, "value": "CZGate"}}),
        (sec, "Menu: pick one", {"part": "menu", "id": "uk-menu-pick", "label": "Pair", "items": PAIRS,
                                 "value": "q1-q2"}),
        (sec, "Menu: pick one, nothing chosen", {"part": "menu", "id": "uk-menu-none", "label": "Pair", "items": PAIRS,
                                                 "placeholder": "Choose a pair"}),
        (sec, "Menu: actions", {"part": "menu", "id": "uk-menu-act", "kind": "action", "label": "More actions",
                                "trigger": {"label": "Actions", "cls": "btn-sm secondary"}, "items": [
                                    {"value": "duplicate", "title": "Duplicate gate"},
                                    {"value": "rename", "title": "Rename"},
                                    {"sep": True},
                                    {"value": "remove", "title": "Remove from the chip", "disabled": True,
                                     "reason": "Used by 2 macros"}]}),
        (sec, "Toolbar", {"part": "toolbar", "label": "View", "items": [
            {"part": "segmented", "label": "Show", "options": [{"label": "All", "selected": True}, {"label": "Changed"}]},
            {"part": "check", "text": "Only calibrated"},
            {"part": "check", "text": "Show units", "checked": True},
            {"part": "toolbar-sep"},
            {"part": "select", "label": "Sort", "options": ["Name", "Length", "Updated"], "value": "Name"},
            {"part": "toolbar-spacer"},
            {"part": "button", "label": "Reset", "cls": "btn-sm secondary"},
            {"part": "button", "label": "Apply", "cls": "btn-sm"}]}),
    ]


def reading_form(state):
    """The form column of the reading-order example (a new two-line gate)."""
    amp = "3.0" if state == "danger" else "0.1"
    num = lambda i, v, u, src: {"part": "field", "label": i, "source": {"text": src, "tone": "accent" if src == "entered" else "neutral"},
                                "control": {"part": "input", "num": True, "value": v, "unit": u, "block": True,
                                            "invalid": state == "danger" and v == "3.0"}}
    return [
        {"part": "panel", "step": 1, "title": "Gate", "body": [
            {"part": "field", "label": "Name", "control": {"part": "input", "value": "cz_fast", "block": True}},
            {"part": "field", "label": "Class", "control": {"part": "select", "options": ["CZ gate", "Square pulse"], "value": "CZ gate", "block": True}},
        ]},
        {"part": "panel", "step": 2, "title": "Pulses", "summary": ["2 lines play together"], "body": [
            {"part": "slot", "title": "Qubit flux pulse", "chip": "q1.z", "line": 1, "body": [
                {"part": "grid2", "items": [num("Length", "100", "ns", "entered"), num("Amplitude", amp, "V", "entered")]}]},
            {"part": "slot", "title": "Coupler pulse", "chip": "coupler_q1_q2", "line": 4, "body": [
                {"part": "grid2", "items": [num("Length", "60", "ns", "entered"), num("Amplitude", "0.05", "V", "default")]}]},
        ]},
        {"part": "panel", "step": 3, "title": "More pairs", "fold": True, "summary": ["only q1-q2"],
         "body": [{"part": "check", "text": "Also q2-q3"}]},
    ]


def reading_evidence(state):
    """The evidence column: conclusion, key numbers, table, checks, what is written."""
    ok = state == "ok"
    banner = ({"part": "verdict", "strong": True, "tone": "ok", "key": "Ready to create",
               "why": "Checked on a copy of the chip: it loads, and the gate runs."} if ok else
              {"part": "verdict", "strong": True, "tone": "danger", "key": "Cannot be created yet",
               "why": "q1.z would play 3 V; the port limit is 2.5 V. Lower the amplitude and check again."})
    stats = {"part": "stats", "label": "Key numbers", "tiles": [
        {"label": "Lines", "value": "2"},
        {"label": "Gate length", "value": "261", "unit": "ns"},
        {"label": "Highest level", "value": "0.1" if ok else "3", "unit": "V on q1.z", "tone": None if ok else "danger"},
        {"label": "Timing", "value": "101", "unit": "ns gap"}]}
    rows = [[{"text": "cz_fast_pulse_q1_q2"}, "100 ns", "0.1 V" if ok else {"text": "3 V (limit 2.5 V)"}],
            [{"text": "cz_fast_coupler_pulse_q1_q2"}, "60 ns", "0.05 V"]]
    table = {"part": "panel", "title": "Entered and actual", "flush": True,
             "summary": [{"text": "all as entered", "level": "ok"} if ok else {"text": "1 over the limit", "level": "danger"}],
             "body": [{"part": "table", "columns": ["Pulse", "Length", "Max level"], "numeric": [1, 2], "rows": rows}]}
    names = ["Chip loads", "Operations exist", "Pulse lengths", "Preview equals config", "Timeline"]
    checks = [{"name": n, "state": "pass"} for n in names]
    checks.append({"name": "Output range", "state": "pass"} if ok else
                  {"name": "Output range", "state": "fail", "why": "cz_fast_pulse_q1_q2 on q1.z: 3 V is above the 2.5 V limit."})
    checks.append({"name": "Hardware timing", "state": "skip"})
    chk = {"part": "panel", "title": "Checks",
           "summary": [{"text": "6 of 6 passed", "level": "ok"}] if ok else [{"text": "1 failed", "level": "danger"}, "5 passed"],
           "body": [{"part": "checks", "checks": checks}]}
    written = {"part": "panel", "title": "Will be written", "fold": True, "hint": "working copy only; the chip changes on Apply",
               "summary": [{"text": "3", "level": "strong"}, "objects"],
               "body": ["Two pulses and one gate macro under the pair."]}
    return [banner, stats, table, chk, written]


def _reading():
    """K19: each reading-order part alone, in every tone (docs/308 section 4c)."""
    sec = "Reading order"
    out = []
    for tone, key, why in (("ok", "Ready to create", "Checked on a copy of the chip: it loads, and the gate runs."),
                           ("warn", "Created with a warning", "The coupler pulse starts 101 ns after the qubit pulse."),
                           ("danger", "Cannot be created yet", "q1.z would play 3 V; the limit is 2.5 V. Lower the amplitude."),
                           ("note", "Not checked on hardware", "Only the samples written to the config are compared.")):
        out.append((sec, "Banner " + tone, {"part": "verdict", "strong": True, "tone": tone, "key": key, "why": why}))
    out += [
        (sec, "Stats, one abnormal tile", {"part": "stats", "label": "Key numbers", "tiles": [
            {"label": "Lines", "value": "2"}, {"label": "Gate length", "value": "261", "unit": "ns"},
            {"label": "Highest level", "value": "3", "unit": "V on q1.z", "tone": "danger"},
            {"label": "Timing", "value": "101", "unit": "ns gap", "tone": "warn"}]}),
        (sec, "Checks, one failed and one skipped", {"part": "checks", "checks": [
            {"name": "Chip loads", "state": "pass"}, {"name": "Pulse lengths", "state": "pass"},
            {"name": "Output range", "state": "fail", "why": "3 V on q1.z is above the 2.5 V limit."},
            {"name": "Hardware timing", "state": "skip"}]}),
        (sec, "Panel with step, hint and summary", {"part": "panel", "step": 2, "title": "Pulses", "hint": "program time",
                                                    "summary": ["2 lines play together"],
                                                    "body": ["A quiet explanation sits in the body, never louder than the content."]}),
        (sec, "Panel folded, summary still visible", {"part": "panel", "title": "Checks", "fold": True,
                                                      "summary": [{"text": "6 of 6 passed", "level": "ok"}],
                                                      "body": [{"part": "note", "text": "Six checks passed."}]}),
        (sec, "Panel with a control in the summary", {"part": "panel", "title": "Waveform", "summary": [
            {"part": "segmented", "label": "Scale", "options": [{"label": "Per lane", "selected": True}, {"label": "Shared V"}]}],
            "body": [{"part": "note", "text": "The chart goes here."}]}),
        (sec, "Text levels", {"part": "panel", "title": "Text levels", "summary": [
            {"text": "strong", "level": "strong"}, " normal ", {"text": "quiet", "level": "quiet"}, " ",
            {"text": "ok", "level": "ok"}, " ", {"text": "warn", "level": "warn"}, " ", {"text": "danger", "level": "danger"}]}),
    ]
    for n in range(1, 7):
        out.append((sec, "Line " + str(n) + " slot", {"part": "slot", "title": "Pulse on line " + str(n), "chip": "q" + str(n) + ".z", "line": n}))
    out.append((sec, "Unit input, invalid", {"part": "input", "label": "Amplitude", "num": True, "value": "3.0", "unit": "V",
                                            "invalid": True}))
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
    out += _choosing()
    out += _reading()
    out += [("Worked example: rule review (A1)", "Review dialog", _review_dialog()),
            ("Worked example: apply to other pairs (A2)", "Apply dialog", _apply_dialog()),
            ("Worked example: environment card (E1, E2)", "Card head and verdicts", _env_card())]
    return out
