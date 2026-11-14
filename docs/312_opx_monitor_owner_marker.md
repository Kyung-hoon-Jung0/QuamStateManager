# 312 OPX monitor: who holds an open QM (owner marker)

The OPX monitor has no dedicated doc page yet; this page covers only the owner column.

## What it shows
A config built by the calibration stack may carry one extra element whose name is an owner marker,
`qmowner__v1__c<tag>__s<state>__x<session>__n<node>`. For every open QM the monitor shows an
owner cell on the Live tab (one compact line per QM), the Jobs tab's open-QM table, the Clean up
table and the Clean up confirm panel:

| Config says | Owner cell |
|---|---|
| no marker-shaped element | "Not reported by the server (no owner marker)." |
| one version-1 marker | marker, tag (or "name (tag)"), a "same tag as this PC" badge when the tag equals the one in this PC's `pc_tag` file (hover: a client that sets its own tag is not recognised, and a tag can be copied by another client), node, session, state digest |
| one marker-shaped name that is not version 1 | "marker present, not readable", its version ("version with N digits" past 6 digits, never converted) and the name |
| two or more | a "several markers (N)" badge and every marker, none picked |
| the worker answer has no `markers` key (older worker) | "Owner not reported: the monitor worker is from an older version; reopen the monitor." |
| any error working out one QM's owner (logged once, without the name) | "marker present, not readable" per name, or "Owner could not be worked out." |
| config could not be read | "Owner unknown: this QM's configuration could not be read." (never "no marker") |

Every server-read text (names, parsed fields, labels) passes `display_text`: non-printable code
points (controls, bidi overrides) become `\uXXXX` escapes, at most 120 characters on screen then
"... (N characters)", at most 300 in `title=`. On Live, a failed or stale poll marks the owner
list "Not current" with the same last-good-read age as the Jobs table.

The marker is the client's claim; the server does not check it. A table note says so once.
It never chooses which QM a close acts on and never skips or shortens a confirm.

## Pieces
- `generator/opx_monitor_worker.py`: per open QM, `markers` = the element names that are
  marker-shaped (`qmowner__v<digits>__` prefix), cached with the config; `None` when the config
  could not be read. Ports are still derived from `controllers` only, so a marker claims no port.
- `core/qm_owner_marker.py`: a pure-stdlib copy of the grammar (`is_marker`, `parse_marker`,
  `marker_version`). It is pinned by `tests/fixtures/qm_owner_marker_vectors.json`, generated from
  the reference marker module by a script kept outside this repository; a new marker version
  regenerates the fixture and `tests/test_opx_owner_marker.py` fails until the copy follows.
- `core/opx_monitor.py`: `owner_info(q)` (kind + markers, no labels), `own_pc_tag()`,
  `load_pc_names()`; the one writer is the Monitor settings save route (`web/opx_monitor_routes.py`).

## PC names
`pc_names.json` (`{tag: name}`) lives in the marker's own per-user app-data folder, next to
`pc_tag`; it is edited only on Monitor settings ("PC names": named tags plus the tags seen in open
QMs, add / change / remove). A save applies only the rows that window changed, each onto the file as read at that moment, so two windows editing different tags keep both, while a row another window changed since refuses the save (409) and shows the current names; a save whose changes give one tag two different names, or name a tag that already has another name (unless the same save removes that tag), is refused whole (409) and names that tag; a refused save draws the form back with what was typed and its message sits next to the Save PC names button; names are trimmed, at most 40 characters, control characters refused; the temp file has a unique name. A missing file is an empty map; an unreadable
one is an empty map with one line on the settings tab ; entries that are not text are counted there. Every save copies the previous file to `pc_names.json.bak` before the new file replaces it.
Names are applied when a page is rendered and nowhere else: not in History, the status JSON,
logs, close replies or error messages. History records no marker data at all.
