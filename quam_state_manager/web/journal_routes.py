"""The Calibration log page (docs/173 S2): the story of a chip, one day at a time.

Renders ``core/story.build_day`` on the open chip's ledger (docs/281): one
card per run event (its exact change rows, gate, figures and attached journal
lines), one per SM write that landed (actor, door, every entry, undo links),
and one per agent run that left no run folder -- with a per-target digest
strip on top. The day is the project-zone day of each instant. Filters
(author, search) and the day are query parameters so htmx re-fetches only
the body. The shareable report's Calibration log section calls ``_build``.
"""

from __future__ import annotations

import logging
import hashlib
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from flask import Blueprint, current_app, jsonify, render_template, request

from quam_state_manager.core import journal as journal_mod
from quam_state_manager.core import story
from quam_state_manager.core.search_query import groups, matches_hay

logger = logging.getLogger(__name__)

journal_bp = Blueprint("journal", __name__)
_DAY_HTML_LOCK = threading.Lock()


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
        return _today()


def _today():
    from zoneinfo import ZoneInfo
    zone = (_r()._display_zone() or {}).get("zone")
    return datetime.now(ZoneInfo(zone) if zone else None).strftime("%Y-%m-%d")


def _ledger_context():
    from quam_state_manager.core.hub_index import context
    r = _r()
    active = r._active_path()
    if not active:
        return None
    ctx = r._active_ctx() or {}
    directory = ctx.get("hub_chip_dir") or r._hub_chip_dir(active)
    if directory is None:
        raise RuntimeError("the chip history is unavailable")
    return context(SimpleNamespace(directory=Path(directory)), instance=current_app.instance_path,
                   project=ctx.get("qualibrate_project"))


def _filters() -> dict:
    return {"author": (request.args.get("author") or "").strip(),
            "q": (request.args.get("q") or "").strip()}


_OP_WORD = {"add": "added", "gone": "removed", "retarget": "retargeted", "first": "first recorded"}


def _value_words(v) -> str:
    """A value as the card shows it, plus its plain number spelling."""
    from quam_state_manager.core.units import group_digits
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        shown = group_digits(v)
        return shown if shown == repr(v) else f"{shown} {v!r}"
    if isinstance(v, dict) and "_array" in v and "_hash" in v:
        return f"[{v['_array']} values]"
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return str(v)


def _row_words(row) -> str:
    return " ".join(x for x in (str(row.get("path") or ""), _OP_WORD.get(row.get("op"), ""),
                                _value_words(row.get("old")) if row.get("op") != "add" else "",
                                _value_words(row.get("new")) if row.get("op") != "gone" else "",
                                "undone" if row.get("taken_back") else "") if x)


def _line_text(e) -> str:
    return " ".join(str(x) for x in (e.get("time"), e.get("kind"), e.get("text"), e.get("because")) if x)


def _search_text(c) -> str:
    """docs/281 review: what a person can SEE on the card -- never internal
    ids, instants or folder hashes (a run number "700" used to match 40 cards
    through their timestamps)."""
    kind = c.get("kind")
    if kind not in ("run", "write", "agent_run"):
        return _line_text(c)
    parts = [c.get("time"), c.get("author"), c.get("plan_id") and f"plan {c['plan_id']}"]
    if kind == "run":
        gate = c.get("gate") or {}
        parts += [c.get("node"), c.get("family_label"), " ".join(c.get("targets") or []),
                  c.get("outcome") or "no outcome", f"#{c.get('run_id')}",
                  gate and f"gate {gate.get('verdict')}", gate.get("reason"),
                  " ".join(f["label"] for f in c.get("flags") or []),
                  c.get("first_state_n") and "first state in the chip history",
                  c.get("because"), c.get("note")]
        parts += [_row_words(r) for r in c.get("writes") or []]
        parts += [f"{p.get('key')} {_value_words(p.get('old'))} {_value_words(p.get('new'))}"
                  for p in c.get("params_diff") or []]
        parts += [_line_text(e) for e in c.get("journal") or []]
    elif kind == "write":
        parts += [c.get("label") or c.get("event_kind"), "undone" if c.get("undone") else "",
                  "partly undone" if c.get("partly_undone") else ""]
        parts += [_row_words(r) + " " + str(r.get("actor") or "") for r in c.get("entries") or []]
        parts += [f"takes back the write of {u.get('time')}" for u in c.get("undoes") or [] if u.get("time")]
    else:
        parts += [c.get("sentence"), c.get("reason"), c.get("purpose"), c.get("short"),
                  c.get("step") is not None and f"step {c['step']}"]
    return " ".join(str(x) for x in parts if x)


def _int_types():
    """docs/281 review: which ledger paths the OPEN chip stores as integers
    (the ledger keeps every number as REAL); None without an open chip."""
    store = _r()._store()
    doc = getattr(store, "merged", None) if store is not None else None
    if not isinstance(doc, dict):
        return None
    from quam_state_manager.core.hub_store import segments
    memo: dict[str, bool] = {}

    def int_of(path: str) -> bool:
        hit = memo.get(path)
        if hit is None:
            node = doc
            for part in segments(path):
                if isinstance(node, dict):
                    node = node.get(part, _MISSING)
                elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                    node = node[int(part)]
                else:
                    node = _MISSING
                if node is _MISSING:
                    break
            hit = memo[path] = isinstance(node, int) and not isinstance(node, bool)
        return hit
    return int_of


_MISSING = object()
#: A day with more cards than this sends each card's row and fetches its body
#: when it is first opened (docs/281: a 551-run day was a 4 MB page).
LAZY_CARDS = 150
_DAY_DATA_LOCK = threading.Lock()


def _matches(card: dict, f: dict) -> bool:
    author = str(card.get("author") or card.get("kind") or "")
    return (not f["author"] or author.startswith(f["author"])) and matches_hay(
        card["search_hay"].lower(), groups(f["q"]))


def _build(day: str, *, filters=None, gate_wait=False, lazy_ok=True) -> dict:
    r = _r()
    ds = r._dataset_store()
    active = r._active_path()
    try:
        ledger, unavailable = _ledger_context(), None
    except RuntimeError as exc:
        # No history dir for the open chip: say so, never fall back to a
        # second history source and present it as the chip's.
        ledger, unavailable = None, f"{exc}, so runs and SM writes cannot be listed."
    key = r._folder_key(ds.folder_path) if ds is not None else None
    def uid_of(run):
        root = (run.get("_hub") or {}).get("root_path")
        run_key = r._folder_key(root) if root else key
        return f"{run_key}:{run['run_id']}" if run_key else None
    if unavailable:
        data = story._empty_day(_chip_name(), day, {"state": "unavailable", "note": unavailable[:1].upper() + unavailable[1:]},
                                current_app.instance_path)
    elif ledger is not None and not (ledger.store.directory / "ledger.sqlite").exists():
        from quam_state_manager.core import hub_sync
        st = hub_sync.status(ledger.store.directory)
        # building -> say so and refresh; no sync at all -> say so once (a
        # page that polls a ledger nobody builds polls forever)
        history = (dict(st, state="building") if st.get("state") == "building" else
                   {"state": "unavailable", "note": "This window has not built a chip history for this chip, "
                                                    "so runs and SM writes cannot be listed."})
        data = story._empty_day(_chip_name(), day, history, current_app.instance_path)
    else:
        data = story.build_day(current_app.instance_path, _chip_name(), day, ds=ds, active_path=active,
                               ledger=ledger, agent_chip=_agent_chip_key(),
                               events=_events(), uid_of=uid_of,
                               # gates are checked in the background for a person; a test
                               # app waits, so a page is the same page twice
                               gate_wait=gate_wait or bool(current_app.config.get(
                                   "JOURNAL_GATE_WAIT", current_app.testing)),
                               int_of=_int_types() if ledger is not None else None)
    f = _filters() if filters is None else filters
    data["filters"] = f
    for order, c in enumerate(data["cards"]):
        c["search_order"] = order
    for c in data["cards"] + data["loose"] + data["unassigned"]:
        c["search_hay"] = _search_text(c)
    for c in data["cards"]:
        for e in c.get("journal") or []:
            e["search_hay"] = _line_text(e)
    data["lazy"] = bool(lazy_ok and len(data["cards"]) > LAZY_CARDS)
    if data["lazy"]:
        # the bodies a lazy day sends later, by their element id
        by_dom = {}
        for c in data["cards"]:
            dom = (c.get("card_id") or f"card-{c.get('run_id')}" if c["kind"] == "run" else
                   f"write-{c.get('id')}" if c["kind"] == "write" else f"agent-{c.get('id')}")
            by_dom[dom] = c
        days = current_app.config.setdefault("journal_day_cards", {})
        with _DAY_DATA_LOCK:
            days.pop((str(active), day), None)
            days[(str(active), day)] = by_dom
            while len(days) > 4:
                days.pop(next(iter(days)))
    data["cards_shown"] = [c for c in data["cards"] if _matches(c, f)]
    data["cards_cached"] = [c for c in data["cards"] if not _matches(c, f)]
    data["runs_shown"] = sum(c["kind"] in ("run", "agent_run") for c in data["cards_shown"])
    data["lines_shown"] = [e for e in data["loose"] + data["unassigned"] if _matches(e, f)]
    data["no_dataset"] = ds is None and ledger is None
    data["folder"] = str(journal_mod.root(current_app.instance_path))
    d0 = datetime.strptime(day, "%Y-%m-%d")
    # docs/191 A02: the day arrives from a date picker with no bounds, and the
    # two ends of the calendar have no neighbour -- `datetime.max + 1 day`
    # raises OverflowError and 500'd the page on 9999-12-31. A day with no
    # next day simply has none; the arrow is left pointing at itself, which
    # the template already renders as a dead end rather than a crash.
    def _step(base, days):
        try:
            return (base + timedelta(days=days)).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return base.strftime("%Y-%m-%d")
    data["prev_day"] = _step(d0, -1)
    data["next_day"] = _step(d0, 1)
    data["is_today"] = day == _today()
    data["days"] = journal_mod.list_days(current_app.instance_path, _chip_name())
    data["authors"] = sorted({c.get("author") for c in data["cards"] if c.get("author")})
    data["loaded"] = bool(active)
    return data


def _agent_chip_key():
    from quam_state_manager.web.agent_api import _chip_key
    return _chip_key()


@journal_bp.route("/journal")
def journal_page():
    r = _r()
    day = _day_arg()
    data = _build(day)
    template = "_journal.html" if r._is_htmx() else "journal.html"
    return render_template(template, **r._ctx(page="journal", story=data))


@journal_bp.route("/journal/day")
def journal_day():
    """The body, plus the day nav and the author list out-of-band.

    docs/191 N01: it used to be the body ONLY, and the nav lives outside
    `#jr-body` -- so `prev_day` / `next_day` / the disabled state / the `today`
    button were whatever the full page render had baked in, and never moved
    again. Measured in Chrome: three presses of the previous-day button moved
    one day, the next-day button stayed disabled forever, and `today` never
    appeared at all."""
    data = _build(_day_arg())
    # Hash the freshly read content, so ledger writes, claims, journal lines,
    # author changes and warmup transitions invalidate the rendered fragment.
    # Keep two days at most; a large day can otherwise retain megabytes of HTML.
    # Every key the template reads, except the three split from "cards" and
    # "loose" by the filters (already in the token through them).
    keys = sorted(k for k in data if k not in ("cards_shown", "cards_cached", "lines_shown"))
    token = hashlib.sha256(json.dumps([[k, data.get(k)] for k in keys], default=str,
                                     ensure_ascii=True).encode()).digest()
    cache = current_app.config.setdefault("journal_day_html", {})
    with _DAY_HTML_LOCK:
        hit = cache.get(token)
    if hit is not None:
        return hit
    html = render_template("_journal_day_swap.html", story=data)
    if len(html.encode("utf-8")) <= 6 * 1024 * 1024:
        with _DAY_HTML_LOCK:
            if len(cache) >= 2:
                cache.pop(next(iter(cache)))
            cache[token] = html
    return html


@journal_bp.route("/journal/card")
def journal_card():
    """docs/281: the body of one card of a very large day, rendered by the
    page's own card template when the card is first opened."""
    day = _day_arg()
    card = request.args.get("card") or ""
    key = (str(_r()._active_path()), day)
    with _DAY_DATA_LOCK:
        found = (current_app.config.get("journal_day_cards") or {}).get(key, {}).get(card)
    if found is None:
        # a restart or another window rebuilt the day: build it again
        _build(day, filters={"author": "", "q": ""})
        with _DAY_DATA_LOCK:
            found = (current_app.config.get("journal_day_cards") or {}).get(key, {}).get(card)
    if found is None:
        return '<div class="jr-body"><p class="muted">This card is no longer on this day.</p></div>'
    module = current_app.jinja_env.get_template("_journal_card.html").module
    resp = current_app.response_class(str(module.card_body(found, day)), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
    # docs/281 review: a run number is per data folder; the card's own
    # identity (folder key + number) keys the claim when the page sends it
    uid = str(data.get("uid") or "").strip() or None
    if str(data.get("author") or "").strip() in ("unknown", ""):
        pass
    # A payload that never mentions the note keeps the note (story.claim_run's
    # _KEEP); only a note the caller actually sent -- including an empty one --
    # decides it.
    kw = {"note": data["note"]} if "note" in data else {}
    rec = story.claim_run(current_app.instance_path, _chip_name(), run_id, author=author, uid=uid, **kw)
    # docs/173 S8: journal the claim on the RUN's own day, so the line sits next to
    # the run it is about (and is not stranded on today's page when the claim is made
    # later). Fall back to now() when the run's date cannot be resolved.
    when = _run_when(run_id)
    # A later claim does not erase the earlier line (the journal is append-only),
    # so the line says what it overrode -- otherwise the day reads as two people
    # each claiming the same run, at the same stamped time, with no way to tell
    # which was said last.
    prev = rec.get("prev") or {}
    line = f"run #{run_id} was run by {author}" + (f": {rec['note']}" if rec.get("note") else "")
    if prev.get("author") and prev["author"] != author:
        line += f" (corrects an earlier claim of {prev['author']})"
    journal_mod.append(current_app.instance_path, _chip_name(), line,
                       kind="human", run_id=run_id, when=when)
    return jsonify(ok=True, claim=rec)


def _run_when(run_id: int):
    """The run's own timestamp (for placing its claim line), or None -> now().

    The journal files every line by the SERVER's wall clock (``journal.append``
    stamps ``datetime.now()``), so the claim keeps that convention -- but of the
    run's true START instant (``story.start_epoch``: its ``run_start``, else
    the run's instant, docs/262), never the folder digits re-read as if they
    were this machine's clock."""
    try:
        from quam_state_manager.web import routes as r
        ds = r._dataset_store()
        run = ds.get_run(int(run_id)) if ds is not None else None
        if not run:
            return None
        ep = story.start_epoch(run, ds)
        return datetime.fromtimestamp(ep) if ep is not None else None
    except Exception:  # noqa: BLE001
        return None


@journal_bp.route("/journal/adopt", methods=["POST"])
def journal_adopt():
    """Move the day's 'unassigned' lines (a session that named no chip)
    under this chip. A person's decision, never automatic."""
    data = request.get_json(silent=True) or request.form.to_dict()
    day = (data.get("day") or "").strip() or datetime.now().strftime("%Y-%m-%d")
    # docs/191 H05: this day reached `day_file` for BOTH the read and the
    # unlink, so `../../victim` moved a file's lines onto itself and deleted it.
    if not journal_mod.is_day(day):
        return jsonify(ok=False, error="day must be YYYY-MM-DD"), 400
    n = journal_mod.adopt_unassigned(current_app.instance_path, _chip_name(), day)
    return jsonify(ok=True, moved=n)
