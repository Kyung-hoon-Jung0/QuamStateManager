"""Release gate: the built wheel ships every tracked package file and imports."""
import subprocess
import sys
import zipfile
from pathlib import Path

wheel = Path(sys.argv[1])
tracked = set(subprocess.run(["git", "ls-files", "quam_state_manager"], capture_output=True,
                             text=True, check=True).stdout.split())
with zipfile.ZipFile(wheel) as zf:
    shipped = {n for n in zf.namelist() if n.startswith("quam_state_manager/")}
missing = sorted(tracked - shipped)
if missing:
    sys.exit(f"wheel is missing {len(missing)} tracked files, e.g. {missing[:10]}")
for must in ("quam_state_manager/web/templates/base.html", "quam_state_manager/knowledge/v1/"):
    if not any(n.startswith(must) for n in shipped):
        sys.exit(f"wheel lacks {must}")
print(f"wheel ok: {len(shipped)} package files, all {len(tracked)} tracked files present")
