# docs/226 — Serving SM under a URL prefix, behind a reverse proxy

2026-09-29, branch `feat/url-prefix` (base `91c8aae`, with `origin/main`
`883bb87a`, `e258f3c6` and `7816b126` merged in). **Not merged to main.** Asked for by a
colleague who mounts SM on a platform that proxies it under a path such as
`https://lab.example/sm/`; their report read v0.9.8, this lands on 1.0.x.

> **Operators (and any AI agent setting this up): read §2 (how to run, proxy
> snippets) and §3 (platform conditions) — that is everything needed to deploy.**
> §6 lists what was not verified. §4–§5 are implementation and test records.
>
> Install: `uv pip install "git+https://github.com/Kyung-hoon-Jung0/QuamStateManager@feat/url-prefix"`
> (or `pip install` the same URL), then `qsm serve --url-prefix /sm --behind-proxy`.

## 1. What

SM can be served from a sub-path. One variable — the mount prefix, `''` at
root — and every consumer of it is the identity at `''`:

- the server knows it as `request.script_root` (a WSGI middleware sets
  `SCRIPT_NAME`, so `url_for` is right for free) and prefixes the ~30 literal
  app paths it emits itself through ONE helper, `routes._rooted`;
- every template writes `{{ root }}` in front of every root-absolute app URL
  (411 attribute literals, 29 JSON `url:` fields, 13 `fetch(`, 4 `htmx.ajax(`,
  8 `{% set base_url %}` — mechanically rewritten, then linted);
- the client learns it from `<html data-root="…">`, read once by
  `web/static/sm-root.js`, the FIRST script of every full document, which
  publishes `window.SM = {root, url(p), path(p)}` and — only under a prefix —
  wraps the three request sinks (`fetch` for string/URL/Request inputs,
  `history.pushState/replaceState`, and every htmx request through an htmx
  EXTENSION named `sm-root`, which htmx runs after every DOM listener).
  Reads that no wrapper can reach (`location.pathname === '/x'`,
  `a.href = '/x'`, attribute selectors, JS-built markup) were hand-edited to
  `window.SM.path(..)` / `window.SM.url(..)` — `app.js` through two guarded
  helpers `_smUrl/_smPath`, the other files through `window.SM ? … : …`.

Rejected on purpose (spec §0): `<base href>` (RFC 3986 — it cannot rewrite
root-absolute paths, ~520 assertions break) and HTML post-processing
middleware (it cannot reach JS-built URLs).

**Root-mounted users see byte-identical behaviour.** With `SM_URL_PREFIX`
and `SM_BEHIND_PROXY` unset: no middleware object is installed
(`app.wsgi_app` is Flask's own bound method), the CSRF check is the same
code, the CSP header string is `==` the old one, every server URL is the same
bytes, `sm-root.js` returns before installing anything, and every rendered
page is byte-identical to `origin/main` after three DECLARED deltas —
`data-root=""` on `<html>`, the one `<script src=…/sm-root.js>` tag per
document, and `window.SM.path(E)`/`window.SM.url(E)` → `E` in inline JS
(spec §3.2 rule 3; identity at root by construction). Pinned by
`tests/test_root_golden.py` against a base checkout (§5).

## 2. How to run

```
qsm serve   --url-prefix /sm --behind-proxy          # browser UI, waitress
qsm browser --url-prefix /sm --behind-proxy          # same + opens the browser
qsm serve   --url-prefix /sm --behind-proxy --frame-ancestors "https://lab.example"
```

| flag / env | meaning | default |
|---|---|---|
| `--url-prefix TEXT` / `SM_URL_PREFIX` | the mount path, e.g. `/sm`. Normalised: `/sm/` → `/sm`, `//a//b/` → `/a/b`; must start with `/`; segments `[A-Za-z0-9._~-]+`; no `.`/`..`, `%`, `?`, `#`, space, control or non-ASCII; ≤ 200 chars. A bad value exits 2 with a one-line message | root |
| `--behind-proxy` / `SM_BEHIND_PROXY` (`1/true/yes/on`; anything else but the false spellings is refused) | trust ONE hop of `X-Forwarded-For/Proto/Host/Port/Prefix` (Werkzeug `ProxyFix`, all five at 1). Never on by default | off |
| `--frame-ancestors TEXT` / `SM_FRAME_ANCESTORS` | extra CSP `frame-ancestors` sources, space-separated, appended to `'self'` (`'none'` alone forbids even same-origin framing). `; , "` and quotes are refused (header-injection guard) | empty = today's CSP, byte-identical |

Precedence: CLI flag > env var > default (typer `envvar=`). `create_app(url_prefix="")`
means root; only `None` reads the env. The desktop window
(`python -m quam_state_manager`) opens `http://127.0.0.1:{port}{prefix}`.

**Config prefix vs header prefix.** `--url-prefix` is authoritative. With
`--behind-proxy` and an `X-Forwarded-Prefix` that normalises to a DIFFERENT
value, SM logs one warning per process and uses the configured one. Header
only (no `--url-prefix`): honoured per request; an invalid or route-colliding
header value is ignored with one warning. **Recommendation: always pass
`--url-prefix` when a prefix exists** — it is correct whether or not the proxy
strips the prefix and whether or not it sends the header, and it is the mode
every render sees the same root in (§6, open issue 1).

**Collision rule.** The prefix's first segment must not equal the first
segment of any SM route (`route_first_segments(app)`, 81 segments at this
head — 80 on the base plus w9's `landing`: `agent api bulk chip datasets diff
explorer landing pulses qubits static …`).
`--url-prefix /qubits` is refused at startup with the colliding route named.
Reason: the tolerant strip (below) and the client's idempotency rule both read
"already starts with the prefix" as "already prefixed".

**The strip is tolerant.** `PrefixMiddleware` strips the prefix from
`PATH_INFO` when it is there and leaves the path alone when it is not, so a
stripping proxy (nginx `proxy_pass http://127.0.0.1:5050/;`, traefik
`StripPrefix`), a non-stripping one (Caddy `handle /sm*`, k8s ingress
defaults) and a loopback client that never heard of the prefix (the agent CLI
via `agent_link`, `mcp.py`, `hook.py`, the desktop window's `_wait_for_server`)
all land on the same route: `GET /qubits` and `GET /sm/qubits` both answer 200
under `--url-prefix /sm`.

**Windows Git Bash** rewrites a POSIX-looking value handed to a native
program: `--url-prefix /sm` (and `SM_URL_PREFIX=/sm`) arrives as
`C:/Program Files/Git/sm`, which SM refuses with its one-line error. Prefix
such a command with `MSYS_NO_PATHCONV=1` (or run it from cmd/PowerShell).

Proxy snippets verified against real nginx 1.30.5 and Caddy 2.11.4 (§5.3):

```nginx
# nginx, strips the prefix, no prefix header, Host preserved  ->  qsm serve --url-prefix /sm
location /sm/ {
    proxy_pass http://127.0.0.1:5050/;
    proxy_set_header Host $http_host;
    proxy_read_timeout 3600s;  proxy_send_timeout 3600s;
    proxy_buffering off;       proxy_request_buffering off;
    proxy_buffer_size 64k;     proxy_buffers 8 64k;   proxy_busy_buffers_size 128k;   # SM's HX-Trigger headers (§3.6)
    client_max_body_size 200m;
}
location = /sm { return 301 /sm/; }
```

```caddyfile
# Caddy, does NOT strip, no header, Host preserved  ->  qsm serve --url-prefix /sm
handle /sm* {
    reverse_proxy 127.0.0.1:5050 {
        flush_interval -1
        transport http { response_header_timeout 3600s }
    }
}
```

## 3. Platform conditions (what the operator must provide)

1. **SM does no authentication and binds `127.0.0.1`.** It is one process per
   user; the platform's own login sits in front. Nothing in SM's file/env
   model changed — every local-PC feature (`/browse`, `/mkdir`, conda probing,
   the agent CLIs) is reachable to whoever the platform lets through.
2. **`Host`**: either preserve it (nginx `proxy_set_header Host $http_host`;
   Caddy's default), or rewrite it to the upstream AND send
   `X-Forwarded-Host: <public host[:port]>` with `--behind-proxy` on. A proxy
   that rewrites `Host` and sends no `X-Forwarded-Host` makes every non-GET a
   403 — that is the same-origin CSRF check working as designed (A's pins:
   the four cases). `--behind-proxy` trusts exactly one hop, so the proxy must
   overwrite any `X-Forwarded-*` a client sends. (Found live: waitress scrubs
   `X-Forwarded-*` by default; `qsm serve --behind-proxy` passes
   `clear_untrusted_proxy_headers=False`, off it is the old call byte for byte.)
3. **Read timeout ≥ 600 s, no buffering.** `/datasets/wait` is a 25 s long
   poll on every page; a Generate/Re-generate build can hold a request ~600 s
   and an agent run-node up to 3600 s. Set `proxy_read_timeout 3600s` (nginx)
   / `response_header_timeout 3600s` (Caddy) and turn response buffering off
   (`proxy_buffering off` / `flush_interval -1`). Verified to 25 s in the rig
   (§5.3); the 600/3600 s holds are covered by config review only.
4. **iframe embedding is opt-in.** The default CSP stays `frame-ancestors
   'self'`; pass `--frame-ancestors https://their-host` to embed cross-origin.
   Known non-feature: `/workbench`'s `frame-src` points at the VIEWER's
   localhost either way (it embeds the user's own qualibrate).
5. Both `/sm/x` and `/x` must reach SM only on loopback — the platform's
   fallback for anything outside `/sm/` is its own 404, which is exactly what
   the rig's proxies do so a leaked URL is loud.
6. **Response-header buffer ≥ 64 KB (nginx: `proxy_buffer_size 64k;
   proxy_buffers 8 64k; proxy_busy_buffers_size 128k;`).** SM answers many
   htmx requests with an `HX-Trigger` header that names every path the
   answer touched (docs/122/144: an undo names its reverted paths, capped at
   `_HEADER_PATCH_CAP` = 150 entries; measured: 1,123 bytes for a 2-path
   batch on the synthetic chip, and past nginx's buffer for a real 5-qubit lab chip's
   4-path batch, whose gate-macro subtree restores dozens of leaves). Found in
   the rig: the Ctrl+Z after that 4-path "Delete together" batch answered **502 "upstream sent too big
   header while reading response header from upstream"** behind nginx's
   default 4–8 KB header buffer, while the same undo works at root and
   behind Caddy (whose default header limit is larger). `proxy_buffer_size`
   governs the header buffer even with `proxy_buffering off`, and nginx's
   config test requires the two companions to agree with it.

## 4. How it is built, by seam

- **Server (A, `web/url_prefix.py`, `web/app.py`, `cli.py`, `main.py`,
  `routes._rooted`)**: `normalize_prefix`, `parse_bool`,
  `validate_frame_ancestors`, `route_first_segments`/`check_collision`,
  `PrefixMiddleware`, `install(app, prefix, behind_proxy)` (installs nothing
  at root; `ProxyFix` OUTSIDE the middleware when behind a proxy);
  `create_app(url_prefix=, behind_proxy=, frame_ancestors=)`; Jinja global
  `root` = `url_root()` (the request's `script_root`, else the configured
  prefix for a background render under `app_context`, else `''`);
  `_build_csp(frame_ancestors)` pinned string-equal to the old constants at
  `""`; `_record_own_port` prefers `SM_BIND_PORT`; `chat_api._sm_url` hands the
  spawned CLI `http://127.0.0.1:{port}{prefix}` when hosted; `SMLink.origin` is
  the netloc (a prefixed base used to send `Origin: …/sm`, which CSRF
  refused); `journal.render(md, root=)`. 72 pins in `tests/test_url_prefix.py`
  incl. a server-literal lint over `web/*.py` + `core/journal.py`.
- **Templates (B)**: the mechanical rewrite; `_sidebar_tree_entries.html`
  imports `entry_rows` `with context` (a macro without context renders
  `root` as `''` — silently wrong under a prefix, byte-identical at root);
  `base.html`'s `href.indexOf("/datasets")` and `onChipPage()` compare
  `window.SM.path(..)`; `<body … hx-ext="sm-root">` only under a prefix.
  `tests/test_template_root_lint.py` (allow-list empty) — extended at
  integration to see an attribute directly after a Jinja tag
  (`{% if x %}hx-get="/…"`) and a single-quoted / conditional `{% set %}`, which
  found four more sites (`_diagnostics_env.html`, `_fit_audit_digest.html`,
  `_pulse_env_strip.html`, `_sidebar_tree_macros.html`) and `_datasets.html`'s
  `_tab_base`.
- **Client (C2 + C1)**: `sm-root.js` (Node-realm safe: only `window.*`),
  `tests/sm_root_selfcheck.cjs` (82), `tests/prefix_hooks_selfcheck.cjs` (26,
  the REAL htmx 2.0.4), `tests/prefix_sites_selfcheck.cjs` (30),
  `tests/test_js_root_lint.py` (allow-list: 4 reviewed lines) and
  `tests/app_prefix_selfcheck.cjs` (242 assertions, root and `/sm` on every
  run). C2 verified the wrappers in real headless Chrome against an echo server
  (20/20 under `/sm`, 20/20 at root).
- **Tests (D)**: `tests/conftest.py` copies `SM_TEST_URL_PREFIX` into
  `SM_URL_PREFIX` so the whole suite runs twice without touching 507
  `test_client()` sites, and REFUSES to run in `/sm` mode on a checkout that
  ignores it (else a vacuous second root pass); `tests/_prefix.py` (`P()`,
  `PREFIX`, `RE_PREFIX`) — ~150 assertion lines converted; `tests/_sm_root_boot.cjs`
  (`install()` as each jsdom harness's first statement: boots `sm-root.js` into
  every window, moves the harness page URL under the prefix, renders
  `{{ root }}` in templates read via `fs`); `tests/tools/render_pages.py` +
  `tests/test_root_golden.py` (root byte-identity vs `SM_GOLDEN_BASE_DIR`) +
  `tests/test_prefix_render_lint.py` (renders every GET route under `/sm` and
  fails on any un-prefixed URL attribute, `url:` field, `fetch(` literal,
  Location/HX-* header, `data-root`, missing `hx-ext`); 29 journeys +
  `prefix_sweep.cjs` follow `SM_BASE_URL` through `tests/browser/journeys/cdp.cjs`;
  the rig (local, not in the repo: nginx 1.30.5 + Caddy 2.11.4,
  signatures/checksums recorded in `VERSIONS.txt`).
- **Integration**: header-only mode kept memoized HTML keyed without the
  root, so a run-watch pre-render at `''` could be served to a proxied request
  — `_TREE_HTML_MEMO`, `_FILTERED_TREE_MEMO` (routes.py) and
  `trend_index.PARAMS_MEMO` (new `render_key=` kwarg) now key on it; the JSON
  memos (`_ALL_VALUES_BODY`, `_LIVE_DIFF_BODY`, `_DATASETS_PAYLOAD`) carry no
  URLs and are unchanged. The w8/w9 merge (883bb87a) added 7 template
  attribute sites, the Json Tree's `/pulses/goto` link + payload
  (`_pulse_gate_payload`, `pulses_page`, `lab_delete_pulses_url` rooted
  server-side; core stays request-free), `setPageSize`'s `base_url` compare,
  `landing-env.js`'s fetch spy, five jsdom harnesses and four journeys — all
  prefixed and pinned; `/pulses/vids`' per-process boot token became a golden
  normaliser (two renders of 883bb87a in two interpreters differ there).

## 5. What was measured

### 5.1 The suite in both modes (head `fdb14b51`, three size-balanced shards each, `cqt`)

| mode | passed | failed | skipped | failures classified |
|---|---|---|---|---|
| root | 12,094 | 7 | 255 | all 7 pass alone on HEAD and on the `e258f3c6` base (two live builds sharing the env, `test_cr_live_env`, `test_qdac_lf_combined`, `test_script_emitter_live`, `test_generate_config_fixes`; the scanner parallel-speedup and store-cache debounce timing bounds; the sidebar-filter race B and D recorded) — 0 deterministic; the run shared the CPU with the user's own SM session |
| `SM_TEST_URL_PREFIX=/sm` | 12,098 | 6 | 252 | 4 pass alone (the scanner bound, a live build, `liveedit_big_grid`'s frame pin, and a landing pin that was root-hardcoded — converted to `P()`); **2 were real** and `/sm`-only: `test_pulses_virtual` caught the virtual Pulses rows carrying un-rooted links (RowMemo renders `_pulse_row.html` through `jinja_env.get_template().render()`, which runs no context processor) — fixed by passing `root=` at both direct renders and folding the root into the memo signature, pinned by a new server lint. After the fix: 0 deterministic |

Root byte-identity golden vs a detached `e258f3c6` checkout (and `883bb87a`
before the last merge): **6 passed**
(every render identical after the three declared deltas + the documented
normalisers; the only new normaliser is `/pulses/vids`' per-process boot
token). Rendered-output leak lint under `/sm`: 0 hits in both full-suite runs (every
GET route rendered under `/sm`); it cannot see a POST door, which is why the
RowMemo defect surfaced through `test_pulses_virtual` in `/sm` mode instead,
and a server lint for direct Jinja renders now stands beside it. Template lint: 0 hits,
allow-list empty. JS lint: 0 hits outside the 4-line allow-list.
Server-literal lint: 0 hits.

`npm run selfcheck` (235 harnesses, run while six pytest shards and two rigs
shared the CPU): root **234 passed / 1 failed** (`chip_jump`, a frame-pump
timing pin); `/sm` **232 passed / 3 failed** (`chip_jump`, `autosync_merge`
— a 2.5 s timer bound — and `liveedit_big_grid`, a per-frame layout pin).
Alone on the same machine `chip_jump` and `autosync_merge` pass in both modes
(the two C1 and D had already recorded as load flakes); `liveedit_big_grid` fails its per-frame layout pin about one run in six on HEAD **and** on the base alike (3 repeats × 2 modes each: HEAD 5/6, `e258f3c6` 5/6) — a timing pin, not a regression.

### 5.2 Real proxies (`run_matrix.sh` → `run_matrix2.sh`, a copy of a real 5-qubit lab chip, heads `6e355ac7` / `fdb14b51`)

**A rig defect found on the way, in D's `run_matrix.sh`** (fixed as
`run_matrix2.sh`, the original left as evidence): each cell `eval`s the
previous cell's `export SM_URL_PREFIX=/sm` BEFORE the next cell's
`proxy_rig.sh up`, and `serve_prefix.py` with no `--url-prefix` calls
`create_app(url_prefix=None)`, which reads that env. Measured from the SM
logs: the two `--behind-proxy` cells ran as `url_prefix='/sm'
behind_proxy=True` (config + header, not header-only) and `root-control` ran
as `url_prefix='/sm'` — its console named `/sm/pair/q2-3/edit`. D's matrix
ran the cells in the same order, so D's "root-control" and header-only
verdicts were the config-prefix mode wearing those names; the first cell of
every run was clean. The fix moves the `unset` in front of `up`.

Cells that ran in the mode their name says, self-checks (proxy `/` → 404
"platform fallback", `/static/app.js` outside the mount → 404, `/sm/` → 200,
`/sm` → 301, a same-origin CSRF POST passes, `/datasets/wait` long poll → 200
after ~25.1–25.3 s) all PASS unless stated:

| cell | SM mode | sweep (34 sidebar pages: open → reload → intact) | `lab_field_edit` (POSTs through the proxy) |
|---|---|---|---|
| `caddy-nostrip-cfg` | `--url-prefix /sm` | 34/34, 0 leaks, 0 ≥400, 0 JS errors, 0 sidebar hrefs outside; crawl: 2 NO-WAY-BACK — the "Edit state.json / wiring.json" badges, the same two D measured at root (a same-path hx swap the crawler cannot classify as a way back; a direct probe shows identical behaviour at root and `/sm`) | 19/20 — the transient "checking" badge missed under load (D: same, 20/20 on re-run) |
| `nginx-strip-cfg` | `--url-prefix /sm` | 34/34, 0 leaks, 0 ≥400, 0 JS errors, 0 sidebar hrefs outside; crawl: the same 2 NO-WAY-BACK records as root | 19/20 (badge) |
| `nginx-strip-hdr` | `--behind-proxy` (header-only, verified from the log) | 34/34, 0 leaks, 0 ≥400, 0 JS errors, 0 sidebar hrefs outside; crawl: the same 2 NO-WAY-BACK records as root; mode verified `url_prefix='' behind_proxy=True` | 19/20 (badge) |
| `caddy-strip-hdr-hostrewrite` | `--behind-proxy`, Host rewritten + `X-Forwarded-Host` | 34/34, 0 leaks, 0 ≥400, 0 JS errors, 0 sidebar hrefs outside; crawl: the same 2 NO-WAY-BACK records as root; mode verified `url_prefix='' behind_proxy=True` | 19/20 (badge); the CSRF POST passes via `X-Forwarded-Host`; mode verified `url_prefix='' behind_proxy=True` |
| `root-control` | none | 34/34, 0 failed, 0 ≥400, 0 JS errors (no prefix: nothing to leak); crawl: 2 NO-WAY-BACK — the baseline the proxy cells equal | 18/20 (the two badge observations, grid + tree); console clean; mode verified `url_prefix=''` — the contaminated run had failed its console check on `/sm/pair/q2-3/edit` |

### 5.3 Real-Chrome journeys behind nginx `/sm/`

`run_w9.sh nginx-strip-cfg` (SM `--url-prefix /sm` behind a stripping nginx,
a copy of a real 5-qubit lab chip, every journey opening `http://127.0.0.1:<proxy>/sm/…` with
`cdp.cjs`'s guard throwing on any same-origin navigation outside the mount),
and the same journeys at `root-control` on the same head as the control:

| journey | behind nginx `/sm/` | root control |
|---|---|---|
| `smallui` (Live Edit edit → Undo → Back → reload, 25 checks) | 25/25 | — |
| `zline` (27 checks) | 27/27 | — |
| `agent_back` (Agent home → Journal → Back → Forward → Back → reload) | every step under `/sm/agent` / `/sm/journal`, reload whole | same at `/agent` |
| `agent_setup_back` (Setup walk, pushState/Back) | whole, journal section restored | — |
| `chip_status` (Chip Status jumps, map inspector, sub-items) | S2/S3/S5 pass (every sub-item scrolls into view, the map inspector opens and closes, 30 RB panels); S1/S4 FAIL: "trends has a clickable point" | **same two FAILs at root** — on this rig no Trends point is openable since `e258f3c6` (a point names the run that WROTE its value, and that run's folder is not under a loaded Datasets folder → "not openable here"); a main-side precondition, not the proxy |
| `pulse_gate` (Json Tree guidance link → Pulses page → Back) | 30/30 after two harness fixes (the link expectation follows the prefix; the reload is fire-and-forget — the first run hung 900 s at `Page.reload` after 13 ok); root control 30/30 | — |
| `pair_add_gate_pulses` (pair Add-gate note → Pulses flux rows → Back → reload) | 8/8 (the note + link → the Pulses page on the pair's flux rows → Back → the pair page whole → reload) | — |
| `ds_outside_fresh` (a run opened from outside the run list → Chip Status → Back) | its first two checks FAIL exactly as at root ("a Trends point opens its run FRESH", "the next table switch keeps that view" — the same Trends-point precondition as `chip_status` S1), then the run exceeded the 900 s bound before its remaining checks — open: the journey's later waits behind a proxy | 10/15: the same two FAILs + three `(fixture)` checks this rig's data cannot satisfy (D: the Datasets journeys need their own rig data); `back` to Chip Status whole |
| `pulses_delete_together` (a lab refusal's "Delete together" offer, the batch, Ctrl+Z, Apply) | **71/71** once nginx's header buffer is raised (§3.6); with the default buffer the Ctrl+Z after the 4-path batch was a 502 and 3 checks fell with it | 71/71 |
| `px_w9_probe` (landing env picker + Pulses open → Back → Forward → reload, every request audited) | **26/26**: the picker discovers 13 envs through the proxy; a row opens its inspector and pushes `/sm/pulses?pulse=…`; Back lands on `/sm/qubits` whole, Forward brings the rows back, reload whole — 0 requests outside the mount, 0 ≥ 400, 0 JS errors over 209 requests | (its two earlier FAIL sets were the probe's own — identical at root) |
| `lab_field_edit` (three edit surfaces = POSTs through the proxy, Apply to live) | 19/20 in every cell (§5.2) | 18/20 |

**One backend death, not reproduced.** The first slot-B rig's SM (up since
19:58, eight journeys served) stopped answering between 20:35:19 (a 200 to
`/sm/param-history/backfill/status` from the Trends page, inside
`ds_outside_fresh`) and 20:35:43 (the first 502, nginx: `connect() failed
(10061)` — the port was gone), with **no Python traceback in `srv_px.log`**;
the OS recorded a WHEA `LiveKernelEvent 124` at 20:06 and a `bad_module_info`
BEX64 application fault at 20:49 — neither attributable to the process. The
two journeys that then ran against the 502s were discarded. On a fresh rig
with `PYTHONFAULTHANDLER=1` the same journey, then every other, ran with the
backend probed (`GET /sm/help`) before and after each — 200 throughout, the
death did not recur. Recorded as an open item (§6), not as a prefix finding.

Three journey defects found on the way, all harness-side: `agent_back`'s
`Runtime.evaluate('location.reload()')` can outlive its own execution context
and never be answered (it hung 900 s behind nginx and completed at root by
luck) — the reload is fire-and-forget now, in `agent_back.cjs` and the probe
(`agent_plan.cjs` still has the pattern; not in this run); and the
`lab_field_edit` "checking badge" observations are timing-bound (the badge is
transient), which every cell shows under load, root included; and my own
`px_w9_probe` opened a Pulses row with a synthetic `.click()`, which real
Chrome does not turn into the row's htmx request (the same three FAILs at
root) — a real mouse click now.

## 6. Open issues

1. Header-only mode (`--behind-proxy` without `--url-prefix`) is supported
   but second-class: every memoized HTML fragment is now keyed on the root,
   yet a render made for a loopback request still carries `''`; the
   recommendation stands — always pass `--url-prefix`.
2. Proxy timeouts of 600 s (builds) / 3600 s (agent run-node): config review
   only; 25 s (`/datasets/wait`) measured through every cell.
3. `/workbench`'s `frame-src 'self' http://127.0.0.1:* http://localhost:*`
   points at the viewer's machine under hosting — known, not addressed.
4. Not verified: the pywebview window in a real GUI under a prefix (mocked),
   `qsm serve --debug` (werkzeug reloader) under a prefix, IPv6 / specific-IP
   binds for `_sm_url`'s loopback address, Linux.
5. Journeys other than the ones in §5.3 were adapted to `SM_BASE_URL` and
   syntax-checked, not run behind a proxy (each needs its own rig data).
6. Caddy's cosign signature: Fulcio chain and Rekor not verified; nginx's key:
   web of trust not checked (D).
7. Questions only the colleague can answer (spec §7): does their proxy strip
   the prefix and which `X-Forwarded-*` does it send; same origin or an iframe
   from another origin; does it preserve `Host`; can the read timeout be
   raised; which version will they re-test. The defaults above are correct
   for every combination.

## 7. Files

`quam_state_manager/web/url_prefix.py` (new), `web/app.py`, `web/routes.py`
(`_rooted`, `_pulse_gate_payload`, the memo keys), `cli.py`, `main.py`,
`core/journal.py`, `core/trend_index.py` (`params_blob(render_key=)`),
`web/chat_api.py`, `core/agent_link.py`, `web/static/sm-root.js` (new), every
first-party `web/static/*.js`, every `web/templates/**`; tests:
`test_url_prefix.py`, `test_template_root_lint.py`, `test_js_root_lint.py`,
`test_prefix_render_lint.py`, `test_root_golden.py`, `test_app_prefix.py`,
`tools/render_pages.py`, `_prefix.py`, `_sm_root_boot.cjs`,
`sm_root_selfcheck.cjs`, `prefix_hooks_selfcheck.cjs`,
`prefix_sites_selfcheck.cjs`, `app_prefix_selfcheck.cjs`,
`sm_root_boot_selfcheck.cjs`, `golden/template_root_allow.txt`,
`golden/js_root_allow.txt`, `golden/prefix_render_allow.txt`,
`browser/journeys/cdp.cjs`, `browser/journeys/prefix_sweep.cjs`,
`browser/proxy_stub.py`; the proxy rig itself is local to the developer machine, not in the repo.
