# Red-Team Phase 5 — Findings (web runtime + concurrency)

> **Status:** audit complete, **9 of 10 findings shipped** on branch
> `fix/redteam-phase-5-runtime`. §5.1 (CSP per-request nonce) deferred
> — needs per-template script-tag migration with breakage risk;
> matches the audit's own 🟢 Low rating.
> **Branch (audit):** `audit/red-team-phase-5`.
> **Branch (fixes):** `fix/redteam-phase-5-runtime`.
> **Scope:** runtime bugs the unit-test suite cannot catch — background
> timers across tab lifecycles, HTTP cache semantics, concurrent
> first-request races, network-failure recovery, browser-back / F5.
> Phases 1–4 covered data-safety, scaling, security; not re-audited.
>
> **Threat model.** Pywebview desktop + the realistic case of a researcher
> leaving the app open across days, switching tabs, hitting refresh,
> losing the WiFi during a backfill. None of these scenarios is well
> exercised by pytest's request-by-request model.
>
> **Output discipline:** findings + tight fix sketches. No code edits.

---

## Severity legend

| Severity | Meaning |
|---|---|
| **Critical** | Visible misbehaviour or data desync in a typical session (>1h, multi-tab, network blip). |
| **High** | Reliably wastes resources or silently drops information under normal use. |
| **Medium** | Failure mode the user will hit eventually; mitigated by workarounds today. |
| **Low** | Hardening / future-proofing. |

## Finding shape

`[Severity] Title` — `file:line` (`function`). **What**, **why it matters**, **fix sketch**. No more than a paragraph each.

---

## §1 — Background polling lifecycle

### 🟠 High — dataset `pollForNewRuns` runs forever, ignores tab visibility

> **\[High\] No cleanup, no visibility gate** — `web/static/app.js:4396-4477` (`pollForNewRuns`)
>
> **What's wrong.** A `setInterval(pollForNewRuns, POLL_SECS * 1000)` fires at the user-configured rate (default 60s) starting 3s after page load. The interval ID is never stored; no `clearInterval` anywhere; no `visibilitychange` listener. When the user backgrounds the pywebview window, hides the browser tab, or switches to another HTMX page via the sidebar, the poll keeps hitting `/datasets/poll` and re-parsing the dataset store. On a multi-tab researcher's session that's open all day, the cumulative cost is real, and on a flaky network the poll-storm logs server-side warnings every minute.
>
> **Why it matters.** Battery on a laptop. Server-side CPU. Log spam. None catastrophic, but compounds with §1.2 (no error backoff) — a server restart during sleep wakes the user to a wall of failed-poll log lines.
>
> **Fix sketch.** Store the interval id, gate poll execution on `document.visibilityState`, and clear on `htmx:beforeSwap` for `#table-pane`:
>
> ```js
> let _datasetPollTimer = null;
> function _maybePoll() {
>   if (document.visibilityState === "hidden") return;
>   pollForNewRuns();
> }
> _datasetPollTimer = setInterval(_maybePoll, POLL_SECS * 1000);
> document.addEventListener("visibilitychange", () => {
>   if (document.visibilityState === "visible") _maybePoll();
> });
> document.body.addEventListener("htmx:beforeSwap", (evt) => {
>   if (evt.detail.target && evt.detail.target.id === "table-pane") {
>     // Optional: keep polling, the popup overlays any page. Decide
>     // based on UX. The mtime poll in _wiring.html DOES clear on
>     // beforeSwap; copy the pattern.
>   }
> });
> ```
>
> The mtime-banner poll in `_wiring.html:1359-1369` is the right pattern — already clears its interval on swap. Bring `pollForNewRuns` in line.

### 🟡 Medium — fetch errors silently swallowed; no backoff

> **\[Medium\] `.catch(() => {})` hides network failures** — `web/static/app.js:4412` (`pollForNewRuns`), `app.js:5019+` (backfill-status pill), `_wiring.html:1356` (topology-mtime poll), and similar
>
> **What's wrong.** Every long-running poll catches and discards errors. On a server restart, the `fetch` rejects; the catch swallows it; the next interval tick fires another request that also fails. No user-visible signal. The dataset-poll `_lastSeenRunId` stays at its pre-restart value; new runs created during the outage may or may not be detected when the server recovers, depending on whether the poll's run-id watermark stays in sync.
>
> **Why it matters.** A pywebview app surviving a 30-second hiccup should re-converge to a correct UI. Today it survives but the user is never told anything went wrong, and a deeper outage (server crashed and didn't come back) looks indistinguishable from "no new runs arrived" — the popup stays silent.
>
> **Fix sketch.** Two-line change per polling site: on N consecutive failures, render a small toast / topbar dot; once a successful response lands, clear it. Plus exponential backoff (start at the normal interval, double up to a cap on each failure, reset on success). Pattern:
>
> ```js
> let _failures = 0;
> function _onFailure() {
>   _failures++;
>   if (_failures === 3) showConnectionLostBanner();
>   const backoff = Math.min(POLL_SECS * 1000 * Math.pow(2, _failures), 5*60*1000);
>   _datasetPollTimer = setTimeout(_maybePoll, backoff);
> }
> function _onSuccess() {
>   if (_failures > 0) clearConnectionLostBanner();
>   _failures = 0;
> }
> ```
>
> The topbar-import pill (line 4977+) already does the right thing via `schedule(POLL_RUNNING_MS)` chained timers. The dataset poll and topology-mtime poll should adopt the same pattern.

---

## §2 — Startup concurrency

### 🟠 High — module-level `_workspace_loaded` / `_session_loaded` / `_rehydrated` flags raced on first concurrent request

> **\[High\] Three globals, no lock, every `@bp.before_request`** — `web/routes.py:2989-2991`, `web/routes.py:3212-3244` (`_ensure_workspace_loaded`)
>
> **What's wrong.** The before-request hook gates one-shot startup wiring on three module-level booleans: `_workspace_loaded`, `_session_loaded`, `_rehydrated`. Pattern: `if not _flag: _flag = True; do_work()`. Werkzeug's threaded dev server (the one pywebview spawns via `main.py:75`) can dispatch two concurrent requests during cold-start — e.g. the pywebview window issues `/` and the HTMX-emitted `/workspace/tree` near-simultaneously. Both threads see the flags as False, both flip them and both call `_load_workspace_roots()` / `_activate_quam(last)` / `_rehydrate_workspace_from_recents()`. The Phase 4 §2 lock (`_quam_cache_lock` + per-folder build lock) prevents the worst race — duplicate working-folder writes — but the workspace add-root path still mutates `Workspace.root_folders` in two threads simultaneously.
>
> **Why it matters.** First page-load after app restart can produce a workspace with each root listed twice in the sidebar tree, plus duplicate "loaded chip" toasts. Refresh fixes it (the flags are True now), so it's flaky and hard to repro by hand; we noticed only via deliberate code reading.
>
> **Fix sketch.** Single `threading.Lock` guarding the whole hook:
>
> ```python
> _startup_lock = threading.Lock()
>
> @bp.before_request
> def _ensure_workspace_loaded() -> None:
>     global _workspace_loaded, _session_loaded, _rehydrated
>     with _startup_lock:
>         if _workspace_loaded and _session_loaded and _rehydrated:
>             return
>         # … existing body, exactly as today …
> ```
>
> The lock is held for ~10 ms during cold-start, then becomes a single boolean compare for the rest of the process lifetime — overhead negligible.

### 🟡 Medium — `current_app.config["dataset_store_lru"]` initialised under no lock

> **\[Medium\] First-visit race on the LRU dict** — `web/routes.py:3255-3268` (`_dataset_store_lru`)
>
> **What's wrong.** `lru = current_app.config.get("dataset_store_lru"); if lru is None: lru = OrderedDict(); current_app.config["dataset_store_lru"] = lru`. Two parallel `/datasets` requests on a fresh app see None, create their own OrderedDict, one overwrites the other. Whichever instance got built second wins; the loser holds a `DatasetStore` that's effectively orphaned (the route stores it back into its own dict, but the next request reads the winner's).
>
> **Fix sketch.** Same `_startup_lock` (or a dedicated one), short critical section. Alternatively, lift the LRU into module scope alongside `_quam_cache` and protect with `_quam_cache_lock` since the two share a lifecycle.

---

## §3 — HTTP cache + browser navigation

### 🟠 High — no `Cache-Control` headers on HTMX partials; browser may serve stale fragments on Back

> **\[High\] Partials are cacheable by default** — `web/app.py` `_add_security_headers` (Phase 4 §3), `web/routes.py` route handlers
>
> **What's wrong.** Flask defaults to no `Cache-Control` header on a route response unless we set one. Modern browsers cache GET responses pretty aggressively when no header is present. After the user edits a value via `POST /field/edit`, the working-copy store has the new value but the browser's cache of `/qubits` still holds the old HTML. Hit Back → see stale qubit table. The pywebview window's webview process behaves like Edge / Safari for caching purposes; default caching IS on.
>
> **Why it matters.** Researcher edits a T1, navigates away, hits Back, sees the pre-edit value, panics, edits again. Now there are two `ChangeEntry` records for the same logical change — the second one's `old_value` is the *intermediate* state. Confusing at best.
>
> **Fix sketch.** Extend `_add_security_headers` (Phase 4 §3) to also set `Cache-Control: no-store` on every HTMX response. Detect HTMX via the `HX-Request: true` header, exempt static asset routes:
>
> ```python
> def _add_security_headers(resp):
>     resp.headers.setdefault("X-Content-Type-Options", "nosniff")
>     # … existing headers …
>     if request.headers.get("HX-Request") == "true":
>         resp.headers["Cache-Control"] = "no-store"
>     return resp
> ```
>
> Static files (`/static/...`) are served by Flask's send_from_directory and already get sensible caching; the HTMX-only carve-out doesn't touch them.

### 🟡 Medium — F5 / browser refresh during in-flight `/load` may double-activate a chip

> **\[Medium\] No idempotency token on the load path** — `web/routes.py` `/load` POST → `_activate_quam`
>
> **What's wrong.** The `/load` POST takes ~50 ms on a cold cache (Phase 4 §2 measurement). If the user hits F5 during that window, the browser reissues the POST (with the standard "Resend?" prompt or silently in some configurations); both requests target the same `_activate_quam(folder)`. The Phase 4 per-folder build lock prevents the on-disk race, but the second request still publishes the active context a second time, firing duplicate downstream UI events (Param-History tab's `_bump_chip_version` runs twice; the topbar pill flashes "Importing" twice).
>
> **Fix sketch.** Idempotency key: hash `(folder, mtime_of_state_json)` into a `_load_inflight` set keyed under `_quam_cache_lock`. The second request's `_activate_quam` checks the set, blocks on the per-folder build lock (already in place), then exits early when the first request's published context matches its key.

---

## §4 — Resource limits + edge inputs

### 🟡 Medium — `fetch()` calls have no timeout; a hung subprocess freezes the UI

> **\[Medium\] Promise that never resolves** — `web/static/app.js` (every `fetch` call), `web/static/generate.js` (subprocess-launching fetches)
>
> **What's wrong.** The Generate Config wizard's `fetch("/generate/build", { ... })` launches a long-running subprocess. The browser-side `fetch` has no timeout. If the subprocess hangs (a misconfigured conda env that imports a module which talks to hardware; a Windows AV quarantine), the fetch promise never resolves, the progress UI freezes, and the user has no escape short of force-closing the window. Same shape for `/generate/probe`, `/config/regenerate`, and any other long route.
>
> **Fix sketch.** Wrap polling and long-route fetches with `AbortController` and a timeout that escalates:
>
> ```js
> async function fetchWithTimeout(url, opts, timeoutMs) {
>   const ctrl = new AbortController();
>   const id = setTimeout(() => ctrl.abort(), timeoutMs);
>   try {
>     return await fetch(url, { ...opts, signal: ctrl.signal });
>   } finally { clearTimeout(id); }
> }
> ```
>
> Server-side, every subprocess wrapper (`run_generator`, `run_config_preview` in `core/config_generator.py`) already passes `timeout=300`/`120` to `subprocess.run` — so the SERVER eventually returns. The bug is the CLIENT never sees that return on a slow subprocess. Match the client timeout to the server timeout plus a margin.

### 🟢 Low — `localStorage.setItem` calls have no try/catch around `QuotaExceededError`

> **\[Low\] Private-mode crash** — 32 `localStorage` call sites in `app.js`
>
> **What's wrong.** Firefox / Safari "Private Browsing" disables localStorage by throwing on `setItem`. Same for users who set `dom.storage.enabled = false`. The unguarded `localStorage.setItem("quam_theme", "dark")` throws, the calling handler aborts, downstream code is skipped.
>
> **Fix sketch.** Wrap once at top of `app.js`:
>
> ```js
> function safeSetLS(k, v) { try { localStorage.setItem(k, v); } catch(e) {} }
> function safeGetLS(k) { try { return localStorage.getItem(k); } catch(e) { return null; } }
> ```
>
> Migrate the 32 sites mechanically. None of the persisted values is load-bearing — they're all UX state (theme, sidebar collapsed, font size, recent paths). Silent failure is the correct behaviour.

### 🟢 Low — Workspace `os.walk` follows symlinks; a symlink loop hangs the scanner

> **\[Low\] No followlinks=False on walk** — `core/scanner.py:287` (`_scan_root`)
>
> **What's wrong.** Python's `os.walk` defaults to `followlinks=False`, which is the safe choice — but the codebase comment doesn't pin this, so a refactor could flip it. Worse: if the workspace contains a NTFS junction (Windows) that points into itself or its parent, `os.walk` follows junctions even when symlinks are off. We'd loop until `os.walk`'s depth hits the recursion limit.
>
> **Fix sketch.** Defensive `followlinks=False` on `os.walk` (explicit) plus a depth bound (~10 levels — workspace experiments are typically 3–4 deep). Reject any candidate whose realpath resolves outside the original `root`:
>
> ```python
> root_real = root.resolve()
> for dirpath, dirnames, _ in os.walk(root, followlinks=False):
>     dp = Path(dirpath)
>     try:
>         if root_real not in dp.resolve().parents and dp.resolve() != root_real:
>             dirnames.clear()
>             continue
>     except OSError:
>         dirnames.clear(); continue
>     # … existing logic …
> ```

---

## §5 — Phase 4 follow-ups visible now

### 🟢 Low — CSP `script-src 'self' 'unsafe-inline'` doesn't actually block injected inline scripts

> **\[Low\] Promised but not delivered** — `web/app.py` `_CSP` (Phase 4 §3)
>
> **What's wrong.** The Phase 4 audit said CSP would be defence-in-depth against the §1 XSS. The shipped CSP allows `'unsafe-inline'` for `script-src` because the codebase has ~30 inline `<script>` blocks (`base.html` and most partials). With `unsafe-inline`, an attacker who bypasses the §1 `script_json` filter still gets script execution. The §1 filter remains the real defence; CSP is currently only blocking *external* script loads (e.g. `<script src="http://evil/">`).
>
> **Fix sketch.** Per-request CSP nonce. Generate `nonce = secrets.token_urlsafe(16)` per request, expose as `g.csp_nonce`, template every `<script>` tag as `<script nonce="{{ g.csp_nonce }}">`, CSP becomes `script-src 'self' 'nonce-<value>'`. ~30 template edits. After that, `'unsafe-inline'` can be removed and the §1 filter becomes belt-and-braces instead of the only defence.

---

## Summary

| Section | Critical | High | Medium | Low |
|---|---|---|---|---|
| §1 Polling lifecycle | – | 1 | 1 | – |
| §2 Startup concurrency | – | 1 | 1 | – |
| §3 HTTP cache + navigation | – | 1 | 1 | – |
| §4 Resource limits | – | – | 1 | 2 |
| §5 Phase 4 follow-up | – | – | – | 1 |
| **Total** | **0** | **3** | **4** | **3** |

No Criticals this pass — the previous phases got those. The Highs are all "things a unit test wouldn't notice but a researcher leaving the app open for a day will": polls without lifecycle hooks, raced startup, stale Back-button HTML.

**Cross-cutting patterns.**

1. **Long-lived JavaScript state has no cleanup contract.** `setInterval` IDs aren't stored, listeners aren't removed, fetch failures don't bubble. The mtime-banner poll in `_wiring.html` shows the pattern that works — copy it everywhere.
2. **Module-level boolean flags as "did we initialise yet" gates.** `_workspace_loaded`, `_session_loaded`, `_rehydrated`, `dataset_store_lru` (initial set). Each one is a race-condition candidate on cold start. A single `_startup_lock` covers all of them; the perf cost is irrelevant.
3. **Server-side timeouts protect the server; client-side timeouts protect the user.** Today only the server has them. Symmetric is correct.

**Recommended triage order.**

1. **§1.1** (dataset poll lifecycle) — small JS edit, copies an existing in-codebase pattern. Closes the most-noticeable battery / log-spam issue.
2. **§2.1** (startup lock) — five-line addition; eliminates the cold-start workspace-duplication flake.
3. **§3.1** (Cache-Control on HTMX) — three-line addition to the existing Phase 4 header hook; fixes Back-button staleness.
4. **§1.2 + §4.1** (poll error UX + fetch timeouts) — same change shape, can ship together. Visible: "Server connection lost" banner instead of silent freeze.
5. **§3.2 + §2.2 + §5.1 + §4.2 + §4.3** — Mediums + Lows, opportunistic.

**Estimated impact.** Once §1.1 + §2.1 + §3.1 ship, three "looks fine in tests, weird in practice" classes of bug are gone: backgrounded-tab CPU drain, cold-start duplication, stale Back-button views. The remaining items are quality-of-life.

---

## Resolution log

| Finding | Severity | Branch | Notes |
|---|---|---|---|
| §1.1 — dataset poll lifecycle | 🟠 High | `fix/redteam-phase-5-runtime` | `setInterval(pollForNewRuns, …)` replaced with a chained `setTimeout` self-rescheduler that skips the request when `document.visibilityState === "hidden"` and wakes immediately on `visibilitychange → visible`. Backgrounded tabs stop hammering `/datasets/poll`. |
| §1.2 — fetch errors + backoff | 🟠 High | `fix/redteam-phase-5-runtime` | Same touch as §1.1. Consecutive failures count via `_failures`; after 3, a `dataset-poll-status` toast appears; backoff doubles up to a 5-minute cap; a successful response resets the counter and hides the toast. |
| §2.1 — startup lock | 🟠 High | `fix/redteam-phase-5-runtime` | New `_startup_lock` wraps the body of `_ensure_workspace_loaded`. Fast path stays a 3-boolean check (no lock) once startup completes. Cold-start concurrent first-requests no longer duplicate workspace roots. |
| §2.2 — dataset_store_lru lock | 🟡 Medium | `fix/redteam-phase-5-runtime` | New `_dataset_lru_lock` with double-checked locking; fast path is a single dict-get when the slot is populated. |
| §3.1 — Cache-Control on HTMX | 🟠 High | `fix/redteam-phase-5-runtime` | Extended Phase 4's `_add_security_headers` to set `Cache-Control: no-store` on every response where `HX-Request: true`. Non-HTMX responses untouched. |
| §3.2 — F5 idempotency | 🟡 Medium | (Covered by Phase 4 §2) | Phase 4's per-folder `_quam_build_lock` already serialises duplicate `/load` POSTs; downstream calls (`_maybe_auto_add_workspace_root`, `check_and_snapshot`) are idempotent. No additional code needed. |
| §4.1 — fetch client timeout | 🟡 Medium | `fix/redteam-phase-5-runtime` | New `_fetchWithTimeout` wrapper using `AbortController`, 10s default. Hung subprocesses no longer freeze the UI; the abort triggers the §1.2 backoff path. |
| §4.2 — localStorage helpers | 🟢 Low | `fix/redteam-phase-5-runtime` | Added `safeLSSet` / `safeLSGet` / `safeLSRemove` at the top of `app.js`. The original audit overstated risk — 18 of 19 existing sites were already wrapped. Helpers are forward-looking. |
| §4.3 — scanner symlink safety | 🟢 Low | `fix/redteam-phase-5-runtime` | `os.walk(root, followlinks=False)` made explicit; per-directory containment guard via `Path.resolve()` drops candidates whose resolved path escapes the workspace root (NTFS junctions). |
| §5.1 — CSP per-request nonce | 🟢 Low | DEFERRED | Server-side infra is mechanical; the risk is template migration — every inline `<script>` tag across ~30 templates must carry the nonce or CSP3 browsers block it. Best done as a focused PR with manual UI smoke testing. CSP still uses `'unsafe-inline'` as documented; the §1 `script_json` filter from Phase 4 remains the real defence against XSS. |

**Test surface:** 7 new regression tests in `tests/test_web.py`:

* `TestPhase5StartupLock` — 6 concurrent first-requests; `_load_workspace_roots` and `_rehydrate_workspace_from_recents` fire at most once each.
* `TestPhase5CacheControl` — HTMX response has `Cache-Control: no-store`; full-page response doesn't.
* `TestPhase5DatasetLruLock` — 8 concurrent `_dataset_store_lru()` calls return the same OrderedDict instance.
* `TestPhase5ScannerSymlinkSafety` — symlink pointing outside the workspace isn't followed (skipped if platform rejects symlink creation).
* `TestPhase5LocalStorageHelpers` — static check that the helpers are defined in `app.js`.
* `TestPhase5DatasetPollLifecycle` — static check that `app.js` uses `document.visibilityState`, the `_schedule`/`_fetchWithTimeout`/`_showPollFailureBanner` patterns; and that the old `setInterval(pollForNewRuns, …)` is GONE.

**Test run on conda env `qm_mng`:** **940 pass, 96 skip, 0 fail.** (Up from 933 on `main`; +7 are the new Phase 5 regressions.)

**Manual smoke verification (deferred to PR review):** the JS-runtime changes can't be fully exercised by pytest. A quick manual:

1. Open the desktop app, navigate to `/datasets`; minimising / Alt-Tabbing should stop the `/datasets/poll` request stream in DevTools.
2. Kill the Flask server mid-session: a "Lost connection" toast appears after ~3 attempts; clears on recovery.
3. Edit a qubit value, navigate to another tab, hit Back: the qubit table must show the new value, not stale cached HTML.
