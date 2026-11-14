"""Replace temporary chip folders in a text by ``<chip>``.

Standard library only and free of import side effects, so both the runner
scripts in this folder (which run in the lab's env) and the SM process may
import it: one implementation of the rule.
"""

from __future__ import annotations

import os
import re


def scrub_paths(text: str, folders) -> str:
    """*text* with every folder in *folders* (and each one's parent folder)
    replaced by ``<chip>``: either separator, a doubled backslash (an escaped
    repr) and any drive-letter case."""
    roots = [f for f in folders or [] if f]
    roots += [os.path.dirname(os.path.normpath(f)) for f in list(roots)]
    forms = set()
    for f in roots:
        f = os.path.normpath(f)
        if len(f) <= 3:          # never scrub a drive root or "/"
            continue
        for v in (f, f.replace("\\", "/"), f.replace("\\", "\\\\")):
            forms.add(v)
    for v in sorted(forms, key=len, reverse=True):
        text = re.sub(re.escape(v), "<chip>", text, flags=re.IGNORECASE)
    return text
