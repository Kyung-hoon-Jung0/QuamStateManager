"""The project's time zone, and what SM does when a run's clock disagrees
(docs/263).

ONE zone setting. A QUAlibrate project carries the IANA zone its times are
shown in; it replaces the per-browser Settings zone (docs/244, ``quam_tz`` in
localStorage), so a page never has two zones. It is a DISPLAY choice: every
stored and compared time stays an instant (UTC); the zone only decides how an
instant reads.

* ``instance/project_time.json`` -- beside ``project_envs.json`` (the env pick,
  ``core/project_env``), keyed by the same QUAlibrate project NAME. Never in
  ``~/.qualibrate``: SM does not write QUAlibrate's files (docs/55).
* A project whose zone was picked shows it on every later launch, and is not
  asked again. A project never set defaults to the zone set most recently for
  any project (``last_zone``), and keeps that default once it is opened.

The run clock. A run says when it was saved (``created_at``, with its offset).
Something else may have watched the same moment: SM, when it saw the run
folder appear (``first_seen``), or the file system that stamped the folder
(``mtime``). docs/256's review note binds which of those are evidence:

* ``first_seen`` only for runs SM saw ARRIVE (``core/run_arrivals``: the
  run watcher looked at the folder at most ``LIVE_GAP_S`` before and it was
  not there);
* folder ``mtime`` only for folders written IN PLACE -- an archive whose
  folder times move WITH the runs' own clock (``classify_archive``); a copy
  stamps every folder with the copy time, which is no evidence at all.

A different ZONE alone is not a disagreement: a run recorded at 01:55 (UTC-7)
and seen by SM at 17:55 (UTC+9) agree on the instant. Only a different
INSTANT is a skew:

* under ``SKEW_ASK_S`` (30 min): never asked; recorded, one Diagnostics line;
* at or above it, once ``MIN_ASK_N`` runs agree: asked ONCE per project
  ("the experiment PC's clock is wrong / this PC's clock is wrong / ignore");
  asked again only when the measured skew changes (``same_skew``);
* a correction exists only after the person said which clock is wrong, is
  always labelled "corrected", and the original instant stays visible.

Nothing here spawns or probes: the clock status comes from
``core/clock_health`` (the OS's NTP sync and zone) when it is installed, and
from a stub that says "unknown" otherwise.
"""
from __future__ import annotations

import json
import re
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from quam_state_manager.core import safe_io, timefmt

FILENAME = "project_time.json"

#: Product decision (docs/256): the ask threshold. Re-exported, never redefined.
SKEW_ASK_S = timefmt.SKEW_ASK_S
#: Product decision: two measured skews are "the same" within half the ask
#: unit -- zone mistakes come in 30/60-min steps, so a 15-min band separates
#: them while absorbing the seconds a save and a poll add.
SAME_SKEW_S = SKEW_ASK_S / 2
#: Product decision: a skew is ASKED about only once this many runs agree --
#: one copied-in folder must never raise the question.
MIN_ASK_N = 3
#: Product decision: the newest this-many witnesses decide the current skew.
REGIME_N = 5
#: [derived] Zones run from UTC-12 to UTC+14 (26 h apart). A gap beyond that
#: is no clock or zone mistake -- it is an old run copied into the folder.
MAX_ZONE_SKEW_S = 26 * 3600
#: Product decision: SM's first sight of a run is a witness only when SM had
#: looked at that folder at most this long before and the run was not there.
LIVE_GAP_S = 120.0
#: Product decision (in-place test): at least this many runs, spanning at
#: least ``INPLACE_MIN_SPAN_S`` of their own clock, whose (claim - mtime)
#: spread (P10..P90) is at most ``INPLACE_MAX_SPREAD_S``.
INPLACE_MIN_N = 5
INPLACE_MIN_SPAN_S = 3600.0
INPLACE_MAX_SPREAD_S = 300.0
#: How many witnesses one project keeps (newest by the run's own instant).
WITNESS_CAP = 200

CHOICES = ("experiment_pc", "this_pc", "ignore")
OS_ANSWERS = ("view", "pc_wrong")
WATCH_ANSWERS = ("matches", "differs")

_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()
_MEMO: dict[str, tuple] = {}
_IANA_RE = re.compile(r"^(UTC|[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){1,2})$")


# ----------------------------------------------------------------- zones
def _zoneinfo(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 -- no tzdata, or not a zone
        return None


def valid_zone(name: Any) -> bool:
    """An IANA zone name. With tzdata present the zone must load; without it
    (a bare Windows Python) the name must at least have the IANA shape -- the
    browser's own list is what offered it."""
    if not isinstance(name, str) or not _IANA_RE.match(name) or len(name) > 64:
        return False
    if name == "UTC":
        return True
    try:
        import zoneinfo  # noqa: F401
    except ImportError:
        return True
    if _zoneinfo(name) is not None:
        return True
    try:                                  # zoneinfo itself works, tzdata absent
        from zoneinfo import available_timezones
        return not available_timezones()
    except Exception:  # noqa: BLE001
        return True


def zone_offset(name: str | None, at: datetime | None = None) -> str | None:
    """``+09:00`` for *name* at *at* (now by default; DST-aware), or None."""
    if not name:
        return None
    if name == "UTC":
        return "+00:00"
    z = _zoneinfo(name)
    if z is None:
        return None
    off = (at or datetime.now(timezone.utc)).astimezone(z).utcoffset()
    return _fmt_offset(off)


def _fmt_offset(off: timedelta | None) -> str | None:
    if off is None:
        return None
    mins = int(off.total_seconds() // 60)
    sign = "-" if mins < 0 else "+"
    h, m = divmod(abs(mins), 60)
    return f"{sign}{h:02d}:{m:02d}"


def offset_text(off: str | None) -> str:
    """``+09:00`` -> ``UTC+9``; ``-03:30`` -> ``UTC-3:30``; ``+00:00`` -> ``UTC``."""
    if not off:
        return ""
    m = re.fullmatch(r"([+-])(\d{2}):(\d{2})", off)
    if not m:
        return off
    sign, h, mm = m.group(1), int(m.group(2)), int(m.group(3))
    if not h and not mm:
        return "UTC"
    return f"UTC{sign}{h}" + (f":{mm:02d}" if mm else "")


def offset_seconds(off: str | None) -> int | None:
    m = re.fullmatch(r"([+-])(\d{2}):(\d{2})", off or "")
    if not m:
        return None
    s = int(m.group(2)) * 3600 + int(m.group(3)) * 60
    return -s if m.group(1) == "-" else s


def skew_text(seconds: float | None) -> str:
    """A measured skew for people: to the minute once it is a minute or more
    (the poll gap and whole-second run clocks make the seconds noise --
    3598.96 s measured is "1 h 00 min", not "59 min 59 s"), else seconds."""
    if seconds is None:
        return ""
    if abs(seconds) >= 60:
        return span_text(round(abs(seconds) / 60.0) * 60)
    return span_text(seconds)


def span_text(seconds: float | None) -> str:
    """``3600`` -> ``1 h 00 min``; ``95`` -> ``1 min 35 s``; ``12`` -> ``12 s``."""
    if seconds is None:
        return ""
    s = int(round(abs(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {sec:02d} s" if sec else f"{m} min"
    return f"{sec} s"


# ----------------------------------------------------------- clock status
_CLOCK_MEMO: dict[str, Any] = {}
CLOCK_TTL_S = 60.0


def _stub_status() -> dict:
    """What SM can say about this PC's clock without ``core/clock_health``:
    the zone offset from the C runtime, the NTP status unknown."""
    now = datetime.now().astimezone()
    win_name = None
    try:
        win_name = time.tzname[0] if time.tzname else None
    except Exception:  # noqa: BLE001
        win_name = None
    if win_name is not None and not win_name.isascii():
        win_name = None                    # the UI is English-only (a localized OS name)
    return {"ntp": {"synced": None, "source": None, "last_sync_utc": None,
                    "offset_s": None, "detail": "NTP status not available"},
            "os_zone": {"iana": None, "utc_offset": _fmt_offset(now.utcoffset()),
                        "windows_name": win_name}}


def clock_status(force: bool = False) -> dict:
    """``clock_health.status()`` (agreed interface, docs/263) or the stub,
    memoized ``CLOCK_TTL_S`` -- the landing fetches it asynchronously, never
    on the page render. Always answers; ``now_utc`` is this server's clock."""
    now = time.time()
    hit = _CLOCK_MEMO.get("v")
    if not force and hit is not None and now - hit[0] < CLOCK_TTL_S:
        out = dict(hit[1])
    else:
        try:
            # import_module, not `from ... import`: it honours sys.modules, so a module
            # swapped in or blocked there is what runs (an attribute already bound on
            # the package would otherwise win)
            import importlib  # noqa: PLC0415
            clock_health = importlib.import_module("quam_state_manager.core.clock_health")
            raw = clock_health.status()
            src = "clock_health"
        except ImportError:
            raw, src = _stub_status(), "stub"
        except Exception as e:  # noqa: BLE001 -- a probe failure is "unknown"
            raw, src = _stub_status(), "stub"
            raw["ntp"]["detail"] = f"clock status failed: {type(e).__name__}"
        out = _normalize_status(raw)
        out["source"] = src
        _CLOCK_MEMO["v"] = (now, dict(out))
    out["now_utc"] = timefmt.iso_z(datetime.now(timezone.utc))
    out["now_ms"] = int(time.time() * 1000)
    return out


def _normalize_status(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    ntp = raw.get("ntp") if isinstance(raw.get("ntp"), dict) else {}
    osz = raw.get("os_zone") if isinstance(raw.get("os_zone"), dict) else {}
    synced = ntp.get("synced")
    off = osz.get("utc_offset")
    return {
        "ntp": {"synced": synced if isinstance(synced, bool) else None,
                "source": ntp.get("source") if isinstance(ntp.get("source"), str) else None,
                "last_sync_utc": (ntp.get("last_sync_utc")
                                  if isinstance(ntp.get("last_sync_utc"), str) else None),
                "offset_s": (float(ntp["offset_s"])
                             if isinstance(ntp.get("offset_s"), (int, float))
                             and not isinstance(ntp.get("offset_s"), bool) else None),
                "detail": str(ntp.get("detail") or "")},
        "os_zone": {"iana": osz.get("iana") if isinstance(osz.get("iana"), str) else None,
                    "utc_offset": off if isinstance(off, str)
                    and re.fullmatch(r"[+-]\d{2}:\d{2}", off) else None,
                    "windows_name": (osz.get("windows_name")
                                     if isinstance(osz.get("windows_name"), str) else None)},
    }


def ntp_text(status: dict) -> str:
    """One English line about this PC's time sync (the watch check shows it)."""
    ntp = (status or {}).get("ntp") or {}
    synced = ntp.get("synced")
    if synced is True:
        out = "Time sync: on"
        if ntp.get("source"):
            out += f" ({ntp['source']})"
        if isinstance(ntp.get("offset_s"), (int, float)):
            out += f", off by {span_text(ntp['offset_s'])}"
        return out
    if synced is False:
        return "Time sync: OFF -- " + (_short_detail(ntp.get("detail")) or "this PC's clock is not synchronized")
    short = _short_detail(ntp.get("detail"))
    return "Time sync: unknown" + (f" ({short})" if short else "")


def _short_detail(detail) -> str:
    """The OS's own message, said briefly: the full text stays in ``ntp.detail`` (the
    UI puts it in a tooltip). A stopped Windows Time service is the common case."""
    d = str(detail or "").strip()
    low = d.lower()
    if "0x80070426" in low or "service has not been started" in low or "service is not started" in low:
        return "the Windows Time service is not running"
    return d if len(d) <= 70 else d[:67].rstrip() + "..."


# ----------------------------------------------------------------- store
def _file(inst) -> Path:
    return Path(inst) / FILENAME


def _stat(p: Path):
    try:
        st = p.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _empty() -> dict:
    return {"version": 1, "projects": {}, "last_zone": None}


def load(inst) -> dict:
    """The whole record, normalized; empty when absent or unreadable (a
    corrupt file never breaks a page). Memoized on (mtime, size)."""
    p = _file(inst)
    key, sig = str(p), _stat(p)
    with _LOCK:
        hit = _MEMO.get(key)
        if hit is not None and hit[0] == sig:
            return json.loads(hit[1])
    data = _empty()
    if sig is not None:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        data = _normalize(raw)
    with _LOCK:
        _MEMO[key] = (sig, json.dumps(data))
    return data


def _normalize(raw: Any) -> dict:
    data = _empty()
    if not isinstance(raw, dict):
        return data
    projs = raw.get("projects")
    if isinstance(projs, dict):
        for name, rec in projs.items():
            if isinstance(name, str) and name and isinstance(rec, dict):
                data["projects"][name] = _norm_project(rec)
    lz = raw.get("last_zone")
    if isinstance(lz, dict) and valid_zone(lz.get("zone")):
        data["last_zone"] = {"zone": lz["zone"], "project": lz.get("project"),
                             "at": _num(lz.get("at"))}
    return data


def _num(x, default=0.0):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else default


def _norm_project(rec: dict) -> dict:
    out: dict[str, Any] = {"zone": None, "how": None, "at": 0.0,
                           "os_check": None, "watch": None,
                           "clock": {"witnesses": [], "answers": [], "run_offsets": {},
                                     "archive": None, "shown": None}}
    if valid_zone(rec.get("zone")):
        out["zone"] = rec["zone"]
        out["how"] = rec.get("how") if rec.get("how") in ("picked", "default", "legacy") else "picked"
        out["at"] = _num(rec.get("at"))
    oc = rec.get("os_check")
    if isinstance(oc, dict) and oc.get("answer") in OS_ANSWERS:
        out["os_check"] = {k: oc.get(k) for k in
                           ("answer", "os_offset", "os_iana", "zone", "zone_offset", "at")}
    w = rec.get("watch")
    if isinstance(w, dict) and w.get("answer") in WATCH_ANSWERS:
        out["watch"] = {k: w.get(k) for k in ("answer", "shown", "zone", "ntp_synced", "at")}
    clk = rec.get("clock") if isinstance(rec.get("clock"), dict) else {}
    out["clock"]["witnesses"] = [w for w in (clk.get("witnesses") or [])
                                 if isinstance(w, dict) and _witness_ok(w)][-WITNESS_CAP:]
    out["clock"]["answers"] = [a for a in (clk.get("answers") or [])
                               if isinstance(a, dict) and a.get("choice") in CHOICES
                               and isinstance(a.get("skew_s"), (int, float))]
    ro = clk.get("run_offsets")
    if isinstance(ro, dict):
        out["clock"]["run_offsets"] = {k: int(v) for k, v in ro.items()
                                       if isinstance(k, str) and offset_seconds(k) is not None
                                       and isinstance(v, int) and not isinstance(v, bool) and v > 0}
    if isinstance(clk.get("archive"), dict):
        out["clock"]["archive"] = clk["archive"]
    sh = clk.get("shown")
    if isinstance(sh, dict) and isinstance(sh.get("skew_s"), (int, float)):
        out["clock"]["shown"] = {"skew_s": float(sh["skew_s"]), "at": _num(sh.get("at"))}
    return out


def _witness_ok(w: dict) -> bool:
    return (isinstance(w.get("key"), str) and isinstance(w.get("node_us"), int)
            and w.get("src") in ("live", "in_place")
            and isinstance(w.get("skew_s"), (int, float)))


def _save(inst, data: dict) -> None:
    _file(inst).parent.mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_json(_file(inst), data)
    with _LOCK:
        _MEMO.pop(str(_file(inst)), None)


def _project(data: dict, name: str) -> dict:
    rec = data["projects"].get(name)
    if rec is None:
        rec = _norm_project({})
        data["projects"][name] = rec
    return rec


# ------------------------------------------------------------ zone setting
def view(inst, project: str | None) -> dict:
    """What the landing / the Settings line show for *project*:
    ``state`` = ``picked`` (the person chose it), ``default`` (saved from the
    last project's zone when the project was first opened), ``suggested``
    (never set; the last zone, not saved yet), ``none`` (no zone anywhere --
    this PC's / browser's zone)."""
    data = load(inst)
    rec = data["projects"].get(project) if project else None
    lz = data.get("last_zone")
    if rec and rec.get("zone"):
        zone, state = rec["zone"], ("default" if rec.get("how") == "default" else "picked")
        frm = None
    elif lz:
        zone, state, frm = lz["zone"], "suggested", lz.get("project")
    else:
        zone, state, frm = None, "none", None
    off = zone_offset(zone)
    out = {"project": project, "zone": zone, "state": state, "from_project": frm,
           "offset": off, "offset_text": offset_text(off),
           "os_check": (rec or {}).get("os_check"), "watch": (rec or {}).get("watch")}
    return out


def display_zone(inst, project: str | None) -> dict:
    """The ONE zone a page renders in: the project's (picked or defaulted),
    else the zone set most recently for any project, else none (the page
    then reads the browser's own zone -- the pre-docs/263 default)."""
    v = view(inst, project)
    return {"zone": v["zone"], "project": project, "state": v["state"],
            "offset_text": v["offset_text"]}


def set_zone(inst, project: str, zone: str, *, how: str = "picked",
             os_check: dict | None = None) -> dict:
    """The person picked *zone* for *project* (or a default is being kept):
    remember it, and as the zone the next new project defaults to."""
    if not project:
        raise ValueError("project is required")
    if not valid_zone(zone):
        raise ValueError(f"not a time zone: {zone!r}")
    with _WRITE_LOCK:
        data = load(inst)
        rec = _project(data, project)
        now = time.time()
        rec["zone"], rec["how"], rec["at"] = zone, how, now
        if os_check is not None:
            if os_check.get("answer") not in OS_ANSWERS:
                raise ValueError("os_check answer must be one of " + ", ".join(OS_ANSWERS))
            rec["os_check"] = {"answer": os_check["answer"],
                               "os_offset": os_check.get("os_offset"),
                               "os_iana": os_check.get("os_iana"),
                               "zone": zone, "zone_offset": zone_offset(zone), "at": now}
        elif how == "picked":
            rec["os_check"] = None          # a new pick that matched the PC: no question stands
        if how != "default":
            data["last_zone"] = {"zone": zone, "project": project, "at": now}
        elif not data.get("last_zone"):
            data["last_zone"] = {"zone": zone, "project": project, "at": now}
        _save(inst, data)
    return view(inst, project)


def ensure_default(inst, project: str | None) -> dict | None:
    """Opening *project*: a project with no zone of its own keeps the one it
    was offered (``last_zone``), so a later pick for another project never
    moves it. Writes only when that changes something."""
    if not project:
        return None
    data = load(inst)
    rec = data["projects"].get(project)
    if rec and rec.get("zone"):
        return None
    lz = data.get("last_zone")
    if not lz:
        return None
    return set_zone(inst, project, lz["zone"], how="default")


def record_watch(inst, project: str, answer: str, *, shown: str | None = None,
                 zone: str | None = None, ntp_synced: bool | None = None) -> dict:
    """The watch check's answer: does "now HH:MM" in the chosen zone match
    the person's own watch? Kept per project; it never moves a time."""
    if answer not in WATCH_ANSWERS:
        raise ValueError("answer must be one of " + ", ".join(WATCH_ANSWERS))
    with _WRITE_LOCK:
        data = load(inst)
        rec = _project(data, project)
        rec["watch"] = {"answer": answer, "shown": (shown or "")[:32], "zone": zone,
                        "ntp_synced": ntp_synced if isinstance(ntp_synced, bool) else None,
                        "at": time.time()}
        _save(inst, data)
    return view(inst, project)


# ---------------------------------------------------------- the run clock
def make_witness(node_json: Any, key: str, *, src: str,
                 first_seen_utc_us: int | None = None, gap_s: float | None = None,
                 folder_mtime_utc_us: int | None = None) -> dict | None:
    """One run's witness record, or None when it is not evidence.

    ``src="live"``: SM saw the folder appear (``first_seen_utc_us``, after a
    look at most ``gap_s`` earlier); ``src="in_place"``: the folder's mtime
    from an archive ``classify_archive`` found written in place.
    The run's own instant is ``timefmt.run_witnesses``'s node witness; only an
    instant with its OWN offset counts -- a naive clock would measure SM's
    guess at its zone, not the clock (docs/256 quality ``offset``).
    ``skew_s`` is SIGNED: run clock minus the observer (positive = the run's
    clock is ahead)."""
    if src not in ("live", "in_place"):
        raise ValueError("src must be live or in_place")
    if src == "live":
        if first_seen_utc_us is None or gap_s is None or gap_s > LIVE_GAP_S:
            return None
        ref = first_seen_utc_us
    else:
        if folder_mtime_utc_us is None:
            return None
        ref = folder_mtime_utc_us
    w = timefmt.run_witnesses(node_json,
                              folder_mtime_utc_us=folder_mtime_utc_us,
                              first_seen_utc_us=first_seen_utc_us if src == "live" else None)
    node_us = w["node_utc_us"]
    if node_us is None or w["node_quality"] != "offset":
        return None
    skew = (node_us - ref) / 1_000_000
    if abs(skew) > MAX_ZONE_SKEW_S:
        return None
    return {"key": key, "src": src, "node_us": int(node_us),
            "node_off": _node_offset(node_json),
            "seen_us": int(first_seen_utc_us) if src == "live" else None,
            "gap_s": round(float(gap_s), 3) if src == "live" and gap_s is not None else None,
            "mtime_us": int(folder_mtime_utc_us) if folder_mtime_utc_us is not None else None,
            "skew_s": round(skew, 3), "span_s": w["skew_s"], "at": time.time()}


def _node_offset(node_json: Any) -> str | None:
    """The offset the run's own clock wrote (``created_at`` first, then
    ``metadata.run_end``) -- the zone a "recorded 01:55 (UTC-7)" names."""
    for d in timefmt._run_dates(node_json):          # the one reading (docs/256)
        if d is not None and d.utcoffset() is not None:
            return _fmt_offset(d.utcoffset())
    return None


def summarize(witnesses: Iterable[dict]) -> dict:
    """The current skew from *witnesses* (pure).

    Live witnesses are preferred once there are ``MIN_ASK_N`` of them (they
    measure the run clock against THIS PC's clock); otherwise the in-place
    ones. The newest ``REGIME_N`` decide: ``skew_s`` = their median, and
    ``spread_s`` = max - min. ``class``:

    * ``none`` -- no witness;
    * ``small`` -- |skew| below ``SKEW_ASK_S``: never asked;
    * ``unsettled`` -- at/above it, but fewer than ``MIN_ASK_N`` runs or they
      disagree by more than ``SAME_SKEW_S``: wait for more runs;
    * ``ask`` -- at/above it, settled.

    ``from_us`` is the run instant where this regime starts: the oldest
    witness, walking back from the newest, whose skew stays the same."""
    ws = sorted((w for w in witnesses if _witness_ok(w)), key=lambda w: w["node_us"])
    live = [w for w in ws if w["src"] == "live"]
    inplace = [w for w in ws if w["src"] == "in_place"]
    use, src = (live, "live") if len(live) >= MIN_ASK_N or not inplace else (inplace, "in_place")
    if not use:
        return {"class": "none", "n": 0, "skew_s": None, "spread_s": None, "src": None,
                "from_us": None, "to_us": None, "n_live": 0, "n_in_place": 0}
    newest = use[-REGIME_N:]
    med = float(statistics.median(w["skew_s"] for w in newest))
    agree = [w for w in newest if same_skew(w["skew_s"], med)]
    skews = [w["skew_s"] for w in agree] or [med]
    spread = max(skews) - min(skews)
    # where this regime starts: walk back over the witnesses that keep the
    # same skew, stepping over ONE that does not (a copied-in folder) when
    # the one before it agrees again
    last = max(i for i, w in enumerate(use) if same_skew(w["skew_s"], med)) if agree else len(use) - 1
    start, k = last, last - 1
    while k >= 0:
        if same_skew(use[k]["skew_s"], med):
            start, k = k, k - 1
        elif k >= 1 and same_skew(use[k - 1]["skew_s"], med):
            k -= 1
        else:
            break
    regime = [w for w in use[start:] if same_skew(w["skew_s"], med)] or [use[-1]]
    if abs(med) < SKEW_ASK_S:
        cls = "small"
    elif len(agree) >= MIN_ASK_N:
        cls = "ask"
    else:
        cls = "unsettled"
    return {"class": cls, "n": len(newest), "n_agree": len(agree), "n_regime": len(regime),
            "skew_s": round(med, 3), "spread_s": round(spread, 3), "src": src,
            "from_us": regime[0]["node_us"], "to_us": regime[-1]["node_us"],
            "n_live": len(live), "n_in_place": len(inplace),
            "whole_units": whole_units(med)}


def same_skew(a: float | None, b: float | None) -> bool:
    """Product decision: one skew, not two, when within ``SAME_SKEW_S``."""
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) < SAME_SKEW_S


def whole_units(skew_s: float | None, tol_s: float = 120.0) -> bool:
    """True when the skew sits within *tol_s* of a whole 30-min step -- the
    shape of a wrong zone or a missed DST change, not of a drifting clock."""
    if skew_s is None or abs(skew_s) < SKEW_ASK_S - tol_s:
        return False
    r = abs(skew_s) % 1800
    return min(r, 1800 - r) <= tol_s


def add_witnesses(inst, project: str, witnesses: Iterable[dict | None]) -> dict:
    """Merge *witnesses* into *project*'s record (one per run key, the newest
    reading wins), keep the newest ``WITNESS_CAP``, and close an answered
    skew whose regime ended. Writes only when something changed. Returns the
    summary."""
    new = [w for w in witnesses if isinstance(w, dict) and _witness_ok(w)]
    if not project:
        return summarize(new)
    with _WRITE_LOCK:
        data = load(inst)
        rec = _project(data, project)
        clk = rec["clock"]
        by_key = {w["key"]: w for w in clk["witnesses"]}
        changed = False
        for w in new:
            old = by_key.get(w["key"])
            # a live reading is never replaced by an in-place one
            if old is not None and old.get("src") == "live" and w["src"] != "live":
                continue
            if old is None or {k: v for k, v in old.items() if k != "at"} != \
                    {k: v for k, v in w.items() if k != "at"}:
                by_key[w["key"]] = w
                changed = True
        ws = sorted(by_key.values(), key=lambda w: w["node_us"])[-WITNESS_CAP:]
        summ = summarize(ws)
        if _close_answers(clk["answers"], summ):
            changed = True
        if changed:
            clk["witnesses"] = ws
            _save(inst, data)
        return summ


def _close_answers(answers: list[dict], summ: dict) -> bool:
    """An open answer whose skew is no longer what is measured (a settled,
    different regime) ends where the new regime starts."""
    if summ["class"] not in ("ask", "small") or summ.get("from_us") is None:
        return False
    if summ.get("n_agree", 0) < MIN_ASK_N:
        return False            # not settled yet: one odd run never ends an answer
    moved = False
    for a in answers:
        if a.get("until_us") is None and not same_skew(a["skew_s"], summ["skew_s"]):
            a["until_us"] = int(summ["from_us"])
            moved = True
    return moved


def note_run_offsets(inst, project: str, offsets: dict[str, int]) -> None:
    """The offsets the project's runs record (``{"-07:00": 120}``), for the
    one quiet note. Writes only on change."""
    clean = {k: int(v) for k, v in (offsets or {}).items()
             if offset_seconds(k) is not None and isinstance(v, int) and v > 0}
    if not project:
        return
    with _WRITE_LOCK:
        data = load(inst)
        rec = _project(data, project)
        if rec["clock"]["run_offsets"] == clean:
            return
        rec["clock"]["run_offsets"] = clean
        _save(inst, data)


def note_archive(inst, project: str, verdict: dict | None) -> None:
    """The newest archive in-place verdict (``classify_archive``), kept for
    the Diagnostics line and the ledger step. Writes only on change."""
    if not project or not isinstance(verdict, dict):
        return
    keep = {k: verdict.get(k) for k in ("in_place", "reason", "n", "span_s",
                                        "spread_s", "skew_s", "root")}
    with _WRITE_LOCK:
        data = load(inst)
        rec = _project(data, project)
        old = rec["clock"].get("archive")
        if old and {k: old.get(k) for k in keep} == keep:
            return
        rec["clock"]["archive"] = dict(keep, at=time.time())
        _save(inst, data)


def clock_view(inst, project: str | None) -> dict:
    """Everything the Diagnostics line, the ask dialog and the ledger step
    read about *project*'s run clock."""
    data = load(inst)
    rec = data["projects"].get(project) if project else None
    clk = (rec or {}).get("clock") or {"witnesses": [], "answers": [],
                                        "run_offsets": {}, "archive": None, "shown": None}
    summ = summarize(clk["witnesses"])
    open_answer = next((a for a in reversed(clk["answers"]) if a.get("until_us") is None), None)
    ask = None
    if summ["class"] == "ask" and not (open_answer and same_skew(open_answer["skew_s"],
                                                                   summ["skew_s"])):
        ask = _ask_payload(clk["witnesses"], summ)
    shown = clk.get("shown")
    # ask ONCE: the question pops up by itself only the first time this skew
    # is seen; after that it waits on the Diagnostics line until answered
    auto = bool(ask) and not (shown and same_skew(shown["skew_s"], ask["skew_s"]))
    return {"project": project, "summary": summ, "answers": list(clk["answers"]),
            "open_answer": open_answer, "ask": ask, "auto_ask": auto, "shown": shown,
            "run_offsets": dict(clk["run_offsets"]), "archive": clk.get("archive"),
            "uncertainty_s": (abs(summ["skew_s"]) if summ["class"] == "small"
                              and summ["skew_s"] is not None else None)}


def _ask_payload(witnesses: list[dict], summ: dict) -> dict:
    """The evidence the question carries: up to three example runs of the
    current regime, newest first."""
    use = [w for w in witnesses if _witness_ok(w) and w["src"] == summ["src"]
           and w["node_us"] >= (summ["from_us"] or 0)]
    use.sort(key=lambda w: w["node_us"], reverse=True)
    ex = []
    for w in use[:3]:
        ref = w["seen_us"] if w["src"] == "live" else w["mtime_us"]
        ex.append({"key": w["key"], "run_utc": _iso_us(w["node_us"]),
                   "run_off": w.get("node_off"), "seen_utc": _iso_us(ref),
                   "skew_s": w["skew_s"]})
    return {"skew_s": summ["skew_s"], "skew_text": skew_text(summ["skew_s"]),
            "ahead": summ["skew_s"] > 0, "n": summ["n_regime"], "src": summ["src"],
            "whole_units": summ.get("whole_units", False),
            "from_utc": _iso_us(summ["from_us"]),
            "from_key": next((w["key"] for w in use if w["node_us"] == summ["from_us"]), None),
            "examples": ex}


def _iso_us(us: int | None) -> str | None:
    if us is None:
        return None
    return timefmt.iso_z(datetime(1970, 1, 1, tzinfo=timezone.utc)
                         + timedelta(microseconds=int(us)))


def mark_shown(inst, project: str, skew_s: float) -> None:
    """The question for *skew_s* was put on screen: it does not pop up by
    itself again for the same skew (``clock_view``'s ``auto_ask``)."""
    if not project or not isinstance(skew_s, (int, float)):
        return
    with _WRITE_LOCK:
        data = load(inst)
        rec = _project(data, project)
        sh = rec["clock"].get("shown")
        if sh and same_skew(sh["skew_s"], skew_s):
            return
        rec["clock"]["shown"] = {"skew_s": float(skew_s), "at": time.time()}
        _save(inst, data)


def answer_skew(inst, project: str, choice: str, skew_s: float) -> dict:
    """The person answered the skew question. Stored with the skew it was
    asked about and the run instant its regime starts at. A correction
    exists from now on only for ``experiment_pc``; ``this_pc`` and
    ``ignore`` never move a run's time."""
    if choice not in CHOICES:
        raise ValueError("choice must be one of " + ", ".join(CHOICES))
    if not project:
        raise ValueError("project is required")
    with _WRITE_LOCK:
        data = load(inst)
        rec = _project(data, project)
        summ = summarize(rec["clock"]["witnesses"])
        if summ["class"] != "ask" or not same_skew(skew_s, summ["skew_s"]):
            raise ValueError("the measured skew changed -- ask again")
        now = time.time()
        for a in rec["clock"]["answers"]:
            if a.get("until_us") is None:
                a["until_us"] = int(summ["from_us"])
        rec["clock"]["answers"].append({
            "choice": choice, "skew_s": summ["skew_s"],
            "correction_s": correction_amount(summ["skew_s"]), "n": summ["n_regime"],
            "src": summ["src"], "from_us": int(summ["from_us"]), "until_us": None,
            "at": now})
        _save(inst, data)
    return clock_view(inst, project)


def correction_amount(skew_s: float) -> float:
    """How far a confirmed skew moves a time: a whole 30-min step exactly
    when the measurement sits on one (``whole_units`` -- a wrong zone or a
    missed DST change is exactly 30/60 min; the second the measurement adds
    is the poll, not the clock), else the measured skew to the second."""
    if whole_units(skew_s):
        return float(round(skew_s / 1800.0) * 1800)
    return float(round(skew_s))


def correction_for(answers: Iterable[dict], run_utc_us: int | None) -> dict | None:
    """The correction for a run recorded at *run_utc_us*, or None (pure).

    Only an ``experiment_pc`` answer corrects a RUN, and only inside the
    regime it was asked about (``from_us`` <= t < ``until_us``) -- runs
    before SM saw the skew have no witness and keep their time.
    Returns ``{"delta_s": -skew, "skew_s", "corrected_us", "from_us"}``."""
    if run_utc_us is None:
        return None
    for a in answers or []:
        if a.get("choice") != "experiment_pc":
            continue
        lo, hi = a.get("from_us"), a.get("until_us")
        if lo is None or run_utc_us < lo or (hi is not None and run_utc_us >= hi):
            continue
        delta = -float(a.get("correction_s", a["skew_s"]))
        return {"delta_s": delta, "skew_s": float(a["skew_s"]), "from_us": lo,
                "corrected_us": int(run_utc_us + round(delta * 1_000_000))}
    return None


def sm_correction_for(answers: Iterable[dict], sm_utc_us: int | None) -> dict | None:
    """For the ledger step (pure): with a ``this_pc`` answer, SM's OWN
    stamps in that regime run behind/ahead of the runs by the skew; the
    correction is ``+skew``. None otherwise."""
    if sm_utc_us is None:
        return None
    for a in answers or []:
        if a.get("choice") != "this_pc":
            continue
        lo, hi = a.get("from_us"), a.get("until_us")
        if lo is None or sm_utc_us < lo or (hi is not None and sm_utc_us >= hi):
            continue
        delta = float(a.get("correction_s", a["skew_s"]))
        return {"delta_s": delta, "skew_s": delta, "from_us": lo,
                "corrected_us": int(sm_utc_us + round(delta * 1_000_000))}
    return None


# --------------------------------------------------- the in-place archive
def classify_archive(samples: Iterable[tuple[int, int]]) -> dict:
    """Is an archive's folder mtime a witness? (pure) *samples* are
    ``(run_utc_us, folder_mtime_utc_us)`` pairs.

    [derived] A folder written in place is stamped while its run is saved,
    so ``run - mtime`` is the same for every run (the save time, plus any
    clock skew) however far apart the runs are. A COPY stamps every folder
    within the copy's few minutes, so ``run - mtime`` then spreads as widely
    as the runs themselves. The test: ``n >= INPLACE_MIN_N`` runs spanning
    ``>= INPLACE_MIN_SPAN_S`` of their own clock, with a P10..P90 spread of
    ``run - mtime`` at most ``INPLACE_MAX_SPREAD_S``. A copy that kept the
    times passes, correctly: its mtimes are still the writer's.
    Cross-checked by simulation in tests/test_project_time.py."""
    pairs = sorted((int(a), int(b)) for a, b in samples
                   if isinstance(a, int) and isinstance(b, int))
    n = len(pairs)
    if n < INPLACE_MIN_N:
        return {"in_place": False, "reason": "too_few", "n": n, "span_s": None,
                "spread_s": None, "skew_s": None}
    span = (pairs[-1][0] - pairs[0][0]) / 1e6
    diffs = sorted((a - b) / 1e6 for a, b in pairs)
    p10 = diffs[int(0.1 * (n - 1))]
    p90 = diffs[int(round(0.9 * (n - 1)))]
    spread = p90 - p10
    med = float(statistics.median(diffs))
    if span < INPLACE_MIN_SPAN_S:
        return {"in_place": False, "reason": "short_span", "n": n, "span_s": round(span, 1),
                "spread_s": round(spread, 1), "skew_s": round(med, 1)}
    if spread > INPLACE_MAX_SPREAD_S:
        return {"in_place": False, "reason": "copied", "n": n, "span_s": round(span, 1),
                "spread_s": round(spread, 1), "skew_s": round(med, 1)}
    return {"in_place": True, "reason": "in_place", "n": n, "span_s": round(span, 1),
            "spread_s": round(spread, 1), "skew_s": round(med, 1)}


# ------------------------------------------------------------- the note
def zone_note(view_zone: str | None, run_offsets: dict[str, int]) -> str | None:
    """The ONE quiet project note: the runs record another offset than the
    one they are shown in. A different zone is not an error, so this is a
    note, never a warning. None when they agree or nothing is known."""
    if not run_offsets:
        return None
    top = max(run_offsets.items(), key=lambda kv: (kv[1], kv[0]))[0]
    shown = zone_offset(view_zone) if view_zone else None
    if shown is not None and offset_seconds(top) == offset_seconds(shown):
        return None
    rec = offset_text(top)
    where = f"{view_zone} ({offset_text(shown)})" if view_zone and shown else (
        view_zone or "this browser's zone")
    return (f"Runs in this project are recorded in {rec}; SM shows them in {where}. "
            "A different zone is not an error; each run's tooltip keeps its recorded time.")


def diagnostics_line(inst, project: str | None, view_zone: str | None = None) -> dict | None:
    """The ONE Diagnostics info line about the run clock (or None):
    ``{"level", "text", "ask"}``."""
    if not project:
        return None
    cv = clock_view(inst, project)
    s = cv["summary"]
    oa = cv["open_answer"]
    note = zone_note(view_zone, cv["run_offsets"])
    tail = f" {note}" if note else ""
    if s["class"] == "none":
        return {"level": "info", "text": note, "ask": False} if note else None
    src = "seen arriving live" if s["src"] == "live" else "folders written in place"
    if s["class"] == "small":
        return {"level": "info", "ask": False,
                "text": (f"Run clock: agrees with this PC within {skew_text(s['skew_s'])} "
                         f"({s['n']} runs {src}); below the {span_text(SKEW_ASK_S)} ask "
                         f"threshold, so never asked.{tail}")}
    direction = "ahead of" if s["skew_s"] > 0 else "behind"
    if oa and same_skew(oa["skew_s"], s["skew_s"]):
        what = {"experiment_pc": "run times are shown corrected (labelled; originals kept)",
                "this_pc": "run times are kept; fix this PC's clock (Settings > Time & language > Sync now)",
                "ignore": "ignored"}[oa["choice"]]
        return {"level": "info", "ask": False,
                "text": (f"Run clock: {skew_text(s['skew_s'])} {direction} this PC "
                         f"({s['n_regime']} runs {src}); your answer: {_choice_text(oa['choice'])} "
                         f"— {what}.{tail}")}
    if s["class"] == "ask":
        return {"level": "warning", "ask": True,
                "text": (f"Run clock: {skew_text(s['skew_s'])} {direction} this PC "
                         f"({s['n_regime']} runs {src}) — waiting for your answer.{tail}")}
    return {"level": "info", "ask": False,
            "text": (f"Run clock: {skew_text(s['skew_s'])} {direction} this PC in "
                     f"{s['n']} runs {src}; waiting for {MIN_ASK_N} runs that agree "
                     f"before asking.{tail}")}


def _choice_text(choice: str) -> str:
    return {"experiment_pc": "the experiment PC's clock is wrong",
            "this_pc": "this PC's clock is wrong", "ignore": "ignore"}[choice]
