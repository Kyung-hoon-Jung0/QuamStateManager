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
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from urllib.parse import urlencode

from flask import Blueprint, current_app, jsonify, make_response, render_template, request
from markupsafe import Markup

from quam_state_manager.core import journal as journal_mod
from quam_state_manager.core import story
from quam_state_manager.core.search_query import groups, matches_hay

logger = logging.getLogger(__name__)

journal_bp = Blueprint("journal", __name__)
_DAY_HTML_LOCK = threading.Lock()


def _r():
    from quam_state_manager.web import routes
    return routes


#: whitespace between two tags, or a line break between two attributes of one
#: tag (a raw ``"`` is always an attribute's quote: values and text escape it)
_GAP = re.compile(r'>\s+<|(?<=")\s*\n\s*(?=[\w:-]+=)')


@journal_bp.app_template_filter("jr_squeeze")
def _jr_squeeze(html, on=True):
    """docs/291: a paged day's rows and strip without the whitespace between
    their tags and the line breaks inside them. Every container there is a
    flex or grid box, where that whitespace never shows; a paged day sends
    thousands of tags, and each gap is a node the browser builds. Attribute
    values and text are never touched."""
    if not on:
        return html
    return Markup(_GAP.sub(lambda m: "><" if m.group(0)[0] == ">" else " ", str(html)))


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
    # S10 C1.5: the log reads the ledger as the open folder sees it (the one
    # folder view every value surface binds)
    return context(SimpleNamespace(directory=Path(directory)), instance=current_app.instance_path,
                   project=ctx.get("qualibrate_project"), folder=r._hub_folder_view(ctx, directory))


def _filters() -> dict:
    return {"author": (request.args.get("author") or "").strip(),
            "q": (request.args.get("q") or "").strip()}


def _page_url() -> str:
    """docs/299: the address of what the page now shows -- the day the person
    picked (none: today, whatever day that is when the page is next opened)
    and the filters -- so a reload, Back after leaving the page and a copied
    link land on the same view. ``/journal`` reads the same three arguments."""
    params = {}
    if (request.args.get("day") or "").strip():
        params["day"] = _day_arg()
    f = _filters()
    if f["q"]:
        params["q"] = f["q"]
    if f["author"]:
        params["author"] = f["author"]
    return "/journal" + ("?" + urlencode(params) if params else "")


def _with_page_url(html: str):
    """Only the page's own day/filter form moves the address: the body's
    "building the history" poll carries the day it shows, not a choice."""
    resp = make_response(html)
    if request.headers.get("HX-Trigger") == "jr-filters":
        resp.headers["HX-Replace-Url"] = _page_url()
    return resp


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
                  " ".join(c.get("targets_as_recorded") or []), c.get("renamed_here"),
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
#: A day with more cards than this is PAGED (docs/291): it renders the newest
#: PAGE_CARDS rows of what the filter matches and sends the earlier (or later)
#: ones a slice at a time; search and the author filter are then answered by
#: the server over the whole day, never by the rows a page happens to hold.
PAGE_CARDS = 300
_DAY_DATA_LOCK = threading.Lock()
#: The window a page may ask a paged day for (docs/291).
_WINDOW_ARGS = ("at", "from", "to")


def _matches(card: dict, f: dict, grps=None, hays=None) -> bool:
    author = str(card.get("author") or card.get("kind") or "")
    if f["author"] and not author.startswith(f["author"]):
        return False
    grps = groups(f["q"]) if grps is None else grps
    if not grps:
        return True
    if hays is None:
        return matches_hay(card["search_hay"].lower(), grps)
    # a paged day's card: its search text is made the first time a search
    # needs it, kept beside the shared build, never written into the card
    hay = hays.get(card["search_order"])
    if hay is None:
        hay = hays[card["search_order"]] = _search_text(card).lower()
    return matches_hay(hay, grps)


def _dom_id(c: dict) -> str:
    """The element id a card renders with (``_journal_card.html``)."""
    if c["kind"] == "run":
        return c.get("card_id") or f"card-{c.get('run_id')}"
    return f"write-{c.get('id')}" if c["kind"] == "write" else f"agent-{c.get('id')}"


def _day_key(day: str):
    return (str(_r()._active_path()), day)


def _cached_day(day: str):
    """The last build of *day* for the open chip that this process holds
    (a lazy day's), or None."""
    with _DAY_DATA_LOCK:
        hit = (current_app.config.get("journal_day_cards") or {}).get(_day_key(day))
    return hit["data"] if hit else None


def _window(matched: list, args: dict) -> tuple[int, int]:
    """Which rows of a paged day's matches render (docs/291): the newest
    page; or the page around the card ``at`` names (a jump to a card the
    page does not hold); or ``from`` a card to ``to`` a card (else to the
    newest) -- the rows a page already shows, kept when the same day is
    built again (a claim, the gates arriving)."""
    n = len(matched)
    if n <= PAGE_CARDS:
        return 0, n
    index = {_dom_id(c): i for i, c in enumerate(matched)}
    at = index.get(args.get("at") or "")
    if at is not None:
        lo = max(0, min(at - PAGE_CARDS // 2, n - PAGE_CARDS))
        return lo, lo + PAGE_CARDS
    first = index.get(args.get("from") or "")
    if first is not None:
        last = index.get(args.get("to") or "")
        return first, (last + 1 if last is not None and last >= first else n)
    return n - PAGE_CARDS, n


def _ts_deltas(cards: list) -> str:
    """Every card's instant, as whole milliseconds rounded UP and written as
    differences (docs/291): a paged page says how many of the WHOLE day are
    new since a person's last visit, not how many of the rows it holds.
    Rounding up keeps ``instant > last visit`` exact for a visit stamped in
    milliseconds."""
    out, prev = [], 0
    for c in cards:
        ts = c.get("ts") or 0
        try:
            ms = -(-round(float(ts) * 1e6) // 1000)
        except (TypeError, ValueError, OverflowError):
            ms = 0
        out.append(str(ms - prev))
        prev = ms
    return ",".join(out)


def _timeline_of(timeline: dict, shown: set) -> dict:
    """The day's per-target strip with only the runs the filter matched --
    a segment, or a target, left with none is gone, exactly what the page's
    own search hides on a day it holds whole (docs/291)."""
    out = {}
    for target, segs in timeline.items():
        kept = []
        for seg in segs:
            steps = [st for st in seg["steps"] if st["card_id"] in shown]
            if steps:
                kept.append(dict(seg, steps=steps))
        if kept:
            out[target] = kept
    return out


def _build(day: str, *, filters=None, gate_wait=False, lazy_ok=True, window=None, reuse=False) -> dict:
    """The day as the page renders it. ``window`` picks the rows of a paged
    day (``_window``); ``reuse`` (docs/291): only the filter changed, so the
    day's last build is filtered again when this process still holds it --
    the same day a page that holds every row filters in the browser."""
    base = _cached_day(day) if reuse and lazy_ok else None
    if base is None:
        base = _build_base(day, gate_wait=gate_wait, lazy_ok=lazy_ok)
    data = _view(base, _filters() if filters is None else filters, window or {})
    data["reused"] = bool(reuse and base.get("paged"))
    return data


def _view(base: dict, f: dict, window: dict) -> dict:
    """One filter (and, on a paged day, one window) over a built day. The
    built day is shared between requests and never changed here."""
    data = dict(base)
    data["filters"] = f
    grps = groups(f["q"])
    hit = [_matches(c, f, grps, base.get("hays")) for c in base["cards"]]
    matched = [c for c, ok in zip(base["cards"], hit) if ok]
    data["window"] = None
    if base.get("paged"):
        lo, hi = _window(matched, window)
        data["cards_shown"] = matched[lo:hi]
        data["cards_cached"] = []
        if len(matched) > PAGE_CARDS:
            data["window"] = {"n": len(matched), "earlier": lo, "later": len(matched) - hi, "page": PAGE_CARDS,
                              "first": _dom_id(matched[lo]), "last": _dom_id(matched[hi - 1]),
                              "filtered": bool(f["q"] or f["author"])}
        if len(matched) < len(base["cards"]):
            data["timeline"] = _timeline_of(base["timeline"], {_dom_id(c) for c in matched})
        # a strip of more runs than a page of rows starts folded, so the rows
        # are on the first screen, and arrives after them (/journal/strip);
        # its button shows the whole strip
        data["strip_runs"] = len({st["card_id"] for segs in data["timeline"].values()
                                  for seg in segs for st in seg["steps"]})
        data["strip_fold"] = data["strip_later"] = data["strip_runs"] > PAGE_CARDS
    else:
        data["cards_shown"] = matched
        data["cards_cached"] = [c for c, ok in zip(base["cards"], hit) if not ok]
    data["runs_shown"] = sum(c["kind"] in ("run", "agent_run") for c in matched)
    data["lines_shown"] = [e for e in base["loose"] + base["unassigned"] if _matches(e, f, grps)]
    return data


def _build_base(day: str, *, gate_wait=False, lazy_ok=True) -> dict:
    from quam_state_manager.core import hub_sync
    r = _r()
    active = r._active_path()
    try:
        ledger, unavailable = _ledger_context(), None
    except RuntimeError as exc:
        # No history dir for the open chip: say so, never fall back to a
        # second history source and present it as the chip's.
        ledger, unavailable = None, f"{exc}, so runs and SM writes cannot be listed."
    except Exception:  # noqa: BLE001 -- S10 walk: the every-surface unreadable note, never a 500
        logger.warning("calibration log: the chip history could not be read", exc_info=True)
        ledger, unavailable = None, _unreadable_note()
    # S10 walk (perf): while the ledger is being built the day is the building
    # line alone, decided HERE from the build's status -- the data folders are
    # never indexed just to say so (3-9 s of Datasets scanning per answer on a
    # 3,500-run folder, and the catch-up stalled behind it)
    building = None
    if ledger is not None and not unavailable:
        st = hub_sync.status(ledger.store.directory)
        if st.get("state") == "building":
            building = dict(st, state="building")
    ds = None if building is not None else r._dataset_store()
    key = r._folder_key(ds.folder_path) if ds is not None else None
    def uid_of(run):
        root = (run.get("_hub") or {}).get("root_path")
        run_key = r._folder_key(root) if root else key
        return f"{run_key}:{run['run_id']}" if run_key else None
    if unavailable:
        data = _unavailable_day(day, unavailable)
    elif building is not None:
        data = story._empty_day(_chip_name(), day, building, current_app.instance_path)
    elif ledger is not None and not (ledger.store.directory / "ledger.sqlite").exists():
        st = hub_sync.status(ledger.store.directory)
        # building -> say so and refresh; no sync at all -> say so once (a
        # page that polls a ledger nobody builds polls forever)
        history = (dict(st, state="building") if st.get("state") == "building" else
                   {"state": "unavailable", "note": "This window has not built a chip history for this chip, "
                                                    "so runs and SM writes cannot be listed."})
        data = story._empty_day(_chip_name(), day, history, current_app.instance_path)
    else:
        try:
            data = story.build_day(current_app.instance_path, _chip_name(), day, ds=ds, active_path=active,
                                   ledger=ledger, agent_chip=_agent_chip_key(),
                                   events=_events(), uid_of=uid_of,
                                   # gates are checked in the background for a person; a test
                                   # app waits, so a page is the same page twice
                                   gate_wait=gate_wait or bool(current_app.config.get(
                                       "JOURNAL_GATE_WAIT", current_app.testing)),
                                   int_of=_int_types() if ledger is not None else None,
                                   rename=r._rename_scope() if ledger is not None else None)
        except Exception:  # noqa: BLE001 -- S10 walk: an unreadable ledger never 500s the log
            if ledger is None:
                raise
            logger.warning("calibration log: the ledger of %s could not be read",
                           ledger.store.directory, exc_info=True)
            data = _unavailable_day(day, _unreadable_note())
    data["lazy"] = bool(lazy_ok and len(data["cards"]) > LAZY_CARDS)
    data["paged"] = bool(lazy_ok and len(data["cards"]) > PAGE_CARDS)
    for order, c in enumerate(data["cards"]):
        c["search_order"] = order
    # docs/291: a paged day's rows carry no search text, so a card's is made
    # only when a search needs it (``_matches``)
    for c in ([] if data["paged"] else data["cards"]) + data["loose"] + data["unassigned"]:
        c["search_hay"] = _search_text(c)
    for c in data["cards"]:
        for e in c.get("journal") or []:
            e["search_hay"] = _line_text(e)
    if data["paged"]:
        data["day_ts"] = _ts_deltas(data["cards"])
        data["hays"] = {}
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
    if data["lazy"]:
        # the bodies a lazy day sends later, by their element id; a paged
        # day's slices and filters read the same build (docs/291). Kept
        # only once complete: other requests read it as it is.
        entry = {"data": data, "by_dom": {_dom_id(c): c for c in data["cards"]}}
        days = current_app.config.setdefault("journal_day_cards", {})
        with _DAY_DATA_LOCK:
            days.pop((str(active), day), None)
            days[(str(active), day)] = entry
            while len(days) > 4:
                days.pop(next(iter(days)))
    return data


def _unreadable_note() -> str:
    """S10 walk: the words every history surface ends in when the chip's
    change ledger cannot be read (``routes._VH_UNAVAILABLE_NOTES``)."""
    return _r()._VH_UNAVAILABLE_NOTES["unreadable"]


def _unavailable_day(day: str, note: str) -> dict:
    return story._empty_day(_chip_name(), day, {"state": "unavailable", "note": note[:1].upper() + note[1:]},
                            current_app.instance_path)


def _agent_chip_key():
    from quam_state_manager.web.agent_api import _chip_key
    return _chip_key()


@journal_bp.route("/journal")
def journal_page():
    r = _r()
    day = _day_arg()
    data = _build(day, window=_window_args())
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
    appeared at all.

    docs/291: on a paged day ``at`` / ``from`` / ``to`` pick the rows
    (``_window``) and ``reuse=1`` says only the filter changed."""
    data = _build(_day_arg(), window=_window_args(), reuse=request.args.get("reuse") == "1")
    # Hash the freshly read content, so ledger writes, claims, journal lines,
    # author changes and warmup transitions invalidate the rendered fragment.
    # Keep two days at most; a large day can otherwise retain megabytes of HTML.
    # Every key the template reads, except the three split from "cards" and
    # "loose" by the filters (already in the token through them).
    # docs/291: a paged day renders the rows it shows and the whole day only
    # through keys of its own (counts, strip, instants), so its token hashes
    # those rows, not all of the day's cards again
    derived = ("cards_cached", "lines_shown", "hays") + (("cards",) if data.get("paged") else ("cards_shown",))
    keys = sorted(k for k in data if k not in derived)
    token = hashlib.sha256(json.dumps([[k, data.get(k)] for k in keys], default=str,
                                     ensure_ascii=True).encode()).digest()
    cache = current_app.config.setdefault("journal_day_html", {})
    with _DAY_HTML_LOCK:
        hit = cache.get(token)
    if hit is not None:
        return _with_page_url(hit)
    html = render_template("_journal_day_swap.html", story=data)
    if len(html.encode("utf-8")) <= 6 * 1024 * 1024:
        with _DAY_HTML_LOCK:
            if len(cache) >= 2:
                cache.pop(next(iter(cache)))
            cache[token] = html
    return _with_page_url(html)


@journal_bp.route("/journal/card")
def journal_card():
    """docs/281: the body of one card of a very large day, rendered by the
    page's own card template when the card is first opened."""
    day = _day_arg()
    card = request.args.get("card") or ""
    found = _cached_card(day, card)
    if found is None:
        # a restart or another window rebuilt the day: build it again
        _build(day, filters={"author": "", "q": ""})
        found = _cached_card(day, card)
    if found is None:
        return '<div class="jr-body"><p class="muted">This card is no longer on this day.</p></div>'
    module = current_app.jinja_env.get_template("_journal_card.html").module
    resp = current_app.response_class(str(module.card_body(found, day)), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _cached_card(day: str, card: str):
    with _DAY_DATA_LOCK:
        hit = (current_app.config.get("journal_day_cards") or {}).get(_day_key(day))
    return hit["by_dom"].get(card) if hit else None


def _window_args() -> dict:
    return {k: (request.args.get(k) or "").strip() for k in _WINDOW_ARGS}


@journal_bp.route("/journal/cards")
def journal_cards():
    """docs/291: the next rows of a paged day, ``before`` or ``after`` the
    card a page shows first or last, under the page's own filter: up to
    PAGE_CARDS rows in time order and, when more remain, the control for
    the rest. Read from the build the page was rendered from while this
    process holds it, else from a new one. A card that is not on the day
    any more (or a day that is not paged now) says so; never a guess."""
    day = _day_arg()
    before = (request.args.get("before") or "").strip()
    after = (request.args.get("after") or "").strip()
    base = _cached_day(day) or _build_base(day)
    f = _filters()
    grps = groups(f["q"])
    matched = [c for c in base["cards"] if _matches(c, f, grps, base.get("hays"))]
    anchor = before or after
    at = (next((i for i, c in enumerate(matched) if _dom_id(c) == anchor), None)
          if anchor and base.get("paged") else None)
    if at is None:
        return render_template("_journal_slice.html", story={"day": day, "filters": f, "gone": True})
    if before:
        lo, hi = max(0, at - PAGE_CARDS), at
    else:
        lo, hi = at + 1, min(len(matched), at + 1 + PAGE_CARDS)
    rows = matched[lo:hi]
    s = {"day": day, "filters": f, "lazy": base.get("lazy"), "cards_shown": rows,
         "window": {"n": len(matched), "page": PAGE_CARDS, "earlier": lo if before else 0,
                    "later": len(matched) - hi if after else 0,
                    "first": _dom_id(rows[0]) if rows else before,
                    "last": _dom_id(rows[-1]) if rows else after,
                    "filtered": bool(f["q"] or f["author"])}}
    resp = current_app.response_class(render_template("_journal_slice.html", story=s), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@journal_bp.route("/journal/strip")
def journal_strip():
    """docs/291: a paged day's per-target strip, sent after its rows: the
    whole day's runs under the page's filter, from the day's last build."""
    data = _build(_day_arg(), reuse=True)
    data["strip_later"] = False
    if not data.get("timeline"):
        return ""
    module = current_app.jinja_env.get_template("_journal_strip.html").module
    resp = current_app.response_class(str(_jr_squeeze(module.strip(data), bool(data.get("paged")))),
                                      mimetype="text/html")
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
