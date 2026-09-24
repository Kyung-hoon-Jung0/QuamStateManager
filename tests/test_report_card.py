"""Tests for the Chip Report Card (core/report_card.py + /topology/report).

The card must be computed from the trust-gated records: an unphysical fit counts
as a 'bad fit' (never below-spec or averaged); below-spec uses chip_health.verdict
on the gated value; worst offenders pick the lowest gated (not raw) value.
"""

from __future__ import annotations

from datetime import datetime, timezone

from quam_state_manager.core import report_card
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine


def _state():
    return {
        "qubits": {
            # healthy
            "qA1": {"id": "qA1", "f_01": 5.0e9, "T1": 3.0e-5, "T2ramsey": 2.5e-5,
                    "gate_fidelity": {"averaged": 0.999}},
            # below spec (low gate fidelity) + unphysical T2 (bad fit)
            "qA2": {"id": "qA2", "f_01": 5.1e9, "T1": 2.8e-5, "T2ramsey": -4.7e-4,
                    "gate_fidelity": {"averaged": 0.93}},
            "qA3": {"id": "qA3", "f_01": 5.2e9, "T1": 2.9e-5, "T2ramsey": 2.6e-5,
                    "gate_fidelity": {"averaged": 0.995}},
        },
        "qubit_pairs": {
            "qA2-qA1": {"id": "qA2-qA1", "qubit_control": "#/qubits/qA2",
                        "qubit_target": "#/qubits/qA1",
                        "macros": {"cz": {"fidelity": {"Bell_State": {"Fidelity": 0.40}}}},
                        "coupler": None},
        },
    }


def _engine():
    return QueryEngine(QuamStore.from_dicts(_state(), {"wiring": {"qubits": {}}}))


def test_bad_fit_is_quarantined_not_below_spec():
    r = report_card.build_report(_engine(), chip_name="t",
                                 generated_at=datetime(2026, 6, 8, tzinfo=timezone.utc))
    # qA2's −473µs T2 is a bad fit, NOT a below-spec value
    assert r["counts"]["bad_fits"] >= 1
    assert any(b["id"] == "qA2" and b["metric"] == "T2ramsey" for b in r["bad_fits"])
    # qA2 IS below spec, but for its gate fidelity (0.93), not the unphysical T2
    qa2_below = [b for b in r["below_spec"] if b["id"] == "qA2"]
    assert qa2_below and qa2_below[0]["metric"] == "gate_fidelity_avg"


def test_overall_verdict_fail_on_low_gate_and_cz():
    r = report_card.build_report(_engine(), chip_name="t")
    assert r["verdict"] == "fail"          # 0.93 gate fid < 0.95 fail; 0.40 cz < 0.90 fail
    assert r["counts"]["cz_below_spec"] == 1


def test_worst_offenders_use_gated_value():
    r = report_card.build_report(_engine(), chip_name="t")
    # the gate-fidelity worst offender is qA2 (0.93), and the value is the gated one
    gf = [w for w in r["worst_offenders"] if w["metric"] == "gate_fidelity_avg"]
    assert gf and gf[0]["id"] == "qA2" and abs(gf[0]["value"] - 0.93) < 1e-9


def test_custom_thresholds_change_below_spec_and_note():
    # A stricter gate-fidelity threshold pushes more qubits below spec, and the
    # card labels the source as the user's edited thresholds (matches the header).
    eng = _engine()
    default = report_card.build_report(eng, chip_name="t")
    strict = report_card.build_report(eng, chip_name="t", thresholds={
        "gate_fidelity_avg": {"warn": 0.9995, "fail": 0.999, "direction": "higher"},
    })
    assert strict["counts"]["below_spec"] >= default["counts"]["below_spec"]
    assert strict["thresholds_source"] == "your UI-edited thresholds"
    assert default["thresholds_source"] == "default spec thresholds"


def test_renderers_produce_nonempty_output():
    r = report_card.build_report(_engine(), chip_name="t")
    md = report_card.render_markdown(r)
    csv = report_card.render_csv(r)
    html = report_card.render_html(r)
    assert "Chip Report Card" in md and "FAIL" in md
    assert "section,id,metric" in csv
    assert "<html>" in html and "Chip Report Card" in html


def test_route_serves_all_formats(tmp_path):
    import json
    from quam_state_manager.web.app import create_app
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {"qubits": {}}, "network": {"host": "1.1.1.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    for fmt, mime in (("md", "text/markdown"), ("csv", "text/csv"), ("html", "text/html")):
        resp = c.get(f"/topology/report?format={fmt}")
        assert resp.status_code == 200
        assert resp.mimetype == mime
        assert "attachment" in resp.headers.get("Content-Disposition", "")
        assert len(resp.get_data()) > 0


def test_route_names_the_chip_folder_and_dates_locally(tmp_path, monkeypatch):
    """QA review of regenerate-r2-33 (+ its F26 sibling): the report was named
    after the chip folder's PARENT (a customer chip in ``chip/260907_KRS_5Q``
    downloaded as ``chip_report_chip_...``) and stamped in UTC, so a 07:49 KST
    report was dated the day before. It follows the chip header's name now,
    and a local stamp that carries its offset."""
    import json
    from datetime import timedelta

    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app

    kst = timezone(timedelta(hours=9))

    class _KstMorning(datetime):          # 2026-09-24 07:49 KST = 09-23 in UTC
        @classmethod
        def now(cls, tz=None):
            t = cls(2026, 9, 24, 7, 49, 18, tzinfo=kst)
            return t if tz is None else t.astimezone(tz)

        def astimezone(self, tz=None):    # "the machine's local zone" = KST here
            return self if tz is None else datetime.astimezone(self, tz)

    chip = tmp_path / "chip" / "260907_KRS_5Q"
    chip.mkdir(parents=True)
    (chip / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(
        {"wiring": {"qubits": {}}, "network": {"host": "1.1.1.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    monkeypatch.setattr(routes, "datetime", _KstMorning)
    resp = c.get("/topology/report?format=md")
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    assert "chip_report_260907_KRS_5Q_2026-09-24.md" in cd, cd
    body = resp.get_data(as_text=True)
    assert "260907_KRS_5Q" in body.splitlines()[0], body.splitlines()[0]
    assert "2026-09-24T07:49:18+09:00" in body
