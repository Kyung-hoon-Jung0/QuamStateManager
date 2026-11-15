# 313 Save to quam_config

The pulse-class editor (docs/312_pulse_class_drafts.md) keeps a class as a draft in the instance folder.
"Save to quam_config" is the one door that turns a draft into a module of the lab code. The route
(`POST /pulse-class/save-config`, `web/pulse_class_page.py`) and, later, the agent tool both call one core
function, `core/quam_config_save.py:save_class`.

## The order (always the same)

1. **Folder.** SM asks the chosen environment where it imports `quam_config` from, without SM's lab code folder in
   front of the path (`lab_worker.run(..., prepend_lab=False)`; the runner is `generator/run_class_save.py`, action
   `discover`). That is the folder QUAlibrate reads. The first press returns only `confirm` (absolute file path,
   the environment's folder, the gates that use the class, the "runs without isolation" sentence); nothing is read
   or written yet. The second press sends the shown folder back as `confirmed_folder`.
   - The target is the environment's folder (left empty, or typed) and SM's own lab code folder for that environment
     differs from it: refused, both paths named. Setting SM's lab code folder to the environment's folder lifts it.
   - The folder is inside site-packages or not writable: refused.
   - The user typed another folder: only writability is checked; the result says the environment does not import the
     file, and gates that use the class stay refused (receipt `importable: false`).
2. **Bytes.** The draft is read once and hashed (sha256). Those bytes are checked and written; nothing reads the
   draft again, so an edit during the check cannot reach the file.
3. **Check, in the lab worker, on copies only.** The quam_config folder is copied to a temporary package, the bytes
   are put in it as `<Class>.py`, and the worker imports `quam_config.<Class>`, builds a default instance and
   computes the waveform (with the amplitude-proportional check). The worker returns the hash of the bytes it ran;
   a different hash is refused. A class without an `amplitude` field is refused. When the open chip already uses
   the class, SM also writes a copy of the chip (the store's in-memory state, `core/chip_copy.py`) and loads it with
   the draft class (the worker reports `chip_loaded`; a used class without it is refused). When a class file is
   being replaced, the copy is first loaded with the old file (worker action `load`, its own process):
   - old file loads, draft does not: refused with the lab's error;
   - old file does not load either: the check cannot judge the draft against this chip, so the save goes ahead with
     a warning naming the old file's load error (`chip_warning`, also in the history line); a draft that loads the
     chip again (a repair) is saved with a line saying so. A draft that fails to import or compute is still refused.
   The copies are removed afterwards; the store, its change log and the working copy are not touched, and nothing is
   written into the state (no `extras`).
   - A template class whose waveform is not proportional to amplitude is refused; a hand-edited one is saved with a
     warning line.
4. **Write.** One file per class. A new name uses a create-only atomic write (`safe_io.atomic_write_bytes(...,
   create_only=True)`): an existing file of that name, or one created during the check, is refused and left as it
   was. An existing file is replaced in place only when it carries SM's header and is the file this draft was
   published to before (`published.json`); the old bytes go to `instance/class_file_backups/` first. A lab-made file
   (no SM header) is never replaced: the draft is refused at save time with "pick another class name". If the
   chip changed during the check, the check runs again (three tries, then the user is told the chip keeps changing).
5. **History.** One JSON line in the chip's history folder, `class_files.jsonl`: time, who (`person` or `agent`, from
   the `X-SM-Agent` header), path, sha256 and the list of this chip's gates that use the class (live and active ones
   marked). If that line cannot be written, the file stays and the screen says the history line failed. When the
   environment imports the folder, the module is added to the modules the environment probe imports (inside
   `save_class`, result `registered`); the route then re-probes. A save to another folder registers nothing.
6. **What the screen says.** The saved path, "this chip now needs quam_config/<file>.py; every computer that opens
   this state needs the same file", the restart line, and any warning.

## Restart wording (measured)

In a long-lived process that had already imported `quam_config` (as a running QUAlibrate has), a module file created
afterwards imported at once through `importlib.import_module` (the call quam uses to resolve a `__class__` string),
while an already imported module that was edited on disk kept its old code. So a new class file says no restart is
needed; an edited one says to restart QUAlibrate before running nodes with the class.

## Gates wait for the class file

`class_refusal(instance, scope, creates, classes, lab_folder)` refuses a gate plan (plan, check and create all go
through `cz_gate_routes._plan_all`) that names, or creates a value of, a class that has a draft in this chip's scope
but no saved quam_config file (or one saved to a folder the environment does not import). A draft never published
whose name matches a lab-made file in SM's lab folder (`lab_package`) does not block gates that use the lab class.

An undo never deletes a class file: its notice names the file left on disk when no gate uses it any more, and the
class list marks an SM file that no gate of the chip uses as "not used". Every undo door gives the notice: plain undo,
the journal step (the one that writes live), and the agent's undo of its own edits; a redo that restores a use gives
none. A use is any `__class__` naming the class in this chip's merged state (a gate macro or a pulse); files published
for another chip are not judged here.

## Save to file

The older "Save to file" door (a copy anywhere) cannot write into any folder named `quam_config` (with or without
`__init__.py`) or over the file Save to quam_config published; both point to Save to quam_config. Each Save to file
write also adds a history line. Save to file updates only `target.json` (the status line); the publication record
`published.json` is written only by Save to quam_config, so a copy saved elsewhere does not make the next Save to
quam_config refuse its own file, nor make gates using the class wait again.

## The class file record on a gate, and its status

- A gate macro that uses SM-made classes (its gate class or pulse classes) gets `extras.sm_class_files` when it is
  created: one entry per class, `{qclass, file, sha256, proportional}` (`quam_config_save.class_files`). `sha256` is
  the hash of the published file's bytes, read before the lab check; the publication record gives only the path,
  the class and `proportional`. A published file that is not there refuses the gate, naming the file. After the check
  the files are read again (`class_files_moved`): bytes that changed during the check refuse it, nothing is written.
  The entry goes into the planned macro, so the lab check loads the gate as it will be written; a gate class without
  an `extras` slot fails that load and the refusal says why. No source text goes
  into state. A gate without SM-made classes gets no key. The one parser is `gate_status.class_file_records(macro)`
  (raw, what the live-write check reads); `gate_status.sm_class_files` is its view of the records that name a file.
- A gate with SM-made classes is created only when the lab check loaded the chip and ran the gate (`load_config` and
  `ops` passed): "not checked" refuses it, whatever its class (`cz_gate_create.may_create(..., sm_classes=True)`).
- The status line compares each record with the bytes of the published file (path from the publication record):
  "class file present" (a detail), "class file missing: quam_config/<file>.py" (several missing files: all named, in
  record order, joined with ", quam_config/"), "class file changed since the gate was checked", or "not a class SM
  made" (a detail). Scaling: "amplitude scaling: checked" / "not proportional" /
  "not known" (a detail) from the recorded `proportional`.
- Make active reads the same states as the line (`gate_status.class_file_states`) before the lab check: a missing
  file refuses with "Save <file> to quam_config first", a changed one with "Re-check the gate first"; nothing is
  written. A record that names no file reads "class file missing" (why: "its class file record has no file name"),
  as the live-write check refuses it, and Make active refuses it too. A record that names a file but has no (or an
  empty) hash reads the same line with why "its class file record has no content hash"; Make active refuses it with
  "Delete the gate and make it again" (no Re-check is offered on a "missing" line).
- For as long as a gate records a file, its line shows "this chip now needs quam_config/<file>.py; every computer
  that opens this state needs the same file" (dialog, pair detail and pulse detail all render the same partial).
- "Re-check" (shown on a "changed" line, `POST /api/pulse/cz-gate/recheck`) runs the lab check of the existing gate
  with the files as they are now and, when it loads and runs, writes the new records as one edit (one Ctrl+Z). The
  hashes are taken the same way as at create (before the check, read again after it); a class that is no longer
  published, or a file that changed during the check, refuses without writing.

## The chip-level Re-check notice

- `gate_status.gates_to_recheck(merged, where)` lists every gate of the chip, over all pairs, whose line says "class
  file changed since the gate was checked" (read through `class_file_states`, so a missing file, a bad record or a
  gate with no SM records is never listed): `[{pair, gate, files}]`, files = the changed ones.
- While that list is non-empty, a warn callout (`_gate_recheck_notice.html`, `ui_callout` only) sits at the top of
  every pair's gate list and above the table on the Pulses page: "N gates wait for Re-check", each
  "pair / gate (file)", the instruction to press Re-check on the gate's status line (in the pair's CZ gates or the
  gate's pulse detail), and "Other chips that use this file were not checked." With an empty list nothing is
  rendered (the Pulses slot `#gate-recheck-slot` stays empty). It only informs; it never refuses a press.
- Refresh: the gate list re-renders it on every answer of its own (Re-check, Make active, delete in the list), and
  re-fetches itself on `pulses-changed` (every gate write answers it, so a Re-check or delete in the pulse detail on
  the pair page renews it) and on `cellsReverted` / `stateRestored` (undo, redo, pull). static/gate-status.js skips
  the `pulses-changed` of the list's own answer (no second fetch) and holds a refresh while a delete offer is open.
  The Pulses slot re-fetches `GET /pulse/cz-gate/recheck-notice` on `pulses-changed` and on the same two events.
- No poll: the notice is exact at every render and re-fetch. Another window that did not make the change shows the
  new state on its own next refresh event (its own gate write, undo, redo or pull, or a page load), as the gate list
  and the Pulses table do.

## The live-write check

`working_copy.apply_to_live` is the one place a live write checks class files (`core/class_file_check.py`), so every
door that writes the chip gets it: Apply, the sync panel's apply, Ctrl+Z / Ctrl+Shift+Z that write live, the dataset
chip apply, version restore to live, an armed Auto-Sync and the agent's apply. When the content to write adds a gate
macro carrying `extras.sm_class_files`, or makes such a gate the pair's active gate (its `cz` alias moves to it), each
listed file must be in the folder the chip's environment imports `quam_config` from (asked without SM's lab folder in
front, the folder Save to quam_config resolves) and its bytes must hash to the recorded sha256. A missing file, a changed file, or an
unknown environment (none selected, or the folder cannot be asked) refuses the write before it is recorded: the live
files stay as they were and the door's message names the gate and the file. Auto-Sync classifies the refusal as
permanent and turns off with that reason. Gates without the key, a gate already active on the chip, and edits that
touch neither are written as before; a chip with no such gate never reads live or asks the environment for this. A
gate re-made under a name already on the chip (another file list) counts as added, every listed file of every such gate
is checked (qubit macros included), and when live state.json is missing or unreadable every such gate counts as added,
so a forced push (Keep mine) is checked too. The web layer tells the check which environment a live folder belongs to
(`class_file_check.set_env_source`): the QUAlibrate project env of the open chip writing that folder, found also from
a thread with no app context (the Auto Calibrate writer); when no open chip writes the folder the refusal says SM cannot
tell which environment the chip uses, never that none is selected.
Tests: `tests/test_class_file_live_check.py`.

