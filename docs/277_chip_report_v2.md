# docs/277 -- the shareable chip report, phase A: pick the sections, send one file

2026-10-04, user step 5 of the current campaign. Branch `feat/chip-report-v2`
from `origin/main` (`370d71df`).

> "A shareable single-file offline HTML report": a person picks what to
> include, presses download, and sends ONE .html file that opens anywhere
> without SM or a network.

This extends the printable report of docs/126 #21 / docs/188
(`GET /chip-status/report`). There is still one report page; it grew a
**Sections** panel, seven more sections, a redaction switch, and a download
that the server finalizes.

## 1. The rules (binding for every section)

1. **Same code.** Every number in the file is produced by the function that
   produces it on its live page (table below). The report formats it with the
   same `qty` filter (`core/units.qty_filter`). Nothing is re-derived.
   A chart is a *picture* of those numbers (section 6 says which pictures are
   the screen's own drawing and which are redrawn as static SVG).
2. **Confidentiality by construction.** An unchecked section is not in the
   file at all: not hidden by CSS, not in a data attribute, not in an inlined
   JSON blob. Two independent gates enforce it:
   * the browser builds the file from a whitelist: it clones the page and
     removes every `section[data-rep-sec]` whose box is unchecked, the panel,
     and every script except the raw-tree renderer (kept only when the raw
     section is in the file);
   * `POST /chip-status/report/finalize` refuses (HTTP 400) a document that
     carries a `data-rep-sec` the request did not declare, or declares a
     section that is not available.
3. **Redaction** (checkbox "Hide network addresses and local folder paths",
   default ON) is applied by the server, by rule, everywhere in the file
   (section 3).
4. **Offline.** CSS inline; charts are static inline SVG; no external fonts,
   scripts or images; no plotly.js. The server's finalize step strips any
   `src`/`href` that is not a `#fragment` or a `data:` URI. The only script a
   file can carry is the raw-tree renderer (inline, no network). The file
   says, in its footer, that its charts are static pictures.
5. **English-only UI; one time format.** Times print as
   `YYYY-MM-DD HH:MM:SS (UTC+9)` (`timefmt.local_text`, docs/244/263) in the
   project zone (`project_time.display_zone`), and the Overview names the zone
   (`Asia/Seoul`), or says "this PC's zone" when no project zone is set.
6. **Honest failure.** A section whose source raises renders
   `Could not be built: <ExceptionType>: <message>` inside its own section,
   never a blank, never a 500 for the page. The same line is used when the
   browser's fetch of a section fails.

## 2. Sections and their sources

The registry is `core/chip_report.SECTIONS` (key, label, one-line
description, default, available). The panel prints the description beside
each checkbox.

| key | label | default | data source (exact function the live page calls) | what the file holds |
|---|---|---|---|---|
| `overview` | Overview | on | `routes._chip_display_name`, `quam_state_manager.__version__`, `timefmt.local_text(zone=...)` + `project_time.display_zone`, `QueryEngine.list_qubits` / `get_pair` counts | chip name, SM version, generated-at with the zone named, source folder, entity counts, the list of sections in the file |
| `chip_status` | Chip Status | on | unchanged docs/188 path: `QueryEngine.list_qubits`, `get_pair`, `routes._topology_with_derived_rb`, `cr_semantics.channel_effective_rf_if`, `routes._report_gate_param_rows`; the map is `ComponentMap.mount` (component-map.js) over `/api/topology` | the component map (drawn by the page, SVG baked in) and every component table; the "Last cal." dates are now in the project zone (they were in this PC's) |
| `trends` | Trends | on | `routes._trend_metrics_with_data` + `routes._trend_series_curated(hm, path, props, tbl)` over `chip_trends_ram.table(hm, path)` -- the calls behind `/topology/trends` (Chip Status > Trends); labels `routes._trend_metric_label`, units `routes._trend_unit` | one static chart per curated metric that has history, one line per qubit, cropped to the chosen window (All / 365 / 90 / 30 / 7 days), plus the newest value per qubit through `qty` |
| `pulses` | Pulses | on | `routes._pulse_index().rows()` (`core/pulse_index.PulseIndex`) + `routes._pulse_draw_sparks` (`waveform_synth.sparkline_svg(synth_for_operation(...))`, or the lab/config fallback the Pulses page uses) | the Pulses page table: owner, channel, operation, class, waveform thumbnail, length, amplitude, used-by count. Identical thumbnails are stored once (`<symbol>` + `<use>`) |
| `zline` | Z-line distortion | on | `routes._zline_rows(store)` (the `/zline` table, factored out of `zline_page`) and `routes._zline_payload(store, line, "sum")` (the `/zline/data` body, factored out of `zline_data`; `zline_filters.step_response`) | the /zline table, and one step-response figure (ideal, exponential only, FIR only, output) per distinct filter set, with the page's own stats line; lines that share a filter set share a figure, and the figure names them |
| `wiring` | Instrument wiring | on | `QueryEngine.get_instrument_wiring()`; the rack picture is drawn by the screen's own `renderInstrumentWiring` (app.js) inside a hidden same-origin frame (`GET /chip-status/report/frame/wiring`) and its SVG is imported into the report | the rack diagram (SVG) and a port table (controller/FEM/port, direction, element, role, label, band, LO, FSP) built from the same model -- the values the diagram only shows on hover |
| `diagnostics` | Diagnostics | on | `routes._active_chip_findings(store)` + `diagnostics.summarize`, rendered through the live page's own `_diagnostics_list.html` with `allow_jump=False`, `allow_fix=False` | every finding grouped by domain; acknowledged findings listed and marked |
| `calibration_log` | Calibration log | -- | **seam for hub S6** (section 7) | not available: disabled box, note "available once the ledger lands" |
| `raw` | Raw state tree | off | `QuamStore.state` and `QuamStore.wiring` (the documents the saver writes, pointers unresolved), through `core/chip_report.raw_payload` | state.json + wiring.json as a collapsible tree, pointer strings as stored. Plain JSON when it is at most `RAW_PLAIN_MAX` (1.5 MB) minified, gzip + base64 above that (decoded in the browser with `DecompressionStream`) |

Raw is off by default: it is the whole chip, the largest section, and the one
most likely to carry something nobody meant to send.

## 3. The redaction rule (`core/report_redact.py`)

Applied when the switch is on. The marker is `[hidden]`. Three layers, each a
rule rather than a list of one chip's keys:

**S -- structural, on state.json / wiring.json** (`Redactor.redact_tree`):

* **S1 network block.** Every scalar inside a dict stored under a key named
  `network` (any depth) is blanked. `null` stays `null` (it carries nothing).
  The QUAM wiring stores `network: {host, port, cluster_name, ...}` there.
* **S2 network vocabulary.** A string value whose key, split into lowercase
  words (`_`, `-`, `.`, camelCase), contains one of `host`, `hostname`, `ip`,
  `ipaddr`, `ipv4`, `ipv6`, `addr`, `address`, `url`, `uri`, `endpoint`,
  `server`, `proxy`, `gateway` is blanked. A key containing the word `port`
  is blanked only when the same dict also holds such a host-like key (a
  `host:port` pair). An OPX port (`port_id`, `ports`, `opx_output`) is never
  touched by S2.
* **S3 values.** Every string leaf goes through layer V.

**V -- value patterns, on any text** (`Redactor.redact_text`), in this order:

1. a URL with a scheme (`scheme://...`);
2. an IPv4 address with an optional `:port`; an IPv6 address in full
   eight-group or `::`-compressed form;
3. a dotted host name or `localhost` followed by `:port`;
4. an absolute filesystem path: a Windows drive path (`C:\...`, `C:/...`,
   segments may contain spaces when a separator follows), a UNC path
   (`\\server\share...`), a POSIX path of at least two segments starting at a
   word boundary (`/home/x`), or `~/...`. A JSON pointer (`#/qubits/q1`) is
   never a path: a `/` preceded by `#`, `.`, a word character or another
   separator does not start one.

A string that *starts* with a match is blanked whole (a value that is a path
is the path); otherwise only the matched span is replaced.

**L -- literals.** Every string of four or more characters that S1/S2/S3
blanked is remembered, and every occurrence of it anywhere in the final
document is replaced too -- a cluster name that a chip key, a diagnostics
message or a title repeats is caught where it is repeated.

**Where it runs.** The raw section's payload is built from the redacted trees
(layer S). Every server-rendered section and the finalized document go
through `Redactor.redact_html`: text nodes and attribute values get layers V
and L; `<style>` bodies, the raw-tree JSON script and `xmlns` attributes are
not touched (the JSON was redacted structurally before it was serialized).
The download file name goes through layer V/L as well. With the switch off,
nothing is changed.

## 4. The download

1. The page holds the checked sections (lazily fetched from
   `GET /chip-status/report/section/<key>?redact=&window=`; Overview and
   Chip Status are inline in the page, as before).
2. `ChipReport.buildStandalone()` waits for in-flight section fetches, the
   map and the wiring picture; clones the document; removes the panel, the
   toolbar's download button, every unchecked section and every script but
   the raw-tree renderer (only when raw is checked); resets the raw tree to
   its unopened state; inlines the stylesheet rules the file can use
   (`usedCss`: a rule is dropped only when one of its classes, ids or element
   names appears nowhere in the file -- pseudo-classes, attribute selectors,
   `:root` and @-rules are kept; 914 KB of pico + style.css becomes ~100 KB);
   and POSTs `{html, sections, redact}` to `/chip-status/report/finalize`.
3. The server refuses (400) a section the request does not declare, a
   declared section the document lacks, an unavailable section; refuses
   (409) a section whose `data-rep-redact` stamp differs from the request's
   switch (a page whose switch changed after a section was fetched). It then
   rewrites the Overview's contents line from the declared sections, takes
   the chip name out of `<title>` and the file name when Overview is not
   declared, and makes ONE pass over the document
   (`report_redact.html_pass`): scripts survive only with `data-rep-keep`
   and their section declared, non-local `src`/`href` are dropped,
   whitespace between tags collapses (the templates' indentation was ~2 MB
   of the big chip's file), and with the switch on every text and attribute
   value is redacted. The response carries the file name
   (`Content-Disposition`, `X-Report-Filename`); the page turns it into a
   download.

The redaction switch and the trends window are in the URL
(`?sections=...&redact=0|1&window=...`), so reload and Back keep them; the
switch reloads the page so every section is re-rendered under it.

## 5. Size and speed budget

Download < 10 MB and < 5 s with every section on, for a 30-qubit chip.
Measured in section 9.

## 6. Pictures

* The component map and the instrument rack are the screen's own drawings
  (ComponentMap; app.js `renderInstrumentWiring` in a hidden frame), SVG
  baked into the file.
* Trends and Z-line pictures are drawn server-side as static SVG
  (`core/report_svg.line_chart`) from exactly the series the live pages plot.
  The live pages draw them with Plotly through app-shell code (`ChipTrends`
  in chip-status.js over app.js; zline.js over `_plotlyRender`); loading the
  app shell into the standalone report starts its polls and is slower than
  the 5 s budget allows for dozens of charts. The y axis uses the fixed unit
  the tables print (`units.fixed_scale`, the factor behind `qty`: T1 in us,
  f_01 in GHz), so axis and legend agree with every table. Series colours: the
  validated dark
  categorical palette (8 slots, fixed order by the chip's natural entity
  order); past eight entities the hue repeats with a different dash
  (secondary encoding), and a legend is always present.

## 7. Seam for the calibration log (hub S6)

`core/chip_report.SECTIONS` holds `calibration_log` with `available=False`
and `note="available once the ledger lands"`. The panel renders it as a
disabled checkbox; the section route and finalize refuse it. S6 fills it by:

1. writing `routes._report_build_calibration_log(rc: _ReportCtx) -> str`
   (the section body HTML -- its own `<h2>` included -- built from the hub
   ledger, `HubStore`, through the same function the Calibration log page
   uses; `rc` carries the chip, the redactor and the trends window, exactly
   as the other builders get them; raising is fine: `_report_section` turns
   it into the honest line);
2. registering it in `_REPORT_BUILDERS["calibration_log"]`;
3. flipping `available=True` in `SECTIONS`.

Nothing else changes: the panel, the lazy fetch, the whitelist, redaction and
the size/time accounting already treat it like every other section.

## 8. Tests

* `tests/test_chip_report_v2.py` (49) -- registry and panel, every section
  route, honest failure for seven sources, same-code checks against the live
  routes (chip status, trends with real history, pulses, z-line, wiring,
  diagnostics, raw), confidentiality gates, redaction on/off, the offline
  pass, the raw payload and sparkline dedupe; it also drives
  `tests/chip_report_v2_selfcheck.cjs` (17 checks: the page's own
  clone-and-filter serializer under jsdom, network stubbed).
* `tests/test_report_redact.py` (40) -- the rule layer by layer, including
  what it must not touch (pointers, OPX ports, dates, times, units, dotted
  parameter paths).
* `tests/browser/journeys/chip_report_v2.cjs` (34 checks, real Chrome) --
  real clicks, a real download opened from disk offline, fault injection
  (a)-(e), reload and Back. 34/34 on both chips.
* Unchanged and green: `tests/test_chip_report.py`, `tests/test_report_card.py`
  and the 18 other files that touch the report, z-line, pulses, trends,
  instrument wiring, units and diagnostics (839 passed);
  `tests/report_download_probe.cjs` 16/16 against the big chip.
* Full suite (cqt, two shards): 13,198 passed, 251 skipped, 0 failed.

## 9. Measured (2026-10-04, real Chrome, every section on, switch ON)

Two chip COPIES (never hardlinks; network rewritten to host 127.0.0.1,
port 1; the data folder pointed inside the rig), served by this worktree on
5145, headless Chrome on 9465:

* **small** -- a copy of a real 5-qubit chip: 5 qubits, 4 pairs, 158 pulses,
  5 flux lines, 15 history snapshots; state.json 0.7 MB minified.
* **big** -- the 30-qubit stress copy: 30 qubits, 69 pairs, two controllers,
  8,779 pulses, 12,836 stored RB numbers, 3,467 2Q gate rows, 30 flux lines
  (5 distinct filter sets), 200 history snapshots; state.json 19 MB on disk,
  9.0 MB minified. It is synthetic and ~5x the fields of a natural 30-qubit
  chip, so it is the worst case, not the typical one.

**File size per section** (bytes in the downloaded file):

| section | small | big |
|---|---:|---:|
| Overview | 834 | 839 |
| Chip Status | 30,505 | 3,458,962 |
| Trends | 19,251 | 155,926 |
| Pulses | 90,914 | 3,688,994 |
| Z-line distortion | 198,559 | 202,778 |
| Instrument wiring | 16,272 | 91,303 |
| Diagnostics | 2,446 | 13,121 |
| Raw state tree | 710,827 (plain JSON) | 1,532,908 (9.0 MB JSON packed) |
| inlined CSS (pruned) | 100,750 | 100,605 |
| **whole file** | **1,175,130 (1.12 MB)** | **9,250,208 (8.82 MB)** |

**Build time per section** (server ms, cold = first open after the chip
loaded and its background warm-up finished / warm = second open). The eight
sections are fetched concurrently, so these overlap and do not add up:

| section | small cold / warm | big cold / warm |
|---|---:|---:|
| Overview | inline / inline | 30 / 0 |
| Chip Status | 30 / 0 (inline) | 1,297 / 1,047 (inline) |
| Trends | 375 / 0 | 4,156 / 61 |
| Pulses | 406 / 202 | 7,406 / 2,640 |
| Z-line distortion | 656 / 250 | 2,609 / 1,234 |
| Instrument wiring (table) | 0 / 0 | 47 / 61 |
| Diagnostics | 47 / 16 | 983 / 30 |
| Raw state tree | 139 / 108 | 2,313 / 1,484 |

**The download (press -> file ready), every section already on the page:**

* small: 0.12-0.13 s (finalize 47 ms); the journey's real button press 0.39 s.
* big: **2.3-2.9 s** over eight presses (2,529 / 2,426 / 2,272 / 2,354 /
  2,871 / 2,919 / 2,661 / 2,571 ms), of which the server's finalize pass is
  1.4-2.0 s. One press during the journey, made right after toggling two
  sections while the server was still busy, took 5.9 s.

**Opening the page with every section on** (all eight built and drawn): small
1.2 s cold / 0.5 s warm; big 12.9-15.3 s cold / 8.5-10.5 s warm (the HTML of
the page alone takes 5.1-6.4 s to load: 5.5 MB of Chip Status tables for the
synthetic chip's 12,836 RB rows).

Budget verdict: < 10 MB **met** (8.82 MB on the 30-qubit stress chip); < 5 s
for the download **met** (2.3-2.9 s); building every preview on that chip
takes 9-15 s and is **not** within 5 s.

What the size/speed work changed on the big chip (same sections): the file
12.2 MB -> 8.8 MB (whitespace between tags 2.1 MB, CSS pruned 914 KB ->
100 KB), press -> file 5.5 s -> 2.3-2.9 s (one tokenizer pass, memoized
redaction of repeated texts and tags: finalize 4.0-4.5 s -> 1.4-2.0 s).

## 10. Fault injection (one per claim)

| claim | what was done | outcome |
|---|---|---|
| (a) an unchecked section is not in the file | Journey J3, both chips: for each of the 8 sections the page picks a value only that section shows (verified absent from every other section and the page shell), builds the file with all sections, then with that one unchecked; for the raw tree, a stored JSON string checked in the unpacked payload. Server gate: `finalize` given a document with an undeclared section. jsdom: the serializer after unchecking Pulses and Raw. | 8/8 sections on both chips: present with, absent without, section element gone; raw blob gone with it. Server: HTTP 400 "not checked: zline". jsdom: no `rep-pulses` table, no `<symbol>`, no script, no JSON blob. |
| (b) redaction ON hides network/paths everywhere, OFF shows them | J4, both chips: secrets = network host, cluster name, data folder, chip folder; file built ON, raw tree unpacked (big: 8.98 MB of JSON) and searched; the switch flipped with a real click (page rebuilds under it), file built OFF. pytest adds a host-like key outside `network`, a POSIX path in free text, and a client-drawn SVG repeating the cluster name and host. | ON: 0 of 4 secrets anywhere, raw included; OFF: 4/4 present as stored; the client-drawn repeat is caught by the server pass. |
| (c) the file opens offline | J2 + J4d, both chips: the saved file opened from disk with `Network.emulateNetworkConditions(offline)`, every request recorded. | 0 non-file requests, 0 loading failures, 0 console errors (redacted and unredacted); raw tree opens (gzip via `DecompressionStream`), map + rack SVG present, computed body/table/heading styles equal the page's. |
| (d) a number equals its live page's | J5, both chips: Z-line stats vs `/zline/data`; wiring LO + FSP vs `/api/instrument/data`; every Diagnostics message vs `/diagnostics`; Trends newest f_01 vs `/topology/trends` last point; a pulse row vs `/pulses`. pytest: chip-status T1 vs `/qubits`, raw tree vs the files on disk. | All equal (big: 16/16 diagnostics messages; small 2/2). |
| (e) a failing source says so | J6: CDP `Fetch` answers HTTP 500 for the Z-line section and the rack frame. pytest: 7 section sources monkeypatched to raise (zline rows, pulse index, trends metrics, instrument model, diagnostics findings, raw payload, `list_qubits`) + the chip-status builder. | "Could not be built: HTTP 500 -- planted fault" in the section; "Could not be built: the drawing page could not be loaded" for the rack in 2 s (not after the 20 s timeout); the file carries both lines. pytest: every section shows "Could not be built: RuntimeError: planted fault in ..."; page HTTP 200. |

## 11. Mutation check

49 mutations of the shipped code (finalize gates, the final pass, every
redaction layer, the section failure path, the trends legend and crop, the
pulses / z-line / wiring / diagnostics / raw builders, the registry, the
switch default, and seven of the page serializer's steps), each run against
the pins that claim it: **49/49 RED**. One was GREEN on the first sweep: the
server's "no Overview -> no chip name in the title" gate, because the test
built a page whose title was already rendered without the name; the test now
models the real path (Overview unchecked after the page loaded) and goes RED
under the mutation.

## 12. Residual risks

* Trends and Z-line pictures are a second DRAWING (static SVG) of the same
  series, not Plotly: axes, ticks and colours differ from the screen; the
  numbers do not. The /zline page's second figure (a flux pulse through the
  filters) is not in the report -- the table and the step response are.
* Chip Status > Trends has no time window on screen; the report's window
  crops the same series and puts the line's value at the window edge (the
  straight line the chart draws between the two neighbouring points).
* Redaction is a rule, so it over- and under-reaches: an IPv4-shaped
  version string (`3.4.0.1`) and a dotted `name:digits` text are blanked; a
  bare host name stored under a key with no network word (and not followed
  by `:port`) is not, nor a bare port number in prose. Client-drawn pictures
  get the text pass (V + L) only.
* The rack is drawn by loading app.js in a hidden same-origin frame for
  ~1 s; app.js starts its read-only polls there until the frame is removed.
* A packed raw tree needs `DecompressionStream` (Chrome 80+, Firefox 113+,
  Safari 16.4+); an older browser shows "Could not be built: this browser
  cannot unpack the compressed tree".
* CSS is pruned from Chrome's CSSOM: a vendor-prefixed declaration Chrome
  drops is not in the file.
* On the stress chip, building every preview takes 9-15 s (above).
