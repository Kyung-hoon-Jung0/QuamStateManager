"""Planted faults: one per invariant, each must turn the harness red BY ASSERTION.

A fault is installed for one scripted sequence (``SCRIPT``) through the hooks
``run_sequence`` calls around every step. The control run (no fault) of the
same script is green, so a red is the fault's doing.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.state_model import oracle as O


#: the synthetic chip the script runs on (tests/_ram_chip.build(3, seed=1))
CHIP_SEED = 1


def script() -> list:
    """A short deterministic sequence touching every door the faults need."""
    a, b, c = "qubits.q1.T1", "qubits.q2.T1", "qubits.q3.chi"
    return [
        ["EDIT_CELL", {"path": a, "text": "1.2345e-05", "value": 1.2345e-05, "window": "a", "agent": False}],
        ["APPLY", {"window": "a", "seen": "fresh", "json_accept": False}],
        ["EDIT_CELL", {"path": b, "text": "2.3456e-05", "value": 2.3456e-05, "window": "a", "agent": False}],
        ["SAVE", {}],
        ["APPLY", {"window": "a", "seen": "fresh", "json_accept": False}],
        ["UNDO", {"n": 1, "window": "a", "stale_sig": False}],
        ["REDO", {"n": 1}],
        ["EDIT_CELL", {"path": c, "text": "1345678.5", "value": 1345678.5, "window": "a", "agent": False}],
        ["APPLY", {"window": "a", "seen": "fresh", "json_accept": False}],
    ]


class Fault:
    name = ""
    invariant = ""
    how = ""

    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.fired = False

    def before(self, rig, i, name):
        pass

    def after(self, rig, i, name, step):
        pass


class DirectLiveWrite(Fault):
    name, invariant = "direct_apply_to_live", "I1"
    how = "after an edit, a staging door saves and calls working_copy.apply_to_live itself (no record)"

    def after(self, rig, i, name, step):
        if name == "EDIT_CELL" and not self.fired:
            from quam_state_manager.core import hub, working_copy
            ctx = rig.ctx()
            ctx["saver"].save()
            working_copy.apply_to_live(ctx["working_copy"], force=True,
                                       record=hub.unrecorded("planted fault"))
            self.fired = True


class DeleteLandedLine(Fault):
    name, invariant = "delete_landed_line", "I2"
    how = "after an Apply, the {landed: id} line is deleted from events.jsonl"

    def after(self, rig, i, name, step):
        if name == "APPLY" and step.obs == "landed" and not self.fired:
            j = rig.chip_dir() / "events.jsonl"
            lines = j.read_text(encoding="utf-8").splitlines()
            if lines and '"landed"' in lines[-1]:
                j.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
                self.fired = True


class EditWorkingFolder(Fault):
    name, invariant = "edit_working_folder", "I3"
    how = "after a Save, one leaf of the working folder's state.json is changed behind SM"

    def after(self, rig, i, name, step):
        if name == "SAVE" and not self.fired:
            wf = Path(rig.ctx()["working_copy"].working_folder) / "state.json"
            doc = json.loads(wf.read_text(encoding="utf-8"))
            p = rig.editable[-1]
            O.put(doc, p, 0.98765)
            wf.write_text(json.dumps(doc), encoding="utf-8")
            self.fired = True


class WrongTraySig(Fault):
    name, invariant = "wrong_tray_signature", "I4"
    how = "the tray renders a constant data-change-sig (routes._change_log_sig_of lies)"

    def before(self, rig, i, name):
        if not self.fired:
            from quam_state_manager.web import routes
            self.mp.setattr(routes, "_change_log_sig_of", lambda changes: "0123456789ab")
            self.fired = True


class EditCursor(Fault):
    name, invariant = "edit_journal_cursor", "I5"
    how = "after an Apply, the undo journal's persisted cursor is set to 0"

    def after(self, rig, i, name, step):
        if name == "APPLY" and step.obs == "landed" and not self.fired:
            from quam_state_manager.core import undo_journal
            undo_journal.save_cursor(rig.sidecar(), 0)
            self.fired = True


class WrongRevertTarget(Fault):
    name, invariant = "post_apply_revert_target", "I6"
    how = "after an Apply, last_apply.pre_ts names the POST-apply snapshot"

    def after(self, rig, i, name, step):
        if name == "APPLY" and step.obs == "landed" and not self.fired:
            hm = rig.app.config["history_manager"]
            hm.check_and_snapshot(rig.ctx()["path"], "manual", force=True)
            rig.ctx()["last_apply"]["pre_ts"] = rig.snapshots()[0]
            self.fired = True


class SilentValueChange(Fault):
    name, invariant = "silent_store_change", "I7"
    how = "after an edit, the store holds a different value than the one the tray recorded"

    def after(self, rig, i, name, step):
        if name == "EDIT_CELL" and not self.fired:
            st = rig.ctx()["store"]
            p = step.args["path"]
            O.put(st.state, p, 0.5)
            st.merged = {**st.state, **st.wiring}
            self.fired = True


class DropLedgerRow(Fault):
    name, invariant = "drop_ledger_event", "I8"
    how = "after an Apply, the event's row is deleted from the ledger (sm_events)"

    def after(self, rig, i, name, step):
        if name == "APPLY" and step.obs == "landed" and not self.fired:
            from quam_state_manager.core import hub
            hub.flush(10)
            con = sqlite3.connect(str(rig.chip_dir() / "ledger.sqlite"))
            con.execute("DELETE FROM sm_events WHERE rowid = (SELECT max(rowid) FROM sm_events)")
            con.commit()
            con.close()
            self.fired = True


class SaveWritesLive(Fault):
    name, invariant = "save_writes_live", "I9"
    how = "Saver.save also rewrites the live state.json (a working-copy door touching live)"

    def before(self, rig, i, name):
        if name == "SAVE" and not self.fired:
            ctx = rig.ctx()
            saver, live = ctx["saver"], rig.live
            real = saver.save

            def save(*a, **k):
                out = real(*a, **k)
                st = ctx["store"].state
                (live / "state.json").write_text(json.dumps(st), encoding="utf-8")
                return out
            self.mp.setattr(saver, "save", save)
            self.fired = True


class IntForFloat(Fault):
    name, invariant = "int_for_float", "I11"
    how = "after a Save, an integral float leaf in the working folder is written as an int"

    def after(self, rig, i, name, step):
        if name == "SAVE" and not self.fired:
            wf = Path(rig.ctx()["working_copy"].working_folder) / "state.json"
            doc = json.loads(wf.read_text(encoding="utf-8"))
            for p, v in O.leaves(doc).items():
                if isinstance(v, float) and v.is_integer() and not p.endswith("__class__"):
                    O.put(doc, p, int(v))
                    break
            wf.write_text(json.dumps(doc), encoding="utf-8")
            self.fired = True


class StatusAlwaysSynced(Fault):
    name, invariant = "status_always_synced", "I12"
    how = "sync_status.derive_state answers 'synced' whatever the facts"

    def before(self, rig, i, name):
        if not self.fired:
            from quam_state_manager.core import sync_status
            self.mp.setattr(sync_status, "derive_state", lambda **k: "synced")
            self.fired = True


ALL = [DirectLiveWrite, DeleteLandedLine, EditWorkingFolder, WrongTraySig, EditCursor,
       WrongRevertTarget, SilentValueChange, DropLedgerRow, SaveWritesLive, IntForFloat,
       StatusAlwaysSynced]
