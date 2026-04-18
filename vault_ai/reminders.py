"""
vault_ai.reminders
~~~~~~~~~~~~~~~~~~
Smart Reminders: whisper warnings when changes are unusually large.
"""
from __future__ import annotations

from vault_ai.diff_engine import diff_working_tree
from vault_ai.utils import find_repo
from pathlib import Path


LARGE_CHANGE_THRESHOLD = 500  # lines


def check_large_change(repo: Path | None = None) -> int:
    """
    Count total lines changed in working tree vs last commit.
    Returns total line count changed. Prints a whisper if ≥ threshold.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return 0

    diffs = diff_working_tree(repo)
    if not diffs:
        return 0

    total_lines = 0
    for d in diffs:
        if d.unified:
            for line in d.unified.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    total_lines += 1
                elif line.startswith("-") and not line.startswith("---"):
                    total_lines += 1

    if total_lines >= LARGE_CHANGE_THRESHOLD:
        print(f"\n  🤫  Smart Reminder: You're about to commit {total_lines} changed lines.")
        print(f"      That's a large change! Consider splitting into smaller commits.")
        print(f"      (Threshold: {LARGE_CHANGE_THRESHOLD} lines)\n")

    return total_lines
