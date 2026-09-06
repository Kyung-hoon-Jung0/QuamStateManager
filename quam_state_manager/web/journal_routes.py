"""The Calibration log page (docs/173 S2): the story of a chip, one day at a time.

Renders ``core/story.build_day`` -- every run in the open dataset folder as a
card (agent-run, human-run, or unknown), the values each wrote, the gate,
the journal lines attached to it, and the day's write cards -- with a
per-target digest strip on top. Filters (author, target) and the day are
query parameters so htmx re-fetches only the body.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request

from quam_state_manager.core import journal as journal_mod
from quam_state_manager.core import story

logger = logging.getLogger(__name__)

journal_bp = Blueprint("journal", __name__)


def _r():
    from quam_state_manager.web import routes
    return routes


def _chip_name() -> str:
    from quam_state_manager.web.agent_api import _chip_name
    return _chip_name()


def _events() -> list[dict]:
    from quam_state_manager.web import agent_api
    with agent_api._events_lock:
        return list(agent_api._events())


def _day_arg() -> str:
    d = (request.args.get("day") or "").strip()
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return d
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d")


def _filters() -> dict:
    return {"author": (request.args.get("author") or "").strip(),
            "q": (request.args.get("q") or "").strip()}


def _matches(card: dict, f: dict) -> bool:
    a = f["author"]
    if a and not str(card.get("author") or "").startswith(a):
        return False
    q = f["q"].lower()
    if q:
        hay = " ".join(card.get("targets") or []) + " " + " ".join(e.get("path") or "" for e in card.get("entries") or []) \
            + " " + " ".join(e.get("text") or "" for e in card.get("journal") or []) + " " + str(card.get("node") or "")
        if q not in hay.lower():
            return False
    return True


def _build(day: str) -> dict:
    r = _r()
    ds = r._dataset_store()
    hm = None
    active = r._active_path()
    try:
        hm = r._history() if active else None
    except Exception:  # noqa: BLE001
        hm = None
    key = r._folder_key(ds.folder_path) if ds is not None else None
    data = story.build_day(current_app.instance_path, _chip_name(), day, ds=ds, hm=hm, active_path=active,
                           events=_events(), uid_of=(lambda run: f"{key}:{run['run_id']}") if key else None)
    f = _filters()
    data["filters"] = f
    data["cards_shown"] = [c for c in data["cards"] if _matches(c, f)]
    data["no_dataset"] = ds is None
    data["folder"] = str(journal_mod.root(current_app.instance_path))
    d0 = datetime.strptime(day, "%Y-%m-%d")
    data["prev_day"] = (d0 - timedelta(days=1)).strftime("%Y-%m-%d")
    data["next_day"] = (d0 + timedelta(days=1)).strftime("%Y-%m-%d")
    data["is_today"] = day == datetime.now().strftime("%Y-%m-%d")
    data["days"] = journal_mod.list_days(current_app.instance_path, _chip_name())
    data["authors"] = sorted({c.get("author") for c in data["cards"] if c.get("author")})
    data["loaded"] = bool(active)
    return data


@journal_bp.route("/journal")
def journal_page():
    r = _r()
    day = _day_arg()
    data = _build(day)
    template = "_journal.html" if r._is_htmx() else "journal.html"
    return render_template(template, **r._ctx(page="journal", story=data))


@journal_bp.route("/journal/day")
def journal_day():
    """The body only -- day / filter changes swap this."""
    data = _build(_day_arg())
    return render_template("_journal_body.html", story=data)


@journal_bp.route("/journal/raw")
def journal_raw():
    day = _day_arg()
    chip = request.args.get("chip") or _chip_name()
    text = journal_mod.read(current_app.instance_path, chip, day)
    return render_template("_journal_raw.html", chip=chip, day=day, text=text,
                           html=journal_mod.render(text),
                           file=str(journal_mod.day_file(current_app.instance_path, chip, day)))


@journal_bp.route("/journal/claim", methods=["POST"])
def journal_claim():
    """A person says who ran a run (or corrects it) and leaves a note."""
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        run_id = int(data.get("run_id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="run_id required"), 400
    who = str(data.get("who") or "").strip()
    author = f"human:{who}" if who else "human"
    if str(data.get("author") or "").strip() in ("unknown", ""):
        pass
    rec = story.claim_run(current_app.instance_path, _chip_name(), run_id, author=author, note=data.get("note"))
    # docs/173 S8: journal the claim on the RUN's own day, so the line sits next to
    # the run it is about (and is not stranded on today's page when the claim is made
    # later). Fall back to now() when the run's date cannot be resolved.
    when = _run_when(run_id)
    journal_mod.append(current_app.instance_path, _chip_name(),
                       f"run #{run_id} was run by {author}" + (f": {rec['note']}" if rec.get("note") else ""),
                       kind="human", run_id=run_id, when=when)
    return jsonify(ok=True, claim=rec)


def _run_when(run_id: int):
    """The run's own timestamp (for placing its claim line), or None -> now()."""
    try:
        from quam_state_manager.web import routes as r
        ds = r._dataset_store()
        run = ds.get_run(int(run_id)) if ds is not None else None
        if not run:
            return None
        ep = story._epoch(run.get("run_start"), run.get("date"), run.get("time"))
        return datetime.fromtimestamp(ep) if ep else None
    except Exception:  # noqa: BLE001
        return None


@journal_bp.route("/journal/adopt", methods=["POST"])
def journal_adopt():
    """Move the day's 'unassigned' lines (a session that named no chip)
    under this chip. A person's decision, never automatic."""
    data = request.get_json(silent=True) or request.form.to_dict()
    day = (data.get("day") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    n = journal_mod.adopt_unassigned(current_app.instance_path, _chip_name(), day)
    return jsonify(ok=True, moved=n)
