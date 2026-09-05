# 169 — The autofit judge on the user's own Claude Code login

**Date:** 2026-09-05 · **Branch:** `feat/physics-daily-flow` (worktree `statemanager-ux`) · **On top of:** `367ae0f` (docs/168)

## 0. The ask

> "고객이 claude code 쓸수있거든? plan으로 login가능하게 해. 지금처럼 구독자들이 쓸수있게 (API 키 말고)"

The autofit vision judge (docs/47 doctrine, docs/78 asks) had two live providers —
`anthropic` (Messages API) and `openai_compat` — both needing a key or an endpoint,
and **no settings surface at all**: the only way to turn the judge on was to hand-write
`instance/autofit_ai.json`. Every "the vision judge needs an API key" line in docs/78,
docs/127 and the CLAUDE.md autofit section was true because of that.

The customer's lab has Claude Code on a subscription. This entry makes that login the
judge, from the browser, with no key.

## 1. What was proved before a line was written

Measured on this machine, on the OAuth login (no `ANTHROPIC_API_KEY` in the env):

| call | result |
|---|---|
| `claude -p --output-format json --json-schema <schema>` | `is_error: false`, `structured_output` = a schema-shaped object |
| the same with `--bare` | `is_error: true`, `"Not logged in · Please run /login"` — **`--bare` skips the keychain** |
| `--allowedTools Read` + a PNG path named in the prompt | the model opened the file and reported the test image's title number (`7731`) and its shape (`dip`) |
| a free-text `verdict` field in the schema | the model answered `"defer"` — a sensible word `parse_audit` rejects → the whole call became `abstain: invalid verdict value` |

Two facts shape the provider:

- **`-p` mode has no image input.** A figure travels as a FILE the model reads itself:
  each PNG from `_images_of(bundle)` is written to a private `tempfile.mkdtemp(prefix="sm-judge-")`
  as `figure_N.png`, the prompt names the paths in order ("open these with your Read tool,
  in this order, before answering"), `Read` is the ONLY tool allowed (and no tool at all
  when there is no figure), and the directory is removed in `finally`.
- **The schema carries the parser's own enums.** `_cc_schema(ask)` builds one schema per
  ask from `VERDICTS` / `FAILURE_MODES` / `SIGNATURES` / `COMPARISONS` / `TRIAGE_STATES`,
  and `_cc_ask_of(bundle)` reads the ask off the bundle's system prompt (the four
  `_*_SYSTEM` constants; anything else is the judge ask). The CLI's `structured_output`
  is re-serialised and handed to the unchanged `parse_*` functions.

## 2. What shipped

**`core/autofit/auditor.py`** — a fifth provider, `claude_code`:

- `_DEFAULTS` gains `claude_bin` (blank = `claude` on PATH).
- `claude_code_available(settings) -> (bool, detail)`: only the BINARY is checked here
  (`shutil.which`). Whether it is logged in is answered by the call itself, and that
  answer is surfaced verbatim.
- `_call_claude_code(settings, bundle) -> str`: the subprocess described in §1. Not
  `--bare`. `--permission-mode bypassPermissions` so the Read never prompts. `--model`
  and `--system-prompt` forwarded when present.
- **Every failure is an `OSError`** — non-JSON stdout, the CLI's `is_error` (the CLI's own
  words, e.g. "Not logged in"), and `subprocess.TimeoutExpired`, which is NOT an OSError and
  unwrapped would escape `audit()`'s catch (`URLError, OSError, ValueError, …`) and take a
  running plan down. The ledger records the real reason as an `abstain`.
- The timeout is `max(timeout_s, 120)`. The shared `timeout_s` default (60 s) is
  API-shaped; a CLI call spawns a process, loads a session, reads the figure and answers —
  measured **30.1 s** (judge, one figure) and **34.3 s** (signature) on haiku — so the
  default model would be cut off. A floor, never a cap.
- `enabled` / `audit()` / `_raw()` dispatch to it, so all four asks (judge / signature /
  compare / triage) ride the same door.

**`web/routes.py`** — three routes before `/autofit/resolve`:

- `GET /autofit/ai` → `{provider, model, base_url, claude_bin, max_calls_per_plan,
  timeout_s, has_api_key, claude_code: {available, detail}}`. **The key is never echoed.**
- `POST /autofit/ai` → patch via `save_settings`. A blank `api_key` means *leave it alone*
  (changing the provider must not wipe a key typed last week); `"CLEAR"` removes it. Provider
  ∈ `off | anthropic | openai_compat | claude_code`, else 400; ints validated.
- `POST /autofit/ai/probe` → ONE real call through the configured provider, calling the
  `_call_*` function DIRECTLY (not `_raw()`, which swallows the reason to `None`). 409 when
  off/unconfigured (with the CLI-not-found detail for claude_code), 502 with the provider's
  own words on failure, 200 `{provider, model, sample}` on success.

**`_autofit.html` + `autofit.js` + `style.css`** — an "AI judge" `<details>` block above the
Chip profile on the Auto Calibrate page: Provider select (off / Claude Code login / Anthropic
key / OpenAI-compatible), Model, API key (password; placeholder announces a saved key),
Base URL, claude executable, a `data-ai-for` visibility rule per row, **Save** and **Test**.
Save patches the readiness chip (`#ai-ready-chip`) IN PLACE; the first cut re-pulled the
whole `/autofit` fragment, which collapsed the block and wiped the status the user was
reading. The block's CSS reuses the plan bar's field style as a compact auto-fit grid.

**docs/56** provider list updated.

## 3. Verified

**Real Auditor → real CLI → real figure** (`cc_e2e.py`, no mocks at any layer; a
synthetic low-SNR resonator trace):

```
AUDIT     verdict=reject   mode=noisy   provider=claude_code  model=claude-haiku-4-5-20251001  (30.1s)
SIGNATURE sig=clear                                                                            (34.3s)
E2E OK
```

**Real headless Chrome over CDP** (`cdp_ai_block.js`, scratch instance, from `provider=off`):
block renders as a grid (1054×124 px body); `off` hides every provider row; choosing
Claude Code shows the executable row and hides the key row; a REAL click on Save →
`saved (claude_code)`, the readiness chip reads `LLM audit: claude_code` **with the block
still open**, `GET /autofit/ai` agrees; a real click on Test → `works — claude_code`
through the actual CLI on this machine's login.

**Pins** — `tests/test_autofit_claude_code.py` (19 + 1 driver) and
`tests/autofit_ai_selfcheck.cjs` (23 asserts against the real `autofit.js` and the real
template block):

| sweep | result |
|---|---|
| provider + routes (`sweep_cc.py`, 14 mutations: `--bare`, free-text verdict, one schema for every ask, no system prompt, no model flag, figures out of order, figures left on disk, Read allowed with no figure, `is_error` swallowed, `enabled` ignoring the binary, GET echoing the key, POST wiping a key, any provider accepted, probe hiding the reason) | **14/14 red** |
| timeout (`TimeoutExpired` unwrapped; the floor removed) | **2/2 red** |
| JS (`sweep_js.py`, 10 mutations: the envelope read as the body ×2, blank key sent as `""`, chip not patched, chip class not toggled, rows for the wrong provider, key field not cleared, probe hiding the reason, missing CLI not named, refused save mute) | **10/10 red** |

Regression: `test_autofit_claude_code + auditor + routes + e2e + gates + engine` → 210 passed.

## 4. Defects found on the way (all fixed above)

1. `--bare` → "Not logged in". Pinned.
2. Free-text `verdict` → `"defer"` → parser abstain. Pinned (per-ask enum schemas).
3. `RuntimeError` escaped `audit()`'s catch → now `OSError`. Pinned.
4. `subprocess.TimeoutExpired` escaped it too → wrapped. Pinned.
5. The probe first read a phantom `aud.last_error`, then `_raw()` swallowed the reason →
   calls `_call_*` directly. Pinned.
6. **The three JS handlers read `fetchJSON`'s answer as the body, but it answers
   `{status, body}`** — the settings never loaded, Save said "not saved" over a save that
   had happened, Test said "failed: ?" over a probe that had succeeded. Every Python pin
   was green; only the real browser saw it. Hence the executable jsdom harness over the
   real file, and the same rule as docs/125: a pin over a JS surface runs the JS.
7. Save re-pulled the page and collapsed the block. Patched in place.
8. The figure-order pin (C6) first checked NAMES in order and went green when the bytes
   were reversed — it checks bytes now. The docs/149-era rule again: a green mutation
   means the fixture cannot reach the state.

## 5. What this does and does not change

- The docs/47 doctrine is untouched: the judge still emits `{verdict, failure_mode,
  reason}` and never a number. The provider only changes WHO answers.
- P9 (a real closed loop on hardware) is still open. What is closed is "the vision judge
  needs an API key" — on a machine where `claude` is installed and signed in, it needs
  nothing else.
- The CLI's own rate/usage limits apply per the subscription; `max_calls_per_plan`
  (default 40) still bounds a plan.
- Nothing is stored by SM for this provider except the provider name and, optionally, the
  executable path; the login lives in Claude Code's own keychain.

## 6. Customer checklist

1. In a terminal: `claude` → sign in with the plan (once).
2. Auto Calibrate → **AI judge** → Provider: *Claude Code login (no API key)* → Save.
   The readiness chip should read `LLM audit: claude_code`.
3. **Test** → expect `works — claude_code`. `failed: claude: Not logged in …` means step 1;
   `claude not found: …` means the CLI is not on PATH — type its full path into
   *claude executable*.
4. Model blank = Claude Code's default; `claude-haiku-4-5-20251001` is the fast one measured above.
