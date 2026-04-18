"""
vault_ai.branch_map
~~~~~~~~~~~~~~~~~~~~
Visual Branching Map: ASCII-art branch visualization for the CLI.
Renders an interactive-style commit graph showing all branches.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, get_head, read_object, get_current_branch


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _list_branches(repo: Path) -> Dict[str, str]:
    """Return {branch_name: head_sha} for all branches."""
    refs_dir = repo / VAULT_DIR / "refs" / "heads"
    branches = {}
    if refs_dir.exists():
        for f in refs_dir.iterdir():
            if f.is_file():
                branches[f.name] = f.read_text().strip()
    return branches


def _walk_commits(repo: Path, start_sha: str, limit: int = 50) -> List[dict]:
    """Walk the commit chain from a starting SHA."""
    commits = []
    sha = start_sha
    while sha and len(commits) < limit:
        try:
            _, payload = read_object(repo, sha)
            data = json.loads(payload)
            data["sha"] = sha
            commits.append(data)
            sha = data.get("parent")
        except Exception:
            break
    return commits


# ---------------------------------------------------------------------------
# ASCII Graph Renderer
# ---------------------------------------------------------------------------

_COLORS = {
    "main":     "\033[32m",   # green
    "dev":      "\033[33m",   # yellow
    "feature":  "\033[36m",   # cyan
    "hotfix":   "\033[31m",   # red
    "release":  "\033[35m",   # magenta
}
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


def _branch_color(name: str) -> str:
    """Get an ANSI color for a branch name."""
    for prefix, color in _COLORS.items():
        if name.startswith(prefix):
            return color
    # Cycle through colors for other branches
    idx = sum(ord(c) for c in name) % len(_COLORS)
    return list(_COLORS.values())[idx]


def render_branch_map(repo: Path | None = None, limit: int = 30) -> str:
    """
    Render an ASCII branch map of the repository.
    Returns a string with the visual graph.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return "  ✗  Not inside a Vault-AI repository."

    branches = _list_branches(repo)
    current = get_current_branch(repo)

    if not branches:
        return "  ⚠  No branches found."

    # Collect all commits from all branches
    all_commits: Dict[str, dict] = {}
    branch_tips: Dict[str, str] = {}  # sha -> branch_name

    for bname, bsha in branches.items():
        branch_tips[bsha] = bname
        for c in _walk_commits(repo, bsha, limit):
            all_commits[c["sha"]] = c

    # Sort by timestamp (newest first)
    sorted_commits = sorted(
        all_commits.values(),
        key=lambda c: c.get("timestamp", 0),
        reverse=True,
    )[:limit]

    # Build the visual graph
    lines = []
    lines.append("")
    lines.append(f"  {_BOLD}📊  Branch Map{_RESET}")
    lines.append(f"  {'─' * 55}")

    # Branch legend
    lines.append(f"  {_DIM}Branches:{_RESET}")
    for bname, bsha in sorted(branches.items()):
        color = _branch_color(bname)
        marker = " ← HEAD" if bname == current else ""
        lines.append(f"    {color}● {bname}{_RESET} ({bsha[:8]}){marker}")
    lines.append(f"  {'─' * 55}")

    # Commit graph
    prev_sha = None
    for i, commit in enumerate(sorted_commits):
        sha = commit["sha"]
        msg = commit.get("message", "")[:45]
        author = commit.get("author", "?")
        ts = time.strftime("%m-%d %H:%M", time.localtime(commit.get("timestamp", 0)))

        # Determine which branch this commit belongs to
        branch_name = branch_tips.get(sha, "")
        if branch_name:
            color = _branch_color(branch_name)
            tag = f" {color}({branch_name}){_RESET}"
        else:
            color = _DIM
            tag = ""

        # Graph connector
        if i == 0:
            connector = "◉"  # tip
        elif commit.get("parent") is None:
            connector = "◯"  # root
        else:
            connector = "●"

        # Is this a merge point? (referenced by multiple branches)
        ref_count = sum(1 for c in all_commits.values() if c.get("parent") == sha)
        if ref_count > 1:
            connector = "◆"  # merge point

        line = f"  {color}{connector}{_RESET}  {_DIM}{sha[:8]}{_RESET}  {ts}  {msg}{tag}"
        lines.append(line)

        # Draw connector line
        if i < len(sorted_commits) - 1:
            lines.append(f"  {_DIM}│{_RESET}")

    lines.append(f"  {'─' * 55}")
    lines.append(f"  {_DIM}{len(sorted_commits)} commits shown, "
                 f"{len(branches)} branch(es){_RESET}")
    lines.append("")

    return "\n".join(lines)


def print_branch_map(repo: Path | None = None, limit: int = 30) -> None:
    """Print the visual branch map."""
    print(render_branch_map(repo, limit))
