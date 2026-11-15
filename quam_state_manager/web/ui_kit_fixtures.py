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
        (sec, "Field without a control id (the field is the label)", {"part": "field", "label": "Amplitude", "hint": "Click the label: the input takes focus.",
                                                                     "control": {"part": "input", "num": True, "value": "0.1", "unit": "V"}}),
        (sec, "Field with a check", {"part": "field", "label": "Show", "hint": "Spectator qubits are drawn grey.",
                                     "control": {"part": "check", "text": "Spectators"}}),
        (sec, "Field with another part (a group wrapper names it)", {"part": "field", "label": "Source file",
                                                                     "control": {"part": "chip", "text": "state.json"}}),
        (sec, "Field keeps the caller's describedby", {"part": "field-stack", "items": [
            {"part": "field", "label": "Length", "hint": "Multiple of 4 ns.",
             "control": {"part": "input", "num": True, "value": "40", "unit": "ns", "describedby": "uk-length-note"}},
            {"part": "note", "id": "uk-length-note", "text": "Lengths come from the pulse class."}]}),
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
                                    {"title": "Open the manual", "desc": "An item without a value"},
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


def line_count_words(n):
    """The Pulses summary words, as the gate dialog writes them (cz-gate.js
    ``fillSlots``): a count only, never a claim that the lines play together,
    which the Timing tile may contradict."""
    return "1 line" if n == 1 else "%d lines" % n


#: the example gate's lines as its table shows them: (line, starts at, ends at), ns
EXAMPLE_SPANS = (("q1.z", 161, 261), ("coupler_q1_q2", 0, 60))
#: the example's largest level per line, V, as its table shows them (state "ok"; "danger" plays 3 V on q1.z)
EXAMPLE_PEAKS = {"q1.z": 0.1, "coupler_q1_q2": 0.05}
EXAMPLE_OVER = {"q1.z": 3.0}
#: the operation each line plays, as the example's table names them
EXAMPLE_OPS = {"q1.z": "cz_fast_pulse_q1_q2", "coupler_q1_q2": "cz_fast_coupler_pulse_q1_q2"}
#: the example's output ports: amplified LF ports, so their range is the one ``gate_check.port_range`` gives
EXAMPLE_PORTS = {"q1.z": ("con1", 1, 5), "coupler_q1_q2": ("con1", 1, 6)}
EXAMPLE_PORT_KIND = {"port_kind": "lf", "output_mode": "amplified"}
#: the dialog's own words around the server's sentences (cz-gate.js ``renderCheck``, one pair);
#: the kit test finds each of them in cz-gate.js
OK_WHY = "The lab's code built this gate on a copy of the chip and every check passed."
WARN_TAIL = " You can still create it; the record says it was not checked."
DANGER_TAIL = " Change the inputs and check again."


def example_limit_v():
    """The example ports' output range, V, from the check's own rule (never typed here)."""
    from quam_state_manager.core.gate_check import port_range
    return port_range(EXAMPLE_PORT_KIND)


def _levels(over):
    return dict(EXAMPLE_PEAKS, **(EXAMPLE_OVER if over else {}))


def example_lanes(over=False):
    """The example gate's lines in the shape ``gate_timeline_view.tiles`` reads:
    its spans, largest levels and port range; with *over*, q1.z plays over it."""
    peaks, lim = _levels(over), example_limit_v()
    return [{"key": k, "group": "gate", "limit_v": [-lim, lim], "offset_v": 0.0,
             "segments": [{"start_ns": a, "end_ns": b, "peak_v": peaks[k], "min_v": 0.0,
                           "flags": ["over_range"] if over and k in EXAMPLE_OVER else []}]}
            for k, a, b in EXAMPLE_SPANS]


def range_check(over=False):
    """The example's Output range check, ``{"status", "message"}``, from the
    check's own rule (``gate_check._range_check``) on the example's lines."""
    from quam_state_manager.core import gate_check
    from quam_state_manager.core.static_timeline import TimelineRow
    peaks = _levels(over)
    found = [{"element": k, "operation": EXAMPLE_OPS[k], "port": EXAMPLE_PORTS[k], **EXAMPLE_PORT_KIND,
              "offset": 0.0, "length": b - a, "waveform": {"type": "constant", "sample": peaks[k]}}
             for k, a, b in EXAMPLE_SPANS]
    rows = [TimelineRow(k, EXAMPLE_PORTS[k], EXAMPLE_OPS[k], a, b, peaks[k], 1.0) for k, a, b in EXAMPLE_SPANS]
    return gate_check._range_check(found, {"rows": rows}, False)


def danger_why():
    """The danger banner's sentence for the example over its range: the
    server's headline (``cz_gate_routes.headline``) plus the dialog's tail."""
    from quam_state_manager.web.cz_gate_routes import headline
    check = range_check(over=True)
    return headline({"checks": {"range": check}}, {"lanes": example_lanes(over=True)},
                    check["message"]) + DANGER_TAIL


def warn_why():
    """The warn banner's sentence for a gate the lab's code could not check
    (the lab environment was busy): the server's reason plus the dialog's tail."""
    from quam_state_manager.core.cz_gate_build import GATE_CLASSES
    from quam_state_manager.core.cz_gate_create import may_create
    _ok, _state, reason = may_create({"status": "busy"}, None, gate_class=next(iter(GATE_CLASSES)),
                                     layout="known")
    return reason + WARN_TAIL


def limit_note():
    """The note beside a table cell over the limit, as cz-gate.js writes it."""
    from quam_state_manager.core.gate_timeline_view import fmt_num
    return "limit %s V" % fmt_num(example_limit_v())


def dialog_tiles(lanes):
    """The Key numbers tiles of *lanes* as the gate dialog shows them: label,
    value, unit, note and tone come from ``gate_timeline_view.tiles`` (the
    dialog's rule), and only the warn and danger tones are kept, as cz-gate.js
    ``renderTiles`` does."""
    from quam_state_manager.core.gate_timeline_view import tiles
    return [{"label": t["label"], "value": t["value"], "unit": t["unit"] or None, "note": t["note"],
             "tone": t["tone"] if t["tone"] in ("warn", "danger") else None}
            for t in tiles({"lanes": lanes})]


def timing_tile(lanes):
    """The Timing tile of *lanes* as the gate dialog shows it (see :func:`dialog_tiles`)."""
    return next(t for t in dialog_tiles(lanes) if t["label"] == "Timing")


def gap_note(tile):
    """The line the gate dialog adds under its verdict for a warn Timing tile
    (cz-gate.js ``renderCheck``, one pair), or None when the tile is not warn."""
    if tile.get("tone") != "warn":
        return None
    return "The gate lines have a %s ns gap with no overlap. Check the port delays." % tile["value"]


def reading_form(state):
    """The form column of the reading-order example (a new two-line gate)."""
    amp = "3.0" if state == "danger" else "0.1"
    num = lambda i, v, u, src: {"part": "field", "label": i, "source": {"text": src, "tone": "accent" if src == "entered" else "neutral"},
                                "control": {"part": "input", "num": True, "value": v, "unit": u, "block": True,
                                            "invalid": state == "danger" and v == "3.0"}}
    return [
        {"part": "zone", "step": 1, "title": "Gate", "body": [
            {"part": "field", "label": "Name", "control": {"part": "input", "value": "cz_fast", "block": True}},
            {"part": "field", "label": "Class", "control": {"part": "select", "options": ["CZ gate", "Square pulse"], "value": "CZ gate", "block": True}},
        ]},
        {"part": "zone", "step": 2, "title": "Pulses", "summary": [line_count_words(2)], "body": [
            {"part": "slot", "title": "Qubit flux pulse", "chip": "q1.z", "line": 1, "body": [
                {"part": "grid2", "items": [num("Length", "100", "ns", "entered"), num("Amplitude", amp, "V", "entered")]}]},
            {"part": "slot", "title": "Coupler pulse", "chip": "coupler_q1_q2", "line": 2, "body": [
                {"part": "grid2", "items": [num("Length", "60", "ns", "entered"), num("Amplitude", "0.05", "V", "default")]}]},
            {"part": "panel", "title": "More pairs", "fold": True, "summary": ["only q1-q2"],
             "body": [{"part": "check", "text": "Also q2-q3"}]},
        ]},
    ]


READING_EMPTY = "Press Check: the lab's code builds this gate on a copy of the chip and the result shows here."


def reading_evidence(state):
    """The evidence column: ONE result zone holding conclusion, key numbers, table, checks, what is written.
    state "before": the zone before a check, quiet but still a visible region."""
    if state == "before":
        return [{"part": "zone", "step": 3, "title": "Result", "fill": True, "quiet": READING_EMPTY}]
    ok = state == "ok"
    banner = ({"part": "verdict", "strong": True, "tone": "ok", "key": "Ready to create", "why": OK_WHY} if ok else
              {"part": "verdict", "strong": True, "tone": "danger", "key": "Cannot be created yet",
               "why": danger_why()})
    lanes = example_lanes(over=not ok)
    gap = gap_note(timing_tile(lanes))
    stats = {"part": "stats", "label": "Key numbers", "tiles": dialog_tiles(lanes)}
    rows = [[{"text": "cz_fast_pulse_q1_q2", "line": 1}, "100 ns",
             "0.1 V" if ok else {"text": "3 V", "tone": "danger", "note": limit_note()},
             {"text": "161 ns", "tone": "warn", "note": "port delay"}],
            [{"text": "cz_fast_coupler_pulse_q1_q2", "line": 2}, "60 ns", "0.05 V", "0 ns"]]
    table = {"part": "panel", "title": "Entered and actual", "flush": True,
             "summary": [{"text": "all as entered", "level": "ok"} if ok else {"text": "1 over the limit", "level": "danger"}],
             "body": [{"part": "table", "columns": ["Pulse", "Length", "Max level", "Starts at"], "numeric": [1, 2, 3], "rows": rows}]}
    names = ["Chip loads", "Operations exist", "Pulse lengths", "Preview equals config", "Timeline"]
    checks = [{"name": n, "state": "pass"} for n in names]
    checks.append({"name": "Output range", "state": "pass"} if ok else
                  {"name": "Output range", "state": "fail", "why": range_check(over=True)["message"]})
    checks.append({"name": "Hardware timing", "state": "skip"})
    chk = {"part": "panel", "title": "Checks",
           "summary": [{"text": "6 of 6 passed", "level": "ok"}] if ok else [{"text": "1 failed", "level": "danger"}, "5 passed"],
           "body": [{"part": "checks", "checks": checks}]}
    written = {"part": "panel", "title": "Will be written", "fold": True, "hint": "working copy only; the chip changes on Apply",
               "summary": [{"text": "3", "level": "strong"}, "objects"],
               "body": ["Two pulses and one gate macro under the pair."]}
    wave = {"part": "panel", "title": "Waveform", "hint": "program time, before filters",
            "summary": [{"part": "line", "line": 1, "text": "q1.z"}, {"part": "line", "line": 2, "text": "coupler_q1_q2"}],
            "body": [{"part": "placeholder", "text": "The lane chart renders here (gate timeline component)"}]}
    return [{"part": "zone", "step": 3, "title": "Result", "fill": True,
             "summary": [{"text": "checked", "level": "ok"} if ok else {"text": "1 problem", "level": "danger"}],
             "body": [banner] + ([{"part": "note", "text": gap}] if gap else []) + [stats, wave, table, chk, written]}]


def _reading():
    """K19: each reading-order part alone, in every tone (docs/308 section 4c)."""
    sec = "Reading order"
    out = []
    for tone, key, why in (("ok", "Ready to create", OK_WHY),
                           ("warn", "Not checked with the lab's code", warn_why()),
                           ("danger", "Cannot be created yet", danger_why()),
                           ("note", "Not checked on hardware", "Only the samples written to the config are compared.")):
        out.append((sec, "Banner " + tone, {"part": "verdict", "strong": True, "tone": tone, "key": key, "why": why}))
    out += [
        (sec, "Stats, a danger and a warn tile", {"part": "stats", "label": "Key numbers",
                                                  "tiles": dialog_tiles(example_lanes(over=True))}),
        (sec, "Checks, one failed and one skipped", {"part": "checks", "checks": [
            {"name": "Chip loads", "state": "pass"}, {"name": "Pulse lengths", "state": "pass"},
            {"name": "Output range", "state": "fail", "why": range_check(over=True)["message"]},
            {"name": "Hardware timing", "state": "skip"}]}),
        (sec, "Panel with step, hint and summary", {"part": "panel", "step": 2, "title": "Pulses", "hint": "program time",
                                                    "summary": [line_count_words(2)],
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
    out += [
        (sec, "Banner with a quiet meta line", {"part": "verdict", "strong": True, "tone": "ok", "key": "FREE",
                                                "why": "No open QM. No action needed.", "meta": "Bench A · updated 3 s ago"}),
        (sec, "Stats with notes and a quiet (unknown) tile", {"part": "stats", "label": "Key numbers", "tiles": [
            {"label": "Open QMs", "value": "3", "note": "1 idle", "tone": "warn"}, {"label": "Jobs running", "value": "1"},
            {"label": "Hottest slot", "value": "unknown", "note": "no reading yet", "tone": "quiet"},
            {"label": "Server", "value": "QOP 3.5", "note": "all calls answered"}]}),
        (sec, "Facts (label / value)", {"part": "facts", "label": "Facts", "items": [
            {"label": "State", "value": "idle", "note": "no job change for 42 min while SM watched"},
            {"label": "FEMs", "value": "con1/1, con1/2"}, {"label": "Owner", "value": "Bench A (ab12cd34)"}]}),
        (sec, "Callout with facts and buttons", {"part": "callout", "tone": "warn", "title": "Close qm-1004?",
                                                 "text": "Any running job will stop.",
                                                 "body": [{"part": "facts", "items": [{"label": "State", "value": "idle"},
                                                                                      {"label": "FEMs", "value": "con1/1"}]}],
                                                 "actions": [{"label": "Confirm close", "cls": "btn-sm"}, P_QUIET]}),
        (sec, "Table row with a callout under it", {"part": "table", "label": "Rows", "columns": ["QM", "State"], "rows": [
            [{"chip": "qm-1004"}, "idle"],
            {"sub": [{"part": "callout", "tone": "warn", "title": "Close qm-1004?", "text": "Any running job will stop."}]},
            [{"chip": "qm-1005"}, "running"]]}),
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


def zone_samples():
    """The standalone zones: with a step, without one, and quiet (docs/308 section 4d)."""
    return [
        {"part": "zone", "step": 1, "title": "Gate", "summary": ["2 lines"],
         "body": [{"part": "field", "label": "Name", "control": {"part": "input", "value": "cz_fast", "block": True}}]},
        {"part": "zone", "title": "Neighbours", "body": [
            {"part": "panel", "title": "Checks", "summary": [{"text": "6 of 6 passed", "level": "ok"}],
             "body": ["A panel groups inside a zone and stays lighter than it."]}]},
        {"part": "zone", "step": 3, "title": "Result", "quiet": READING_EMPTY},
        {"part": "zone", "title": "Danger", "tone": "danger", "fold": True, "summary": ["cannot be undone"],
         "body": ["Actions here stop other people's work; each one asks first."]},
    ]


def _zones():
    sec = "Zones"
    z = zone_samples()
    return [(sec, "Zone with a step and a summary", z[0]), (sec, "Zone without a step, a panel inside", z[1]),
            (sec, "Zone, quiet (nothing to show yet)", z[2]), (sec, "Zone, danger tone, folded", z[3])]


def specs():
    """[(section, label, spec)] in page order."""
    out = _parts()
    out += _choosing()
    out += _reading()
    out += _zones()
    out += [("Worked example: rule review (A1)", "Review dialog", _review_dialog()),
            ("Worked example: apply to other pairs (A2)", "Apply dialog", _apply_dialog()),
            ("Worked example: environment card (E1, E2)", "Card head and verdicts", _env_card())]
    return out
