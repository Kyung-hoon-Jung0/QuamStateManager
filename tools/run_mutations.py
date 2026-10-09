"""Run mutation and fault tools sequentially in an isolated release copy.

Start from the test environment: python tools/run_mutations.py --scratch DIR.
DIR must be inside this worktree under tools/.tmp*. Reports and logs remain
there; the release package, tests, docs and vendor files are never modified.
Use --only STEM ... to repeat individual tools after an anchor repair.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_fixtures(tree):
    """Keep copied test bytes without exposing them to tool-source scans."""
    tree = tree.resolve()
    relative = tree.relative_to(ROOT / "tools")
    copy_name = relative.parts[-1]
    numbered = copy_name.startswith("tree") and copy_name[4:].isdigit()
    if not relative.parts[0].startswith(".tmp") or not (copy_name == "tree" or numbered):
        raise ValueError("fixture copy must be under tools/.tmp*/tree")
    # S10 C7: fixture copies -> text artifacts, do not contaminate source deletion pins.
    for path in (tree / "tests").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".html", ".js", ".css", ".cjs"}:
            target = path.with_name(path.name + ".fixture.txt")
            count = 1
            while target.exists():
                target = path.with_name(path.name + f".fixture{count}.txt")
                count += 1
            if tree not in target.resolve().parents:
                raise ValueError("fixture target escaped its copy")
            path.rename(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    scratch = args.scratch.resolve()
    if ROOT / "tools" not in scratch.parents or not scratch.relative_to(ROOT / "tools").parts[0].startswith(".tmp"):
        parser.error("scratch must be under tools/.tmp* inside this worktree")
    scripts = sorted(p for p in (ROOT / "tools").glob("*.py")
                     if p.name.startswith(("mutate_", "faults_")))
    # S10 C7: silent empty selection -> explicit refusal, do not report an unknown tool as green.
    unknown = set(args.only or ()) - {p.stem for p in scripts}
    if unknown:
        parser.error("unknown tools: " + ", ".join(sorted(unknown)))
    if args.only is not None and not args.only:
        parser.error("select at least one tool")
    # S10 C7: reused fault data -> fresh copy, preserve earlier reports without stale fixtures.
    tree = scratch / "tree"
    count = 2
    while tree.exists():
        tree = scratch / f"tree{count}"
        count += 1
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", check=True).stdout.splitlines()
    # S10 C7: in-place mutations -> isolated copy, preserve the release tree byte for byte.
    tracked = [p for p in tracked if not p.startswith("docs/") and Path(p).name != "CLAUDE.md"]
    extra = [p.relative_to(ROOT).as_posix() for p in (ROOT / "tools").glob("*.py")
             if not p.name.startswith(".tmp")]
    files = sorted(set(tracked + extra))
    before = {p: digest(ROOT / p) for p in files if (ROOT / p).is_file()}
    for rel in before:
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               NODE_PATH="D:/work/statemanager/node_modules", USERNAME="operator")
    results = []
    for source in scripts:
        if args.only is not None and source.stem not in args.only:
            continue
        # Copy updated tool definitions before a repeat; source files stay pristine.
        for tool in (ROOT / "tools").glob("*.py"):
            if not tool.name.startswith(".tmp"):
                shutil.copy2(tool, tree / "tools" / tool.name)
        out = scratch / (source.stem + ("." + tree.name if tree.name != "tree" else ""))
        out.mkdir(parents=True, exist_ok=True)
        report = out / "report.json"
        command = [sys.executable, str(tree / "tools" / source.name)]
        if source.stem != "faults_hub_folder_view":
            command += ["--report", str(report)]
        if source.stem in {"mutate_hub_builder", "mutate_hub_query", "faults_hub_chip_status", "faults_hub_versions"}:
            command += ["--scratch", str(tree / "tools" / ".tmp_runs" / source.stem)]
        if source.stem in {"mutate_hub_chip_status", "mutate_hub_versions"}:
            command += ["--basetemp", str(out / "pytest"), "--logs", str(out / "logs")]
        print("START " + source.stem, flush=True)
        with (out / "stdout.txt").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(command, cwd=tree, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            for line in proc.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            code = proc.wait()
        # S10 C7: report-only exits -> failing run, keep surviving mutations visible to callers.
        tally = None
        if source.name.startswith("mutate_") and report.exists():
            data = json.loads(report.read_text(encoding="utf-8"))
            rows = data if isinstance(data, list) else data.get("results", data.get("mutations", []))
            red = sum(bool(r.get("red", r.get("killed_by_assertion", r.get("assertion_failure", False))))
                      for r in rows)
            tally = {"red": red, "total": len(rows)}
            if red != len(rows):
                code = code or 1
        # Mutation tools must restore their isolated inputs too.
        # S10 C7: existing-file checks -> complete input checks, detect deleted copies too.
        changed = [rel for rel in before if not rel.startswith("tools/")
                   and (not (tree / rel).is_file() or digest(tree / rel) != before[rel])]
        assert not changed, changed
        assert all(digest(ROOT / rel) == value for rel, value in before.items()), "release tree changed"
        results.append({"tool": source.stem, "exit_code": code,
                        "tally": tally,
                        "report": report.relative_to(scratch).as_posix() if report.exists() else None})
        (scratch / "summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print("END " + source.stem + " rc=" + str(code), flush=True)
    archive_fixtures(tree)
    raise SystemExit(int(any(r["exit_code"] for r in results)))


if __name__ == "__main__":
    main()
