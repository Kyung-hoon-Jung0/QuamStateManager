"""The context a verdict is only valid inside (docs/78 D-13.3, §17 B3).

A verdict is not a fact about a chip. It is a fact about *this run's data, read
by THIS analysis, under THIS env* — the verification **triple** of D-13, whose
third axis is the analysis tree's own revision. Two verdicts produced by
different revisions are not comparable, and until now nothing downstream could
tell them apart:

* ``fit_audit.audit_run`` returned ``gate_hash`` + ``lib_versions`` but no
  ``env`` and no root — it put both in the cache KEY and handed the payload
  back unlabelled;
* ``figure_gen.generate`` returned all four;
* the engine's gate verdicts carried none at all.

Three shapes for one contract. This module is the one shape, and all three
paths stamp it.

The motivation is not theoretical. In a single session sixteen shipped gate
bands were overturned by measurement and thirty-seven jump limits recalibrated
(docs/78 §15.2b, §20.6). Every verdict written before those edits means
something different from one written after — and a ledger that cannot say which
is a ledger that quietly mixes them.

**Two analyses, stamped differently on purpose.** Pretending they share an
identity would be the same fiction as the missing stamp:

* ``lab_replay`` — the lab's own analysis re-run inside a customer env. Its
  identity is (``env``, ``lib_versions``, source root + revision, ``gate_hash``
  over the analysis bytes).
* ``sm_gates`` — SM's own deterministic gates, computed in this process. No env
  is spawned, so naming one would be a lie; its identity is ``analysis_rev``, a
  content hash of ``gates.py`` + ``families.py``. That is the SM-side twin of
  ``gate_hash`` — and it is the thing that actually changed sixteen times.

``run_generation`` (the state-generation fingerprint) is the third axis in both.

Nothing here raises. A context that could not be determined records ``None`` and
says so through :meth:`VerificationContext.missing` — an unknown provenance must
read as unknown, never as a default that looks like an answer.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# analyses that can produce a verdict
LAB_REPLAY = "lab_replay"
SM_GATES = "sm_gates"

# where the figure the judge looked at came from. "archived" is the run's own
# saved PNG (the lab's plotting at RUN time — a different revision from
# anything we replay), "regenerated" is figure_gen through the pinned tree.
FIGURE_SOURCES = ("archived", "regenerated", "none")


@dataclass
class VerificationContext:
    """(analysis × env × source root × run generation) — one verdict's warrant.

    Every field is optional because an honest unknown beats an invented
    default; :meth:`missing` names what could not be established so a reader is
    never left to assume.
    """
    analysis: str = SM_GATES
    env: str | None = None
    lib_versions: dict = field(default_factory=dict)
    source_root: str | None = None
    root_kind: str | None = None        # live | pinned | installed
    root_rev: str | None = None
    root_dirty: bool | None = None
    gate_hash: str | None = None        # lab-side analysis bytes
    analysis_rev: str | None = None     # SM-side gates+families bytes
    run_generation: str | None = None
    figure_source: str | None = None
    # docs/135: the chip-profile answer set the judgment branched on.
    # None = no profile consulted — the SAME context as before profiles
    # existed, so every old verdict stays comparable.
    profile_hash: str | None = None

    def as_dict(self) -> dict:
        return {"analysis": self.analysis, "env": self.env,
                "lib_versions": dict(self.lib_versions or {}),
                "source_root": self.source_root, "root_kind": self.root_kind,
                "root_rev": self.root_rev, "root_dirty": self.root_dirty,
                "gate_hash": self.gate_hash, "analysis_rev": self.analysis_rev,
                "run_generation": self.run_generation,
                "figure_source": self.figure_source,
                "profile_hash": self.profile_hash}

    # -- identity ---------------------------------------------------------
    def key(self) -> tuple:
        """The identity two verdicts must SHARE to be comparable.

        Deliberately excludes ``figure_source`` and ``root_dirty``: the first
        says what the judge looked at (evidence, not analysis), the second is a
        warning about reproducibility, not a different analysis. Both are
        reported; neither splits the comparison.
        """
        if self.analysis == LAB_REPLAY:
            return (self.analysis, self.env, self.root_rev or self.source_root,
                    self.gate_hash, self.run_generation, self.profile_hash)
        return (self.analysis, self.analysis_rev, self.run_generation,
                self.profile_hash)

    def missing(self) -> list[str]:
        """Which axes of the triple could not be established.

        A verdict with a non-empty ``missing()`` is still a verdict — it just
        cannot be compared to another one on the axes it lost.
        """
        out = []
        if self.run_generation is None:
            out.append("run generation")
        if self.analysis == LAB_REPLAY:
            if not self.env:
                out.append("env")
            if not (self.root_rev or self.source_root or
                    self.root_kind == "installed"):
                out.append("analysis tree")
            if not self.gate_hash:
                out.append("analysis bytes (gate_hash)")
        elif not self.analysis_rev:
            out.append("gate revision")
        return out

    def describe(self) -> str:
        """One honest sentence — never a hash dump, never a false precision."""
        if self.analysis == LAB_REPLAY:
            quam = (self.lib_versions or {}).get("quam")
            where = {"live": "the live analysis tree",
                     "pinned": "a pinned analysis revision",
                     "installed": "the env's installed analysis",
                     }.get(self.root_kind or "", "an unrecorded analysis tree")
            bits = [f"the lab's analysis re-run in {self.env or 'an unnamed env'}"
                    + (f" (quam {quam})" if quam else ""), where]
            if self.root_rev:
                bits.append(f"rev {self.root_rev[:12]}")
            if self.root_dirty:
                bits.append("with uncommitted edits — not reproducible from a "
                            "revision")
            head = ", ".join(bits)
        else:
            rev = self.analysis_rev[:12] if self.analysis_rev else "unknown"
            head = f"SM's deterministic gates (rev {rev})"
        if self.figure_source == "archived":
            head += "; the judge saw the run's own archived figure"
        elif self.figure_source == "regenerated":
            head += "; the judge saw a regenerated figure"
        gaps = self.missing()
        if gaps:
            head += " — unrecorded: " + ", ".join(gaps)
        return head


def comparable(a, b) -> tuple[bool, str]:
    """May these two verdicts be read side by side? ``(ok, reason)``.

    Used wherever the loop reasons ACROSS runs — the cross-experiment review,
    the history trend, a report that trends a metric over a night. Comparing
    verdicts from two analysis revisions is the mistake D-13 exists to stop,
    and an unknown context is not a matching one: it answers False, because an
    unverifiable premise is not a satisfied one (same rule as class-B
    preconditions in :mod:`action_space`).
    """
    ca = a if isinstance(a, VerificationContext) else from_dict(a)
    cb = b if isinstance(b, VerificationContext) else from_dict(b)
    if ca is None or cb is None:
        return False, "one of the verdicts carries no verification context"
    if ca.analysis != cb.analysis:
        return False, (f"different analyses ({ca.analysis} vs {cb.analysis}) — "
                       f"their verdicts do not mean the same thing")
    ma, mb = ca.missing(), cb.missing()
    if ma or mb:
        return False, ("incomplete verification context: "
                       + "; ".join(filter(None, [", ".join(ma), ", ".join(mb)])))
    if ca.key() != cb.key():
        if ca.analysis == SM_GATES:
            return False, ("different gate revisions — the bands that produced "
                           "these verdicts are not the same bands")
        return False, ("different (env, analysis tree, run generation) — these "
                       "verdicts were produced under different conditions")
    return True, "same verification context"


def from_dict(d) -> VerificationContext | None:
    if isinstance(d, VerificationContext):
        return d
    if not isinstance(d, dict):
        return None
    known = {f for f in VerificationContext.__dataclass_fields__}
    return VerificationContext(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# the SM-side analysis revision
# ---------------------------------------------------------------------------
# Sources whose bytes DEFINE a deterministic gate verdict. families.py holds
# every band; gates.py holds the pipeline that reads them. A change to either
# changes what "pass" means, which is precisely why this hash exists.
_SM_ANALYSIS_SOURCES = ("families.py", "gates.py")
_SM_REV: str | None = None
_SM_REV_LOCK = threading.Lock()


def sm_analysis_rev() -> str | None:
    """Content hash of the SM-side gate analysis. Computed once per process.

    Once per process is correct rather than lazy: these files cannot change
    under a running interpreter without a reload, and a per-call stat would buy
    nothing but I/O on the verdict path.
    """
    global _SM_REV
    with _SM_REV_LOCK:
        if _SM_REV is not None:
            return _SM_REV or None
        here = Path(__file__).parent
        h = hashlib.sha256()
        try:
            for name in _SM_ANALYSIS_SOURCES:
                h.update(name.encode("utf-8"))
                h.update(b"\0")
                # EOL-normalized: with core.autocrlf the SAME commit checks
                # out CRLF on Windows and LF on POSIX, so a raw-bytes hash
                # split one analysis into two "revisions" per OS and
                # comparable() refused to reconcile identical gates
                # (docs/101 C1). Content identity must not depend on the
                # checkout's line-ending policy.
                h.update((here / name).read_bytes().replace(b"\r\n", b"\n"))
        except OSError:
            logger.warning("could not hash the SM gate analysis", exc_info=True)
            _SM_REV = ""
            return None
        _SM_REV = h.hexdigest()
        return _SM_REV


# ---------------------------------------------------------------------------
# root description (cached per process)
# ---------------------------------------------------------------------------
# Resolving a root's kind/rev costs a `git rev-parse` + `git status`. Caching
# per process is safe for the reason sourceroot.is_dirty already documents:
# `gate_hash` stamps the exact analysis BYTES, so a tree edited mid-sweep is
# still caught there — this cache only affects the human-readable revision
# label, never the identity that `key()` compares.
_ROOT_CACHE: dict[str, dict] = {}
_ROOT_LOCK = threading.Lock()


def describe_root(source_root: str | None) -> dict:
    """``{"root_kind", "root_rev", "root_dirty"}`` for a source root.

    ``None`` root ⇒ ``installed``: the analysis is whatever the env has
    installed. That is a real, nameable kind — not a missing value — and saying
    so is what stops it being read as "unrecorded".
    """
    if not source_root:
        return {"root_kind": "installed", "root_rev": None, "root_dirty": None}
    key = str(source_root)
    with _ROOT_LOCK:
        hit = _ROOT_CACHE.get(key)
    if hit is not None:
        return dict(hit)

    from quam_state_manager.core.autofit import sourceroot

    out = {"root_kind": None, "root_rev": None, "root_dirty": None}
    try:
        # a materialized revision carries its own marker — no git call needed,
        # and the pinned cache is not a git tree in the first place
        marker = Path(key) / ".sm_pinned_ok"
        if marker.is_file():
            out = {"root_kind": "pinned",
                   "root_rev": marker.read_text(encoding="utf-8").strip() or None,
                   "root_dirty": False}
        elif sourceroot.is_git_tree(key):
            out = {"root_kind": "live", "root_rev": sourceroot.resolve_rev(key),
                   "root_dirty": sourceroot.is_dirty(key)}
        else:
            out = {"root_kind": "unversioned", "root_rev": None,
                   "root_dirty": None}
    except OSError:
        logger.warning("could not describe source root %s", key, exc_info=True)
    with _ROOT_LOCK:
        _ROOT_CACHE[key] = dict(out)
        while len(_ROOT_CACHE) > 64:
            _ROOT_CACHE.pop(next(iter(_ROOT_CACHE)), None)
    return dict(out)


# ---------------------------------------------------------------------------
# builders — one per path, so the three shapes converge here
# ---------------------------------------------------------------------------

def run_generation(run_folder) -> str | None:
    """The run's state-generation fingerprint, or None when unreadable."""
    if not run_folder:
        return None
    try:
        from quam_state_manager.core.autofit import envmatrix
        return envmatrix.generation_fingerprint(run_folder)
    except Exception:  # noqa: BLE001 — provenance must never break a verdict
        logger.warning("could not fingerprint %s", run_folder, exc_info=True)
        return None


def for_lab_replay(*, env: str | None, source_root: str | None = None,
                   lib_versions: dict | None = None,
                   gate_hash: str | None = None,
                   root_kind: str | None = None, root_rev: str | None = None,
                   root_dirty: bool | None = None,
                   run_folder=None, generation: str | None = None,
                   figure_source: str | None = None) -> VerificationContext:
    """Context for a verdict produced by re-running the LAB's own analysis.

    ``root_kind``/``root_rev`` are taken from the caller when it already knows
    them (``envmatrix.choose_context`` returns both) and resolved from the path
    otherwise — so a caller that never learned them still records them instead
    of leaving the axis blank.
    """
    if root_kind is None and root_rev is None:
        desc = describe_root(source_root)
        root_kind, root_rev = desc["root_kind"], desc["root_rev"]
        if root_dirty is None:
            root_dirty = desc["root_dirty"]
    return VerificationContext(
        analysis=LAB_REPLAY, env=env, lib_versions=dict(lib_versions or {}),
        source_root=str(source_root) if source_root else None,
        root_kind=root_kind, root_rev=root_rev, root_dirty=root_dirty,
        gate_hash=gate_hash,
        run_generation=generation if generation is not None
        else run_generation(run_folder),
        figure_source=figure_source)


def for_sm_gates(run_folder=None, *, generation: str | None = None,
                 figure_source: str | None = None) -> VerificationContext:
    """Context for a verdict produced by SM's own deterministic gates."""
    return VerificationContext(
        analysis=SM_GATES, analysis_rev=sm_analysis_rev(),
        run_generation=generation if generation is not None
        else run_generation(run_folder),
        figure_source=figure_source)


def from_figure_gen(result: dict, *, run_folder=None,
                    generation: str | None = None) -> VerificationContext:
    """Adopt ``figure_gen.generate``'s envelope — it already carries all four."""
    r = result or {}
    return for_lab_replay(
        env=r.get("env"), source_root=r.get("source_root"),
        lib_versions=r.get("lib_versions"), gate_hash=r.get("gate_hash"),
        root_kind=r.get("root_kind"), root_rev=r.get("root_rev"),
        run_folder=run_folder, generation=generation,
        figure_source="regenerated" if r.get("figures") else None)
