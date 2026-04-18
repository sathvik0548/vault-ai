"""
vault_ai.sandbox
~~~~~~~~~~~~~~~~~
Virtual Sandbox: shallow-clone current state into .vault/sandbox/
for "What-If" experiments without touching main branch history.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, get_head, read_object


_SANDBOX_DIR = "sandbox"
_SANDBOX_META = "sandbox_meta.json"


def _sandbox_path(repo: Path) -> Path:
    return repo / VAULT_DIR / _SANDBOX_DIR


def _sandbox_meta_path(repo: Path) -> Path:
    return repo / VAULT_DIR / _SANDBOX_META


def is_sandbox_active(repo: Path | None = None) -> bool:
    """Check if a sandbox is currently active."""
    if repo is None:
        repo = find_repo()
    if repo is None:
        return False
    return _sandbox_path(repo).exists()


def enter_sandbox(repo: Path | None = None) -> bool:
    """
    Create a shallow-clone of the current working tree into .vault/sandbox/.
    Users can freely modify files in the sandbox without affecting history.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return False

    sb = _sandbox_path(repo)
    if sb.exists():
        print("  ⚠  Sandbox already active. Use `vault sandbox exit` first.")
        return False

    head = get_head(repo)
    sb.mkdir(parents=True)

    # Copy all working-tree files (not .vault) into sandbox
    for item in repo.iterdir():
        if item.name.startswith(".vault") or item.name.startswith("."):
            continue
        dest = sb / item.name
        if item.is_file():
            shutil.copy2(item, dest)
        elif item.is_dir():
            shutil.copytree(item, dest, ignore=shutil.ignore_patterns(".vault*", ".*"))

    # Save metadata
    meta = {
        "original_head": head,
        "files_copied": len(list(sb.rglob("*"))),
    }
    _sandbox_meta_path(repo).write_text(json.dumps(meta, indent=2))

    file_count = meta["files_copied"]
    print(f"  🧪  Sandbox created with {file_count} files in {sb}")
    print(f"       Edit files inside .vault/sandbox/ freely.")
    print(f"       Run `vault sandbox exit` to discard, or `vault sandbox merge` to keep changes.")
    return True


def exit_sandbox(repo: Path | None = None, merge: bool = False) -> bool:
    """
    Exit the sandbox. If merge=True, copy sandbox files back to working tree.
    Otherwise discard all sandbox changes.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return False

    sb = _sandbox_path(repo)
    if not sb.exists():
        print("  ⚠  No active sandbox.")
        return False

    if merge:
        # Copy sandbox files back to working tree
        count = 0
        for item in sb.rglob("*"):
            if item.is_file():
                rel = item.relative_to(sb)
                dest = repo / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)
                count += 1
        print(f"  ✦  Merged {count} files from sandbox back to working tree.")

    # Clean up
    shutil.rmtree(sb)
    meta_path = _sandbox_meta_path(repo)
    if meta_path.exists():
        meta_path.unlink()

    if merge:
        print("  ✓  Sandbox merged and removed. Run `vault save` to commit.")
    else:
        print("  🗑  Sandbox discarded. No changes applied.")
    return True


def sandbox_status(repo: Path | None = None) -> None:
    """Print the current sandbox status."""
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return

    sb = _sandbox_path(repo)
    if not sb.exists():
        print("  ⚠  No active sandbox.")
        return

    meta_path = _sandbox_meta_path(repo)
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())

    file_count = len(list(sb.rglob("*")))
    head = meta.get("original_head", "unknown")[:10]
    print(f"\n  🧪  Active Sandbox")
    print(f"  {'─'*35}")
    print(f"  Based on:  commit {head}")
    print(f"  Files:     {file_count}")
    print(f"  Path:      {sb}")
    print(f"\n  Commands:  vault sandbox exit   (discard)")
    print(f"             vault sandbox merge  (apply changes)")
    print()
