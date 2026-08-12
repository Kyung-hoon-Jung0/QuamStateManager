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

## v0.9.7 (2026-08-11)

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

## v0.9.8 (2026-08-12)

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
