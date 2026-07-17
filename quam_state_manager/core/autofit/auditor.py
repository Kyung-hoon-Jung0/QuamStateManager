"""Autofit LLM auditor — judge-only fit verdicts (docs/56 §2d, doctrine docs/47).

The model's ONLY job is a discrete trust verdict on a fit the deterministic
gates marked *suspect*. The contract is structurally number-free:

    {"verdict": "accept" | "reject" | "abstain",
     "failure_mode": "wrong_peak" | "no_signal" | "noisy" | "drifted" | null,
     "reason": "<one sentence>"}

* The schema has NO numeric field. If a model volunteers a corrected value it
  is discarded and logged — no code path carries it anywhere (docs/47: an
  acceptance criterion, not a config toggle).
* ``failure_mode`` is qualitative and only selects the family's deterministic
  adaptation rule for the re-measure retry; it never parameterizes math.
* The auditor sees ONLY gate-suspect targets. Deterministic hard-fails are
  never submitted (an LLM accept must not be able to override G3 — one ack
  never collapses two gates).
* Providers: ``anthropic`` (Messages API vision), ``openai_compat``
  (``/v1/chat/completions`` — Ollama/gateways), ``fake`` (deterministic, for
  tests), ``off``. stdlib urllib only — no new dependency, key is BYO and
  lives in ``instance/autofit_ai.json``.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VERDICTS = ("accept", "reject", "abstain")
FAILURE_MODES = ("wrong_peak", "no_signal", "noisy", "drifted")

_SETTINGS_FILE = "autofit_ai.json"
_DEFAULTS = {
    "provider": "off",            # off | fake | anthropic | openai_compat
    "api_key": "",
    "base_url": "",               # openai_compat only (e.g. http://localhost:11434)
    "model": "",
    "max_calls_per_plan": 40,
    "timeout_s": 60,
}


def load_settings(instance_path) -> dict:
    p = Path(instance_path) / _SETTINGS_FILE
    out = dict(_DEFAULTS)
    try:
        out.update(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return out


def save_settings(instance_path, patch: dict) -> dict:
    from quam_state_manager.core import safe_io

    cur = load_settings(instance_path)
    cur.update({k: v for k, v in patch.items() if k in _DEFAULTS})
    Path(instance_path).mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_json(Path(instance_path) / _SETTINGS_FILE, cur)
    return cur


# ---------------------------------------------------------------------------
# Verdict object
# ---------------------------------------------------------------------------

@dataclass
class AuditVerdict:
    verdict: str                       # accept | reject | abstain
    failure_mode: str | None = None
    reason: str = ""
    provider: str = ""
    model: str = ""
    discarded_numeric: bool = False    # the model tried to emit a number

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "failure_mode": self.failure_mode,
                "reason": self.reason, "provider": self.provider,
                "model": self.model,
                "discarded_numeric": self.discarded_numeric}


_ABSTAIN = AuditVerdict(verdict="abstain", reason="auditor unavailable")


# ---------------------------------------------------------------------------
# Prompt bundle
# ---------------------------------------------------------------------------

_SYSTEM = """You are a calibration fit auditor for superconducting-qubit \
experiments. You judge whether a node's automated fit is trustworthy by \
looking at the figure and the numeric context. You NEVER estimate, correct, \
or emit any numeric value — the calibration number always comes from the \
experiment's own fitter. Respond with EXACTLY one JSON object:
{"verdict": "accept"|"reject"|"abstain", "failure_mode": \
"wrong_peak"|"no_signal"|"noisy"|"drifted"|null, "reason": "<one sentence>"}
accept = the claimed fit is consistent with the data shown.
reject = the fit is clearly wrong (locked a sidelobe, no real feature, …).
abstain = you cannot tell. When uncertain, abstain — never guess accept."""


def build_bundle(*, family_label: str, target: str, fit_entry: dict,
                 gate_reasons: list[str], sweep_note: str = "",
                 figure_path: Path | None = None) -> dict:
    """The provider-agnostic audit request: numeric context + optional PNG."""
    ctx = {
        "family": family_label,
        "target": target,
        "claimed_fit": {k: v for k, v in fit_entry.items()
                        if isinstance(v, (int, float, bool, str))},
        "deterministic_gate_concerns": gate_reasons,
        "sweep": sweep_note,
    }
    image_b64 = None
    if figure_path is not None:
        try:
            image_b64 = base64.b64encode(Path(figure_path).read_bytes()).decode()
        except OSError:
            image_b64 = None
    return {"context": ctx, "image_b64": image_b64}


def _parse_verdict(text: str, provider: str, model: str) -> AuditVerdict:
    """Extract + validate the JSON verdict; discard any numeric emissions."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return AuditVerdict(verdict="abstain", reason="unparseable reply",
                            provider=provider, model=model)
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return AuditVerdict(verdict="abstain", reason="unparseable reply",
                            provider=provider, model=model)
    verdict = obj.get("verdict")
    if verdict not in VERDICTS:
        return AuditVerdict(verdict="abstain", reason="invalid verdict value",
                            provider=provider, model=model)
    fm = obj.get("failure_mode")
    if fm not in FAILURE_MODES:
        fm = None
    # numeric-emission guard: any extra numeric field is discarded + flagged
    discarded = any(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for k, v in obj.items()
                    if k not in ("verdict", "failure_mode", "reason"))
    return AuditVerdict(verdict=verdict, failure_mode=fm,
                        reason=str(obj.get("reason") or "")[:500],
                        provider=provider, model=model,
                        discarded_numeric=discarded)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _post_json(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _call_anthropic(settings: dict, bundle: dict) -> str:
    content: list[dict] = []
    if bundle.get("image_b64"):
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": "image/png",
                                   "data": bundle["image_b64"]}})
    content.append({"type": "text",
                    "text": json.dumps(bundle["context"], indent=1)})
    out = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": settings.get("api_key", ""),
         "anthropic-version": "2023-06-01"},
        {"model": settings.get("model") or "claude-haiku-4-5-20251001",
         "max_tokens": 300, "system": _SYSTEM,
         "messages": [{"role": "user", "content": content}]},
        float(settings.get("timeout_s", 60)))
    parts = out.get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def _call_openai_compat(settings: dict, bundle: dict) -> str:
    content: list[Any] = [{"type": "text",
                           "text": json.dumps(bundle["context"], indent=1)}]
    if bundle.get("image_b64"):
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/png;base64,"
                                             + bundle["image_b64"]}})
    base = (settings.get("base_url") or "").rstrip("/")
    headers = {}
    if settings.get("api_key"):
        headers["Authorization"] = f"Bearer {settings['api_key']}"
    out = _post_json(f"{base}/v1/chat/completions", headers,
                     {"model": settings.get("model") or "",
                      "messages": [{"role": "system", "content": _SYSTEM},
                                   {"role": "user", "content": content}],
                      "max_tokens": 300},
                     float(settings.get("timeout_s", 60)))
    choices = out.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "")


class FakeProvider:
    """Deterministic test double. ``script`` maps ``(node_hint, target)`` or
    ``target`` → a verdict dict; unmatched → abstain. Counts calls."""

    def __init__(self, script: dict | None = None):
        self.script = script or {}
        self.calls: list[dict] = []

    def __call__(self, bundle: dict) -> str:
        self.calls.append(bundle)
        ctx = bundle.get("context") or {}
        key = (ctx.get("family"), ctx.get("target"))
        obj = self.script.get(key) or self.script.get(ctx.get("target")) \
            or {"verdict": "abstain", "failure_mode": None,
                "reason": "fake default"}
        return json.dumps(obj)


# ---------------------------------------------------------------------------
# The auditor
# ---------------------------------------------------------------------------

class Auditor:
    """Per-plan-run auditor with a hard call budget. Never raises into the
    engine — network/provider failures come back as ``abstain``."""

    def __init__(self, settings: dict, fake_provider: FakeProvider | None = None):
        self.settings = dict(settings)
        self.fake = fake_provider
        self.calls_made = 0

    @property
    def enabled(self) -> bool:
        p = self.settings.get("provider", "off")
        if p == "fake":
            return self.fake is not None
        if p == "anthropic":
            return bool(self.settings.get("api_key"))
        if p == "openai_compat":
            return bool(self.settings.get("base_url"))
        return False

    def audit(self, bundle: dict) -> AuditVerdict:
        provider = self.settings.get("provider", "off")
        model = str(self.settings.get("model") or "")
        if not self.enabled:
            return _ABSTAIN
        budget = int(self.settings.get("max_calls_per_plan",
                                       _DEFAULTS["max_calls_per_plan"]))
        if self.calls_made >= budget:
            return AuditVerdict(verdict="abstain",
                                reason=f"LLM budget exhausted ({budget} calls)",
                                provider=provider, model=model)
        self.calls_made += 1
        try:
            if provider == "fake":
                text = self.fake(bundle)                      # type: ignore[misc]
            elif provider == "anthropic":
                text = _call_anthropic(self.settings, bundle)
            elif provider == "openai_compat":
                text = _call_openai_compat(self.settings, bundle)
            else:  # pragma: no cover
                return _ABSTAIN
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            logger.warning("LLM audit call failed: %s", exc)
            return AuditVerdict(verdict="abstain",
                                reason=f"provider error: {exc}",
                                provider=provider, model=model)
        v = _parse_verdict(text, provider, model)
        if v.discarded_numeric:
            logger.info("LLM verdict carried a numeric field — discarded "
                        "(judge-only contract)")
        return v
