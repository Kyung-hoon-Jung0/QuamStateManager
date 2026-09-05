"""The user telling SM that an environment finding is expected (docs/168).

Customer, 2026-09-05: *"SM says the type is wrong. It cannot know that I
introduced this key on purpose — but I must be able to tell it, and after that
the check should pass it as healthy."*

Measured on their own 20-qubit chip, with a real probe of the `cqt` env:

    summary {'errors': 4, 'warnings': 0, 'checked_nodes': 847}
      error unimportable_class QdacBiasedFixedFrequencyTransmon  x11
      error unimportable_class QdacBiasLine                      x11
      error unknown_field      Quam .qdac                        x1
      error unimportable_class QdacInstrument                    x1

Twenty-four places, a red banner on every page load, and the only action
offered on any of them was "Go to field".

WHY THIS IS NOT :mod:`type_verdicts`
------------------------------------
That store answers *what type does this field have* — an ``override`` records a
TypeSpec and changes what the editor enforces. Three of the four findings above
are not type claims at all (a class this env cannot import has no field, let
alone a type), and its key is ``class.field`` while a finding's identity is
``(kind, class, field, code)``: two different findings on one field would
collide and one acknowledgement would silence both. So this is its own store,
and it changes NO expectation anywhere — :mod:`type_policy` never reads it.

WHAT AN ACKNOWLEDGEMENT DOES AND DOES NOT DO
--------------------------------------------
It does not make the finding false and does not lower its severity. The finding
keeps saying what it says, and ``Quam.load()`` in that environment would still
fail exactly as before. What changes is that SM stops *raising* it: the
acknowledged bucket is excluded from the ``issues`` count that drives the red
banner and the badge, the same way ``advisory`` already is. The row stays on
the Diagnostics page, marked, with the date it was acknowledged, and revocable.

IT IS SCOPED TO THE ENVIRONMENT IT WAS MADE IN. An acknowledgement says "this
env does not declare that, and I know" — a statement about one env's ignorance.
Point SM at a different env and it means nothing, so it does not resolve there.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ACKS_FILENAME = "env_acks.json"
FORMAT = 1

# Bounded like type_verdicts: a lab that keeps every acknowledgement of every
# env it ever probed should not grow an unbounded sidecar.
MAX_ENVS = 10
MAX_PER_ENV = 500


def acks_path(instance_path: Any) -> Path:
    return Path(instance_path) / ACKS_FILENAME


def finding_key(kind: str, class_path: str | None, field: str | None,
                code: str | None) -> str:
    """The finding's OWN identity, which is what an acknowledgement is about.

    ``analyze_state`` aggregates on ``(kind, class, field, code)``; anything
    coarser would let one acknowledgement silence a different finding that
    happens to share a field.
    """
    return "|".join([kind or "", class_path or "", field or "", code or ""])


def load_store(instance_path: Any) -> dict:
    """Read the sidecar; a missing or unreadable file is an empty store.

    Never raises: an acknowledgement that cannot be read must degrade to "not
    acknowledged", which shows the finding — the safe direction.
    """
    p = acks_path(instance_path)
    try:
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("env acknowledgements unreadable at %s", p, exc_info=True)
        return {}
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return {}
    envs = data.get("envs")
    return envs if isinstance(envs, dict) else {}


def _save_store(instance_path: Any, envs: dict) -> None:
    p = acks_path(instance_path)
    # Oldest env first, so the bound drops what has not been touched.
    if len(envs) > MAX_ENVS:
        ordered = sorted(envs.items(),
                         key=lambda kv: max((r.get("at", 0) for r in kv[1].values()),
                                            default=0))
        envs = dict(ordered[-MAX_ENVS:])
    for env_key, recs in envs.items():
        if len(recs) > MAX_PER_ENV:
            keep = sorted(recs.items(), key=lambda kv: kv[1].get("at", 0))[-MAX_PER_ENV:]
            envs[env_key] = dict(keep)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"format": FORMAT, "envs": envs}, indent=2),
                   encoding="utf-8")
    tmp.replace(p)


def acknowledge(instance_path: Any, env_key: str, *, kind: str,
                class_path: str | None, field: str | None, code: str | None,
                detail: str = "", note: str = "") -> dict:
    """Record that the user has seen this finding and expects it.

    ``detail`` is stored VERBATIM as the sentence the user was looking at when
    they pressed the button. If the finding later says something different, the
    acknowledgement no longer applies — the user agreed to a fact, not to a
    location.
    """
    if not env_key:
        raise ValueError("an acknowledgement needs an environment to be about")
    if not kind:
        raise ValueError("an acknowledgement needs a finding kind")
    envs = load_store(instance_path)
    recs = dict(envs.get(env_key) or {})
    key = finding_key(kind, class_path, field, code)
    recs[key] = {
        "kind": kind,
        "class_path": class_path or "",
        "field": field or "",
        "code": code or "",
        "detail_at_decision": detail,
        "note": note,
        "at": int(time.time()),
    }
    envs = dict(envs)
    envs[env_key] = recs
    _save_store(instance_path, envs)
    return recs[key]


def revoke(instance_path: Any, env_key: str, key: str) -> bool:
    envs = load_store(instance_path)
    recs = dict(envs.get(env_key) or {})
    if key not in recs:
        return False
    recs.pop(key)
    envs = dict(envs)
    if recs:
        envs[env_key] = recs
    else:
        envs.pop(env_key, None)
    _save_store(instance_path, envs)
    return True


def resolve(instance_path: Any, env_key: str) -> dict:
    """The acknowledgements in force for THIS env, keyed by finding identity.

    Empty for an env with none, and empty when the env is unknown — an
    acknowledgement made against one environment says nothing about another.
    """
    if not env_key:
        return {}
    return dict(load_store(instance_path).get(env_key) or {})


def applies(record: dict, detail: str) -> bool:
    """Does a stored acknowledgement still cover what the finding now says?

    The user agreed to a sentence. If the finding's own detail has changed --
    a different count, a different example, a different reason -- the agreement
    is about something that is no longer on screen, so it lapses and the
    finding comes back. Silence that outlived its subject is the failure this
    guards against.
    """
    if not record:
        return False
    stored = record.get("detail_at_decision")
    if not stored:
        return True          # recorded before details were kept: trust it
    return stored == detail
