"""
vault_ai.merge
~~~~~~~~~~~~~~
AI Judge: Resolves merge conflicts automatically using intent extraction.
Uses Ollama/Gemini to semantically merge code blocks if confidence > 90%.
"""
from __future__ import annotations

import json
from pathlib import Path

from vault_ai import VAULT_DIR
from vault_ai.conflict import predict_conflicts, ConflictWarning, _get_branch_head, _read_tree_entries, _collect_blobs
from vault_ai.store import get_head, read_object, snapshot_tree, create_commit, get_current_branch
from vault_ai.utils import find_repo


def _get_commit_message(repo: Path, sha: str) -> str:
    """Retrieve the log message from a commit."""
    try:
        _, payload = read_object(repo, sha)
        return json.loads(payload).get("message", "No message")
    except Exception:
        return ""


def _extract_file_content(repo: Path, blob_sha: str | None) -> str:
    """Retrieve the text content of a blob."""
    if not blob_sha:
        return ""
    try:
        _, payload = read_object(repo, blob_sha)
        return payload.decode("utf-8")
    except Exception:
        return ""


def _ai_judge_merge(
    file_path: str,
    content_a: str, intent_a: str,
    content_b: str, intent_b: str
) -> tuple[bool, str]:
    """
    Ask the AI to merge two conflicting file versions.
    Expected to return JSON with {"confidence": INT, "code": "..."}
    """
    try:
        from vault_ai.llm import ask
    except ImportError:
        return False, "AI model not available."

    prompt = f"""\
You are an AI Merge Judge. You must resolve a git-style merge conflict.
File: {file_path}

Branch A Intent: {intent_a}
Branch B Intent: {intent_b}

=== Branch A Content ===
{content_a}

=== Branch B Content ===
{content_b}

Merge the two files logically, preserving the intent of both users.
Respond ONLY with a valid JSON object matching this schema exactly:
{{
  "confidence": INT_0_TO_100,
  "code": "THE_MERGED_FILE_CONTENT",
  "reason": "Brief explanation"
}}
"""
    result = ask(prompt)
    if not result:
        return False, "AI failed to respond."

    # Parse AI JSON
    # Strip markdown block if present
    clean_json = result.strip()
    if clean_json.startswith("```json"):
        clean_json = clean_json.split("```json", 1)[1]
    if clean_json.endswith("```"):
        clean_json = clean_json.rsplit("```", 1)[0]
    
    clean_json = clean_json.strip()

    try:
        data = json.loads(clean_json)
        conf = data.get("confidence", 0)
        code = data.get("code", "")
        reason = data.get("reason", "Low confidence.")
        
        if conf >= 90 and code:
            return True, code
        elif code:
            print(f"\n  🛡  High-Trust Merge Pause:")
            print(f"      'I merged these using semantic logic because {reason}, but I am only {conf}% sure. Please review.'")
            confirm = input("      Accept AI merge proposal? [y/N]: ").strip().lower()
            if confirm in ("y", "yes"):
                return True, code
            else:
                diff_marker = f"<<<<<<< Branch A\n{content_a}\n=======\n{content_b}\n>>>>>>> Branch B"
                return False, f"User rejected AI merge.\n\nFalling back to Smart-Diff:\n{diff_marker}"
        else:
            diff_marker = f"<<<<<<< Branch A\n{content_a}\n=======\n{content_b}\n>>>>>>> Branch B"
            return False, f"AI Failed mapping.\n\nFalling back to Smart-Diff:\n{diff_marker}"
    except json.JSONDecodeError:
        return False, "Failed to parse AI output as JSON."


def merge_branch(target_branch: str, repo: Path | None = None) -> bool:
    """
    Merge `target_branch` into the current branch using AI Judge.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return False

    current_branch = get_current_branch(repo)
    if current_branch == target_branch:
        print("  ✗  Cannot merge a branch into itself.")
        return False

    # 1. Predict conflicts (handles retrieving trees/blobs)
    print(f"  🔍  Analyzing logic dependencies between '{current_branch}' and '{target_branch}'...")
    warnings = predict_conflicts(current_branch, target_branch, repo)

    current_sha = get_head(repo)
    target_sha = _get_branch_head(repo, target_branch)

    if not target_sha:
        print(f"  ✗  Branch '{target_branch}' does not exist.")
        return False

    intent_curr = _get_commit_message(repo, current_sha)
    intent_target = _get_commit_message(repo, target_sha)

    # We need blobs to do file-level merges
    from vault_ai.conflict import _get_commit_tree, _collect_blobs
    curr_tree = _get_commit_tree(repo, current_sha)
    targ_tree = _get_commit_tree(repo, target_sha)
    curr_blobs = _collect_blobs(repo, curr_tree)
    targ_blobs = _collect_blobs(repo, targ_tree)

    has_unresolved_conflicts = False

    if warnings:
        print(f"\n  ⚖️  AI Judge invoked for {len(warnings)} conflict(s)...")

    for w in warnings:
        print(f"      Judging file: {w.file_path}")
        content_curr = _extract_file_content(repo, curr_blobs.get(w.file_path))
        content_targ = _extract_file_content(repo, targ_blobs.get(w.file_path))

        success, merged_code_or_err = _ai_judge_merge(
            w.file_path,
            content_curr, intent_curr,
            content_targ, intent_target
        )

        dest = repo / w.file_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if success:
            print(f"      ✓ Auto-fixed logically (>90% confidence).")
            dest.write_text(merged_code_or_err)
        else:
            print(f"      ✗ Manual review required.")
            dest.write_text(merged_code_or_err)
            has_unresolved_conflicts = True

    # 2. Bring over non-conflicting files
    conflicting_files = {w.file_path for w in warnings}
    for fp, sha in targ_blobs.items():
        if fp not in conflicting_files and fp not in curr_blobs:
            content = _extract_file_content(repo, sha)
            dest = repo / fp
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)

    if has_unresolved_conflicts:
        print("\n  ⚠  Merge paused. Please resolve conflicts marked in the files above, then 'vault save'.")
        return False
    else:
        # Auto-commit the merge
        msg = f"merge {target_branch} into {current_branch}"
        print(f"\n  ✓  Merge successful! Committing...")
        from vault_ai.store import direct_commit
        direct_commit(msg, repo)
        return True
