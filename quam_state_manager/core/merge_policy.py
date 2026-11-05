"""Server-owned Auto-Sync failure classification and consecutive retry policy."""
from __future__ import annotations

UNKNOWN_CAP = 10
BACKOFF = (2, 5, 15, 30, 60)


def classify_failure(reason, *, code=""):
    """Classify structured refusal codes or an exception (including its cause).

    Windows replace access-denied can be a held handle; a positive folder
    permission verdict is permanent. Do not infer permanence from errno 5 alone.
    """
    if code in {"staged", "readonly", "chip_mismatch", "archive", "foreign_owner",
                "collision", "no_pull", "needs_confirm", "missing_live",
                "shared_working_copy", "no_backup", "identity", "empty_state",
                "human_groups", "agent_groups", "agent_rows_held", "auto_seen_off"}:
        return "permanent"
    if code in {"stale_live", "conflict", "live_moved", "torn_live", "live_press", "live_busy",
                "live_mid_save", "live_unbacked", "unseen_changes"}:
        return "transient"
    if code == "verdict_failed":
        return "unknown"
    seen, chain = set(), []
    exc = reason if isinstance(reason, BaseException) else None
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    # safe_io wraps the original OS error. A positive permission denial must
    # survive that wrapper, while replace's Windows access-denied stays retryable.
    if any(getattr(e, "winerror", None) in (5, 32, 33, 1175, 1176, 1177) for e in chain):
        return "transient"
    if any(isinstance(e, (PermissionError, FileNotFoundError))
           or getattr(e, "errno", None) in (1, 13, 30) for e in chain):
        return "permanent"
    if any(type(e).__name__ in {"LiveFileError", "StaleLiveError"} for e in chain):
        return "transient"
    text = str(reason).lower()
    if any(x in text for x in ("winerror 32", "winerror 33", "sharing violation",
                              "mid-save", "torn pair", "still writing", "still in flight",
                              "holds a live file open")):
        return "transient"
    if any(x in text for x in ("read-only", "unwritable", "no write permission",
                              "another window", "another live", "different chip", "identity mismatch",
                              "no state loaded", "empty state", "missing live")):
        return "permanent"
    return "unknown"


def failed(session, reason, *, code=""):
    kind = classify_failure(reason, code=code)
    session["merge_tries"] = session.get("merge_tries", 0) + 1
    if kind == "unknown":
        session["unknown_tries"] = session.get("unknown_tries", 0) + 1
        if session["unknown_tries"] >= UNKNOWN_CAP:
            kind = "permanent"
            reason = "kept failing: " + str(reason)
    n = session["merge_tries"]
    verdict = {"class": kind, "reason": str(reason), "tries": n,
               "delay": BACKOFF[min(n - 1, len(BACKOFF) - 1)]}
    session["merge_retry"] = verdict
    return verdict


def landed(session):
    session["merge_tries"] = 0
    for key in ("unknown_tries", "merge_retry", "merge_due"):
        session.pop(key, None)
