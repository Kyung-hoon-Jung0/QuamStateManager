"""Autofit LLM auditor — judge-only fit verdicts (docs/56 §2d, doctrine docs/47).

The model's ONLY job is a discrete trust verdict on a fit the deterministic
gates marked *suspect*. The contract is structurally number-free:

    {"verdict": "accept" | "reject" | "abstain",
     "failure_mode": "wrong_peak" | "no_signal" | "noisy" | "drifted"
                     | "feature_present_fit_failed" | null,
     "reason": "<one sentence>",
     "feature_visible": true | false | null,      # v2 in-loop vision hints
     "direction": "left" | "right" | null}        # (qualitative, never numbers)

* The schema has NO numeric field. If a model volunteers a corrected value it
  is discarded and logged — no code path carries it anywhere (docs/47: an
  acceptance criterion, not a config toggle).
* ``failure_mode`` is qualitative and only selects the family's deterministic
  adaptation rule for the re-measure retry; it never parameterizes math. The
  v2 hints are the same class: ``feature_visible`` splits a failed fit into
  the step-refine vs the widen/seed ladder, ``direction`` picks WHICH way a
  seed-shift rung looks — magnitudes stay window math (docs/56 v2 rail ①).
* The auditor sees gate-SUSPECT targets (trust verdicts) and — v2, families
  with no deterministic raw-data localizer only — node-FAILED targets for a
  presence reading. A presence reading refines the failure_mode of an
  already-failed verdict; it can never turn a deterministic fail into a pass
  (one ack never collapses two gates).
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quam_state_manager.core.autofit import judge_pack

logger = logging.getLogger(__name__)

VERDICTS = ("accept", "reject", "abstain")
FAILURE_MODES = ("wrong_peak", "no_signal", "noisy", "drifted",
                 "feature_present_fit_failed")
DIRECTIONS = ("left", "right")
# P3b: the §1.3 terminator. A (step, target) is DONE only when the
# deterministic gates pass AND the judge calls the signature `clear`.
SIGNATURES = ("clear", "unclear", "absent")
# P3b: D-8 tier 2b. Comparative judgment is far more reliable than
# self-reported confidence, and stays a discrete verdict with no number.
COMPARISONS = ("better", "worse", "same")

_SETTINGS_FILE = "autofit_ai.json"
# the judge model D-10 selected; see the fallback in _call_anthropic
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"

# P3c-0, two-stage looking (docs/78 §18). A real chip's figure is one sheet of
# N panels. Asking "is qubit 5 good?" against that sheet is the D-11.1 defect;
# asking "WHICH panels look wrong?" is a different, well-posed question about
# the same picture — and it costs ONE call instead of N, which is what makes
# the judge affordable on a 17-qubit chip at all (the plan budget is 40 calls).
# The targets it names are then re-plotted ALONE and judged individually.
TRIAGE_STATES = ("all_fine", "some_suspect", "unreadable")
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
    feature_visible: bool | None = None   # v2 presence hint (bool, no numbers)
    direction: str | None = None          # v2 seed direction: left | right

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "failure_mode": self.failure_mode,
                "reason": self.reason, "provider": self.provider,
                "model": self.model,
                "discarded_numeric": self.discarded_numeric,
                "feature_visible": self.feature_visible,
                "direction": self.direction}


_ABSTAIN = AuditVerdict(verdict="abstain", reason="auditor unavailable")


@dataclass
class SignatureVerdict:
    """The §1.3 terminator: is this a CORRECT experimental signature?

    Deliberately NOT the same field as the trust verdict — "the fit is
    consistent with the data" and "this figure shows the experiment working"
    are different questions, and a loop that conflated them could terminate on
    a self-consistent fit of noise. `unclear` is the honest middle: something
    is there, but not a signature you would sign off on.
    """
    signature: str                     # clear | unclear | absent
    failure_mode: str | None = None
    reason: str = ""
    provider: str = ""
    model: str = ""
    discarded_numeric: bool = False

    @property
    def accepted(self) -> bool:
        return self.signature == "clear"

    def as_dict(self) -> dict:
        return {"signature": self.signature, "failure_mode": self.failure_mode,
                "reason": self.reason, "provider": self.provider,
                "model": self.model,
                "discarded_numeric": self.discarded_numeric}


@dataclass
class ComparisonVerdict:
    """D-8 tier 2b: previous figure vs current one, for the no-progress stop."""
    comparison: str                    # better | worse | same
    reason: str = ""
    provider: str = ""
    model: str = ""
    discarded_numeric: bool = False

    def as_dict(self) -> dict:
        return {"comparison": self.comparison, "reason": self.reason,
                "provider": self.provider, "model": self.model,
                "discarded_numeric": self.discarded_numeric}


@dataclass
class TriageVerdict:
    """Stage 1 of two-stage looking: which panels of a multi-target sheet want a
    dedicated look. It is a ROUTER, never a verdict — nothing terminates or
    fails on it, so its worst outcome is a wasted per-target call."""
    state: str                          # all_fine | some_suspect | unreadable
    suspects: list[str] = field(default_factory=list)
    reason: str = ""
    provider: str = ""
    model: str = ""
    discarded_numeric: bool = False

    def as_dict(self) -> dict:
        return {"state": self.state, "suspects": list(self.suspects),
                "reason": self.reason, "provider": self.provider,
                "model": self.model,
                "discarded_numeric": self.discarded_numeric}


_UNCLEAR = SignatureVerdict(signature="unclear", reason="judge unavailable")
_SAME = ComparisonVerdict(comparison="same", reason="judge unavailable")


def dedicated_look_set(gate_suspects, triage: "TriageVerdict | None",
                       targets) -> list[str]:
    """WHO gets a per-target panel: the UNION of what the deterministic gates
    flagged and what the overview flagged (docs/78 §18).

    Union, not either alone, because the two fail differently: the gates miss
    the self-consistent noise fit (the archived #575 class — a fit that agrees
    with itself on garbage), and the eye misses a small numeric error no picture
    shows. Neither is a superset of the other, so trusting one to filter the
    other reintroduces D-11.1 wearing a different hat. An unreadable sheet
    escalates EVERY target rather than none.
    """
    order = list(targets)
    if triage is not None and triage.state == "unreadable":
        return order
    want = set(gate_suspects or ())
    if triage is not None:
        want |= {s for s in triage.suspects if s in set(order)}
    return [t for t in order if t in want]        # caller's order, deduped


# ---------------------------------------------------------------------------
# Prompt bundle
# ---------------------------------------------------------------------------

_SYSTEM = """You are a calibration fit auditor for superconducting-qubit \
experiments. You judge whether a node's automated fit is trustworthy by \
looking at the figure and the numeric context. You NEVER estimate, correct, \
or emit any numeric value — the calibration number always comes from the \
experiment's own fitter. Respond with EXACTLY one JSON object:
{"verdict": "accept"|"reject"|"abstain", "failure_mode": \
"wrong_peak"|"no_signal"|"noisy"|"drifted"|"feature_present_fit_failed"|null, \
"reason": "<one sentence>", "feature_visible": true|false|null, \
"direction": "left"|"right"|null}
accept = the claimed fit is consistent with the data shown.
reject = the fit is clearly wrong (locked a sidelobe, no real feature, …).
abstain = you cannot tell. When uncertain, abstain — never guess accept.
feature_visible = whether a genuine spectroscopic feature (peak/dip/fringe) \
is visible ANYWHERE in the figure, regardless of what the fit claims; null \
if unsure. direction = when the data suggests the true feature lies OUTSIDE \
the swept window, which side (left = below the axis range, right = above); \
null otherwise. These are qualitative hints only — never report a position."""


_SIGNATURE_SYSTEM = """You are a calibration signature judge for \
superconducting-qubit experiments. You are shown ONE figure from a calibration \
run and must say whether it shows a CORRECT EXPERIMENTAL SIGNATURE for that \
measurement — not whether the fitted number is right, but whether the \
experiment itself worked and produced the shape it is supposed to produce. \
You NEVER estimate, correct, or emit any numeric value. Respond with EXACTLY \
one JSON object:
{"signature": "clear"|"unclear"|"absent", "failure_mode": \
"wrong_peak"|"no_signal"|"noisy"|"drifted"|"feature_present_fit_failed"|null, \
"reason": "<one sentence>"}
clear = an unmistakable, well-formed signature of this measurement.
unclear = something is there, but you would not sign off on it.
absent = no signature of this measurement at all.
Judge SHAPE and RELATIVE GEOMETRY only. Where a feature sits inside the swept \
window is an artefact of the window the experimenter chose, not physics — \
never use it as evidence, and never report a position. When you cannot tell, \
answer "unclear"; never guess "clear"."""

_TRIAGE_SYSTEM = """You are triaging one calibration figure that contains \
SEVERAL panels — one per qubit or per qubit pair, each labelled with its own \
name. Your ONLY job is to say which panels deserve a closer look on their own. \
You are NOT deciding whether any panel is acceptable; a second, dedicated look \
at each named panel does that. You NEVER estimate, correct, or emit any numeric \
value, and you never report a position. Respond with EXACTLY one JSON object:
{"state": "all_fine"|"some_suspect"|"unreadable", \
"suspects": ["<panel name>", ...], "reason": "<one sentence>"}
all_fine = every panel carries the expected signature; suspects is [].
some_suspect = list the panel names that look wrong, empty, or unlike their \
neighbours. Copy each name EXACTLY as printed on the panel.
unreadable = the panels are too small, unlabelled, or otherwise not judgeable \
from this image; suspects is [].
Prefer naming a panel over staying silent — a named panel only costs one closer \
look, while a missed one is never looked at again. Judge shape and relative \
geometry only; where a feature sits inside a sweep is the experimenter's \
choice, not physics."""


_COMPARE_SYSTEM = """You compare two figures from the SAME calibration \
measurement on the same qubit: the PREVIOUS attempt and the CURRENT one, in \
that order. Say whether the current figure is a better, worse, or equally good \
measurement — clearer feature, less noise, feature better contained in the \
window. You NEVER estimate, correct, or emit any numeric value, and you never \
report a position. Respond with EXACTLY one JSON object:
{"comparison": "better"|"worse"|"same", "reason": "<one sentence>"}
Use "same" when the difference is not one you would act on."""


def build_bundle(*, family_label: str, target: str, fit_entry: dict,
                 gate_reasons: list[str], sweep_note: str = "",
                 figure_path: Path | None = None, ask: str = "judge") -> dict:
    """The provider-agnostic audit request: numeric context + optional PNG.
    ``ask``: ``judge`` (trust verdict on a suspect fit) | ``presence`` (the
    fit FAILED — report only feature_visible/direction, docs/56 v2)."""
    ctx = {
        "family": family_label,
        "target": target,
        "claimed_fit": {k: v for k, v in fit_entry.items()
                        if isinstance(v, (int, float, bool, str))},
        "deterministic_gate_concerns": gate_reasons,
        "sweep": sweep_note,
        "ask": ask,
    }
    if ask == "presence":
        ctx["note"] = ("The node's own fit FAILED for this target — there is "
                       "no claim to judge. Report feature_visible (is a real "
                       "feature anywhere in the figure?) and direction (if "
                       "the data suggests it lies outside the swept window). "
                       "Use verdict=abstain.")
    image_b64 = None
    if figure_path is not None:
        try:
            image_b64 = base64.b64encode(Path(figure_path).read_bytes()).decode()
        except OSError:
            image_b64 = None
    return {"context": ctx, "image_b64": image_b64}


def _b64(path) -> str | None:
    try:
        return base64.b64encode(Path(path).read_bytes()).decode()
    except (OSError, TypeError):
        return None


def build_signature_bundle(*, family_key: str, family_label: str, target: str,
                           figure_path, sweep_note: str = "",
                           pack_version: str = judge_pack.DEFAULT_VERSION
                           ) -> dict:
    """The §1.3 terminator request: family knowledge + ONE figure.

    Deliberately carries NO fit numbers. The question is whether the experiment
    produced its signature; handing over the claimed value invites the model to
    reason backwards from it and call a self-consistent noise fit "clear".
    """
    entry = judge_pack.entry_for(family_key, pack_version)
    ctx = {"family": family_label, "target": target, "ask": "signature",
           "sweep": sweep_note,
           "family_knowledge": judge_pack.prompt_block(entry),
           "taught": bool(entry)}
    return {"context": ctx, "system": _SIGNATURE_SYSTEM,
            "images_b64": [b for b in (_b64(figure_path),) if b],
            "kind": "signature"}


def build_triage_bundle(*, family_key: str, family_label: str,
                        targets: list[str], figure_path,
                        pack_version: str = judge_pack.DEFAULT_VERSION) -> dict:
    """Stage 1: the whole sheet, once, asking WHICH panels want a closer look."""
    entry = judge_pack.entry_for(family_key, pack_version)
    ctx = {"family": family_label, "ask": "triage",
           "panels_expected": list(targets),
           "family_knowledge": judge_pack.prompt_block(entry),
           "taught": bool(entry)}
    return {"context": ctx, "system": _TRIAGE_SYSTEM,
            "images_b64": [b for b in (_b64(figure_path),) if b],
            "kind": "triage"}


def parse_triage(text: str, provider: str = "", model: str = "",
                 known_targets: list[str] | None = None) -> TriageVerdict:
    obj = _extract_obj(text)
    if obj is None:
        # unparseable ⇒ escalate everything: a router that fails must widen the
        # net, never narrow it
        return TriageVerdict(state="unreadable", reason="unparseable reply",
                             provider=provider, model=model)
    state = obj.get("state")
    if state not in TRIAGE_STATES:
        return TriageVerdict(state="unreadable",
                             reason="invalid state value",
                             provider=provider, model=model)
    raw = obj.get("suspects")
    names = [str(s) for s in raw] if isinstance(raw, list) else []
    if known_targets is not None:
        known = set(known_targets)
        names = [n for n in names if n in known]   # never invent a target
    return TriageVerdict(
        state=state, suspects=names,
        reason=str(obj.get("reason") or "")[:500], provider=provider,
        model=model,
        discarded_numeric=_numeric_emission(obj, ("state", "suspects",
                                                  "reason")))


def build_comparison_bundle(*, family_label: str, target: str,
                            previous_figure, current_figure,
                            change_note: str = "") -> dict:
    """D-8 tier 2b: previous vs current, in that order."""
    imgs = [b for b in (_b64(previous_figure), _b64(current_figure)) if b]
    ctx = {"family": family_label, "target": target, "ask": "compare",
           "what_changed": change_note,
           "note": "The first image is the PREVIOUS attempt, the second is the "
                   "CURRENT one."}
    return {"context": ctx, "system": _COMPARE_SYSTEM, "images_b64": imgs,
            "kind": "compare"}


def _extract_obj(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _numeric_emission(obj: dict, allowed: tuple[str, ...]) -> bool:
    """THE numeric guard, shared by every ask (docs/47: an acceptance
    criterion, not a config toggle). A model that volunteers a corrected value
    has it discarded and flagged — no code path carries it anywhere."""
    return any(isinstance(v, (int, float)) and not isinstance(v, bool)
               for k, v in obj.items() if k not in allowed)


def parse_signature(text: str, provider: str = "", model: str = "") -> SignatureVerdict:
    obj = _extract_obj(text)
    if obj is None:
        return SignatureVerdict(signature="unclear", reason="unparseable reply",
                                provider=provider, model=model)
    sig = obj.get("signature")
    if sig not in SIGNATURES:
        return SignatureVerdict(signature="unclear",
                                reason="invalid signature value",
                                provider=provider, model=model)
    fm = obj.get("failure_mode")
    return SignatureVerdict(
        signature=sig, failure_mode=fm if fm in FAILURE_MODES else None,
        reason=str(obj.get("reason") or "")[:500], provider=provider,
        model=model,
        discarded_numeric=_numeric_emission(
            obj, ("signature", "failure_mode", "reason")))


def parse_comparison(text: str, provider: str = "", model: str = "") -> ComparisonVerdict:
    obj = _extract_obj(text)
    if obj is None:
        return ComparisonVerdict(comparison="same", reason="unparseable reply",
                                 provider=provider, model=model)
    cmp_ = obj.get("comparison")
    if cmp_ not in COMPARISONS:
        # "same" is the safe unknown: it neither claims progress nor
        # manufactures a regression that would trip the stop-loss
        return ComparisonVerdict(comparison="same",
                                 reason="invalid comparison value",
                                 provider=provider, model=model)
    return ComparisonVerdict(
        comparison=cmp_, reason=str(obj.get("reason") or "")[:500],
        provider=provider, model=model,
        discarded_numeric=_numeric_emission(obj, ("comparison", "reason")))


def _parse_verdict(text: str, provider: str, model: str) -> AuditVerdict:
    """Extract + validate the JSON verdict; discard any numeric emissions."""
    obj = _extract_obj(text)
    if obj is None:
        return AuditVerdict(verdict="abstain", reason="unparseable reply",
                            provider=provider, model=model)
    verdict = obj.get("verdict")
    if verdict not in VERDICTS:
        return AuditVerdict(verdict="abstain", reason="invalid verdict value",
                            provider=provider, model=model)
    fm = obj.get("failure_mode")
    if fm not in FAILURE_MODES:
        fm = None
    fv = obj.get("feature_visible")
    if not isinstance(fv, bool):
        fv = None
    direction = obj.get("direction")
    if direction not in DIRECTIONS:
        direction = None
    # numeric-emission guard: any extra numeric field is discarded + flagged
    # (feature_visible/direction are bool/enum — structurally number-free)
    discarded = _numeric_emission(obj, ("verdict", "failure_mode", "reason",
                                        "feature_visible", "direction"))
    return AuditVerdict(verdict=verdict, failure_mode=fm,
                        reason=str(obj.get("reason") or "")[:500],
                        provider=provider, model=model,
                        discarded_numeric=discarded,
                        feature_visible=fv, direction=direction)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

def _post_json(url: str, headers: dict, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _images_of(bundle: dict) -> list[str]:
    """One or many, in ORDER — the comparison ask is order-dependent
    (previous, then current) and a silent re-order would invert its verdict."""
    imgs = bundle.get("images_b64")
    if isinstance(imgs, list):
        return [b for b in imgs if b]
    return [bundle["image_b64"]] if bundle.get("image_b64") else []


def _call_anthropic(settings: dict, bundle: dict) -> str:
    content: list[dict] = []
    for b in _images_of(bundle):
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": "image/png",
                                   "data": b}})
    content.append({"type": "text",
                    "text": json.dumps(bundle["context"], indent=1)})
    out = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": settings.get("api_key", ""),
         "anthropic-version": "2023-06-01"},
        # D-10 chose Sonnet: docs/47 measured Haiku at ~17% false-accept,
        # concentrated in the hard 2-D families — which are 7 of our 9. A blank
        # `model` used to fall back to exactly that model, silently, so the
        # recorded decision held only if the operator also typed a model name.
        {"model": settings.get("model") or DEFAULT_ANTHROPIC_MODEL,
         "max_tokens": 300, "system": bundle.get("system") or _SYSTEM,
         "messages": [{"role": "user", "content": content}]},
        float(settings.get("timeout_s", 60)))
    parts = out.get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


def _call_openai_compat(settings: dict, bundle: dict) -> str:
    content: list[Any] = [{"type": "text",
                           "text": json.dumps(bundle["context"], indent=1)}]
    for b in _images_of(bundle):
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + b}})
    base = (settings.get("base_url") or "").rstrip("/")
    headers = {}
    if settings.get("api_key"):
        headers["Authorization"] = f"Bearer {settings['api_key']}"
    out = _post_json(f"{base}/v1/chat/completions", headers,
                     {"model": settings.get("model") or "",
                      "messages": [{"role": "system",
                                    "content": bundle.get("system") or _SYSTEM},
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
        ask = ctx.get("ask") or "judge"
        key = (ctx.get("family"), ctx.get("target"))
        obj = self.script.get((ask, *key)) or self.script.get((ask, ctx.get("target")))
        if obj is None and ask in ("judge", "presence"):
            # bare keys answer the TRUST asks only. Letting a judge-shaped
            # script also answer `signature`/`compare`/`triage` would hand the
            # parser a payload of the wrong shape and read as a scripted
            # verdict when it is really a fallthrough.
            obj = self.script.get(key) or self.script.get(ctx.get("target"))
        if obj is None:
            # the default per ask is the SAFE one: never "clear", never a
            # progress claim (docs/78 D-7 — an absent judge must not terminate
            # the loop or silence the stop-loss)
            obj = {"signature": "unclear", "reason": "fake default"} \
                if ask == "signature" else \
                {"comparison": "same", "reason": "fake default"} \
                if ask == "compare" else \
                {"state": "unreadable", "suspects": [],
                 "reason": "fake default"} if ask == "triage" else \
                {"verdict": "abstain", "failure_mode": None,
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

    # -- P3b: the two additional asks ---------------------------------------
    def _raw(self, bundle: dict) -> tuple[str | None, str, str]:
        """Shared call path: budget, provider dispatch, never raises.
        Returns (text | None, provider, model)."""
        provider = self.settings.get("provider", "off")
        model = str(self.settings.get("model") or "")
        if not self.enabled:
            return None, provider, model
        budget = int(self.settings.get("max_calls_per_plan",
                                       _DEFAULTS["max_calls_per_plan"]))
        if self.calls_made >= budget:
            return None, provider, model
        self.calls_made += 1
        try:
            if provider == "fake":
                return self.fake(bundle), provider, model  # type: ignore[misc]
            if provider == "anthropic":
                return _call_anthropic(self.settings, bundle), provider, model
            if provider == "openai_compat":
                return _call_openai_compat(self.settings, bundle), provider, model
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            logger.warning("LLM call failed: %s", exc)
        return None, provider, model

    def signature(self, bundle: dict) -> SignatureVerdict:
        """The §1.3 terminator. An unavailable judge answers `unclear`, never
        `clear`: the loop must not be able to terminate because nobody looked.
        """
        text, provider, model = self._raw(bundle)
        if text is None:
            return SignatureVerdict(signature="unclear",
                                    reason="judge unavailable or budget spent",
                                    provider=provider, model=model)
        v = parse_signature(text, provider, model)
        if v.discarded_numeric:
            logger.info("signature verdict carried a numeric field — discarded")
        return v

    def triage(self, bundle: dict,
               known_targets: list[str] | None = None) -> TriageVerdict:
        """Stage 1. An unavailable judge answers `unreadable`, which escalates
        EVERY target to a dedicated look — the router's failure must widen the
        net, never narrow it."""
        text, provider, model = self._raw(bundle)
        if text is None:
            return TriageVerdict(state="unreadable",
                                 reason="judge unavailable or budget spent",
                                 provider=provider, model=model)
        v = parse_triage(text, provider, model, known_targets)
        if v.discarded_numeric:
            logger.info("triage verdict carried a numeric field — discarded")
        return v

    def compare(self, bundle: dict) -> ComparisonVerdict:
        """D-8 tier 2b. An unavailable judge answers `same` — it neither claims
        progress nor manufactures a regression."""
        text, provider, model = self._raw(bundle)
        if text is None:
            return ComparisonVerdict(comparison="same",
                                     reason="judge unavailable or budget spent",
                                     provider=provider, model=model)
        v = parse_comparison(text, provider, model)
        if v.discarded_numeric:
            logger.info("comparison verdict carried a numeric field — discarded")
        return v
