"""Adapt a QM configuration to the shared instrument wiring drawing."""
from copy import deepcopy


PORT_GROUPS = {
    "analog_outputs": ("output_ports", "output"),
    "analog_inputs": ("input_ports", "input"),
    "digital_outputs": ("digital_ports", "digital"),
    "digital_inputs": ("digital_input_ports", "digital-input"),
}


def qm_rack(port_map, wiring=None):
    """Use the whole chip chassis only when it covers every declared FEM."""
    if not isinstance(port_map, dict) or "error" in port_map or "fems" not in port_map:
        return None
    fems = port_map["fems"] or []
    chip = (wiring or {}).get("controllers", {})
    covered = bool(fems) and all(
        f["slot"] is not None
        and str(f["slot"]) in chip.get(f["controller"], {}).get("fems", {})
        for f in fems
    )
    controllers = deepcopy(chip) if covered else {}
    # Keep geometry and roles, but never show another QM's element assignments.
    for ctrl in controllers.values():
        for fem in ctrl.get("fems", {}).values():
            fem.pop("ports", None)
            for group, _ in PORT_GROUPS.values():
                fem[group] = {}
    highlight = []
    unplaced = port_map.get("octave", 0) + port_map.get("unparsed", 0)
    for f in fems:
        if f["slot"] is None:
            unplaced += max(1, len(f["ports"]))
            continue
        con, slot = f["controller"], str(f["slot"])
        ctrl = controllers.setdefault(con, {"fems": {}, "max_output_port": 8})
        fem = ctrl["fems"].setdefault(slot, {})
        kind = {"MW": "mw-fem", "LF": "lf-fem"}.get(f.get("type"))
        fem["type"] = kind or fem.get("type") or "type unknown"
        original = chip.get(con, {}).get("fems", {}).get(slot, {}) if covered else {}
        for p in f["ports"]:
            group, io = PORT_GROUPS[p["kind"]]
            port = str(p["port"])
            ctrl["max_output_port"] = max(ctrl.get("max_output_port", 8), int(port))
            old = original.get(group, original.get("ports", {}) if io == "output" else {}).get(port, [])
            roles = {a.get("element"): a.get("role") for a in old}
            # Unknown roles use the renderer's neutral fill; no role is inferred from a slot.
            names = p.get("elements") or []
            fem.setdefault(group, {})[port] = [
                {"element": name, "label": name, "role": roles.get(name) or (
                    "digital" if io.startswith("digital") else "configured"
                )} for name in names
            ] or [{"label": "Declared port " + port, "role": "configured"}]
            highlight.append([con, slot, port, io])
    return {
        "data": {"controllers": controllers, "stats": {
            "refs_seen": unplaced, "refs_placed": 0, "octave_detected": bool(port_map.get("octave")),
        }},
        "highlight": highlight,
        "source": "Chassis from the open chip's wiring" if covered else "Only the FEMs this QM declares",
    }
