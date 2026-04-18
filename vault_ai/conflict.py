"""
vault_ai.conflict
~~~~~~~~~~~~~~~~~~
Predictive Conflict Resolution: AI analyzes logic dependencies before a merge
to warn if Branch A will break Branch B.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, get_head, read_object, get_current_branch


# ---------------------------------------------------------------------------
# Tree diffing helpers
# ---------------------------------------------------------------------------

def _read_tree_entries(repo: Path, tree_sha: str) -> dict:
    """Read a tree object and return {name: (mode, sha)} mapping."""
    _, payload = read_object(repo, tree_sha)
    entries = json.loads(payload)
    return {e["name"]: (e["mode"], e["sha"]) for e in entries}


def _collect_blobs(repo: Path, tree_sha: str, prefix: str = "") -> dict:
    """Recursively collect {relative_path: blob_sha} from a tree."""
    blobs = {}
    entries = _read_tree_entries(repo, tree_sha)
    for name, (mode, sha) in entries.items():
        full_path = f"{prefix}{name}" if not prefix else f"{prefix}/{name}"
        if mode == "blob":
            blobs[full_path] = sha
        elif mode == "tree":
            blobs.update(_collect_blobs(repo, sha, full_path))
    return blobs


def _get_branch_head(repo: Path, branch_name: str) -> Optional[str]:
    """Get the HEAD SHA for a given branch name."""
    ref_path = repo / VAULT_DIR / "refs" / "heads" / branch_name
    if ref_path.exists():
        return ref_path.read_text().strip()
    return None


def _get_commit_tree(repo: Path, commit_sha: str) -> str:
    """Get the tree SHA from a commit object."""
    _, payload = read_object(repo, commit_sha)
    return json.loads(payload)["tree"]


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

@dataclass
class ConflictWarning:
    file_path: str
    kind: str          # "both_modified", "delete_modify", "add_both"
    detail: str
    ai_analysis: Optional[str] = None


def _find_structural_conflicts(
    base_blobs: dict,
    branch_a_blobs: dict,
    branch_b_blobs: dict,
) -> List[ConflictWarning]:
    """Compare two branches against a common base to find conflicting changes."""
    warnings = []
    all_files = set(base_blobs) | set(branch_a_blobs) | set(branch_b_blobs)

    for fp in sorted(all_files):
        in_base = fp in base_blobs
        in_a = fp in branch_a_blobs
        in_b = fp in branch_b_blobs

        base_sha = base_blobs.get(fp)
        a_sha = branch_a_blobs.get(fp)
        b_sha = branch_b_blobs.get(fp)

        # Both branches modified the same file differently
        if in_base and in_a and in_b:
            if a_sha != base_sha and b_sha != base_sha and a_sha != b_sha:
                warnings.append(ConflictWarning(
                    file_path=fp,
                    kind="both_modified",
                    detail="Both branches modified this file with different results.",
                ))

        # One branch deleted, other modified
        elif in_base and in_a and not in_b:
            if a_sha != base_sha:
                warnings.append(ConflictWarning(
                    file_path=fp,
                    kind="delete_modify",
                    detail="Branch B deleted this file, but Branch A modified it.",
                ))
        elif in_base and not in_a and in_b:
            if b_sha != base_sha:
                warnings.append(ConflictWarning(
                    file_path=fp,
                    kind="delete_modify",
                    detail="Branch A deleted this file, but Branch B modified it.",
                ))

        # Both branches added the same filename with different content
        elif not in_base and in_a and in_b:
            if a_sha != b_sha:
                warnings.append(ConflictWarning(
                    file_path=fp,
                    kind="add_both",
                    detail="Both branches added this file with different content.",
                ))

    return warnings


def _ai_analyze_conflicts(warnings: List[ConflictWarning], repo: Path) -> None:
    """Enhance conflict warnings with AI analysis."""
    try:
        from vault_ai.llm import ask
    except ImportError:
        return

    if not warnings:
        return

    summary = "\n".join(
        f"- {w.file_path}: {w.kind} — {w.detail}" for w in warnings
    )

    prompt = (
        "You are a merge-conflict analyst. Given these potential conflicts between "
        "two branches, explain which ones are likely to cause real bugs and which "
        "are safe to merge. Be brief (1-2 sentences per conflict).\n\n"
        f"Conflicts:\n{summary}\n\n"
        "Reply with a brief analysis for each file."
    )

    result = ask(prompt)
    if result:
        # Attach AI analysis to the first warning as a group summary
        warnings[0].ai_analysis = result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_conflicts(
    branch_a: str,
    branch_b: str,
    repo: Path | None = None,
) -> List[ConflictWarning]:
    """
    Predict merge conflicts between two branches.
    Uses tree comparison + optional AI analysis.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return []

    sha_a = _get_branch_head(repo, branch_a)
    sha_b = _get_branch_head(repo, branch_b)

    if sha_a is None:
        print(f"  ✗  Branch '{branch_a}' not found.")
        return []
    if sha_b is None:
        print(f"  ✗  Branch '{branch_b}' not found.")
        return []

    tree_a = _get_commit_tree(repo, sha_a)
    tree_b = _get_commit_tree(repo, sha_b)

    blobs_a = _collect_blobs(repo, tree_a)
    blobs_b = _collect_blobs(repo, tree_b)

    # Find common ancestor (walk back from both until we find a shared commit)
    # Simplified: use the parent of branch_a as "base" (works for simple histories)
    _, payload_a = read_object(repo, sha_a)
    parent_a = json.loads(payload_a).get("parent")

    if parent_a:
        base_tree = _get_commit_tree(repo, parent_a)
        base_blobs = _collect_blobs(repo, base_tree)
    else:
        base_blobs = {}

    warnings = _find_structural_conflicts(base_blobs, blobs_a, blobs_b)

    # AI enhancement
    _ai_analyze_conflicts(warnings, repo)

    return warnings


def print_conflict_report(
    branch_a: str, branch_b: str,
    warnings: List[ConflictWarning],
) -> None:
    """Print a formatted conflict prediction report."""
    if not warnings:
        print(f"\n  ✓  No predicted conflicts between '{branch_a}' and '{branch_b}'.")
        return

    print(f"\n  ⚠  Predicted {len(warnings)} conflict(s) merging '{branch_a}' ← '{branch_b}':\n")

    icons = {
        "both_modified": "✎ ",
        "delete_modify": "✗ ",
        "add_both": "＋",
    }

    for w in warnings:
        icon = icons.get(w.kind, "? ")
        print(f"  {icon}  {w.file_path}")
        print(f"       {w.detail}")

    # Print AI analysis if available
    ai = next((w.ai_analysis for w in warnings if w.ai_analysis), None)
    if ai:
        print(f"\n  🧠  AI Analysis:")
        for line in ai.splitlines():
            print(f"      {line}")

    print()
