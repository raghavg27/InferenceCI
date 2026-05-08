from __future__ import annotations

import subprocess

from inferenceci.schemas import GitInfo


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def collect() -> GitInfo:
    commit = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    merge_base = None
    for ref in ("origin/main", "main"):
        merge_base = _git("merge-base", "HEAD", ref)
        if merge_base:
            break
    return GitInfo(commit=commit, branch=branch, merge_base=merge_base)
