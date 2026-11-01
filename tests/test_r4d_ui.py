"""R4d pins: one fit observer per map, Flux export through the existing route."""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"


def test_fit_observer_is_created_once_per_map_body():
    js = (WEB / "static" / "component-map.js").read_text(encoding="utf-8")
    assert js.count("new MutationObserver") == 1
    assert "!body._cmapFitObs" in js
    assert "body._cmapFitApply = apply" in js


def test_flux_table_exports_through_the_existing_route():
    html = (WEB / "templates" / "_flux.html").read_text(encoding="utf-8")
    assert 'href="/export?props=z_joint_offset' in html


def test_flux_export_props_are_real_columns(tmp_path):
    from quam_state_manager.web.app import create_app
    import json
    (tmp_path / "chip").mkdir()
    st = {"qubits": {"q1": {"id": "q1", "z": {"joint_offset": 0.1,
          "independent_offset": 0.0, "flux_point": "joint"}}}}
    (tmp_path / "chip" / "state.json").write_text(json.dumps(st))
    (tmp_path / "chip" / "wiring.json").write_text("{}")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": (tmp_path / "chip").as_posix()})
    r = c.get("/export?props=z_joint_offset&props=z_flux_point")
    body = r.get_data(as_text=True)
    assert "q1" in body and "0.1" in body and "joint" in body


def test_tool_windows_cascade_and_the_newest_is_in_front():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    i = js.index("D-120 (TL-3)")
    seg = js[i:i + 900]
    assert '"settings-dropdown", "calc-popover", "manual-popover"' in seg
    assert "left += 28 * n; top += 28 * n;" in seg
    assert "pop.style.zIndex" in seg


def test_calculator_sizes_to_its_content():
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    i = css.index(".calc-popover {")
    rule = css[i:css.index("}", i)]
    assert "height: min(560px" not in rule and "max-height: 94vh" in rule


def test_blocked_network_step_focuses_the_first_bad_field():
    js = (WEB / "static" / "generate.js").read_text(encoding="utf-8")
    i = js.index("function markNetworkInvalid")
    seg = js[i:js.index("function tryNext", i)]
    assert "if (!first) first = el;" in seg and "first.focus(" in seg
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    assert ".gen-field input::placeholder" in css
