"""
vault_ai.watcher
~~~~~~~~~~~~~~~~~
Background Snapshot Daemon: polls the file tree for changes and
takes lightweight snapshots automatically.

Usage:
    vault watch          # start watching (foreground)
    vault watch --bg     # start as background daemon (optional)
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, is_ignored
from vault_ai.snapshot import take_snapshot


DEFAULT_INTERVAL = 30  # seconds between polls


def _tree_hash(repo: Path) -> str:
    """Compute a fast hash of all file mtimes + sizes in the working tree."""
    hasher = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(repo):
        dp = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_ignored(dp / d, repo)]
        for fname in sorted(filenames):
            fp = dp / fname
            if is_ignored(fp, repo):
                continue
            try:
                stat = fp.stat()
                hasher.update(f"{fp}:{stat.st_mtime}:{stat.st_size}".encode())
            except OSError:
                pass
    return hasher.hexdigest()


def watch(repo: Path | None = None, interval: int = DEFAULT_INTERVAL) -> None:
    """
    Poll the working tree for changes. When a change is detected,
    take a lightweight snapshot. Runs until interrupted (Ctrl+C).
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return

    print(f"  👁  Watcher started (polling every {interval}s). Press Ctrl+C to stop.")
    prev_hash = _tree_hash(repo)
    snap_count = 0

    try:
        while True:
            time.sleep(interval)
            current_hash = _tree_hash(repo)
            if current_hash != prev_hash:
                tree_sha = take_snapshot(repo, label="autosave")
                if tree_sha:
                    snap_count += 1
                    ts = time.strftime("%H:%M:%S")
                    print(f"  📸 [{ts}] Autosave #{snap_count} (tree {tree_sha[:10]})")
                prev_hash = current_hash
    except KeyboardInterrupt:
        print(f"\n  ⏹  Watcher stopped. Took {snap_count} snapshot(s).")
