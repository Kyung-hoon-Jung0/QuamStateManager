"""Offline archive -> per-chip ledger: python -m ...hub_build ROOT --out DIR.

No SM server, archive writes, fit attribution, or copied-state inference.
Discovery is frozen for each build; concurrent new folders wait for the next
invocation. Each successful event and its root watermark commit together.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quam_state_manager.core import hub_rules as rules
from quam_state_manager.core import run_time, safe_io, timefmt
from quam_state_manager.core.hub_store import (
    CHIP_UNCERTAIN,
    NODE_UNREADABLE,
    REVERTS_TO_EARLIER,
    TIME_ASSUMED,
    HubStore,
    json_bytes,
    segments,
)

_RUN = re.compile(r"^#(\d+)_(.+)_(\d{6})$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Run:
    folder: Path
    node: dict
    run_id: int
    experiment: str
    instant: int = 0
    quality: str = "none"
    #: node.json could not be read. The saved pair is still the run's fact
    #: (docs/275): such a run keeps its state and is flagged NODE_UNREADABLE.
    error: str | None = None


def _read_shared(path: Path) -> bytes:
    # docs/270 review P2 #7: a run folder can be written by a live experiment;
    # a share-delete handle never blocks its atomic replace.
    with safe_io.open_shared(path) as f:
        return f.read()


def read_node(folder: Path) -> tuple[dict, str | None]:
    """The slim ``node.json`` metadata the ledger keeps (never measurement
    payloads), and the read error when it is unreadable. Shared by the offline
    builder and the in-SM sync (docs/275), so one run reads one way."""
    error = None
    try:
        node = json.loads(_read_shared(folder / "node.json"))
        if not isinstance(node, dict):
            raise ValueError("node root must be an object")
    except (OSError, ValueError) as exc:
        node, error = {}, f"node.json: {exc}"
    meta = node.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    data = node.get("data")
    data = data if isinstance(data, dict) else {}
    # The real archive stores parameters under data.parameters.
    params = node.get("parameters", data.get("parameters", {}))
    params = params if isinstance(params, dict) else {}
    model = params.get("model", params)
    model = model if isinstance(model, dict) else {}
    slim = {"created_at": node.get("created_at"), "metadata": {
        k: meta.get(k) for k in ("run_start", "run_end", "status", "name")},
        "parents": node.get("parents", []), "patches": node.get("patches", []),
        "targets": {k: model[k] for k in ("qubits", "qubit_pairs", "pairs", "cz_macro_name") if k in model}}
    return slim, error


def run_of(folder: Path) -> Run | None:
    """One run folder's metadata (no saved state), or None for a folder that
    is not a run (``#<id>_<name>_<HHMMSS>``)."""
    match = _RUN.fullmatch(folder.name)
    if not match:
        return None
    slim, error = read_node(folder)
    return Run(folder, slim, int(match[1]), slim["metadata"].get("name") or match[2], error=error)


def resolve_instant(run: Run, hint: str | None) -> None:
    """``run.instant`` / ``run.quality`` through the one instant rule."""
    run.instant, run.quality = run_time.resolve(
        run.node.get("created_at"), run.node["metadata"].get("run_end"), run.folder,
        offset_hint=hint, read_node=False)
    if run.instant is None:
        # Standard run folders have a clock; explicitly retain anomalous ones.
        run.instant = run.folder.stat().st_mtime_ns // 1000
        run.quality = "mtime"


def enumerate_runs(root: Path) -> tuple[list[Run], str | None]:
    """Keep metadata only, not measurement payloads or saved states, in RAM."""
    runs = []
    for day in sorted(root.iterdir()):
        if not _DAY.fullmatch(day.name) or not day.is_dir():
            continue
        # Freeze each directory listing; tolerate new runs on later days too.
        for folder in list(day.iterdir()):
            if not folder.is_dir():
                continue
            run = run_of(folder)
            if run is not None:
                runs.append(run)
    hint = timefmt.archive_offset_hint(run.node for run in runs)
    for run in runs:
        resolve_instant(run, hint)
    runs.sort(key=lambda r: (r.instant, r.run_id, r.experiment, r.folder.name))
    return runs, hint


def state_paths(folder: Path) -> tuple[Path, Path]:
    """Standard layout first; older archives also have alternate subfolders.

    S10 walk (perf): the alternate subfolders are searched only when neither
    standard place holds a state -- the first one that does is the answer
    either way, and the search was a directory scan per run on every read."""
    candidates = [folder / "quam_state" / "state.json", folder / "state.json"]
    state = next((p for p in candidates if p.is_file()), None)
    if state is None:
        state = next((p for p in sorted(folder.glob("*/state.json")) if p.is_file()), candidates[0])
    return state, state.with_name("wiring.json")


def read_pair(folder: Path) -> tuple[bytes, bytes]:
    """Read only stable pairs, without opening customer files for writing."""
    paths = state_paths(folder)
    for _ in range(3):
        before = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
        raw = tuple(_read_shared(p) for p in paths)
        after = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
        if before == after:
            return raw
    raise ValueError("saved state pair changed during read")


def read_doc(folder: Path) -> dict:
    state, wiring = (json.loads(raw) for raw in read_pair(folder))
    if not isinstance(state, dict) or not isinstance(wiring, dict):
        raise ValueError("state and wiring roots must be objects")
    return rules.merged(state, wiring)


def _proven(node: dict, changes: list[rules.Change], flat: dict) -> set[str]:
    """Only an explicit leaf patch with exactly the saved new value proves it."""
    proven = set()
    patches = node.get("patches")
    if not isinstance(patches, list):
        return proven
    by_parts = {tuple(segments(c.path)): c.path for c in changes if c.op != "gone"}
    for patch in patches:
        if not isinstance(patch, dict) or patch.get("op") not in ("add", "replace"):
            continue
        raw = patch.get("path")
        if not isinstance(raw, str) or not raw.startswith("/") or "value" not in patch:
            continue
        parts = [p.replace("~1", "/").replace("~0", "~") for p in raw.split("/")[1:]]
        if parts and parts[0] == "quam":
            parts.pop(0)
        path = by_parts.get(tuple(parts))
        if path is not None:
            proposed = patch["value"]
            if isinstance(flat[path], dict):
                proposed = rules.flatten(proposed).get("")
            if rules.same(proposed, flat[path]):
                proven.add(path)
    return proven


#: a run imported from its Param History capture (its run folder is gone):
#: its chip was never judged -- Param History filed it under this chip
#: (``hub_sync.attach_captured_run``)
RUN_CAPTURE_SRC = "param_history:run_capture"


def _chip_identity(state: dict, wiring: dict, folder: Path) -> dict | None:
    from quam_state_manager.core.history import fingerprint_token, identity_from_dicts

    ident = identity_from_dicts(state, wiring, folder)
    fp = ident.fingerprint
    if not ident.name and not (fp and (fp.network or fp.qubits or fp.pairs)):
        return None
    # v1.3.0 A9: the controller the chip is wired to (``wiring.network``'s
    # host / cluster_name), so a nameless identity can be compared part by
    # part; None when the state names none
    return {"name": ident.name, "fingerprint": fingerprint_token(fp),
            "qubits": sorted(fp.qubits) if fp else [],
            "network": dict(fp.network) if fp and fp.network else None}


def identity_of(state: Any, wiring: Any, folder: Path | None = None) -> dict | None:
    """THE chip identity of one saved pair, read from its MERGED document --
    the document the ledger keeps, so a run judged again from the ledger
    (:func:`reseed`) is judged exactly as its ingestion judged it."""
    if not isinstance(state, dict):
        return None
    doc = rules.merged(state, wiring if isinstance(wiring, dict) else {})
    return _chip_identity(doc, doc, folder or Path("."))


def same_controller(chip: dict, identity: dict) -> bool | None:
    """Whether two identities name one controller: the cluster name decides
    when both name one (the host is only the address it is reached at, and
    an address can change), else the host. None when either side names no
    controller (an identity stored before v1.3.0 carries none)."""
    a, b = chip.get("network"), identity.get("network")
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    for key in ("cluster_name", "host"):
        if a.get(key) is not None and b.get(key) is not None:
            return a[key] == b[key]
    return None


def identity_disagrees(chip: dict, identity: dict) -> bool:
    """Whether a run's saved chip *identity* is NOT the ledger's *chip* --
    the one rule the offline builder, the in-SM sync, the folder lanes and
    run naming judge by (``CHIP_UNCERTAIN``).

    *chip* is the chip the user OPENED: its live state is this chip by
    definition (``routes._hub_live_identity`` seeds it, named or nameless;
    only a chip with no identity at all leaves the seed to its first
    identified run). A chip changes over its life -- a qubit added or
    removed, its controller reached at another address -- so a nameless
    identity is compared part by part, never as one fingerprint:

    * both declare a name: the names decide; one name with no qubit in
      common is two chips (docs/275 review);
    * the run declares a name the chip does not: not proven the chip's own
      (a chip gains a name over its life -- SM asks for one -- and hardly
      ever loses it), so it disagrees; the other way round (a nameless run
      of a chip named since) is judged as below;
    * otherwise, when both name a controller (:func:`same_controller`):
      another controller disagrees; on the same controller, a run sharing
      at least one qubit with the chip (or with the same qubit set) is the
      chip's own, one with no qubit in common is not;
    * when either names no controller, the whole fingerprint must match.

    A disagreeing run is KEPT, flagged uncertain, shown and never named as
    the writer (only a run whose saved state DECLARES another chip leaves
    the folder's timeline, ``hub_lanes``). What stays honestly AMBIGUOUS:
    a run on another controller may be this chip before a move -- it is
    flagged, never dropped; and another nameless chip on the same
    controller that shares a qubit label (one layout) cannot be told apart
    from this chip after a qubit change by any file the ledger reads -- such
    a run is consistent with the chip and nothing proves otherwise, so it is
    not flagged (nor are two controllers that share one cluster name, a
    default one say: one controller to this rule). Declaring
    ``extras.chip_name`` is what tells such chips apart."""
    if chip.get("name") and identity.get("name"):
        if chip["name"] != identity["name"]:
            return True
        mine, theirs = set(chip.get("qubits") or ()), set(identity.get("qubits") or ())
        return bool(mine and theirs and not (mine & theirs))
    if identity.get("name"):
        return True
    controller = same_controller(chip, identity)
    if controller is None:
        return chip.get("fingerprint") != identity.get("fingerprint")
    if not controller:
        return True
    mine, theirs = set(chip.get("qubits") or ()), set(identity.get("qubits") or ())
    return mine != theirs and not (mine & theirs)


def parse_state(raw: tuple[bytes, bytes], folder: Path, chip: dict | None, *, want_pair: bool = False):
    """``(merged doc, S2 flat, flags, chip identity)`` of one saved pair: the
    parse, the shared merge and the chip-identity check (``want_pair``: also
    the parsed ``(state, wiring)``). Raises ``ValueError`` / ``TypeError`` for
    a pair that is not two JSON objects. *chip* is the ledger's identity
    (the open chip, :func:`identity_disagrees`); a ledger that has none yet
    -- no chip was handed to it -- takes the first identified run's."""
    state, wiring = (json.loads(data) for data in raw)
    if not isinstance(state, dict) or not isinstance(wiring, dict):
        raise ValueError("state and wiring roots must be objects")
    doc = rules.merged(state, wiring)
    flat = rules.flatten(doc)
    flags = 0
    # v1.3.0 A9: from the merged document (``identity_of``), as a re-judge reads it
    identity = _chip_identity(doc, doc, folder / "quam_state")
    if identity is None:
        flags |= CHIP_UNCERTAIN
    elif chip is None:
        chip = identity
    elif identity_disagrees(chip, identity):
        flags |= CHIP_UNCERTAIN
    if want_pair:
        return doc, flat, flags, chip, (state, wiring)
    return doc, flat, flags, chip


# ----------------------------------------------------------------------
# v1.3.0 A9: a new seed judges every run again
# ----------------------------------------------------------------------

#: the top-level keys a chip identity reads: label keys only, and whole subtrees
_LABEL_ROOTS = ("qubits", "qubit_pairs")
_OWN_ROOTS = ("extras", "network")


def _identity_parts(shape: Any) -> dict:
    """What a chip identity reads of one ledger shape: the qubit and pair
    keys, and the shapes of ``extras`` and ``network``."""
    if not isinstance(shape, dict):
        return {}
    parts = {k: frozenset(shape[k]) for k in _LABEL_ROOTS if isinstance(shape.get(k), dict)}
    parts.update({k: shape[k] for k in _OWN_ROOTS if k in shape})
    return parts


def _fill(store: HubStore, node: Any, path: str, leaves: dict) -> Any:
    if node is None:
        leaf = leaves[path]
        return store.blob(leaf["_hash"]) if isinstance(leaf, dict) else leaf
    if isinstance(node, dict):
        return {k: _fill(store, v, f"{path}.{rules._segment(k)}", leaves) for k, v in node.items()}
    return [_fill(store, v, f"{path}.{i}", leaves) for i, v in enumerate(node)]


def _identity_at(store: HubStore, parts: dict, leaves: dict) -> dict | None:
    """The chip identity of one ledger state, rebuilt from its shape and the
    ``extras`` / ``network`` leaves in force -- the document parts
    ``identity_of`` reads, so the answer is ingestion's."""
    doc: dict = {}
    for key, part in parts.items():
        doc[key] = dict.fromkeys(part, {}) if key in _LABEL_ROOTS else _fill(store, part, rules._segment(key),
                                                                             leaves)
    return _chip_identity(doc, doc, Path("."))


def reseed(store: HubStore, chip: dict) -> int:
    """Make *chip* the ledger's identity (``meta.chip_identity``) and judge
    the chip of every run again against it, inside the caller's transaction
    -- a ledger seeded from another chip's run (v1.2.0 took a nameless
    chip's seed from the first run ingested) heals here, with no stale flag
    left. Each run gets exactly the verdict ingestion gives under this seed
    (:func:`parse_state`): a run with no saved state, or no identity, stays
    uncertain; a run byte-identical to the state before it takes that
    state's verdict; any other run is judged by :func:`identity_disagrees`
    on its own saved identity, rebuilt from the ledger (no run folder is
    read; the ``extras`` / ``network`` leaves are replayed exactly as
    ``HubStore.state_at`` replays them: from the latest checkpoint or SM
    anchor, then the change rows after it). A run the ledger cannot rebuild
    keeps its flag (nothing proven), and a run captured by Param History is
    never judged (it never was). A reader follows by itself: a re-flagged
    run is an in-place change the rewrite log records (docs/298), the
    commit moves the read index, and the lane rule's verdicts are kept per
    seed (``hub_lanes.declares_another_chip``). Returns how many runs
    changed."""
    from quam_state_manager.core.hub_store import OPS, value

    conn = store.conn
    store.set_meta("chip_identity", json_bytes(chip).decode("utf-8"))
    rows: dict[int, list] = {}
    for eid, path, op, num, txt in conn.execute(
            "SELECT c.eid, p.path, c.op, c.num, c.txt FROM changes c JOIN paths p USING(pid) "
            "WHERE p.path IN ('extras', 'network') OR p.path GLOB 'extras.*' OR p.path GLOB 'network.*'"):
        rows.setdefault(eid, []).append((path, op, num, txt))
    # the starting points state_at replays from (a checkpoint wins a tie)
    bases = {eid: ("anchor", h) for eid, h in conn.execute("SELECT eid, hash FROM sm_anchors")}
    bases.update({eid: ("checkpoint", h) for eid, h in conn.execute("SELECT eid, hash FROM checkpoints")})
    decoded: dict = {}

    def base_leaves(base) -> dict:
        if base not in decoded:
            doc = store.sm_pair(base[1]) if base[0] == "anchor" else store.blob(base[1])
            own = {k: doc[k] for k in _OWN_ROOTS if isinstance(doc, dict) and k in doc}
            decoded[base] = rules.flatten(own)
        return decoded[base]

    gone = object()
    base, since, version = None, {}, 0
    parts: dict = {}
    verdicts: dict = {}
    prev = None             # the nearest good event: (state hash, shape hash, its verdict)
    changed = 0
    for ev in conn.execute("SELECT eid, kind, src, flags, error, state_hash, shape_hash FROM events "
                           "ORDER BY ord").fetchall():
        if ev["eid"] in bases:
            base, since = bases[ev["eid"]], {}
        else:
            for path, op, num, txt in rows.get(ev["eid"], ()):
                since[path] = gone if op == OPS["gone"] else value(num, txt)
        version += 1 if ev["eid"] in bases or ev["eid"] in rows else 0
        bit = ev["flags"] & CHIP_UNCERTAIN
        if ev["kind"] == "run" and ev["error"] is None and ev["src"] != RUN_CAPTURE_SRC:
            if prev is not None and ev["state_hash"] == prev[0] and prev[1] is not None:
                bit = prev[2]
            elif ev["shape_hash"]:
                key = (ev["shape_hash"], version)
                if key not in verdicts:
                    try:
                        if ev["shape_hash"] not in parts:
                            parts[ev["shape_hash"]] = _identity_parts(store.blob(ev["shape_hash"]))
                        leaves = dict(base_leaves(base)) if base is not None else {}
                        for path, val in since.items():
                            if val is gone:
                                leaves.pop(path, None)
                            else:
                                leaves[path] = val
                        ident = _identity_at(store, parts[ev["shape_hash"]], leaves)
                    except (KeyError, ValueError, TypeError, OSError, zlib.error):
                        verdicts[key] = None        # not rebuildable here: nothing proven
                    else:
                        verdicts[key] = CHIP_UNCERTAIN if ident is None or identity_disagrees(chip, ident) else 0
                if verdicts[key] is not None:
                    bit = verdicts[key]
            if bit != ev["flags"] & CHIP_UNCERTAIN:
                conn.execute("UPDATE events SET flags=? WHERE eid=?",
                             ((ev["flags"] & ~CHIP_UNCERTAIN) | bit, ev["eid"]))
                changed += 1
        if ev["error"] is None:
            prev = (ev["state_hash"], ev["shape_hash"], bit)
    return changed


def event_fields(run: Run, *, root_id: int, rel: str, hint: str | None, digest: str | None,
                 base_hash: str | None, flags: int, error: str | None, src: str) -> dict:
    """The ``events`` row of one run, without its order rank. One constructor
    for the offline builder and the in-SM sync (docs/275)."""
    meta = run.node["metadata"]
    patches = run.node.get("patches")
    t_src = run.node.get("created_at")
    if run.quality == "offset" and run_time.iso_instant(t_src)[1] != "offset":
        t_src = meta.get("run_end")
    elif run_time.iso_instant(t_src)[0] is None:
        t_src = meta.get("run_end")
    return dict(kind="run", t_utc_us=run.instant, t_src=t_src or str(run.folder), t_quality=run.quality,
                root_id=root_id, rel_path=rel, run_id=run.run_id, experiment=run.experiment,
                # The node's own status is a fact about the run; a "finished"
                # run that saved no state stays "finished" (review, docs/270).
                # Why the ledger has no state for it lives in `error`.
                status=meta.get("status"),
                run_start_us=run_time.resolve(meta.get("run_start"), offset_hint=hint, read_node=False)[0],
                run_end_us=run_time.resolve(meta.get("run_end"), offset_hint=hint, read_node=False)[0],
                parents=json_bytes(run.node.get("parents", [])).decode("utf-8"),
                targets=json_bytes(run.node["targets"]).decode("utf-8"),
                patches_n=len(patches) if isinstance(patches, list) else 0,
                src=src, state_hash=digest, base_hash=base_hash,
                state_ref=str(run.folder), flags=flags, error=error)


def build(root: str | Path, out: str | Path, *, limit: int | None = None,
          checkpoint_interval: int = 250, progress=None, identity: dict | None = None) -> dict:
    """Resume an ordered offline build. ``limit`` caps new events this call.

    Completed locations are authoritative for this immutable offline archive.
    Identity deduplication also admits an archive copy as extra locations.
    Earlier unknown runs fail explicitly here; the in-SM sync (``hub_sync``,
    docs/275) inserts them and repairs their successors.

    ``identity``: the chip the ledger is for (``identity_of`` of its state),
    as the in-SM sync is handed the open chip -- every run is judged against
    it (a stored seed that differs is replaced and the held runs judged
    again, :func:`reseed`). Without one, a ledger with no seed takes the
    first identified run's: in a folder several chips share, hand the chip.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative")
    started = time.perf_counter()
    root = Path(root).resolve()
    runs, hint = enumerate_runs(root)
    added = duplicates = errors = zero = deferred = 0
    with HubStore(out, checkpoint_interval=checkpoint_interval) as store:
        root_id = store.register_root(root, hint)
        key = store.conn.execute("SELECT folder_key FROM roots WHERE root_id=?", (root_id,)).fetchone()[0]
        if identity is not None and store.meta("chip_identity") != json_bytes(identity).decode("utf-8"):
            with store.conn:
                reseed(store, identity)
        last_good = store.conn.execute("SELECT * FROM events WHERE error IS NULL ORDER BY ord DESC LIMIT 1").fetchone()
        doc = store.state_at(last_good["eid"]) if last_good else {}
        flat = rules.flatten(doc)
        head_hash = last_good["state_hash"] if last_good else None
        shape_hash = last_good["shape_hash"] if last_good else None
        chip = json.loads(store.meta("chip_identity")) if store.meta("chip_identity") else None
        head = store.head()
        ordinal = int(head["ord"]) if head else 0
        last_order = None
        if head:
            # docs/271: an SM write is a head with no root, run id or
            # experiment; it orders by its own instant alone
            key_row = store.conn.execute("SELECT folder_key FROM roots WHERE root_id=?", (head["root_id"],)).fetchone()
            last_order = (head["t_utc_us"], key_row[0] if key_row else "", head["run_id"] or 0,
                          head["experiment"] or "")
        for index, run in enumerate(runs):
            rel = run.folder.relative_to(root).as_posix()
            if store.conn.execute("SELECT 1 FROM locations WHERE root_id=? AND rel_path=?", (root_id, rel)).fetchone():
                continue
            if limit is not None and added >= limit:
                break
            # docs/275: an unreadable node.json no longer hides the saved pair
            # (docs/270 review P2 #6); the run keeps its state, flagged.
            node_error = run.error
            error, digest = None, None
            raw = None
            try:
                raw = read_pair(run.folder)
                digest = rules.state_hash(*raw)
            except (OSError, ValueError) as exc:
                error = str(exc)
            known = store.conn.execute("SELECT eid FROM events WHERE kind='run' AND t_utc_us=? "
                                       "AND run_id=? AND experiment=? AND state_hash IS ?",
                                       (run.instant, run.run_id, run.experiment, digest)).fetchone()
            if known:
                with store.conn:
                    store.conn.execute("INSERT INTO locations VALUES(?,?,?)", (known[0], root_id, rel))
                    store.set_meta(f"watermark:{root_id}", json_bytes(
                        {"eid": known[0], "t_utc_us": run.instant, "rel_path": rel}).decode("utf-8"))
                duplicates += 1
                continue
            if (error is not None or node_error is not None) and index == len(runs) - 1:
                # The newest discovered folder may still be in flight: node.json
                # written, quam_state not yet saved. A committed location is never
                # revisited, so recording it now would freeze an error event and move
                # its changes onto the next run. Leave it for a build that also sees a
                # later run; only then is a stateless run final (review, docs/270).
                deferred += 1
                continue
            order = (run.instant, key, run.run_id, run.experiment)
            if last_order is not None and order < last_order:
                raise ValueError("unknown run precedes ledger head (a run, or an SM write recorded after it); "
                                 "the in-SM sync inserts late runs (hub_sync, docs/275) -- this offline "
                                 "builder appends only: build all roots in canonical order or rebuild offline")
            flags = TIME_ASSUMED if run.quality in ("assumed_local", "mtime") else 0
            if node_error is not None:
                flags |= NODE_UNREADABLE
            changes, next_doc, next_flat, next_shape = [], doc, flat, shape_hash
            if error is None and digest != head_hash:
                try:
                    next_doc, next_flat, id_flags, chip = parse_state(raw, run.folder, chip)
                    changes = rules.diff(flat, next_flat)
                    next_shape = None
                    flags |= id_flags
                except (OSError, ValueError, TypeError) as exc:
                    error = str(exc)
                    next_doc, next_flat, next_shape, changes = doc, flat, shape_hash, []
            elif error is None:
                zero += 1
                flags |= last_good["flags"] & CHIP_UNCERTAIN if last_good else 0
            if error is not None:
                flags |= CHIP_UNCERTAIN
                errors += 1
            elif digest != head_hash and store.conn.execute(
                    "SELECT 1 FROM events WHERE state_hash=? AND error IS NULL LIMIT 1", (digest,)).fetchone():
                flags |= REVERTS_TO_EARLIER
            ordinal += 1
            event = event_fields(run, root_id=root_id, rel=rel, hint=hint, digest=digest, base_hash=head_hash,
                                 flags=flags, error=error, src="offline_archive")
            event["ord"] = ordinal
            # Chip metadata participates in the same commit as its first event.
            if chip:
                store.set_meta("chip_identity", json_bytes(chip).decode("utf-8"))
            eid = store.append(event, changes, next_doc, next_flat, shape_hash=next_shape,
                               proven=_proven(run.node, changes, next_flat))
            if error is None:
                doc, flat, head_hash = next_doc, next_flat, digest
                last_good = store.event(eid)
                shape_hash = last_good["shape_hash"]
            added += 1
            last_order = order
            if progress and (added % 100 == 0):
                progress(added, len(runs))
        totals = dict(events=store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                      change_rows=store.conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0])
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return dict(discovered=len(runs), added=added, extra_locations=duplicates, errors=errors, deferred=deferred,
                raw_zero_change=zero, offset_hint=hint, seconds=round(time.perf_counter() - started, 3), **totals)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--chip", type=Path, help="the chip's quam_state folder (state.json + wiring.json): "
                        "runs are judged against it, not against the first run found")
    args = parser.parse_args(argv)
    identity = None
    if args.chip is not None:
        state, wiring = (json.loads(_read_shared(args.chip / name)) for name in ("state.json", "wiring.json"))
        identity = identity_of(state, wiring, args.chip)
        if identity is None:
            parser.error("--chip names no chip identity (no name, controller or qubit)")
    result = build(args.root, args.out, limit=args.limit, identity=identity)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
