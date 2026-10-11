"""The A.6 operations, each through the REAL Flask app on real files.

``Rig`` owns one chip: a live folder in ``tmp``, an app built with
``create_app(testing=True, instance_path=tmp/_inst)``, two windows (two
``test_client`` on the one app -- a second window shares the server context),
and the oracle :class:`~tests.state_model.oracle.Model`.

Every op is ``rig.run(name, args)``: deterministic given ``args`` (all random
choices are drawn by :func:`propose` and recorded), so a red sequence replays
from its op list alone. An op performs the requests, reads what SM did, moves
the model, and returns a :class:`Step` the invariant checks read.

Live writes from "outside" are tmp + ``os.replace`` (an experiment's atomic
save); torn and vanished live files are written the same way the A.3 table
names them.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.state_model import oracle as O

AGENT = {"X-SM-Agent": "claude"}
JSONH = {"Accept": "application/json"}
HX = {"HX-Request": "true"}

_CNT = re.compile(r'data-change-count="(\d+)"')
_SIG = re.compile(r'data-change-sig="([0-9a-f]*)"')
_TFX_SIG = re.compile(r'class="tfx-card[^"]*"[^>]*data-sig="([^"]*)"')
_TFX_PATH = re.compile(r'class="tfx-pick"[^>]*data-path="([^"]+)"')

#: leaf names the generator edits (numeric, user-tunable calibration values)
EDIT_LEAVES = ("T1", "T2ramsey", "T2echo", "chi", "f_01", "joint_offset", "amplitude",
               "alpha", "threshold", "detuning", "phase_shift_control", "phase_shift_target",
               "pump_amplitude", "time_of_flight", "depletion_time", "flat_length",
               "neg_offset_v", "b_over_a")


@dataclass
class Step:
    name: str
    args: dict
    status: int | None = None
    obs: str = ""                 # what SM did, classified by the op
    exp: O.Outcome | None = None  # what the model says it must do
    live_writer: bool = False     # the op is a door that may write live
    outside: bool = False         # the harness itself wrote the live files
    refusal: bool = False         # a refused press: nothing may change (docs/255)
    notes: list = field(default_factory=list)
    observations: list = field(default_factory=list)   # facts for the report, not failures
    skip_status: bool = False


def merged(state: dict, wiring: dict) -> dict:
    out = copy.deepcopy(state)
    out.update(copy.deepcopy(wiring))
    return out


def sha_pair(folder: Path) -> tuple:
    out = []
    for n in ("state.json", "wiring.json"):
        try:
            out.append(hashlib.sha256((folder / n).read_bytes()).hexdigest())
        except OSError:
            out.append(None)
    return tuple(out)


def read_doc(folder: Path) -> Any:
    try:
        sb = (folder / "state.json").read_bytes()
        wb = (folder / "wiring.json").read_bytes()
    except FileNotFoundError:
        return O.GONE
    except OSError:
        return O.UNREADABLE
    try:
        return merged(json.loads(sb), json.loads(wb))
    except ValueError:
        return O.UNREADABLE


def resolve_pointer(doc: dict, holder_path: str, ptr: str) -> str | None:
    """The dot path a QUAM pointer at leaf ``holder_path`` names (``#/abs``,
    ``#./x`` relative to the holding dict, ``#../x`` to its parent)."""
    if not isinstance(ptr, str) or not ptr.startswith("#"):
        return None
    hold = O.segs(holder_path)[:-1]          # the dict holding the leaf
    if ptr.startswith("#/"):
        segs = [s for s in ptr[2:].split("/") if s]
    elif ptr.startswith("#./"):
        segs = hold + [s for s in ptr[3:].split("/") if s]
    elif ptr.startswith("#../"):
        segs = hold[:-1] + [s for s in ptr[4:].split("/") if s]
    else:
        return None
    path = ".".join(segs)
    return path if O.get(doc, path) is not O.MISSING else None


def resolve_chain(doc: dict, path: str, hops: int = 4) -> str | None:
    for _ in range(hops):
        v = O.get(doc, path)
        if isinstance(v, str) and v.startswith("#"):
            nxt = resolve_pointer(doc, path, v)
            if nxt is None:
                return None
            path = nxt
        else:
            return path
    return None


class Rig:
    """One chip, one app, two windows, one model."""

    def __init__(self, tmp: Path, state: dict, wiring: dict, *, label: str = "chip"):
        from quam_state_manager.core import hub
        self.tmp = Path(tmp)
        self.label = label
        self.live = self.tmp / "chips" / "live"
        self.live.mkdir(parents=True, exist_ok=True)
        self.wiring_keys = set(wiring)
        self._write_files(self.live, state, wiring)
        self.inst = self.tmp / "_inst"
        hub.set_inline(True)
        self._boot()
        self.chip_dir0 = self.chip_dir()
        self.model = O.Model(merged(state, wiring))
        self.seen: dict[str, tuple[int, str]] = {"a": (0, O.change_sig([])), "b": (0, O.change_sig([]))}
        self.editable = self._editable_paths()
        self.pulses = self._pulse_paths()
        self.mirrors = self._mirror_paths()
        self.fsp_paths = sorted(p for p in O.leaves(self.model.W)
                                if p.endswith(".full_scale_power_dbm")
                                and isinstance(O.get(self.model.W, p), (int, float)))
        self.live_by_harness = False      # last live write used the harness serializer
        self.stale_live_hash: str | None = None
        self.typed: list[tuple[str, Any]] = []
        self.name_seq = 0
        self.step_no = 0
        self.saved_unit_ids: list[str] = []
        self.hidden_mtimes = None         # SU-01: a rewrite SM cannot see by mtime
        self.pending_saved: list[str] = []  # units saved since the content last reached live
        self.blocked_saves = 0            # outside replaces that met a sharing violation
        self.landed_ids: list[str] = []

    # -- boot / files ------------------------------------------------------
    def _boot(self):
        from quam_state_manager.web.app import create_app
        self.app = create_app(testing=True, instance_path=str(self.inst))
        self.app.config["HUB_SYNC_ON_OPEN"] = False
        self.ca = self.app.test_client()
        self.cb = self.app.test_client()
        r = self.ca.post("/load", data={"folder": str(self.live)}, follow_redirects=True)
        assert r.status_code in (200, 302), r.get_data(as_text=True)[:300]
        self.boot_html = r.get_data(as_text=True)
        self.token = self.ca.get("/chip/active-token").get_json().get("token") or ""

    def _write_files(self, folder: Path, state: dict, wiring: dict, *, indent=None):
        folder.mkdir(parents=True, exist_ok=True)
        for name, doc in (("state.json", state), ("wiring.json", wiring)):
            tmp = folder / (name + ".tmp_outside")
            tmp.write_text(json.dumps(doc, indent=indent), encoding="utf-8")
            self._replace(tmp, folder / name)

    def _replace(self, src: Path, dst: Path):
        """os.replace as an experiment's save does it; a sharing violation
        (someone holds the file without FILE_SHARE_DELETE) is retried and
        counted -- docs/28: SM must never block that save."""
        for i in range(40):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                self.blocked_saves += 1
                time.sleep(0.05)
        os.replace(src, dst)

    def split(self, doc: dict) -> tuple[dict, dict]:
        st = {k: v for k, v in doc.items() if k not in self.wiring_keys}
        wi = {k: v for k, v in doc.items() if k in self.wiring_keys}
        return st, wi

    # -- SM facts ------------------------------------------------------------
    def ctx(self) -> dict:
        for c in self.app.config["contexts"].values():
            if c.get("type") == "quam":
                return c
        raise AssertionError("no chip context")

    def sm_W(self) -> dict:
        st = self.ctx()["store"]
        with st._lock:
            return merged(st.state, st.wiring)

    def sm_Ws(self) -> Any:
        return read_doc(Path(self.ctx()["working_copy"].working_folder))

    def sm_L(self) -> Any:
        return read_doc(self.live)

    def sm_log(self) -> list[O.Entry]:
        st = self.ctx()["store"]
        with st._lock:
            return [O.Entry(e.dot_path, copy.deepcopy(e.old_value), copy.deepcopy(e.new_value),
                            gid=e.group_id, created=bool(e.created), deleted=bool(e.deleted))
                    for e in st.change_log]

    def sidecar(self) -> Path:
        from quam_state_manager.core import undo_journal
        return undo_journal.sidecar_path(self.app.instance_path, self.ctx()["path"])

    def journal(self) -> tuple[list[dict], int]:
        from quam_state_manager.core import undo_journal
        return undo_journal.load_state(self.sidecar())

    def chip_dir(self) -> Path:
        from quam_state_manager.web import routes
        with self.app.app_context():
            return Path(routes._hub_chip_dir(self.ctx()["path"]))

    def hub_lines(self) -> list[dict]:
        from quam_state_manager.core import hub
        return [o for _, _, o in hub.Hub.for_chip(self.chip_dir()).read()]

    def live_hash(self) -> str | None:
        from quam_state_manager.core import working_copy
        try:
            return working_copy.live_content_hash(self.ctx()["working_copy"])
        except Exception:  # noqa: BLE001
            return None

    def tray(self, client=None) -> tuple[int, str]:
        r = (client or self.ca).get("/state/tray")
        html = r.get_data(as_text=True)
        return self._tray_of(html)

    @staticmethod
    def _tray_of(html: str) -> tuple[int, str] | None:
        c, s = _CNT.search(html or ""), _SIG.search(html or "")
        if not c or not s:
            return None
        return int(c.group(1)), s.group(1)

    def _saw(self, w: str, html: str | None):
        t = self._tray_of(html or "")
        if t is not None:
            self.seen[w] = t

    def status(self) -> str | None:
        body = None
        for _ in range(3):
            body = self.ca.get("/state/drift").get_json() or {}
        sync = body.get("sync") or {}
        return sync.get("state")

    def snapshots(self) -> list[str]:
        hm = self.app.config["history_manager"]
        snaps = hm.list_snapshots(self.ctx()["path"])
        return [s.timestamp if hasattr(s, "timestamp") else s["timestamp"] for s in snaps]

    def snapshot_doc(self, ts: str) -> dict:
        hm = self.app.config["history_manager"]
        st = hm.load_snapshot(self.ctx()["path"], ts)
        return merged(st.state, st.wiring)

    def client(self, w: str):
        return self.ca if w == "a" else self.cb

    # -- the chip's inventory -------------------------------------------------
    def _editable_paths(self) -> list[str]:
        out = []
        for p, v in O.leaves(self.model.W).items():
            if p.startswith(("ports.", "wiring.", "network.")) or ".ports." in p:
                continue
            if p.rsplit(".", 1)[-1] not in EDIT_LEAVES:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            if isinstance(v, float) and not math.isfinite(v):
                continue
            if ".fidelity." in p or ".extras." in p:
                continue
            out.append(p)
        return sorted(out)

    def _pulse_paths(self) -> list[str]:
        out = []
        for p, v in O.leaves(self.model.W).items():
            if (p.endswith(".__class__") and isinstance(v, str) and ".operations." in p
                    and (v.endswith("Pulse") or "pulses." in v)):
                out.append(p[: -len(".__class__")])
        return sorted(out)

    def _mirror_paths(self) -> list[tuple[str, str]]:
        """(pulse field path, resolved target) for pulse fields that are
        pointers to a numeric leaf elsewhere."""
        out = []
        for pulse in self.pulses:
            body = O.get(self.model.W, pulse)
            if not isinstance(body, dict):
                continue
            for k, v in body.items():
                if isinstance(v, str) and v.startswith("#") and k not in ("length",) or (
                        k == "length" and isinstance(v, str) and v.startswith("#../")):
                    tgt = resolve_chain(self.model.W, f"{pulse}.{k}")
                    if tgt and isinstance(O.get(self.model.W, tgt), (int, float)) \
                            and not isinstance(O.get(self.model.W, tgt), bool):
                        out.append((f"{pulse}.{k}", tgt))
        return sorted(out)

    # -- run ---------------------------------------------------------------------
    def run(self, name: str, args: dict) -> Step:
        self.step_no += 1
        fn = getattr(self, "op_" + name.lower())
        step = fn(**args)
        step.name, step.args = name, args
        return step

    # ===================================================================
    # staging doors
    # ===================================================================
    def _expect_set(self, path: str, value: Any, gid=None) -> O.Entry:
        return O.Entry(path, copy.deepcopy(O.get(self.model.W, path)), value, gid=gid)

    def _adopt_new_entries(self, before_len: int, step: Step, expect: list[O.Entry] | None):
        log = self.sm_log()
        new = log[before_len:]
        if len(log) < before_len:
            step.notes.append(("I4", f"the log shrank from {before_len} to {len(log)} on a staging op"))
            return
        if expect is not None:
            got = [(e.path, e.created, e.deleted) for e in new]
            want = [(e.path, e.created, e.deleted) for e in expect]
            if got != want:
                step.notes.append(("I4", f"staged entries {got} != expected {want}"))
            else:
                for g, w in zip(new, expect):
                    if not (O._eq(g.new, w.new) or g.deleted) or not (O._eq(g.old, w.old) or g.created):
                        step.notes.append(("I7", f"{g.path}: staged old/new {g.old!r}->{g.new!r} "
                                                 f"!= expected {w.old!r}->{w.new!r}"))
        self.model.stage_entries(new)

    def op_edit_cell(self, path: str, text: str, value: Any, window: str = "a", agent: bool = False):
        st = Step("", {})
        c = self.client(window)
        n0 = len(self.sm_log())
        h = dict(AGENT) if agent else {}
        r = c.post("/field/edit", data={"dot_path": path, "value": text, "expect_chip": self.token},
                   headers=h)
        st.status = r.status_code
        body = r.get_json(silent=True) or {}
        if r.status_code == 200 and body.get("ok"):
            st.obs = "staged"
            self._saw(window, body.get("tray_html"))
            self._adopt_new_entries(n0, st, [self._expect_set(path, value)])
            self.typed.append((path, value))
        else:
            st.obs = "refused"
            st.refusal = True
            st.notes.append(("I7", f"a valid edit of {path}={text} was refused: {r.status_code} "
                                   f"{(body.get('error') or r.get_data(as_text=True))[:200]}"))
        st.exp = O.Outcome("staged")
        self._after_stage(st)
        return st

    def op_edit_batch(self, rows: list, window: str = "a", independent: bool = False):
        st = Step("", {})
        c = self.client(window)
        n0 = len(self.sm_log())
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": p, "value": t} for p, t, _v in rows],
            "independent": independent, "expect_chip": self.token})
        st.status = r.status_code
        body = r.get_json(silent=True) or {}
        if r.status_code == 200 and body.get("ok"):
            st.obs = "staged"
            self._saw(window, body.get("tray_html"))
            want = []
            shadow = copy.deepcopy(self.model.W)
            for p, _t, v in rows:
                want.append(O.Entry(p, copy.deepcopy(O.get(shadow, p)), v))
                O.put(shadow, p, v)
            self._adopt_new_entries(n0, st, want)
            new = self.sm_log()[n0:]
            gids = {e.gid for e in new}
            if len(rows) > 1 and (len(gids) != 1 or None in gids):
                st.notes.append(("I4", f"a {len(rows)}-row batch is not one group: {gids}"))
            self.typed.extend((p, v) for p, _t, v in rows)
        else:
            st.obs = "refused"
            st.refusal = True
            st.notes.append(("I7", f"a valid batch was refused: {r.status_code} {str(body)[:300]}"))
        st.exp = O.Outcome("staged")
        self._after_stage(st)
        return st

    def op_edit_invalid(self, path: str, text: str, kind: str):
        """Values a numeric cell must refuse; a refusal changes nothing."""
        st = Step("", {})
        n0 = len(self.sm_log())
        data = {"dot_path": path, "value": text, "expect_chip": self.token}
        if kind == "token":
            data["expect_chip"] = "f" * 16
        r = self.ca.post("/field/edit", data=data)
        st.status = r.status_code
        body = r.get_json(silent=True) or {}
        if r.status_code == 200 and body.get("ok"):
            new = self.sm_log()[n0:]
            vals = [(e.path, e.new) for e in new]
            st.obs = "staged"
            if kind in ("text", "list", "class", "token"):
                st.notes.append(("I4", f"an invalid value {text!r} ({kind}) into {path} was ACCEPTED: {vals!r}"))
            else:
                st.observations.append(f"{kind}: {text!r} into {path} accepted as {vals!r}")
                for _p, v in vals:
                    if isinstance(v, float) and not math.isfinite(v):
                        st.notes.append(("I11", f"a non-finite number was staged for {path}: {v!r}"))
            self.model.stage_entries(new)
        else:
            st.obs = "refused"
            st.refusal = True
            if kind == "token" and not (r.status_code == 409 and body.get("chip_mismatch")):
                st.notes.append(("I4", f"a foreign chip token was not refused as chip_mismatch: {r.status_code}"))
        st.exp = O.Outcome("refused" if kind in ("text", "list", "class", "token") else st.obs)
        return st

    def op_edit_mirror(self, field: str, target: str, text: str, value: Any):
        """/pulse/edit value mode on a pointer field writes at the resolved target."""
        st = Step("", {})
        n0 = len(self.sm_log())
        pulse = field.rsplit(".", 1)[0]
        r = self.ca.post("/pulse/edit", data={"path": pulse, "dot_path": field,
                                              "mode": "value", "value": text})
        st.status = r.status_code
        if r.status_code == 200:
            new = self.sm_log()[n0:]
            st.obs = "staged" if new else "noop"
            self._saw("a", r.get_data(as_text=True))
            self._adopt_new_entries(n0, st, [self._expect_set(target, value)] if new else [])
            if new:
                self.typed.append((target, value))
                ptr_now = O.get(self.sm_W(), field)
                if not (isinstance(ptr_now, str) and ptr_now.startswith("#")):
                    st.notes.append(("I11", f"the pointer at {field} was replaced by {ptr_now!r}"))
        else:
            st.obs = "refused"
            st.refusal = True
            st.observations.append(f"mirror edit refused {r.status_code}: {r.get_data(as_text=True)[:200]}")
        st.exp = O.Outcome("staged")
        self._after_stage(st)
        return st

    def op_edit_fsp(self, path: str, value: float, ack: str):
        st = Step("", {})
        n0 = len(self.sm_log())
        r = self.ca.post("/field/edit", data={"dot_path": path, "value": repr(value),
                                              "expect_chip": self.token})
        body = r.get_json(silent=True) or {}
        if r.status_code != 409 or "fsp_compensation" not in body:
            st.notes.append(("I4", f"an FSP edit without an ack was not offered the plan: {r.status_code} {str(body)[:200]}"))
            if r.status_code == 200:
                self.model.stage_entries(self.sm_log()[n0:])
            st.status = r.status_code
            st.exp = O.Outcome("refused")
            return st
        if len(self.sm_log()) != n0:
            st.notes.append(("I4", "the FSP plan offer staged something"))
        if not ack:
            st.status, st.obs, st.refusal = 409, "refused", True
            st.exp = O.Outcome("refused")
            return st
        r = self.ca.post("/field/edit", data={"dot_path": path, "value": repr(value),
                                              "expect_chip": self.token, "fsp_ack": ack})
        st.status = r.status_code
        body = r.get_json(silent=True) or {}
        new = self.sm_log()[n0:]
        if r.status_code != 200 or not new:
            st.obs = "refused"
            st.notes.append(("I7", f"an acknowledged FSP edit was refused: {r.status_code} {str(body)[:200]}"))
            self.model.stage_entries(new)
            st.exp = O.Outcome("staged")
            return st
        st.obs = "staged"
        self._saw("a", body.get("tray_html"))
        old_f = O.get(self.model.W, path)
        fsp = [e for e in new if e.path == path]
        amps = [e for e in new if e.path != path]
        if len(fsp) != 1 or not O._eq(fsp[0].new, value):
            st.notes.append(("I7", f"FSP entry wrong: {[(e.path, e.new) for e in fsp]}"))
        if ack == "solo" and amps:
            st.notes.append(("I4", f"fsp_ack=solo also changed {[e.path for e in amps]}"))
        if ack == "comp":
            k = 10 ** ((float(old_f) - float(value)) / 20.0)
            for e in amps:
                if not isinstance(e.old, (int, float)) or not isinstance(e.new, (int, float)):
                    continue
                if not math.isclose(e.new, e.old * k, rel_tol=1e-9, abs_tol=1e-15):
                    st.notes.append(("I7", f"FSP compensation {e.path}: {e.old}*{k:.6g} != {e.new}"))
            if len(new) > 1 and (len({e.gid for e in new}) != 1 or new[0].gid is None):
                st.notes.append(("I4", f"the FSP bundle is not one group: {[e.gid for e in new]}"))
        self.model.stage_entries(new)
        self.typed.append((path, value))
        st.exp = O.Outcome("staged")
        self._after_stage(st)
        return st

    def op_typefix(self, stale: bool):
        st = Step("", {})
        n0 = len(self.sm_log())
        html = self.ca.get("/type-fix/plan").get_data(as_text=True)
        sig = (_TFX_SIG.search(html) or [None, None])[1] if _TFX_SIG.search(html) else None
        paths = _TFX_PATH.findall(html)
        if not paths:
            st.obs = st.status = "none"
            st.exp = O.Outcome("noop")
            return st
        r = self.ca.post("/type-fix/apply", json={"paths": paths,
                                                  "sig": ("0" * 12) if stale else (sig or "")})
        st.status = r.status_code
        body = r.get_json(silent=True) or {}
        new = self.sm_log()[n0:]
        if stale:
            st.refusal = True
            st.obs = "refused" if r.status_code == 409 and body.get("error_kind") == "stale_plan" else "other"
            if st.obs != "refused" or new:
                st.notes.append(("I4", f"a stale type-fix plan was not refused cleanly: {r.status_code} {str(body)[:200]}"))
            self.model.stage_entries(new)
            st.exp = O.Outcome("refused")
            return st
        st.obs = "staged" if r.status_code == 200 and new else "refused"
        for e in new:
            self.model.typefixed.add(e.path)
            if not isinstance(e.old, str) or isinstance(e.new, str):
                st.notes.append(("I11", f"type-fix {e.path}: {e.old!r} -> {e.new!r}"))
            elif not math.isclose(float(e.old), float(e.new)):
                st.notes.append(("I7", f"type-fix changed the number at {e.path}: {e.old!r} -> {e.new!r}"))
        self.model.stage_entries(new)
        self._saw("a", body.get("tray_html"))
        st.exp = O.Outcome("staged")
        self._after_stage(st)
        return st

    def op_pulse(self, action: str, path: str, new_name: str = "", force: bool = False):
        st = Step("", {})
        n0 = len(self.sm_log())
        W0 = copy.deepcopy(self.model.W)
        if action == "duplicate":
            r = self.ca.post("/api/pulse/duplicate", data={"path": path, "new_name": new_name})
        elif action == "rename":
            r = self.ca.post("/api/pulse/rename", data={"path": path, "new_name": new_name, "retarget": "1"})
        else:
            r = self.ca.post("/api/pulse/delete", data={"path": path, "force": "1" if force else "0"})
        st.status = r.status_code
        new = self.sm_log()[n0:]
        html = r.get_data(as_text=True)
        users = self._referrers(W0, path)
        if r.status_code != 200:
            st.obs = "refused"
            st.refusal = True
            if new:
                st.notes.append(("I4", f"a refused pulse {action} staged {[e.path for e in new]}"))
            if action == "delete" and not force and users:
                pass                                   # used_by 409: the contract
            elif action in ("duplicate", "rename") and O.get(W0, path.rsplit(".", 1)[0] + "." + new_name) is not O.MISSING:
                pass                                   # name collision: the contract
            else:
                st.observations.append(f"pulse {action} {path} refused {r.status_code}: {html[:160]}")
            self.model.stage_entries(new)
            st.exp = O.Outcome("refused")
            return st
        st.obs = "staged" if new else "noop"
        self._saw("a", html)
        self.model.stage_entries(new)
        W1 = self.model.W
        parent = path.rsplit(".", 1)[0]
        if action == "delete":
            if O.get(W1, path) is not O.MISSING:
                st.notes.append(("I7", f"deleted pulse {path} still present"))
            if users and not force:
                st.notes.append(("I4", f"a used pulse {path} was deleted without force (used by {users})"))
        else:
            dst = f"{parent}.{new_name}"
            src_body = O.get(W0, path)
            got = O.get(W1, dst)
            if got is O.MISSING:
                st.notes.append(("I7", f"pulse {action}: {dst} missing"))
            elif not _same_mod_pointers(src_body, got):
                st.notes.append(("I7", f"pulse {action}: {dst} differs from the source beyond pointers"))
            if action == "rename":
                if O.get(W1, path) is not O.MISSING:
                    st.notes.append(("I7", f"renamed pulse {path} still present"))
                if len({e.gid for e in new}) != 1 or new[0].gid is None:
                    st.notes.append(("I4", f"rename is not one group: {[e.gid for e in new]}"))
                for holder in users:
                    if O.get(W1, holder) is O.MISSING:
                        continue
                    tgt = (resolve_pointer(W1, holder, O.get(W1, holder))
                           if isinstance(O.get(W1, holder), str) else None)
                    if tgt is None or not (tgt == dst or tgt.startswith(dst + ".")):
                        st.notes.append(("I7", f"rename left referrer {holder}={O.get(W1, holder)!r} "
                                               f"not pointing into {dst}"))
        st.exp = O.Outcome("staged")
        self._after_stage(st)
        return st

    @staticmethod
    def _referrers(doc: dict, pulse: str) -> list[str]:
        out = []
        for p, v in O.leaves(doc).items():
            if isinstance(v, str) and v.startswith("#") and not p.startswith(pulse + "."):
                tgt = resolve_pointer(doc, p, v)
                if tgt and (tgt == pulse or tgt.startswith(pulse + ".")):
                    out.append(p)
        return out

    def _after_stage(self, st: Step):
        """An armed push session flushes every staged edit (auto-apply.js)."""
        if self.model.push_armed and st.obs == "staged":
            self._flush(st)

    # ===================================================================
    # tray: undo / redo / discard
    # ===================================================================
    def op_undo(self, n: int = 1, window: str = "a", stale_sig: bool = False):
        st = Step("", {})
        st.live_writer = True
        c = self.client(window)
        sig = ("0" * 12) if stale_sig else self.seen[window][1]
        data = {"n": str(n), "expect_chip": self.token, "expect_sig": sig}
        r = c.post("/undo", data=data)
        st.status = r.status_code
        cr = _trigger(r).get("cellsReverted") or {}
        m = self.model
        if m.T and sig != m.sig():
            # the presser's tray is not the server's: refused, nothing popped
            st.exp = O.Outcome("refused", "undo-foreign")
            st.refusal = True
        else:
            st.exp = m.undo_press(n)
        st.obs = ("refused" if r.status_code == 409 else
                  "live" if cr.get("live") else
                  "rolled_back" if str(cr.get("message", "")).startswith("Not undone") else
                  "staged" if cr.get("entries") or str(cr.get("message", "")).startswith("Undone") else "noop")
        if st.exp.kind == "tray" and st.obs == "staged":
            st.obs = "tray"
        self._saw(window, r.get_data(as_text=True))
        self._adopt_gids()
        return st

    def _adopt_gids(self):
        """The model names a staged journal step by its own key, SM by the
        unit id; a redo re-mints group ids. Align gids position by position
        (the entries themselves are compared by the invariants)."""
        log = self.sm_log()
        if len(log) != len(self.model.T):
            return
        for mine, theirs in zip(self.model.T, log):
            if mine.path == theirs.path:
                mine.gid = theirs.gid

    def op_redo(self, n: int = 1, window: str = "a"):
        st = Step("", {})
        st.live_writer = True
        r = self.client(window).post("/redo", data={"n": str(n), "expect_chip": self.token})
        st.status = r.status_code
        cr = _trigger(r).get("cellsReverted") or {}
        st.exp = self.model.redo_press(n)
        msg = str(cr.get("message", ""))
        st.obs = ("live" if cr.get("live") else
                  "unstaged" if "un-staged" in msg else
                  "tray" if msg.startswith("Redone") else
                  "refused" if r.status_code == 409 or msg.startswith("Not redone") else "noop")
        self._saw(window, r.get_data(as_text=True))
        self._adopt_gids()
        return st

    def op_discard(self, which: str = "last"):
        st = Step("", {})
        m = self.model
        if not m.T:
            st.obs, st.exp = "noop", O.Outcome("noop")
            return st
        idx = len(m.T) - 1 if which == "last" else 0
        path = m.T[idx].path
        bundle = [i for i, e in enumerate(m.T) if m.T[idx].gid and e.gid == m.T[idx].gid
                  and self._is_fsp_bundle(m.T[idx].gid)]
        r = self.ca.post("/discard", data={"index": str(idx), "expect_path": path})
        st.status = r.status_code
        if r.status_code == 200 and _trigger(r):
            st.obs = "discarded"
            if bundle:
                for i in sorted(bundle, reverse=True):
                    m.discard(i)
            else:
                m.discard(idx)
        else:
            st.obs = "refused"
        st.exp = O.Outcome("discarded")
        self._saw("a", r.get_data(as_text=True))
        return st

    def _is_fsp_bundle(self, gid) -> bool:
        return any(e.path.endswith(".full_scale_power_dbm") for e in self.model.T if e.gid == gid)

    def op_discard_all(self):
        st = Step("", {})
        r = self.ca.post("/discard_all")
        st.status = r.status_code
        st.obs = "discarded"
        self.model.discard_all()
        self._saw("a", r.get_data(as_text=True))
        st.exp = O.Outcome("discarded")
        return st

    # ===================================================================
    # working-copy writers
    # ===================================================================
    def op_save(self):
        st = Step("", {})
        u0 = {u.get("id") for u in self.journal()[0]}
        r = self.ca.post("/save")
        st.status = r.status_code
        st.exp = self.model.save()
        st.obs = "saved" if "Saved" in r.get_data(as_text=True) else "noop"
        if self.model.push_armed and st.obs == "saved":
            self._flush(st)
        self.saved_unit_ids += [u.get("id") for u in self.journal()[0] if u.get("id") not in u0]
        self.pending_saved += [u.get("id") for u in self.journal()[0] if u.get("id") not in u0]
        self._saw("a", r.get_data(as_text=True))
        return st

    def op_take_live(self, force: bool, mode: str = "discard"):
        st = Step("", {})
        data = {"mode": mode}
        if force:
            data["force"] = "1"
        data["seen_changes"], data["seen_sig"] = str(self.seen["a"][0]), self.seen["a"][1]
        r = self.ca.post("/state/sync", data=data)
        st.status = r.status_code
        body = r.get_json(silent=True) or {}
        m = self.model
        unseen = bool(m.T) and self.seen["a"][1] != m.sig()
        if unseen and mode == "discard":
            st.exp = O.Outcome("refused", "unseen_changes")
        elif mode == "discard":
            st.exp = m.take_live(force=force)
        else:
            st.exp = m.reapply(force=force)
        st.obs = {"ok": "pulled", "needs_confirm": "needs_confirm"}.get(body.get("status"), body.get("status") or str(r.status_code))
        if st.exp.kind == "reapplied" and st.obs == "pulled":
            st.obs = "reapplied"
            self._adopt_gids()
        if st.exp.kind in ("refused", "needs_confirm"):
            st.refusal = True
        self._saw("a", body.get("tray_html"))
        return st

    def op_bring_back(self):
        st = Step("", {})
        n0 = len(self.sm_log())
        k0 = len(self.model.T)
        r = self.ca.post("/state/take-live/restore")
        st.status = r.status_code
        trig = _trigger(r)
        msg = str((trig.get("syncToast") or {}).get("message", ""))
        st.obs = "brought_back" if msg.startswith("Brought back") else "noop"
        st.exp = self.model.bring_back()
        if st.exp.kind == "brought_back":
            # the replay's grouping is SM's; the values are the model's
            log = self.sm_log()[n0:]
            mine = self.model.T[k0:]
            want = {e.path: e.new for e in mine}
            got = {e.path: e.new for e in log}
            if set(want) != set(got) or any(not O._eq(want[p], got[p]) for p in want):
                st.notes.append(("I7", f"Bring back re-staged {got!r}, expected {want!r}"))
            for a, b in zip(mine, log):
                a.gid = b.gid
        self._saw("a", r.get_data(as_text=True))
        return st

    def op_stage(self, k: int, from_tray: bool = False, force: bool = True):
        st = Step("", {})
        m = self.model
        if from_tray:
            la = self.ctx().get("last_apply") or {}
            ts = la.get("pre_ts")
        else:
            snaps = self.snapshots()
            ts = snaps[min(k, len(snaps) - 1)] if snaps else None
        if not ts:
            st.obs, st.exp = "noop", O.Outcome("noop")
            return st
        doc = self.snapshot_doc(ts)
        if not from_tray:
            ex_now = O.get(m.W, "extras")
            if isinstance(ex_now, dict) and isinstance(doc.get("extras"), dict):
                for key in ("chip_name", "data_folder"):
                    if key in ex_now:
                        doc["extras"][key] = copy.deepcopy(ex_now[key])
        q = "?force=1" if force else ""
        q += ("&" if q else "?") + "from=tray" if from_tray else ""
        r = self.ca.post(f"/state-history/{ts}/stage{q}")
        st.status = r.status_code
        st.exp = m.stage(doc, force=force)
        st.obs = "staged_doc" if r.status_code == 200 else ("needs_confirm" if r.status_code == 409 else str(r.status_code))
        if st.exp.kind == "needs_confirm":
            st.refusal = True
        if m.push_armed and st.obs == "staged_doc":
            self._flush(st)                       # the session pushes a dirty working copy too
        st.args_ts = ts
        return st

    # ===================================================================
    # live writers
    # ===================================================================
    def op_apply(self, window: str = "a", seen: str = "fresh", force: bool = False,
                 expect: str = "fresh", json_accept: bool = False, agent: bool = False):
        st = Step("", {})
        st.live_writer = True
        c = self.client(window)
        data: dict = {}
        if seen != "none":
            cnt, sig = self.seen[window]
            data["seen_changes"], data["seen_sig"] = str(cnt), sig
        if seen == "ack":
            data["ack_unseen"] = "1"
        sent = None
        if force:
            data["force"] = "1"
            sent = self.live_hash() if expect == "fresh" else (self.stale_live_hash or "0" * 40)
            if sent:
                data["expect_live_hash"] = sent
        headers = {}
        if json_accept or agent:
            headers.update(JSONH)
        if agent:
            headers.update(AGENT)
        m = self.model
        unseen = (seen in ("fresh", "stale") and self.seen[window][1] != m.sig())
        if agent and any(getattr(e, "actor", "human") == "human" for e in m.T):
            pass
        fresh = None
        if force:
            now = self.live_hash()
            fresh = (sent == now) or now is None
        r = c.post("/state/apply-to-live", data=data, headers=headers)
        st.status = r.status_code
        html = r.get_data(as_text=True)
        body = r.get_json(silent=True) or {}
        gone = m.L is O.GONE
        st.exp = m.apply(force=force, expect_fresh=fresh, unseen=unseen, html=not (json_accept or agent))
        st.obs = _classify_apply(r, html, body)
        if st.exp.kind == "refused":
            st.refusal = True
        if gone and not force and st.obs == "error500":
            # SU-02: the live state file is gone; the press answers 500, after
            # its save when edits were pending (they move into the working copy)
            st.notes.append(("K:SU-02", "Apply with the live state file missing answered HTTP 500"))
            if st.exp.reason == "live unreadable":
                m.save()
                st.exp = O.Outcome("saved")
            st.refusal = False
        self._saw(window, html if not body else body.get("tray_html"))
        self.stale_live_hash = self.live_hash()
        return st

    def op_merge(self, picks_live: list | None = None, ack: bool = False):
        st = Step("", {})
        st.live_writer = True
        picks = {p: "live" for p in (picks_live or [])}
        data = {"mode": "apply", "check_collisions": "1", "expect_chip": self.token,
                "seen_changes": str(self.seen["a"][0]), "seen_sig": self.seen["a"][1]}
        if picks:
            data["picks"] = json.dumps(picks)
        if ack:
            data["ack_collision"] = "1"
        m = self.model
        unseen = self.seen["a"][1] != m.sig()
        n_units0 = len(self.journal()[0])
        r = self.ca.post("/state/sync", data=data)
        st.status = r.status_code
        body = r.get_json(silent=True) or {}
        st.exp = m.merge_apply(picks=picks, check=True, ack=ack, unseen=unseen)
        s = body.get("status")
        st.obs = ("landed" if s == "ok" and body.get("hub_event") else
                  "pulled_only" if s == "ok" else
                  "collision" if s == "collision" else
                  "refused" if s in ("conflict", "unseen_changes", "error") or r.status_code >= 400 else str(s))
        if st.exp.kind in ("refused", "collision"):
            st.refusal = True
        if st.exp.kind == "landed":
            self._adopt_gids()
            self._adopt_journal_tail(n_units0)
        self._saw("a", body.get("tray_html"))
        return st

    def _adopt_journal_tail(self, n0: int):
        """Replayed edits are grouped by SM's replay map; when the units this
        press appended carry the same leaf changes as the model's, take SM's
        unit boundaries (the content is what the invariants judge)."""
        from tests.state_model.invariants import _unit_effect
        units, _cur = self.journal()
        m = self.model
        if len(units) < n0 or len(m.J) < n0:
            return
        theirs = {}
        for u in units[n0:]:
            for k, v in _unit_effect(u).items():
                theirs[k] = (theirs[k][0], v[1]) if k in theirs else v
        mine = {}
        for u in m.J[n0:]:
            for k, v in u.effect().items():
                mine[k] = (mine[k][0], v[1]) if k in mine else v
        same = set(theirs) == set(mine) and all(O._eq(theirs[k][0], mine[k][0]) and O._eq(theirs[k][1], mine[k][1])
                                                for k in mine)
        if same:
            m.J = m.J[:n0] + [O.Unit(entries=[O.Entry(e["path"], e.get("old"), e.get("new"),
                                                      created=bool(e.get("created")),
                                                      deleted=bool(e.get("deleted")))
                                              for e in u.get("entries") or []],
                                     kind="wholesale" if (u.get("meta") or {}).get("wholesale") else "edit")
                              for u in units[n0:]]
            m.cursor = m.pcursor = len(m.J)

    def op_restore_live(self, k: int):
        st = Step("", {})
        st.live_writer = True
        snaps = self.snapshots()
        if not snaps:
            st.obs, st.exp = "noop", O.Outcome("noop")
            return st
        ts = snaps[min(k, len(snaps) - 1)]
        doc = self.snapshot_doc(ts)
        r = self.ca.post(f"/state-history/{ts}/restore-live?force_pending=1&force_align=1")
        st.status = r.status_code
        st.exp = self.model.restore_live(doc, force_pending=True)
        st.obs = "landed" if r.status_code == 200 else ("refused" if r.status_code == 409 else str(r.status_code))
        if st.exp.kind == "refused":
            st.refusal = True
        return st

    # ===================================================================
    # sessions / settings
    # ===================================================================
    def op_toggle_undo_live(self, enabled: bool, agent: bool = False):
        st = Step("", {})
        r = self.ca.post("/settings/undo-live", data={"enabled": "1" if enabled else "0"},
                         headers=AGENT if agent else {})
        st.status = r.status_code
        body = r.get_json(silent=True) or {}
        if r.status_code == 200 and body.get("ok"):
            self.model.undo_live = bool(body.get("enabled"))
            st.obs = "set"
            if agent:
                st.observations.append("SUSPECT-AGENT-UNDO-LIVE: an X-SM-Agent request set "
                                       f"'Ctrl+Z writes live' to {enabled} (200)")
        else:
            st.obs = "refused"
        st.exp = O.Outcome(st.obs)
        return st

    def op_arm(self, push: bool, pull: bool):
        st = Step("", {})
        r = self.ca.post("/auto-sync/set", data={"push": "1" if push else "0", "pull": "1" if pull else "0"})
        st.status = r.status_code
        if r.status_code == 200:
            self.model.push_armed, self.model.pull_armed = push, pull
            st.obs = "armed" if (push or pull) else "disarmed"
            if push and _trigger(r).get("showToast"):
                # push withheld (live changed outside SM)
                self.model.push_armed = False
        else:
            st.obs = "refused"
            st.observations.append(f"arm push={push} pull={pull} refused {r.status_code}: "
                                   f"{r.get_data(as_text=True)[:160]}")
        st.exp = O.Outcome(st.obs)
        if self.model.push_armed and (self.model.T or self.model.dirty):
            self._flush(st)
        return st

    def op_disarm(self):
        st = Step("", {})
        r = self.ca.post("/auto-apply/disarm")
        st.status = r.status_code
        self.model.push_armed = self.model.pull_armed = False
        self.ca.post("/auto-sync/set", data={"push": "0", "pull": "0"})
        st.obs = "disarmed"
        st.exp = O.Outcome("disarmed")
        return st

    def _flush(self, st: Step):
        """auto-apply.js: POST /state/apply-to-live, HX headers, no seen_*;
        follow autoSyncMerge -> /state/sync mode=apply check_collisions=1."""
        m = self.model
        st.live_writer = True
        r = self.ca.post("/state/apply-to-live", headers=HX)
        html = r.get_data(as_text=True)
        trig = _trigger(r)
        moved = m.moved()
        if moved and not (m.readable() and O.same(m.L, m.W)):
            if m.pull_armed and m.merge_tries < 3 and not (m.dirty or m.staged):
                m.merge_tries += 1
                st.flush = "stale_merge"
                m.refused_at = O.digest(m.B)
                if "autoSyncMerge" in trig:
                    r2 = self.ca.post("/state/sync", data={"mode": "apply", "expect_chip": self.token,
                                                           "check_collisions": "1"})
                    body = r2.get_json(silent=True) or {}
                    o = m.merge_apply(check=True)
                    if o.kind in ("landed", "converged"):
                        m.merge_tries = 0
                    st.exp = o
                    st.obs = ("landed" if body.get("status") == "ok" and body.get("hub_event") else
                              body.get("status") or str(r2.status_code))
                    if o.kind == "landed":
                        self._adopt_gids()
                else:
                    st.notes.append(("I12", f"an armed stale flush with pull on sent no autoSyncMerge: {list(trig)}"))
                    st.exp = O.Outcome("refused", "stale")
                    st.obs = "refused"
            else:
                o = m.apply(html=True)
                m.push_armed = False
                st.exp = o
                st.obs = _classify_apply(r, html, {})
                if "autoApplyDisarm" not in trig:
                    st.notes.append(("I12", "a refused armed flush did not disarm the session"))
        else:
            o = m.apply(html=True)
            if o.kind in ("landed", "converged"):
                m.merge_tries = 0
            st.exp = o
            st.obs = _classify_apply(r, html, {})
            if st.obs == "noop" and "autoApplyApplied" in trig:
                st.obs = "landed"
        self._saw("a", html)

    def poll_autopull(self) -> Step | None:
        """The 5 s drift poll's Auto-Sync pull, driven the way app.js does."""
        m = self.model
        if not m.pull_armed:
            return None
        d = self.ca.get("/state/drift").get_json() or {}
        if not d.get("auto_pull"):
            return None
        st = Step("AUTOPULL", {})
        st.live_writer = True
        r = self.ca.post("/auto-sync/pull")
        st.status = r.status_code
        trig = _trigger(r)
        if r.status_code == 200 and "autoSyncPulled" in trig:
            st.obs = "pulled"
        elif "autoSyncMergePull" in trig:
            r2 = self.ca.post("/state/sync", data={"mode": "reapply", "expect_chip": self.token})
            body = r2.get_json(silent=True) or {}
            st.obs = "reapplied" if body.get("status") == "ok" else (body.get("status") or str(r2.status_code))
        elif "liveConflict" in trig:
            st.obs = "conflict"
        elif "autoApplyDisarm" in trig:
            st.obs = "disarmed"
            m.push_armed = m.pull_armed = False
        else:
            st.obs = "none"
        # the model
        if not m.moved():
            st.exp = O.Outcome("none")
        elif not m.T and not m.dirty:
            m._adopt_live()
            st.exp = O.Outcome("pulled")
        elif m.dirty:
            st.exp = O.Outcome("conflict")
        elif m.conflicts():
            st.exp = O.Outcome("conflict")
        else:
            st.exp = m.reapply(force=False)
            if st.obs == "reapplied":
                self._adopt_gids()
        self._saw("a", r.get_data(as_text=True))
        if m.push_armed and st.obs in ("reapplied",):
            self._flush(st)
        return st

    # ===================================================================
    # outside the app
    # ===================================================================
    def op_outside(self, kind: str, changes: dict | None = None):
        st = Step("", {})
        st.outside = True
        m = self.model
        cur = self.sm_L()
        base = cur if not isinstance(cur, O._Sentinel) else copy.deepcopy(
            m.L if not isinstance(m.L, O._Sentinel) else m.B)
        if kind in ("atomic", "wiring", "resave", "repair"):
            doc = copy.deepcopy(base)
            for p, v in (changes or {}).items():
                O.put(doc, p, v)
            s, w = self.split(doc)
            if kind == "repair" and not self.live.exists():
                self.live.mkdir(parents=True)
            if kind == "wiring":
                (self.live / "wiring.json.tmp_outside").write_text(json.dumps(w), encoding="utf-8")
                self._replace(self.live / "wiring.json.tmp_outside", self.live / "wiring.json")
            else:
                self._write_files(self.live, s, w, indent=4 if kind == "resave" else None)
            self.live_by_harness = kind != "resave"
            m.outside(doc)
        elif kind == "touch":
            m.touched = True
            if self.live.exists():
                for n in ("state.json", "wiring.json"):
                    p = self.live / n
                    if p.exists():
                        s0 = os.stat(p)
                        os.utime(p, ns=(s0.st_atime_ns, s0.st_mtime_ns + 7_000_000_000))
        elif kind == "samewidth":
            p = self.live / "state.json"
            if not self.live_by_harness or isinstance(cur, O._Sentinel) or not changes:
                st.obs = "skipped"
                st.exp = O.Outcome("noop")
                return st
            doc = copy.deepcopy(cur)
            for path, v in changes.items():
                O.put(doc, path, v)
            s, _w = self.split(doc)
            data = json.dumps(s).encode("utf-8")
            old = os.stat(p)
            if len(data) != old.st_size:
                st.obs = "skipped"
                st.exp = O.Outcome("noop")
                return st
            tmp = self.live / "state.json.tmp_outside"
            tmp.write_bytes(data)
            self._replace(tmp, p)
            os.utime(p, ns=(old.st_atime_ns, old.st_mtime_ns))
            m.outside(doc)
            self.hidden_mtimes = tuple(os.stat(self.live / n).st_mtime_ns
                                       for n in ("state.json", "wiring.json"))
        elif kind == "torn":
            p = self.live / "state.json"
            if p.exists():
                data = p.read_bytes()
                tmp = self.live / "state.json.tmp_outside"
                tmp.write_bytes(data[: max(1, len(data) // 2)])
                self._replace(tmp, p)
                m.outside(O.UNREADABLE)
        elif kind == "delete_state":
            try:
                (self.live / "state.json").unlink()
            except FileNotFoundError:
                pass
            m.outside(O.GONE)
        elif kind == "delete_folder":
            shutil.rmtree(self.live, ignore_errors=True)
            m.outside(O.GONE)
        st.obs = kind
        st.exp = O.Outcome("outside")
        return st

    # ===================================================================
    # misc
    # ===================================================================
    def op_refresh(self, window: str = "b"):
        st = Step("", {})
        t = self.tray(self.client(window))
        if t:
            self.seen[window] = t
        st.obs = st.exp = "refreshed"
        st.exp = O.Outcome("refreshed")
        return st

    def op_app_reload(self):
        st = Step("", {})
        r = self.ca.post("/app/reload", data={"mode": "state"})
        st.status = r.status_code
        body = r.get_json(silent=True) or {}
        st.obs = "reloaded" if body.get("ok") else "failed"
        st.exp = O.Outcome("reloaded")
        return st

    def op_restart(self):
        """A new process on the same instance dir: the ctx dies, the files stay."""
        from quam_state_manager.core import pending_tray
        from quam_state_manager.web import routes
        st = Step("", {})
        # a process restart: the background tray writer lands its sidecar,
        # the module-level chip cache dies with the process
        pending_tray.flush_all()
        routes._quam_cache.clear()
        self._boot()
        m = self.model
        lost = m.restart()
        ctx = self.ctx()
        note = ctx.get("_tray_recovery_notice")
        text = (note.get("text") if isinstance(note, dict) else (note or "")) + self.boot_html
        tray_html = self.ca.get("/state/tray").get_data(as_text=True)
        text += tray_html
        self.seen = {"a": self.tray(self.ca), "b": self.tray(self.cb)}
        st.obs = "restarted"
        st.exp = O.Outcome("restarted")
        st.observations.append(f"restart: dirty={ctx.get('working_dirty')} "
                               f"staged={ctx.get('staged_base')} notice_named={'could not be recovered' in text}")
        if lost:
            # SU-05: the rows are dropped (the live chip moved); I7 holds only
            # if the notice names every one of them
            missing = [p for p in lost if p not in str(text)]
            if missing:
                st.notes.append(("I7", f"restart dropped unapplied edits {missing[:4]} and no notice names them"))
            else:
                st.notes.append(("K:SU-05", f"restart dropped {len(lost)} unapplied edit(s) because the "
                                            "live chip moved; only a notice + a retained file keep them"))
        return st


def _same_mod_pointers(a: Any, b: Any) -> bool:
    """Equal, except pointer strings may be rewritten."""
    if isinstance(a, str) and a.startswith("#") and isinstance(b, str) and b.startswith("#"):
        return True
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) - {"id"} != set(b) - {"id"}:
            return False
        return all(_same_mod_pointers(a[k], b[k]) for k in a if k != "id")
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same_mod_pointers(x, y) for x, y in zip(a, b))
    return O.same(a, b)


def _trigger(r) -> dict:
    raw = r.headers.get("HX-Trigger")
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {k.strip(): True for k in raw.split(",")}


def _classify_apply(r, html: str, body: dict) -> str:
    if r.status_code == 200 and "pending-tray-conflict" not in (html or "") and not body.get("conflict"):
        if ("Applied to the live chip" in (html or "") or body.get("ok")
                or "autoApplyApplied" in (r.headers.get("HX-Trigger") or "")):
            return "landed"
        return "noop"
    if body.get("status") == "unseen_changes":
        return "refused"
    if r.status_code in (200, 409):
        return "refused"
    return f"error{r.status_code}"


# ======================================================================
# the generator
# ======================================================================

def _value_for(cur: Any, rng) -> tuple[str, Any]:
    if isinstance(cur, int) and not isinstance(cur, bool):
        v = max(1, int(cur) + rng.randint(1, 40))
        return str(v), v
    base = float(cur) if isinstance(cur, (int, float)) and cur else 0.1
    v = float(f"{base * rng.uniform(0.6, 1.4):.9g}")
    return repr(v), v


WEIGHTS = (
    ("EDIT_CELL", 16), ("EDIT_BATCH", 6), ("FILLDOWN", 3), ("EDIT_INVALID", 3),
    ("EDIT_MIRROR", 3), ("EDIT_FSP", 2), ("TYPEFIX", 1), ("PULSE", 3), ("AGENT_EDIT", 1),
    ("APPLY", 12), ("KEEP_MINE", 3), ("MERGE", 4),
    ("UNDO", 10), ("REDO", 4), ("DISCARD", 2),
    ("OUTSIDE", 10), ("OUTSIDE_ODD", 4),
    ("SAVE", 3), ("TAKE_LIVE", 3), ("REAPPLY", 1), ("BRING_BACK", 2), ("STAGE", 2),
    ("REVERT_LAST", 1), ("RESTORE_LIVE", 1),
    ("TOGGLE_UNDO_LIVE", 1), ("ARM", 1), ("DISARM", 1), ("REFRESH_B", 2), ("EDIT_B", 2),
    ("APP_RELOAD", 1), ("RESTART", 1),
)


def propose(rig: Rig, rng) -> tuple[str, dict]:
    m = rig.model
    names = [n for n, _ in WEIGHTS]
    weights = [w for _, w in WEIGHTS]
    if not m.readable() and rng.random() < 0.5:
        return "OUTSIDE", {"kind": "repair", "changes": {}}
    for _ in range(50):
        name = rng.choices(names, weights)[0]
        got = _args_for(rig, name, rng)
        if got is not None:
            return got
    return "REFRESH", {"window": "b"}


def _pick_path(rig: Rig, rng, prefer_tray: float = 0.0) -> str | None:
    m = rig.model
    if m.T and rng.random() < prefer_tray:
        cands = [p for p in m.fields() if p in rig.editable
                 and isinstance(O.get(m.W, p), (int, float)) and not isinstance(O.get(m.W, p), bool)]
        if cands:
            return rng.choice(cands)
    cands = [p for p in rig.editable if isinstance(O.get(m.W, p), (int, float))
             and not isinstance(O.get(m.W, p), bool)]
    return rng.choice(cands) if cands else None


def _args_for(rig: Rig, name: str, rng):
    m = rig.model
    if name in ("EDIT_CELL", "AGENT_EDIT", "EDIT_B"):
        p = _pick_path(rig, rng, prefer_tray=0.3)
        if p is None:
            return None
        t, v = _value_for(O.get(m.W, p), rng)
        return "EDIT_CELL", {"path": p, "text": t, "value": v,
                             "window": "b" if name == "EDIT_B" else "a",
                             "agent": name == "AGENT_EDIT"}
    if name in ("EDIT_BATCH", "FILLDOWN"):
        k = rng.randint(2, 3)
        if name == "FILLDOWN":
            leaf = rng.choice(sorted({p.rsplit(".", 1)[-1] for p in rig.editable}))
            col = [p for p in rig.editable if p.endswith("." + leaf)
                   and isinstance(O.get(m.W, p), (int, float)) and not isinstance(O.get(m.W, p), bool)]
            if len(col) < 2:
                return None
            paths = rng.sample(col, min(k, len(col)))
            t, v = _value_for(O.get(m.W, paths[0]), rng)
            if any(type(O.get(m.W, p)) is not type(v) for p in paths):
                return None
            rows = [[p, t, v] for p in paths]
        else:
            live_cands = [p for p in rig.editable if isinstance(O.get(m.W, p), (int, float))
                          and not isinstance(O.get(m.W, p), bool)]
            if len(live_cands) < 2:
                return None
            paths = rng.sample(live_cands, min(k, len(live_cands)))
            rows = []
            for p in paths:
                t, v = _value_for(O.get(m.W, p), rng)
                rows.append([p, t, v])
        return "EDIT_BATCH", {"rows": rows, "independent": name == "FILLDOWN"}
    if name == "EDIT_INVALID":
        p = _pick_path(rig, rng)
        if p is None:
            return None
        kind, text = rng.choice([("text", "abc"), ("big", "1e400"), ("list", "[1, 2]"),
                                 ("pointer", "#/qubits"), ("class", "x"), ("token", "0.5")])
        if kind == "class":
            p = p.rsplit(".", 1)[0] + ".__class__"
            if O.get(m.W, p) is O.MISSING:
                return None
        return "EDIT_INVALID", {"path": p, "text": text, "kind": kind}
    if name == "EDIT_MIRROR":
        if not rig.mirrors:
            return None
        # a mirror whose knobs live in another entity (a pair macro) is the
        # lab-shaped case: it gets half of the draws when the chip has one
        far = [mt for mt in rig.mirrors if mt[1].split(".")[0] != mt[0].split(".")[0]]
        field, tgt = rng.choice(far if far and rng.random() < 0.5 else rig.mirrors)
        if O.get(m.W, field) is O.MISSING or resolve_chain(m.W, field) != tgt:
            return None
        t, v = _value_for(O.get(m.W, tgt), rng)
        return "EDIT_MIRROR", {"field": field, "target": tgt, "text": t, "value": v}
    if name == "EDIT_FSP":
        if not rig.fsp_paths:
            return None
        p = rng.choice(rig.fsp_paths)
        cur = O.get(m.W, p)
        if not isinstance(cur, (int, float)) or isinstance(cur, bool):
            return None
        v = float(int(cur) + rng.choice([-3, -2, -1, 1, 2, 3]))
        if isinstance(cur, int):
            v = int(v)
        return "EDIT_FSP", {"path": p, "value": v, "ack": rng.choice(["", "comp", "comp", "solo"])}
    if name == "TYPEFIX":
        has = any(isinstance(v, str) and _numeric_text(v) for p, v in O.leaves(m.W).items()
                  if p.startswith("twpas."))
        if not has:
            return None
        return "TYPEFIX", {"stale": rng.random() < 0.3}
    if name == "PULSE":
        cands = [p for p in rig.pulses if O.get(m.W, p) is not O.MISSING]
        if not cands:
            return None
        lab = [p for p in cands if "pulses." not in str(O.get(m.W, p + ".__class__"))
               or "Waveform" in str(O.get(m.W, p + ".__class__"))]
        p = rng.choice(lab if lab and rng.random() < 0.4 else cands)
        action = rng.choice(["duplicate", "rename", "delete"])
        rig.name_seq += 1
        nm = f"p{rng.randint(0, 9999)}x{rig.name_seq}"
        return "PULSE", {"action": action, "path": p, "new_name": nm, "force": rng.random() < 0.5}
    if name == "APPLY":
        seen = rng.choices(["fresh", "none", "ack", "stale"], [6, 2, 1, 1])[0]
        w = "b" if rng.random() < 0.2 else "a"
        return "APPLY", {"window": w, "seen": "fresh" if seen == "stale" else seen,
                         "json_accept": rng.random() < 0.3}
    if name == "KEEP_MINE":
        return "APPLY", {"force": True, "expect": "fresh" if rng.random() < 0.8 else "stale"}
    if name == "MERGE":
        conf = m.conflicts() if m.readable() else []
        picks = [p for p in conf if rng.random() < 0.5]
        return "MERGE", {"picks_live": picks, "ack": rng.random() < 0.2}
    if name == "UNDO":
        return "UNDO", {"n": rng.choice([1, 1, 1, 2, 3]),
                        "window": "b" if rng.random() < 0.1 else "a",
                        "stale_sig": rng.random() < 0.05}
    if name == "REDO":
        return "REDO", {"n": rng.choice([1, 1, 2])}
    if name == "DISCARD":
        if not m.T:
            return None
        if rng.random() < 0.15:
            return "DISCARD_ALL", {}
        return "DISCARD", {"which": "last"}
    if name == "OUTSIDE":
        if not m.readable():
            return "OUTSIDE", {"kind": "repair", "changes": {}}
        k = rng.randint(1, 3)
        ch = {}
        for _ in range(k):
            p = _pick_path(rig, rng, prefer_tray=0.4)
            if p is None:
                continue
            _t, v = _value_for(O.get(m.L, p) if O.get(m.L, p) is not O.MISSING else O.get(m.W, p), rng)
            ch[p] = v
        return "OUTSIDE", {"kind": "atomic", "changes": ch}
    if name == "OUTSIDE_ODD":
        kind = rng.choice(["touch", "resave", "samewidth", "torn", "delete_state", "delete_folder"])
        ch = {}
        if kind == "samewidth" and m.readable():
            p = _pick_path(rig, rng)
            cur = O.get(m.L, p) if p else None
            if isinstance(cur, float) and repr(cur)[-1].isdigit() and "e" not in repr(cur):
                s = repr(cur)
                d = s[-1]
                nd = str((int(d) + 1) % 10) if d != "9" else "8"
                ch[p] = float(s[:-1] + nd)
        if kind == "resave" and m.readable():
            pass
        if not m.readable() and kind in ("torn", "delete_state", "delete_folder", "samewidth", "resave"):
            return "OUTSIDE", {"kind": "repair", "changes": {}}
        return "OUTSIDE", {"kind": kind, "changes": ch}
    if name == "SAVE":
        return "SAVE", {}
    if name == "TAKE_LIVE":
        return "TAKE_LIVE", {"force": rng.random() < 0.6, "mode": "discard"}
    if name == "REAPPLY":
        return "TAKE_LIVE", {"force": rng.random() < 0.6, "mode": "reapply"}
    if name == "BRING_BACK":
        return "BRING_BACK", {}
    if name == "STAGE":
        return "STAGE", {"k": rng.randint(0, 4), "force": rng.random() < 0.7}
    if name == "REVERT_LAST":
        return "STAGE", {"k": 0, "from_tray": True, "force": True}
    if name == "RESTORE_LIVE":
        return "RESTORE_LIVE", {"k": rng.randint(0, 4)}
    if name == "TOGGLE_UNDO_LIVE":
        return "TOGGLE_UNDO_LIVE", {"enabled": rng.random() < 0.6, "agent": rng.random() < 0.2}
    if name == "ARM":
        return "ARM", {"push": rng.random() < 0.6, "pull": rng.random() < 0.6}
    if name == "DISARM":
        return "DISARM", {}
    if name == "REFRESH_B":
        return "REFRESH", {"window": "b"}
    if name == "APP_RELOAD":
        return "APP_RELOAD", {}
    if name == "RESTART":
        return "RESTART", {}
    return None


def _numeric_text(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
