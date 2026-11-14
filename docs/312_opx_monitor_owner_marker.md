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
| one version-1 marker | marker, tag (or "name (tag)"), a "this PC" badge when the tag is this PC's own, node, session, state digest |
| one marker-shaped name that is not version 1 | "marker present, not readable", its version number and the raw name |
| two or more | a "several markers (N)" badge and every marker, none picked |
| config could not be read | "Owner unknown: this QM's configuration could not be read." (never "no marker") |

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
  `load_pc_names()` / `save_pc_names()`.

## PC names
`pc_names.json` (`{tag: name}`) lives in the marker's own per-user app-data folder, next to
`pc_tag`; it is edited only on Monitor settings ("PC names": named tags plus the tags seen in open
QMs, add / change / remove; written atomically). A missing file is an empty map; an unreadable
one is an empty map with one line on the settings tab (saving over it keeps a `.bak`).
Names are applied when a page is rendered and nowhere else: not in History, the status JSON,
logs, close replies or error messages. History records no marker data at all.
