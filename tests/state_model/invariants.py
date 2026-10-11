"""I1-I12 (VPLAN A.4), checked from the files and the responses after every step.

``pre_facts`` is read before a step, ``check_step`` after it; each returns a
list of ``(invariant, message)``. ``final_sweep`` is the end-of-sequence check
(hub catch-up, ledger, the versions listing).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from tests.state_model import oracle as O
from tests.state_model.ops import Rig, Step, read_doc, sha_pair

#: model outcome kinds that are refusals: a refused press changes nothing (docs/255)
REFUSED = ("refused", "needs_confirm", "collision", "rolled_back")
#: kinds whose SM classification must agree with the model's
DECISION = {
    "APPLY": {"landed": ("landed",), "refused": ("refused",), "noop": ("noop", "landed"), "saved": ("error500",),
              "converged": ("landed", "noop")},
    "MERGE": {"landed": ("landed",), "refused": ("refused",), "collision": ("collision",),
              "pulled_only": ("pulled_only",), "noop": ("pulled_only", "landed", "noop"),
              "converged": ("pulled_only", "landed")},
    "RESTORE_LIVE": {"landed": ("landed",), "refused": ("refused",), "needs_confirm": ("refused",),
                     "converged": ("landed",)},
    "UNDO": {"live": ("live",), "staged": ("staged",), "rolled_back": ("rolled_back",),
             "tray": ("tray",), "noop": ("noop",), "refused": ("refused",)},
    "REDO": {"live": ("live",), "tray": ("tray",), "unstaged": ("unstaged",),
             "noop": ("noop",), "refused": ("refused", "noop")},
    "TAKE_LIVE": {"pulled": ("pulled",), "reapplied": ("reapplied",),
                  "needs_confirm": ("needs_confirm",), "refused": ("unseen_changes", "error", "409", "404")},
    "BRING_BACK": {"brought_back": ("brought_back",), "noop": ("noop",)},
    "SAVE": {"saved": ("saved",), "noop": ("noop",)},
    "STAGE": {"staged_doc": ("staged_doc",), "needs_confirm": ("needs_confirm",), "noop": ("noop",)},
}


def _doc_eq(a: Any, b: Any) -> bool:
    if isinstance(a, O._Sentinel) or isinstance(b, O._Sentinel):
        return isinstance(a, O._Sentinel) and isinstance(b, O._Sentinel)
    return O.same(a, b)


def _type_only(a: Any, b: Any) -> bool:
    """Every difference is the same number in another type (1 vs 1.0) -- I11."""
    if isinstance(a, O._Sentinel) or isinstance(b, O._Sentinel):
        return False
    d = O.leaf_diff(a, b)
    return bool(d) and all(
        isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool)
        and not isinstance(y, bool) and x == y for x, y in d.values())


def _diff_text(a: Any, b: Any, n: int = 6) -> str:
    if isinstance(a, O._Sentinel) or isinstance(b, O._Sentinel):
        return f"{a!r} vs {b!r}"
    d = O.leaf_diff(a, b)
    items = sorted(d.items())[:n]
    return "; ".join(f"{p}: {va!r} vs {vb!r}" for p, (va, vb) in items) + (
        f" (+{len(d) - n} more)" if len(d) > n else "")


def pre_facts(rig: Rig) -> dict:
    ctx = rig.ctx()
    wc = ctx["working_copy"]
    wf = Path(wc.working_folder)
    sc = rig.sidecar()
    lines = rig.hub_lines()
    return {
        "live_sha": sha_pair(rig.live),
        "live_doc": rig.sm_L(),
        "lines": len(lines),
        "log": [(e.path, repr(e.old), repr(e.new), e.gid) for e in rig.sm_log()],
        "working": sha_pair(wf),
        "working_fp": tuple(_mtime(wf / n) for n in ("state.json", "wiring.json")),
        "journal": sc.read_bytes() if sc.exists() else None,
        "dirty": bool(ctx.get("working_dirty")),
        "stash": json.dumps(ctx.get("pending_reapply"), sort_keys=True, default=repr),
        "last_apply": copy.deepcopy(ctx.get("last_apply")),
    }


def _mtime(p: Path):
    try:
        return os.stat(p).st_mtime_ns
    except OSError:
        return None


def check_step(rig: Rig, step: Step, pre: dict) -> list[tuple[str, str]]:
    from quam_state_manager.core import working_copy
    out: list[tuple[str, str]] = list(step.notes)
    m = rig.model
    out += m.notes
    m.notes = []
    ctx = rig.ctx()
    exp = step.exp or O.Outcome("?")

    # --- the decision -------------------------------------------------------
    known_500 = any(t == "K:SU-02" for t, _ in step.notes)
    table = DECISION.get(step.name)
    if known_500:
        table = None
    if table and exp.kind in table and step.obs not in table[exp.kind]:
        out.append(("D", f"{step.name}: model says {exp.kind}({exp.reason}) but SM did {step.obs} "
                         f"(HTTP {step.status})"))
    if isinstance(step.status, int) and step.status >= 500 and not known_500:
        out.append(("D500", f"{step.name} answered HTTP {step.status}"))

    # --- I8: one chip, one ledger: the history dir never moves -----------------
    cd = rig.chip_dir()
    if cd != rig.chip_dir0:
        out.append(("K:SU-06", f"the chip's history dir moved from {rig.chip_dir0.name} to {cd.name} "
                          f"after {step.name}: later writes go to a second ledger"))
        rig.chip_dir0 = cd

    # --- I1 / I9: live bytes move only under a door that said it wrote --------
    post_sha = sha_pair(rig.live)
    live_doc = rig.sm_L()
    lines = rig.hub_lines()
    new_lines = lines[pre["lines"]:]
    landed = [ln["landed"] for ln in new_lines if "landed" in ln]
    if not step.outside:
        content_moved = not _doc_eq(pre["live_doc"], live_doc)
        if content_moved and not (step.live_writer and landed):
            out.append(("I1", f"{step.name} changed the live content with no landed write "
                              f"(writer={step.live_writer}, landed={len(landed)}): "
                              f"{_diff_text(pre['live_doc'], live_doc)}"))
        if post_sha != pre["live_sha"] and not step.live_writer:
            out.append(("I9", f"{step.name} (a staging/working door) rewrote the live files"))
    # --- I2: one record per live write ----------------------------------------
    writes = [ln for ln in new_lines if "id" in ln]
    ids_landed = [ln["landed"] for ln in lines if "landed" in ln]
    if len(ids_landed) != len(set(ids_landed)):
        out.append(("I2", "an event id is landed twice"))
    for w in writes:
        outc = [ln for ln in lines if ln.get("landed") == w["id"] or ln.get("failed") == w["id"]]
        if len(outc) != 1:
            out.append(("I2", f"event {w['id']} ({w.get('src')}) has {len(outc)} outcome lines"))
    if not step.outside and landed:
        lw = [w for w in writes if w["id"] in landed]
        if lw and not isinstance(pre["live_doc"], O._Sentinel) and not isinstance(live_doc, O._Sentinel):
            s0, w0 = rig.split(pre["live_doc"])
            s1, w1 = rig.split(live_doc)
            h0 = working_copy.content_hash(s0, w0)
            h1 = working_copy.content_hash(s1, w1)
            if lw[0].get("base_hash") != h0:
                out.append(("I2", f"landed event base_hash != the live hash before the press ({lw[0].get('src')})"))
            if lw[-1].get("post_hash") != h1:
                out.append(("I2", f"landed event post_hash != the live hash after the press ({lw[-1].get('src')})"))
        if not _doc_eq(pre["live_doc"], live_doc) is False and len(landed) and _doc_eq(pre["live_doc"], live_doc):
            pass
    if not step.outside and exp.events and len(landed) != exp.events and step.obs in ("landed", "live"):
        out.append(("I2", f"{step.name}: {len(landed)} landed events, model expects {exp.events}"))
    rig.landed_ids += landed

    # --- content: live / working / working folder / tray ----------------------
    if not _doc_eq(live_doc, m.L):
        tag = "I11" if _type_only(live_doc, m.L) else "I1" if not step.outside and pre["live_sha"] != post_sha and not exp.writes_live else (
            "D" if not step.outside and pre["live_sha"] == post_sha and exp.writes_live else "I7")
        out.append((tag, f"live != model after {step.name}: {_diff_text(live_doc, m.L)}"))
    sw = rig.sm_W()
    if not _doc_eq(sw, m.W):
        out.append(("I11" if _type_only(sw, m.W) else "I7", f"working view != model after {step.name}: {_diff_text(sw, m.W)}"))
    sws = rig.sm_Ws()
    if not _doc_eq(sws, m.Ws):
        out.append(("I11" if _type_only(sws, m.Ws) else "I3", f"working folder != model after {step.name}: {_diff_text(sws, m.Ws)}"))
    # SM-internal I3: the working folder is the store with the unsaved log taken back
    log = rig.sm_log()
    if not isinstance(sws, O._Sentinel):
        back = copy.deepcopy(sw)
        try:
            for e in reversed(log):
                O.apply_entry(back, e, False)
            if not O.same(back, sws):
                out.append(("I3", f"store minus its log != working folder: {_diff_text(back, sws)}"))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            out.append(("I3", f"store minus its log not computable: {exc!r}"))
    mine = [(e.path, e.created, e.deleted) for e in m.T]
    theirs = [(e.path, e.created, e.deleted) for e in log]
    if mine != theirs:
        out.append(("I4", f"tray != model: SM {theirs[:8]} vs model {mine[:8]}"))
    else:
        for a, b in zip(log, m.T):
            if a.created and O._eq(a.new, O.get(m.W, a.path)):
                continue                       # SU-09: a created entry shows its subtree now
            if not (O._eq(a.new, b.new) or a.deleted) or not (O._eq(a.old, b.old) or a.created):
                out.append(("I7", f"tray entry {a.path}: SM {a.old!r}->{a.new!r} vs model {b.old!r}->{b.new!r}"))
                break
        if [len(s) for s in O.segment(log)] != [len(s) for s in O.segment(m.T)]:
            out.append(("I4", f"tray groups differ: SM {[len(s) for s in O.segment(log)]} "
                              f"vs model {[len(s) for s in O.segment(m.T)]}"))
    t = rig.tray()
    if t is None:
        out.append(("I4", "the tray fragment carries no data-change-count / data-change-sig"))
    else:
        if t[0] != len(log):
            out.append(("I4", f"data-change-count {t[0]} != len(log) {len(log)}"))
        if t[1] != O.change_sig([e.path for e in log]):
            out.append(("I4", "data-change-sig != the log's path signature"))
    if bool(ctx.get("working_dirty")) != m.dirty:
        out.append(("I3", f"working_dirty {bool(ctx.get('working_dirty'))} != model {m.dirty}"))

    # --- I4 (docs/255): a refused press changes nothing ----------------------
    if exp.kind in REFUSED and step.refusal:
        post = pre_facts(rig)
        for k in ("log", "working", "working_fp", "journal", "dirty", "stash", "live_sha"):
            if k == "journal" and pre[k] is not None and post[k] is not None and _journal_same(pre[k], post[k]):
                continue
            if post[k] != pre[k]:
                out.append(("I4", f"refused {step.name} ({exp.reason}) changed {k}"))

    # --- I5: the journal --------------------------------------------------------
    units, pcur = rig.journal()
    if len(units) > O.MAX_UNITS:
        out.append(("I5", f"journal holds {len(units)} units (> {O.MAX_UNITS})"))
    if not 0 <= pcur <= len(units):
        out.append(("I5", f"persisted cursor {pcur} outside [0, {len(units)}]"))
    rc = ctx.get("undo_cursor")
    # units whose net effect is nothing change no value anywhere; SM may keep
    # one (a replay that re-staged a value equal to live) -- compare the rest,
    # and each cursor as the count of value-changing units below it
    sm_all = [_unit_effect(u) for u in units]
    md_all = [u.effect() for u in m.J]
    sm_eff = [e for e in sm_all if e]
    md_eff = [e for e in md_all if e]
    below = lambda effs, c: sum(1 for e in effs[:max(0, c or 0)] if e)
    if below(sm_all, rc) != below(md_all, m.cursor):
        out.append(("I5", f"RAM cursor {rc} != model {m.cursor}"))
    if below(sm_all, pcur) != below(md_all, m.pcursor):
        out.append(("I5", f"persisted cursor {pcur} != model {m.pcursor}"))
    if len(sm_eff) != len(md_eff):
        out.append(("I5", f"journal has {len(sm_eff)} units, model {len(md_eff)}"))
    else:
        for i, (a, b) in enumerate(zip(sm_eff, md_eff)):
            if set(a) != set(b) or any(not (O._eq(a[p][0], b[p][0]) and O._eq(a[p][1], b[p][1])) for p in a):
                only_sm = sorted(set(a) - set(b))[:4]
                only_md = sorted(set(b) - set(a))[:4]
                vals = [p for p in set(a) & set(b) if not (O._eq(a[p][0], b[p][0]) and O._eq(a[p][1], b[p][1]))][:3]
                out.append(("I5", f"journal unit {i} effect differs: SM {_short(a)} vs model {_short(b)} "
                                  f"(only SM {only_sm}, only model {only_md}, values {[(p, a[p], b[p]) for p in vals]})"))
                break
    if step.obs == "live" and step.name == "UNDO" and not isinstance(live_doc, O._Sentinel):
        u = units[pcur] if pcur < len(units) else None
        if u is not None:
            for p, (old, _new) in _unit_effect(u).items():
                if not O._eq(O.get(live_doc, p), old):
                    out.append(("I5", f"after a live undo the chip holds {O.get(live_doc, p)!r} at {p}, unit.old {old!r}"))
                    break
    if not m.stash:
        rig.pending_saved = []
    disc = [u.get("id") for u in units if (u.get("meta") or {}).get("discarded")
            and u.get("id") in rig.pending_saved]
    if disc:
        # SU-03: App reload rebuilds the store and marks every saved-not-landed
        # unit discarded although the working copy it re-read still holds them
        tag = "K:SU-03" if step.name == "APP_RELOAD" else "I5"
        out.append((tag, f"saved units {disc[:3]} are marked discarded while the working copy "
                         f"still holds them (after {step.name})"))
        rig.pending_saved = [u for u in rig.pending_saved if u not in disc]

    # --- I6: the pre-apply snapshot ----------------------------------------------
    if (exp.kind == "landed" and step.obs == "landed" and step.name in ("APPLY", "MERGE", "RESTORE_LIVE")
            and not m.push_armed and "before" in exp.extra
            and not isinstance(exp.extra["before"], O._Sentinel)):
        la = ctx.get("last_apply") or {}
        ts = la.get("pre_ts")
        if not ts:
            # SU-16: after a torn live read the next Apply can land without one
            out.append(("K:SU-16" if getattr(rig, "saw_torn", False) else "I6",
                        f"{step.name} landed with no last_apply.pre_ts"))
        else:
            try:
                snap = rig.snapshot_doc(ts)
                if not isinstance(exp.extra["before"], O._Sentinel) and not O.same(snap, exp.extra["before"]):
                    out.append(("I6", f"last_apply.pre_ts {ts} != the live content before the write: "
                                      f"{_diff_text(snap, exp.extra['before'])}"))
            except Exception as exc:  # noqa: BLE001
                out.append(("I6", f"last_apply.pre_ts {ts} unreadable: {exc!r}"))
    if step.name in ("UNDO", "REDO") and step.obs == "live":
        if (ctx.get("last_apply") or None) != pre["last_apply"]:
            out.append(("I6", "a Ctrl+Z walk step moved last_apply"))

    # --- I12: the status control ---------------------------------------------------
    if rig.hidden_mtimes is not None and _live_mtimes(rig) != rig.hidden_mtimes:
        rig.hidden_mtimes = None
    if not step.skip_status:
        got = rig.status()
        want = m.verdict()
        if got != want:
            # SU-01: a same-size rewrite that put the old mtime back is never
            # re-read by the status control (its facts are keyed on mtimes)
            tag = "K:SU-01" if rig.hidden_mtimes is not None else "I12"
            out.append((tag, f"status {got!r} != model {want!r} after {step.name}"))
        # I3: the sync point is the live hash exactly when nothing moved
        if not isinstance(live_doc, O._Sentinel):
            wc = ctx["working_copy"]
            s1, w1 = rig.split(live_doc)
            at_sync = wc.synced_live_hash == working_copy.content_hash(s1, w1)
            if want in ("synced", "mine", "staged") and not at_sync:
                out.append(("I3", f"status {want} but synced_live_hash != hash(live)"))
            if want in ("live", "both", "collide") and at_sync:
                out.append(("I3", f"status {want} but synced_live_hash == hash(live)"))
            meta = Path(wc.meta_path())
            try:
                mh = json.loads(meta.read_text(encoding="utf-8")).get("synced_live_hash")
                if mh != wc.synced_live_hash:
                    out.append(("I3", "the meta sidecar's synced_live_hash != the RAM one"))
            except (OSError, ValueError):
                out.append(("I3", "the working copy's meta sidecar is unreadable"))
    return out


def _live_mtimes(rig: Rig):
    return tuple(_mtime(rig.live / n) for n in ("state.json", "wiring.json"))


def _journal_same(a: bytes, b: bytes) -> bool:
    """A journal is the same when its units' contents and cursor are (a
    landed-mark timestamp may move)."""
    try:
        da, db = json.loads(a), json.loads(b)
    except ValueError:
        return a == b
    strip = lambda d: ([{k: v for k, v in u.items() if k != "meta"} for u in d.get("units", [])],
                       d.get("cursor"))
    return strip(da) == strip(db)


def _unit_effect(u: dict) -> dict:
    ents = [O.Entry(e["path"], e.get("old"), e.get("new"), created=bool(e.get("created")),
                    deleted=bool(e.get("deleted"))) for e in u.get("entries") or []]
    return O.Unit(entries=ents).effect()


def _short(eff: dict, n: int = 4) -> str:
    items = sorted(eff.items())[:n]
    return "{" + ", ".join(f"{p}: {a!r}->{b!r}" for p, (a, b) in items) + (
        f", +{len(eff) - n}" if len(eff) > n else "") + "}"


def final_sweep(rig: Rig) -> list[tuple[str, str]]:
    """End of sequence: the hub caught up, the ledger names every landed write
    once, the versions listing lists each once."""
    from quam_state_manager.core import hub
    from quam_state_manager.core.hub_store import HubStore
    from quam_state_manager.web import routes
    out = []
    chip = rig.chip_dir()
    hub.catch_up(chip)
    hub.flush(30)
    lines = rig.hub_lines()
    landed = [ln["landed"] for ln in lines if "landed" in ln]
    writes = {ln["id"]: ln for ln in lines if "id" in ln}
    for i in landed:
        if i not in writes:
            out.append(("I2", f"landed line for an unknown event {i}"))
    try:
        with HubStore(chip) as st:
            rows = st.conn.execute("SELECT sm_id, outcome FROM sm_events").fetchall()
        ids = [r[0] for r in rows]
        if len(ids) != len(set(ids)):
            out.append(("I8", "the ledger holds an SM event twice"))
        lset = {r[0] for r in rows if r[1] == "landed"}
        miss = set(landed) - lset
        if miss:
            out.append(("I8", f"{len(miss)} landed events are not landed in the ledger"))
        extra = lset - set(landed)
        if extra:
            out.append(("I8", f"{len(extra)} ledger events are landed that the journal does not land"))
    except Exception as exc:  # noqa: BLE001
        out.append(("I8", f"ledger unreadable: {exc!r}"))
    try:
        with rig.app.test_request_context("/state/versions"):
            ctx = routes._active_ctx()
            snaps = routes._history().list_snapshots(Path(ctx["path"]))
            res = routes._versions_read(ctx, snaps, limit=routes._STATE_VERSIONS_CAP)
        seen = {}
        for r in res.get("rows") or []:
            key = r.get("sm_id") or (r.get("source") or {}).get("sm_id") or r.get("event_id")
            if key:
                seen[key] = seen.get(key, 0) + 1
        dup = [k for k, n in seen.items() if n > 1]
        if dup:
            out.append(("I8", f"the versions listing shows {len(dup)} SM writes more than once"))
        rig.versions_rows = len(res.get("rows") or [])
    except Exception as exc:  # noqa: BLE001
        out.append(("I8", f"versions listing failed: {exc!r}"))
    return out
