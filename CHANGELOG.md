# Changelog

## v0.1.0 (2026-04-05)

Initial release.

### Core

- JSON pointer resolution engine (`#/`, `#../`, `#./`) with cycle detection and caching
- QuamStore: thread-safe loader merging state.json + wiring.json with RLock
- Type-coerced inline editing with undo, batch rollback, and change log
- Atomic saves via tmp file + `os.replace()` with timestamped .bak backups
- 2-way diff with float tolerance, N-way experiment trend analysis
- Real-time search: prefix map + trigram index (<1ms keystroke latency)
- Workspace scanner with LRU cache (max 10 stores, ~40MB)

### Web UI (53 routes, 47 templates)

- Chip Status dashboard with topology cards, heatmap coloring, auto-fit scaling
- Explorer: full JSON tree with lazy loading and pagination
- Qubits/Pairs tables with chain filtering and color-coded fidelity cells
- Property table with grouped selector and CSV/Markdown export
- Instrument wiring diagram
- Diff viewer with side-by-side comparison
- Dataset browser with HDF5 multi-select plotting, bookmarks, tags, notes
- Trend dashboard with sparklines and N-way experiment comparison
- Global search with category tabs
- Pending changes tray with per-change discard
- History panel: auto-snapshot on file change, timeline, snapshot comparison
- Live monitoring: mtime-based polling with configurable interval
- Folder browser dialog with recent folders and path autocomplete

### CLI (10 commands)

- `show`, `list`, `search`, `set`, `diff`, `compare`, `export`, `scan`, `trend`, `table`

### Desktop

- pywebview wrapper with random port assignment and health check
- PyInstaller onedir bundle (instant cold start)

### Quality

- 680 tests across 14 test files
- Error handling for malformed JSON, missing files, Windows file locking
- Path validation guardrails for browse/load endpoints
- HTMX race condition prevention with `hx-sync`
- Loading indicators on all clickable rows

## v0.5.0 (2026-07-16)

Generate-Config wizard: customer feedback batch r3 (`docs/53_generate_feedback_r3.md`).

### Wizard

- CZ pairs auto-orient by frequency: higher-RF_freq qubit = control (per-pair `manual` pin; CR/regenerate never flip; build-time warning safety net)
- User-settable qubit naming: scheme presets (q1…, q0…, grid letters qA1/qB2, custom prefix) + per-qubit rename with one-pass identity remap
- As-you-type inline validation in the Populate step: hardware reach, bands, LO window/demod hole, |amp|>1, immediate feedline Σ|amp|>1 clip, FSP bounds — unit-aware, on the keystroke
- Absolute-dBm power entry (Power input toggle): pulse powers in dBm, port FSP auto-allocated (−20 dBm → FSP 0 / amp 0.1); readout feedline Σ|amp|>1 clip warning now fires in BOTH power modes
- Default-value presets archive: named server-side sets of populate defaults (save/apply/delete from step 6; `instance/gen_presets/`)
- Editable Python build-script export: step-7 toggle writes `01_make_wiring.py` / `02_build_machine.py` / `03_generate_config.py` / `README.md` with the chip's values inlined — verified to rebuild JSON-identical state/wiring in a real QM env

### Fixes

- Folder browser: fetch timeout + Retry, stale-response guard, POSIX breadcrumbs (Linux navigation was broken), mkdir double-submit guard, per-input last-folder memory; `/browse` reports unreadable folders instead of listing empty; POSIX default listing is `$HOME`
- Output/scripts folder paths survive a lost browser session (localStorage mirror)
- Step-4 pair dropdowns re-render on step entry (stale Control/Target after external reorder)
- Qubit renumber now also remaps TWPA qubit lists

### CLI

- New `qsm` console alias + `qsm browser` command (serve + auto-open browser)

## v0.7.0 (2026-07-25)

### Autofit — one-button automatic fitting scheduler (docs/56_autofit_scheduler.md)

- New sidebar page `/autofit` (below Scheduler): one button runs a plan of calibration nodes over chosen targets, gates every fit deterministically, LLM-audits only the residual suspects, reverts rejected node writes, re-measures with adapted parameters, and reports everything from an append-only ledger with per-target keep/applied/retry/defer decisions
- Doctrine (docs/47, binding): the LLM **never emits a number** — its schema is `{verdict, failure_mode, reason}` (numeric emissions are discarded and flagged); corrections are ① deterministic gates ② revert via the node's own `patches[].old` (compare-and-swap, `coerce=False`) ③ re-run with family-keyed parameter adaptations — the calibration numbers always come from the node's own fitter, and a deterministic gate fail can never be overridden by an LLM accept
- Gate ladder per fit: node outcome → physical bands → raw-data cross-check (argmax only where provably valid, spectral signal-presence for oscillation/decay/2-D) → metric consistency/error ratios → history jump limits vs the pre-update anchor; per-family registry is code-curated (`core/autofit/families.py`), with iq_blobs verify-only
- Autonomy modes: `full` (apply with revert guarantee + pre-plan pinned snapshot) and `review` (restore-at-end); while a plan runs, the scheduler edit-lock engages and every `/scheduler/*` control route 409s (no two masters); all writes go through one writer path (build lock → `batch_set` → save → apply-to-live, one pull+re-stage retry)
- Ground-truth sim backend: `/autofit/start {backend: sim}` runs the whole loop hardware-free through the REAL write path (synthetic run folders indistinguishable to every SM reader, corruption modes wrong_peak/no_signal/noisy/out_of_band/drift); a CI accuracy ledger pins per-family false-accept coverage; RealBackend drives the Scheduler chassis with a lost-wakeup watchdog and normalized-prefix + time-window run attribution

### Typed editing + environment validation (docs/56)

- Every list/matrix element is editable via dot-form numeric paths (`confusion_matrix.0.1`) in the Explorer, All values, and livediff accept — with a strict index gate (negative/malformed indices rejected, out-of-range = clean 400)
- Per-key expected types, layered: the selected python env's quam class schemas (introspected in-env, cached version+commit-keyed) > click-to-assign user types (⚙ in the Explorer; env overrides need an explicit confirm) > value inference; wrong-type writes are BLOCKED with provenance ("expected int — quam schema: DragCosinePulse.length")
- State↔env validation in Diagnostics: unknown fields / unimportable classes / missing required fields (the exact things that make `Quam.load()` fail) as aggregated error findings with Explorer deep-links; type/version mismatches as warnings; Probe + deep Validate (real `Quam.load` in the env) from the new card
- Explorer: add key (＋, with the class's missing-schema-keys suggestions), delete key (✕, with pointer blast-radius count), expected-type chips in the editor, server rejection reasons shown inline (no more silent red flash)
- All values v2: arrays + empty containers visible and editable (✎ JSON editor), pointer rows edit-through to their resolved target with a shared-by hint, per-row type chips
- Pulses: the selected env's pulse-class roster overlays the static catalog — env-verified classes lose the caution banner and re-enter DAC linting; false "unmodeled field" warnings for renamed fields disappear
- Fixed (latent since v0.1): the Explorer live-diff overlay always failed with "Could not render the live diff"; Accept-all now applies per-row so one rejected value can't roll back the rest
- Fixed: Infinity/NaN can no longer be written into state.json (invalid strict JSON)

### Cross-platform path handling (Windows / macOS / Linux audit)

- One canonical folder-identity helper (`path_match.fs_key`: resolve → NFC → case-fold only on case-insensitive hosts) now backs the working-copy key, the chip context/build-lock cache, scheduler folder verdicts, and workspace membership — on Linux two case-different folders no longer share (and cross-write!) one working copy; on macOS NFD-typed paths no longer split one folder into two copies; existing working copies migrate automatically
- Loading a chip through a symlink and through its real path now yields ONE context and ONE build lock (they used to race each other over the same working folder)
- Re-generate refuses a case-variant spelling of the source chip as the output on macOS (it would have rebuilt INTO the source and silently lost calibrations); history snapshot ids are validated against traversal-shaped input on Windows
- Folder browser: permission errors can no longer 500 the dialog; `~` is expanded everywhere paths are typed; build output/scripts paths must be absolute (a replayed Windows path on a mac/Linux server used to silently build into a literal `D:\...` directory under CWD); truncated listings say so; dot-folders are completable when you type the dot; POSIX names containing a backslash no longer corrupt every breadcrumb
- New-folder names are checked for cross-platform portability (Windows-reserved names, trailing dots, `<>:"|?*`)
- Datasets: same-tick in-place rewrites on coarse-clock filesystems (SMB/FAT) are now detected (size-aware fingerprints; Rescan forces a true re-check); the sidebar staleness check no longer trusts the local clock (skewed network mounts froze or thrashed it); on macOS two case-variant registrations of one folder dedupe by inode (they used to double every run and break HDF5 lock serialization); symlinked archive folders are discovered like the Datasets page always did (with cycle + runaway-walk bounds)
- macOS conda installs are discovered (`/opt/*`, Homebrew Caskroom, `~/.conda/environments.txt`); selecting a Windows `python.exe` from a mac/Linux server is refused with guidance (its features could never read POSIX work files) except under WSL
- Saves are rename-durable on POSIX (parent-directory fsync); rapid same-second saves keep every `.bak`; shared settings files write atomically; read-only dataset folders return a clear message instead of a 500

### Generate Config wizard + Live State Edit polish (review-r7)

- TWPA ports (pump + isolation/spectroscopy) are now recognized by the step-5 wiring diagram's drag&drop and the wiring table's "Auto-allocated" column — they used to silently reject every drop (the diagram's role-color infrastructure already anticipated TWPA, but the wizard's own allocation regroup never mapped qualang_tools' terse `p`/`i` line-type keys onto it)
- The JSON drill-down panel (Wiring JSON popups, the Generate Config preview) defaults taller and is resizable by dragging its top edge, persisted globally across every call site
- Auto-allocate / Load into app / Preview config now match the compact Back/Next button style instead of rendering as full-size Pico default buttons
- Live State Edit's Properties menu shows every derived column by default (curated AND dynamic) — the prior opt-in model buried fields the search couldn't find; a column is hidden only once the user explicitly unchecks it (empirically timed at ~60ms warm / 106 extra columns on a real 17-qubit chip — no caching layer needed)
- Fixed: unchecking a dynamic column in the Properties menu reloaded the table and silently collapsed the menu itself (the htmx swap replaced the whole pane, including the `<details>` the menu lives in)

### Resonators / Flux / Couplers nav pages (customer request)

- Three new sidebar pages alongside Qubits/Pairs, each a curated channel-scoped table: Resonators (readout frequency/amplitude/length/threshold/time-of-flight, per qubit), Flux (joint/independent offset, flux point, delay, per qubit), Couplers (decouple/interaction offset, delay, per pair) — same filter/sort/JSON-drill-down/pagination as Qubits/Pairs, and a row click opens the existing Qubit/Pair inspector
- A qubit/pair without that channel is simply not a row (`QueryEngine.get_qubit`/`get_pair` gained `has_resonator`/`has_z`/`has_coupler`, a structural — not resolved-value — presence check); Flux and Couplers additionally hide themselves from the sidebar entirely when NO qubit/pair on the chip has that channel (e.g. a fixed-frequency chip has no Flux nav item), computed once per request in `_ctx()`

### Chip Status topology: control/target orientation on the pair edges (customer feedback)

- Every pair edge that resolves to a real control/target now shows which end is which: the edge label's own target-facing EDGE is reshaped into a point (clip-path flag/price-tag shape — right/left/up/down, picked from the edge's dominant grid axis), with control→target text inside, always upright. Two review rounds got here: a separate in-line arrowhead on the line read as too small to notice (removed); a small triangle glued onto the label box then read as a decoration rather than a direction, so the whole target-facing side of the box became the point instead
- Fixed along the way: `QueryEngine.get_pair`/`get_topology` derived `qubit_control`/`qubit_target` by taking the last `/`-segment of the raw pointer string — correct for chips that point straight at the qubit (`"#/qubits/qA2"`), but real tunable-coupler chips route through a wiring-side indirection (`"#/wiring/qubit_pairs/<w-pair>/c/control_qubit"` → `"#/qubits/qA2"`), where the naive split returned the literal wiring key `"control_qubit"` instead of a qubit id — surfacing as `"control_qubit"→"target_qubit"` in the topology label, and in the Pairs/Couplers tables' Control/Target columns. Now fully resolved through the pointer chain (which already recurses through any depth) and read off the landed qubit's own `id`, falling back to the old heuristic for anything that doesn't resolve to a qubit (dangling pointers, already-bare ids)
- NOT fixed (flagged, out of scope): `core/cr_semantics.pair_endpoints()` has the identical bug independently (it can't reuse the fix — it takes a bare pair dict, no store/root to resolve pointers against) and feeds `physical_edge_key`/`directed_partner`/the Generate wizard's `_pair_arch` gate-family detection; none of these are rendered as visible text today, but any of them could show/behave wrong for chips using the wiring-indirection convention. A proper fix needs `pair_endpoints()`'s signature widened to accept a store/root — deferred, separate from this fix.

### Chip Status heatmap: default color palette (customer feedback)

- The default heatmap + distribution-bar color palette changed from GnBu (pale mint → teal → dark blue) to the existing-but-unused "Blues" palette (pale blue → navy) — customers found GnBu not a pretty color; Blues reads as simpler/more modern and coordinates with the app's own primary-blue accents. A user's saved palette choice (`quam_heatmap_palette`/`quam_bar_palette`) is unaffected; only the un-set default changed. Considered but not built: a bespoke palette anchored exactly on the app's primary-blue token (kept in mind if Blues doesn't land well in practice)
- The Overview panel's per-metric tile accents (1Q/2Q fidelity, readout fidelity, T1, T2echo, T2Ramsey, gate coverage) were a SEPARATE hardcoded 3-tier red/amber/green pass-fail scheme, untouched by the palette change above — customer feedback: "pass/fail is ultimately just a magnitude read anyway," so `cardColor()` now interpolates continuously through the SAME active palette (`dCfg.colorScale`) instead of stepping between 3 fixed RAG colors; the old threshold triplets are kept only as a `[hi, lo]` calibration range (the middle "warn" cutoff no longer has a distinct role on a continuous gradient)

### Chip Status topology: default value colours + edge tiers unified to the app-blue ramp (customer feedback)

- The default qubit-card metric chips still repainted every thresholded value (T1, T2, fidelities) with a discrete green/amber/red spec verdict ("Spec colours" mode, on by default) while the "... more" hover panel kept the continuous palette — users called the clashing RAG mix dated. The spec-verdict repaint and its Health-bar checkbox are gone: colour now ALWAYS means relative magnitude on the shared palette (Blues by default), identical across the default cards, the hover panel and every heatmap cell. Pass/warn/fail verdicts still live on their dedicated surfaces (Health tiles, verdict banner, worst-qubit list, report card) — they just no longer paint the numbers
- The pair-edge fidelity tiers (SVG lines + edge labels) moved from green/orange/red to the same single-hue blue ramp — deep blue = good, washed-out toward the page = bad — with a luminance-flipped ramp on the dark theme (brightest = best against a dark background). A single-hue luminance ramp is inherently colorblind-safe, so the colorblind toggle's special-cased edge-colour overrides are gone too (the toggle itself stays for its other effects)
- Fixed (pre-commit review): `cardColor()` was only half-migrated to its new flat `[hi, lo]` range and still indexed the old nested triplet shape — every Overview tile accent rendered the darkest palette stop regardless of the value

## v0.8.0 – v0.8.4 (2026-07-28 → 2026-07-29)

The project becomes the primary frame, plus the four point releases that followed it over the next two days.

### Project-centric shell (docs/63_project_centric_reorg.md)

- A QUAlibrate **project** is now the organizing frame of the app — a LENS over the same stores, never a data wall. No storage migration; loading a standalone folder keeps working and is displayed as-is
- `GET /` is a project-first landing (lazy project cards + Resume + recents); startup NEVER auto-activates the last chip any more — the landing offers Resume instead, and the remembered project is only a highlight
- The active project is DERIVED on every activation from a stat-cached reverse index over each project's `state_path` (QUAlibrate's own active project wins → a unique match wins → otherwise none; it never guesses), and PINNED by an explicit "Open in SM". The context field is a memo: eviction or a restart re-derives it
- Lenses over the existing stores: history snapshots carry the project they came from (display-only — the raw `chip_key` contracts are untouched), Param/State History headers and rows show it, and Datasets/Trends seed their folder selection from the roots recorded when a project was opened ("All" always escapes; an unscoped session is byte-identical to before)
- The topbar badge shows SM's own scope plus, muted, QUAlibrate's active project when the two differ — a scope that merely differs never re-colours the badge

### Config location picker + WSL bridge (docs/63 §B)

- The QUAlibrate config location can be chosen in the UI and is persisted, sitting below the environment variables in precedence: Windows and Linux homes differ, and a WSL-hosted QUAlibrate install is invisible to a native-Windows default
- Config values are foreign-dialect paths, so path mapping now runs BOTH directions (Windows and WSL) plus the WSL distro-share bridge for POSIX values outside the mounted drives — without it every existence badge on the page lied

### Feedback batches r8–r11

- **SUPER-CRITICAL**: field edits returned 500 in any environment without `typer` installed — the web app imports a CLI helper whose module imports typer at top level. Fixed, plus a one-click "Update f_01" fix on the diagnostic that surfaced it
- The Interactive tab died completely on `ds_*.h5` files that a newer runner environment had written as NetCDF-classic through xarray's scipy engine — the whole tab, not just the one figure. A magic-byte fallback restores it
- Datasets "Load State" stages INTO the open chip's working copy instead of hijacking the context to the run's archived copy
- Projects subnav cap, compact rows, and a config popup on the landing; the real culprit behind the loose landing grid turned out to be Pico's `[type=submit] margin-bottom`

### Pre-release adversarial audit (docs/63 §A)

- Context pin cross-wiring, memo-first stamps, an atomic tray-status cache, and NUL/type hardening — found by an adversarial pass rather than by use

## v0.9.0 (2026-07-31)

Every number on the screen gets a history, and one Ctrl+Z that means what it says.

### Every value's history, in place (docs/20 amendments)

- Every editable value — inspector rows on hover, focused grid cells — carries a clock icon that opens a change-point timeline for that exact dot-path: tracked properties via the SQLite index, any other leaf via a capped direct scan of the snapshot copies, pointers resolved per snapshot
- The timeline is MERGED with a runs tier that reads the workspace runs' own `quam_state` copies directly (newest 60, gated by name and fingerprint), so today's runs appear with working Data links whether or not they have been ingested yet
- Each row names the experiment that introduced the value, with a trigger-coloured mini trend chart, **Use** (fills the edit input — committing stays explicit) and **Data** (opens that run's detail in the inspector)

### Column History + one Ctrl+Z (docs/20 v2b)

- Every bulk-grid column header carries a clock opening a two-tab panel in ONE response: **Changes** (per-row change points over the merged snapshot+runs series, with manual applied edits appearing as their own chips) and **By run** (rows against the last six matching runs, with per-run "Use all")
- **Ctrl+Z is tiered**: wizard undo → an in-memory stack for un-staged grid typing → the server's change-group undo, one Review-tray group per press, with the tray swapping atomically with the response. The tray's undo button runs the same chain and its tooltip names what is next
- Both apply paths snapshot the PRE-apply live state first, which is what powers an explicit "Revert last apply" — Ctrl+Z itself never crosses the apply boundary

### Chip identity is a ladder (docs/20 v2)

- Identity resolves `extras.chip_name` (user-declared, and it travels into every run's state copy) **>** hardware fingerprint **>** the legacy path-derived name — through ONE choke point used for both reads and writes. The chip key stays the canonical on-disk directory name; pretty names live in an alias file so renaming keeps history continuity
- First open of an unnamed live chip asks for a name and stages it through the working copy only; declining is remembered per fingerprint. An optional data folder can be declared alongside it and auto-registers as a dataset root
- A flag-gated migration repairs the pre-ladder contamination where index rows stayed pinned to the path-derived directory while the snapshots had already forked by fingerprint

### State roundtrip, Tab, and the r10 audit (docs/64, docs/65, docs/66)

- Content loaded WHOLESALE into the working copy (a dataset Load State, a State-History stage, a Revert-last-apply) has no change-log entries, so sync must never pull-first over it — it now delegates straight to save-and-push, and the destructive modes ask once before proceeding
- Tab is first-class navigation again: a leaked capture-phase focus trap (reachable by double-opening Ctrl+K) had been swallowing every Tab app-wide. Traps are now leak-proof at the source, both Live-Edit grids hop between edit cells, and the topbar calculator hops its visible inputs
- The r10 adversarial audit closed 19 findings, two of them reproduced first — including a cached run verdict that leaked one chip's values into another after a chip switch

## v0.9.5 (2026-08-09)

Nine days of customer feedback batches r12–r16, the runner/agent programme, and a multi-instance and packaging pass. A point release rather than 1.0: it is substantial but not finished — the runner's signature calibration still needs a provider key, and its loop has not yet been run against hardware.

### Runner + AI calibration agent (docs/78_runner_agent.md)

- The programme that turns Autofit into a one-button bring-up loop: press → experiments run → fits gated → state updated → **done when the gates pass AND a vision judge accepts the signature**. Scope is the nine families of the x180 chain
- Doctrine amendment: not "the AI may not emit numbers" but *a number the AI emitted never reaches state.json before passing a tier-A (the node's own analysis, replayed) or tier-B (grid-exact) verifier*
- A verification context is the triple **(environment × analysis-tree revision × run generation)**; a pinned revision is materialized read-only via `git archive`, never a checkout, because no single environment can replay the whole corpus and the lab's own tree keeps moving. Verdicts carry the context they are only valid inside, and values obtained under different contexts are NOT compared — the refusal is recorded instead
- **Every band is corpus-derived, never invented**: replay real runs, split by the node's OWN accept/reject verdict, keep a floor only where it sits below the accepted minimum and still separates. The ledger is 0 false rejects over 276 accepted and 115 rejected targets, and the method overturned 16 already-shipped gates that were rejecting good fits
- The vision judge's family knowledge is versioned DATA, not prose in a prompt, and the rule that forbids sizing a feature against the swept window is enforced at LOAD — a bad exemplar is dropped and logged rather than taught to the next chip
- Also shipped: a bounded action space (bounds derived from the data, out-of-bound proposals rejected rather than clamped), three tiers of stop-loss including the plan step cap and wall clock, a cross-run consistency review, notifications, and offline replay scoring whose metric is *fewer runs to the same conclusion* — never agreement with the operator

### Feedback batches r12–r16

- **r12 — nothing changes powers quietly.** Editing a port's full-scale power keeps pulse powers constant only if the amplitudes rescale, so the edit now returns a 409 carrying a compensation plan listing every old and new value, and only an explicit choice commits (compensating puts the port and the amplitudes in ONE change group, so it is one tray bundle and one Ctrl+Z). The conservative chip-identity confirm shares the same never-automatic doctrine
- **r13 — datasets.** A nested sidebar tree over the real folder levels with newest-first ordering (the 50-row cap used to keep the OLDEST 50, which was the entire "the sidebar sometimes doesn't refresh" report on days with more than 50 runs); NetCDF-classic support in the Raw Data tab behind one reader adapter; figures-first detail view; dangling-pair handling in re-generate
- **r14 — a number stored as text is now visible.** Values like `"0.13"` were byte-identical to the real thing in every input. They are surfaced actively (diagnostics, a delta-gated banner, Explorer marks), displayed honestly everywhere (quotes and an amber tint), and floats are labelled **real** rather than "number"
- **r15 — information architecture.** The sidebar is ordered structure to health, a Chip Components group lists ALL entities with active marks (marks, never filters), the execution trio is renamed to Experiment Runner / Fit Replay / Auto Calibrate, pulse creation became environment-aware, uv and `.venv` layouts are discovered, and pair creation leads with the gate
- **r16 — re-generate becomes adaptive.** A slot whose only channel had been user-trimmed no longer vanishes from the wizard (the declared port inventory is unioned in, rather than derived from live channels alone); the user's in-wizard Populate edits are protected from being reverted by the value-carrying merge; band is a user-settable column where Nyquist bands overlap; script export defaults on with its path following the chosen output folder. Alongside it: the Pairs page can no longer 500 on a lab's own pair-naming, undo navigates to what it reverted while preserving typing in progress, apply-to-live verifies by reading the file back, and a run-parameter compare table joins the dataset comparison

### One implementation for every before and after (docs/76, docs/77)

- Old, new AND the difference now render through ONE implementation on every surface that shows a change — server-side and client-side, pinned character-for-character against each other because the same screen renders both. The subtraction is exact decimal (in float, 5.2 minus 5.1 prints as 0.10000000000000053) and a difference renders only when it means something: booleans, nulls, pointers and created or deleted subtrees show nothing rather than a fabricated zero
- One-click repair for numbers stored as text: a proposal listing each field with what it is now and what it becomes, plus the refusals with their reasons (identity keys, schema-declared strings, leading zeros, thousands separators). The number itself never changes — only its type — and the whole confirmed set converts in one change group

### Multi-instance, sync and history (docs/79 – docs/89)

- Two windows can drive two chips: sibling windows are visible to each other, runner state is per-chip, and a second runner can no longer drive the same instrument
- Sync gained a third choice — keep mine and overwrite live — and stopped swapping out the panel the user is currently looking at
- Param History indexes EVERY numeric parameter as change points, with a "what changed" feed over all of them; a diff workbench compares two sources across four tabs showing differences only
- Live State Edit shows every property by default and lets search find the rest; Settings and Calculator became sidebar tools

### The chip as a picture (docs/92, docs/93)

- One shared chip layout renders above the component tables, with an honest layout selector, a hero map on Chip Status, per-feedline colours and a legend, the active chain lighting its qubits, and frequency-inequality chevrons on the pair edges

### Derived grid columns address the row's real key (docs/62 amendment)

- A user could not find `exponential_filter` in Live State Edit while Json Tree View found it at once. Three defects stacked: the per-neighbour suffix fold minted column names that exist on NO qubit, cells addressed by formatting that name, and the column cap then cut the real column away with its truncation note filtered out of the response
- The template is now column IDENTITY and the row supplies the ADDRESS — the same split the pair grid has used since it shipped. Measured across 40 real chips: dead columns 1,671 down to 344 (the remainder are runtime self-references and genuinely dangling pointers), live cells 55,906 up to 60,713, and not one cell that resolved before addresses anything different now
- Mode is per row too, and it follows the pointer chain: a cross-reference landing on an inferred value renders read-only instead of offering a text box whose commit would replace the pointer with a literal

### Arrow keys move through the grid again

- Live State Edit hides rows two INDEPENDENT ways, both `display: none` — the search box and the qubit picker — but the movement helpers only ever filtered the first. Pick a subset of qubits and the up/down arrows aimed at a row that was still in the DOM and not on the screen, so `.focus()` became a silent no-op and the keys appeared dead. Left/right kept working, which is what made it look arbitrary
- Vertical movement now also WALKS instead of giving up on the immediate neighbour: that row's cell in this column can be read-only (a per-neighbour operation the qubit does not carry renders as a blank), and stopping there stranded the caret
- The pair grid got the same two fixes, and both grids now skip read-only cells rather than parking on them — those inputs already declared themselves out of the tab order with `tabindex="-1"`, and the handlers had been overriding that
- There was NO arrow-key coverage in the suite before this, which is why it went unnoticed; four pins now cover the plain move, both hide mechanisms, and the read-only skip

### Packaging and CLI

- `pip install .` is a supported path: a per-user instance directory when not running from a checkout, the previously-transitive dependencies declared, and a user-first README with the developer material moved below
- ASCII-only CLI banners — a cp949 console (the Windows default in Korean and Japanese locales) cannot encode an em-dash, so `qsm serve` raised and died before binding the port. The pin that catches this has now caught it twice
- The frozen bundle ships the vision judge's data packs; without them the loader answered with an empty pack rather than raising, which would have silently stripped every exemplar the judge reasons from
- No customer or device identifiers remain in any tracked file

## v0.9.6 (2026-08-09)

One thing, done everywhere: the app now speaks a single search grammar. Users kept asking why typing two words filters the Live State Edit grid and finds nothing in the Json Tree View — the measured answer was 24 search controls behind 13 matcher implementations with five mutually incompatible semantics. This release replaces all of them with one rule set, verified surface by surface in a real browser on real chips.

**The grammar, everywhere:** `space` = AND (narrows, exactly as the tokenizing surfaces always did — a plain-word query is byte-for-byte unchanged, fuzz-pinned); a standalone `|` = OR (`q1 | q2` finds both; binds tighter than AND, so `x180 amplitude | length` means x180 AND (amplitude OR length)); every other pipe stays a literal character, because ket notation (`|e>`) lives in 25.7% of real run descriptions and must remain searchable.

**Where it landed:** both Json-tree search paths (Explorer, dataset-detail state tree, diff workbench, both compare trees), the Live State Edit qubit and pair grids, the Datasets table (composing with its scopes and negation), the sidebar workspace filter, the global search, `/pulses`, the param-history typeahead (whose whole-query SQL `LIKE` had made a multi-word query structurally unable to hit), the scheduler library filter, and the dataset sort-key filters.

### One query grammar: space = AND, `|` = OR (docs/96)

- Users kept asking why two words work in Live State Edit and find nothing in the Json Tree View: the app had grown five mutually incompatible search semantics behind 24 controls. The boolean structure now lives in ONE module (`web/static/search-query.js` + its Python twin `core/search_query.py`, pinned to each other structure-for-structure), adopted in one change by both tree search paths, the Live State Edit qubit and pair grids, the Datasets table and the sidebar workspace filter
- `q1 | q2` finds both; OR binds tighter than AND (`x180 amplitude | length` = x180 AND (amplitude OR length)); every other pipe stays a literal character — measured, not stylistic: ket notation (`|e>`) lives in 25.7% of real node.json descriptions and must stay searchable. A query of plain words behaves exactly as before, pinned by a 2,000-case fuzz and per-surface checks
- Found by the same audit and fixed: the global search's category tabs re-issued the query unencoded, so a search containing `&`, `+` or `#` silently broke
- Second pass, same day: the grammar reached every remaining surface — the global search index, `/pulses`, the param-history typeahead (whose whole-query SQL `LIKE` made a multi-word query structurally unable to hit), the scheduler library filter and the dataset sort-key filters — and the two measured drifts between the Datasets and sidebar parsers were closed (commas now split on both; the negation guard shares one shape, with the remaining difference named as capability, not grammar). Every surface was verified in a real browser on real chips

## v0.9.7 (2026-08-12)

The everyday flow. This release is about the twenty small frictions a user hits
between opening a chip and applying a value — the ones that never made a bug
report because each one is survivable. Six streams, each audited on its own
branch and then together, then run against real chips in a real browser.

**A surface you come back to is the surface you left.** Searching in the Json
Tree View, filtering the Live State Edit grid, scrolling a long table — leaving
for another menu used to reset all of it. The pane is now kept alive: state is
parked when you navigate away and restored when you return, including the query
text, the filtered view and the scroll position. This was the most-repeated
request of the round.

**Editing a grid feels like editing a grid.** Fill-down (`Ctrl+D`) over a
selection, paste a column from Excel, shift-click multi-select, and pin the rows
you keep coming back to so they float to the top. Tab and the arrow keys already
walked the grid; the selection now agrees with them.

**Datasets navigate from the keyboard.** `j`/`k` move between runs, `Enter`
opens, and while you are reading, new runs arriving on disk collect behind an
"N new runs" pill instead of moving the row under your cursor — click it when
you are ready.

**Keyboard polish.** `/` focuses the search box on any surface, `?` shows the
shortcut sheet, `Ctrl+Enter` applies. Measured before shipping: the three new
global handlers cost 0.005 ms per keystroke (the first draft cost 2.34 ms and
was rewritten to check the key before touching the DOM).

**Failures explain themselves.** A load that fails now says why — wrong folder,
missing `state.json`, a folder that holds the right files one level down (with
the candidates listed) — and it no longer wipes the surface you had open. A
live folder that is read-only is named as read-only before an edit is offered
rather than after it fails. And a pointer is marked "dangling" only when it
actually fails to resolve: the mark used to be decided by a comparison that was
true for every pointer on a real chip, painting all 26 of them red.

**Onboarding.** A Help page, and the working-copy model — your edits are private
until you press Apply to live, and that is reversible — taught once in the tray
instead of being folklore.

### Fixed in the same release

- **A qubit-id search filtered nothing.** Typing `qA1` in Live State Edit left
  every row visible on any chip whose pair-gate columns carry the partner
  qubit's name (which is the normal shape for a coupler chip). The two axes each
  treated a token that matched a column AND a row as "neutral", so it matched
  everything. A token that names a row now filters rows; column search is
  unchanged. The pair grid had the same defect and the same fix
- The onboarding tray sentence wrapped to four lines inside the top bar, giving
  every page a 250 px header; it is two lines and 157 px now
- The `Ctrl+D` fill and column paste now apply the same `f_01` <-> RF coupling
  the manual edit path applies, instead of writing the raw number
- A grid reload that never lands (error, navigation) no longer leaves the
  leave-confirm suppressed, which had opened a window where navigating away
  discarded unapplied edits with no prompt
- Browser Back onto a kept-alive route no longer lands on a blank pane

### Verified

Real browser, real chips: a 21-qubit chip (4,851 cells / 231 columns) and a
customer 10-qubit / 9-tunable-coupler chip (5,100 cells / 510 columns, every
route 200 with no traceback). Search lands in 8-60 ms on both grids and
15-193 ms on a fully expanded 18,310-node tree. Full suite at the documented
Windows environmental baseline, no new failures.

### Apply to chip reaches the chip (docs/116)

One press of **Apply to chip** now reaches the live chip in the situation it
usually fails in.

**The bug.** Pressing "Apply to chip" on a dataset run often wrote nothing and
asked instead whether to pull the live state into SM — a question from a
different flow, whose "take live" option would have discarded the run the user
had just chosen. Root cause: the safety gate compared the live files against
*SM's own last sync point*, never against the content it was about to write. So
it fired whenever anything had touched the live chip since SM loaded it —
including when the live chip **already held byte-for-byte what the button would
write**. Reproduced with both sides holding the same value: a provable no-op,
refused. That is the ordinary case for this button, because the run being
applied is usually the very program that last wrote the chip.

**The fix.** An apply that would change nothing is no longer a conflict: SM
advances its sync point and reports success. The carve-out is identical content
only — a live chip holding genuinely different values is still never
overwritten without an explicit choice.

**When it IS a real difference**, the answer now arrives where the button was,
not in a one-line pointer to the top bar, and it offers the choice the press
actually meant: **⚡ Apply run #N over live** · Review changes · Leave live as
it is — with the reversibility (↺ Revert last apply) named on screen.

**One question, one answer.** The gate panels ("You have unsaved edits…",
"This run looks like a DIFFERENT chip…") stacked a browser confirm dialog on top
of a panel that already was the confirmation, so one decision cost two answers.
The dialog is gone; the panel stays. The conflict tray's own force button keeps
its dialog — it has no prose beside it to name what disappears.

### Also

- A drift banner could print a stale count ("N values differ") left over from an
  earlier divergence: the count is cleared everywhere its verdict is, and a
  re-raised banner with no fresh count now says nothing rather than inventing one
- Verified in a real browser on a copy of a real chip; 141 tests across the
  live-write suites pass, with four new pins covering the no-op apply, a real
  difference still conflicting, the in-place continuation, and the single ask

### Auto-apply (docs/117)

**Auto-apply.** Several labs asked for a VS-Code-style auto-save, and this
release changes SM's rule to allow it — deliberately, and only when you turn it
on yourself.

Press **⚡ Auto-apply** once. From then on, editing a value and simply **leaving
the field** writes it straight to the live chip — in the Live State Edit grids,
the Json Tree View, the inspectors, anywhere an edit lands. A live **Applied to
live** log sits at the top of the window, newest first, and every row carries
its own **✕** that puts that one change back.

**The rule, restated.** SM's covenant used to be "the live chip is written only
on an explicit Apply press". It is now: *"a direct live write happens on an
explicit Apply press **or** inside a user-enabled auto-apply session"* — default
OFF, visible on every page while it is on, and switched off automatically the
moment the chip refuses a write. One explicit act still stands between an edit
and the chip; it now authorizes a session instead of a single write. The change
is recorded in `docs/107`, the module that quotes the covenant, and the README.

### What it does and does not do

- **Immediate, without hammering the disk.** A single edit is written with no
  delay; edits that arrive while a write is in flight are coalesced into
  exactly one more, so tabbing across ten rows is one write, not ten
- **Never overwrites a chip that moved.** If something else (an experiment
  node, another window) writes the live files, the next flush refuses, the mode
  switches itself off, and the existing conflict screen appears with the live
  content intact and your edit safe in the working state
- **Every change stays revertible.** The log's ✕ is compare-and-swap: if the
  value has moved on since, it refuses rather than clobbering the newer value.
  ↺ **Revert this session** puts back the state as it was when you armed the
  mode
- **Cannot be armed** on a read-only folder, a dataset archive, or a chip that
  has already drifted
- **Off by default, and off after a restart** — an armed session never outlives
  the window that shows it

### Also

- The onboarding line no longer claims edits stay private while auto-apply is
  on (it says what the mode does instead), and the revert button names the
  session rather than "the last apply"
- History does not flood: one pre-apply anchor per session and a throttled
  post-apply snapshot, instead of a full state copy per edit

### Fixed by the two-chip audit and two customer reports (docs/118)

Audited State load · Config generation · Live edit · Json tree view against two
real chips (a 10-qubit / 9-tunable-coupler chip and a 21-qubit / 31-pair CR
chip), 60 checks. The 21-qubit chip came back clean on all 31. Three real
defects on the other, plus the two reports:

- **Re-generate silently lost pair calibration.** A pair's control/target is a
  pointer, and on a chip built by a modern `quam_builder` it is a TWO-hop
  pointer through wiring. SM read only the last path segment, got the literal
  field name, and dropped every pair from the reconstructed spec with a false
  "references qubit(s) not on this chip" — while the build still reported
  success. Measured: 1,878 pair leaves in the source, 774 in the rebuild. Now
  9/9 pairs, all nine CZ macro variants preserved, and 98% of pair leaves
  carried; the rebuilt chip loads and generates its config
- **A capped list looked like a total.** The transparency panel counted a
  200-entry slice, so a rebuild that lost 1,104 leaves displayed "200". The
  true totals now ride alongside
- **The Interactive panel.** A run opened as a full page could not switch tabs
  at all (Interactive never loaded); nothing re-sized a figure when its
  container's geometry changed (a sidebar collapse left a 615 px plot in a
  748 px box); the render cap could blank a visible tile forever; and Pin &
  Browse round-tripped live plots through a string, leaving figures that could
  never rebuild
- **`vs prev` highlighted things that were not differences.** The row verdict
  and the cell highlight used two different rules — so `100` vs `100.0` was
  listed with nothing highlighted, and a fit that failed in BOTH runs showed
  `nan | nan` in amber. One rule now, with the tolerance every sibling surface
  already had. `/compare`'s "Exact" preset keeps meaning exact

## v0.9.8 (2026-08-20)

Customer feedback round on a real 20-qubit QDAC-biased chip (docs/119,
docs/126 — three feedback rounds, every item verified in a real browser
against the customer's own archive).

**QDAC-II bias in the Generate Config wizard.** Chips that flux-bias qubits
from an external QDAC-II build natively: per-qubit QDAC assignment in the
wizard, trigger wiring on an isolated Connectivity, degrade-only (a missing
customer module costs the feature, never the build). Verified end-to-end on a
real 20Q mixed chip.

**Search finds the coupler.** On a QDAC chip the coupler is the only entity
on an OPX flux port, and its port-chain fields (`exponential_filter`, …) were
on NO grid — the pair grid now expands port chains exactly like the qubit
grid, so Live State Edit search covers every field the chip has.

**Chip Status carries the numbers.** Frequency/2Q metrics render on the map
itself (per-edge best RB/Bell, per-gate pulse-variant toggle), C/T/M markers
half-overlap the stones instead of covering the values, zoom slider + compact
mode for small monitors, and a printable **chip report** (printer icon beside
Chip Status): the component map + all five component tables as one dark,
self-contained HTML you can print or download.

**A value in spec is never an "outlier".** The report card flagged a 99.67%
RB as "16.8× MAD" among 99.85–99.92 siblings — MAD collapses on tight
distributions. Both outlier implementations now ask the spec verdict first.

**Versions, one click.** The top-bar chip is labeled *Versions*, opens
vertically, leads with the diff against the previous version (≤50 rows shown
in place), refreshes live during an Auto-Sync session, and each row's restore
is one concise error-tinted *Pull to Live* button (both force gates intact).

**Apply to chip asks one question.** The dataset State tab's apply now gates
on chip identity ONLY (user-directed): drift is overwritten and named,
pending edits are replaced and reported, ↺ Revert last apply stays armed.

**Everyday polish.** Auto-Sync panel anchored under its bolt (gray off /
orange on, working checkboxes); sidebar search no longer lags or freezes on
retype (hx-sync + one sync group + server memos — clear-the-box measured
350 ms → 1 ms); workspace Refresh is incremental (3.5–4 s → 0.7–0.8 s on a
2,655-run archive) with a visible spinner and a ✓; the brand shows a progress
bar with real N/M counts where a loop reports them; run-number jump + ±10
skips live on the Prev State comparison bar; duplicate sidebar highlights are
gone; every toast has a ✕; the hamburger cycles sidebar → topbar → floating ☰.

**New pulses.** `CosineBipolarPulse` is first-class (catalog + bit-exact
preview), the customer's Gaussian-CZ macro script is one Pulses-page button,
and a bare digital-marker `Pulse` (QDAC trigger) is recognized instead of
"Unrecognized".

**Digital triggers on Instrument Wiring** (post-release addition, 2026-08-20).
The rack diagram now collects and draws digital output ports: a DIG sub-column
per FEM (8 physical slots) with the QDAC trigger lines — read from both the
state channel (`…digital_outputs.<marker>.opx_output`, two-hop pointers
followed) and the wiring-level `qt.digital_output`, deduplicated to one entry
per physical line — shared ports show every qubit on them, the hover popup
shows marker/channel/delay/buffer/shareable/inverted, and a chip with no
digital wiring renders byte-identically to before. Same drawing in the
floating wiring panel and the drag-drop preview.

## v0.9.9 (2026-09-03)

172 commits since v0.9.8. Two headlines: **every menu got faster**, and **the
screen stops resetting under you**.

### Speed

One cause under all of it — every render walked the whole run archive. Invisible
on a local disk, brutal on an SMB share where each `stat` is a round-trip
(docs/141, 142, 143, 154, 155).

- **Chip Status: 7,831 archive file-ops per render → 182.**
- Local-disk A/B at 1,440 runs (~a year of daily measurement): **Param History
  627 → 23 ms (27×)**, Chip Status **247 → 18 ms**. At 30 runs op counts already
  drop ~90%. **No route got slower at any size.**
- **Live State Edit revisit 4–5 s → 1.0 s**; its document **8.98 → 2.30 MB**.
- **Param History first open 18–22 s → 0.5 s**, render **16.9 s → 0.16 s**.
- At 5,000 runs: **chip load 12.8 → 4.2 s** (1.3 s warm), sidebar HTML
  **4.1 MB → 113 KB**.
- Thousands of snapshots on one chip: a warm Versions list is **3 file
  operations**, flat in N — was 4,006.
- **A new run folder reaches the screen in under a second** (push, not polling).

### The view no longer resets

- **Sync / pull / apply keep tree search, expansion, scroll and grid state** —
  only the values change, in place (docs/144).
- Leaving a tab and coming back keeps the page.
- **Red "modified" cells clear the moment the tray goes clean** — they used to
  sit under a "No differences" verdict.
- The loading popup is visible **while you actually wait**.

### Compare

- **Compare Selected opens 2–5 runs side by side** — differing values only,
  baseline column you pick (docs/141).
- Keys column is a **collapsible tree**, with the app's search grammar in it.
- **Figures pair left-to-right even across different experiments** — they used
  to stack A above B (docs/147).
- 2-way names its direction: **`A #256 → B #255`**.

### Versions

- Every row is **EXP / MANUAL / BACKUP** — who made this snapshot, and why.
- EXP rows carry an **`After #<run>`** chip straight to that run's data.
- **Per-row Diff**; tick 3+ for a **differences-only N-way compare**.
- **Take a single value with ✓** — editable before you take it, Ctrl+Z after.
- Zero-change snapshots hidden by default, with an honest count (docs/128, 132).

### Chip Status

- **A 2Q RB number is per-Clifford or per-gate.** One CZ read 97.1% and 99.09%
  on the same page: `StandardRB` is per-Clifford, `InterleavedRB` is per-gate.
  Both are labeled now and the default is the gate number (docs/138).
- **Fidelity splits into 2Q / 1Q / Readout.** An absent GEF says which leaf it
  fills from instead of silently not existing, and the GEF per-state diagonals
  are computed at all (docs/148).
- **Overview tiles are yours** — pick the statistic (avg / median / min / max),
  add, remove, drag to reorder, hover for a per-qubit value list, ⚙ to set every
  tile at once (docs/150–153).

### QDAC-II — reading it, not just building it

v0.9.8 could *build* a QDAC-biased chip; this release can **read, edit and check
one** (docs/136, 137).

- **11 QDAC-biased qubits showed zero bias fields** on their own inspector page.
  They have them now, derived structurally rather than by class name.
- **New `/qdac` page** — trigger wiring as a table of cables
  (`ext1 → con1/fem4/p1 → q1 q9 q17`), the grouping that makes a shared port
  read as correct instead of as a collision.
- **Diagnostics checks these qubits** — nothing linted them before.
- **Bias tee** (QDAC holds the DC point, LF-FEM plays pulses on top) is
  recognized and buildable; its flux port wears its own colour on the rack.
- `qdac` quick-filter on Live State Edit and Json Tree.

### Generate / Wiring wizard

- **Auto-allocate is the default** — step 5 draws its diagram on its own; the
  button used to sit dead beside a list (docs/134).
- **The rack diagram was cropping FEMs**, silently, on every surface — one chip
  read as 3 MW + 2 LF when it has 3 MW + 5 LF. Fit / 1:1 with real scrolling
  (docs/135).
- **Digital trigger ports appear in the wizard's diagram too**, and drag to
  re-cable.
- Environment discovery **6.1 → 1.8 s**, and it names what it is doing.
- The config-path picker gets a folder browser and keyboard navigation.

### Undo

- Every Ctrl+Z / Ctrl+Shift+Z answers with a panel: **path, old → new, and one
  "go to field" button**.
- Held keys coalesce into one step; inline fields take Ctrl+Z too.
- An undo repaints **the cells it changed, not the whole grid** — 2,418 → 55 ms.

### Config Manual (new)

- Sidebar → **a manual for every key in state.json**: 111 classes / ~940 keys,
  from the selected environment's own docstrings plus the official QM docs.
- **F1** on a value, `?` on a tree row, or a grid header opens it.

### Auto-calibration (research — offline only, no hardware in the loop)

- The whole verification stack **replayed against a real 2,655-run customer
  archive** (docs/127); seven defects fixed, including a numpy-2 break and a
  revert path that missed 295 list-indexed targets.
- **Case manuals for eight families across six labs**, plus a future-blind
  replay benchmark that crashes rather than let a run see its own future
  (docs/129–133).
- **The manual never supplies an absolute number** — enforced when the pack
  loads, and it has refused real cases twice.

## Unreleased

### Calculator

- **The Calculator opens as its own browser window** — the ↗ in the popover
  header pops it out (`/calc-window`): movable across monitors, above other
  apps, and it outlives navigation. While it is open, the Calculator button and
  Alt+C bring that window forward instead of opening a second one; Escape closes
  it and it reopens where you left it. Browser mode only — the desktop shell
  keeps the in-page floating popover (docs/156).

### Sidebar

- **The experiment list is easier to read** — larger rows, the run number is a
  bold badge, names wrap at their own `_` joints (two lines at most for every
  name in a 2,655-run archive), and the sidebar opens 300 px wide by default
  (docs/157).

### Param History

- **None means none.** The Properties / Qubits None buttons used to bounce back
  to the defaults (or every qubit) with the whole page re-rendering; an empty
  selection is now a selection, a chip click re-renders only the results, and
  the page-load popup no longer flashes over it (docs/158).

### Live State Edit

- **Ctrl+Z / Ctrl+Shift+Z on a list cell (`exponential_filter`, a confusion
  matrix) now changes what you see.** The value moved in the working copy but
  the cell kept its old preview; it repaints in place now, and the 🕘 value
  history reaches list cells too (docs/159).

### Undo after Apply (covenant amendment)

- **Ctrl+Z / Ctrl+Shift+Z keep working after Apply to live — on the chip.** The
  Apply press was the consent; undo withdraws it, through the same apply door with
  the same never-clobber gate. Default ON, Settings → "Ctrl+Z writes live" (OFF is
  the old stage-only behaviour). State-History stage → Apply, dataset Apply to
  chip and restore-live are walkable too (docs/160).

### Sidebar selection

- **The tick box is a real control**: 18 px, rounded, SM-blue fill with a check,
  a tooltip saying what it selects for, and a hint under the compare buttons
  while nothing is ticked. **Compare Selected / Trend Tracker collapse an open
  run detail first** so the result is on top, not under it (docs/161).

### Review sweep (docs/156–161)

- A ten-angle code review of this round found and fixed 15 defects, most in the
  new live undo: a mixed stage+edit apply journaled paths twice, the tray ✕ and a
  failed save could corrupt the walk, a concurrent edit from another window
  could ride a Ctrl+Z onto the chip, a recycled PID hid the user's own history,
  NaN leaves blocked wholesale undos, pointer re-links could not be redone, and
  the calculator window lost typed values after a page reload (docs/160 §5c).

- A second round over that sweep found 15 more, all fixed: a refused redo no
  longer jams the redo stack, a skipped step keeps the walk and the stack in
  step, ↺ Revert last apply stays anchored on the apply you pressed, a held
  Ctrl+Z no longer writes two history snapshots per press, a coalesced burst
  finishes, a reverted list cell drops its "unapplied edit" marker, and a
  calculator window that answers the page's ping is remembered by every entry
  point (docs/160 §5d).
