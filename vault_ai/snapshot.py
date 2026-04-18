"""
vault_ai.snapshot
Lightweight snapshot helpers for the Time Machine engine.
(Phase 2 scaffold — background watcher will be added later.)
"""
from __future__ import annotations


import json
import time
from pathlib import Path
from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo
from vault_ai.store import snapshot_tree


def take_snapshot(repo: Path | None = None, label: str = "autosave") -> str | None:
    """
    Take a lightweight snapshot of the working tree.
    Stores the tree SHA + metadata in .vault/snapshots/<timestamp>.json
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return None

    tree_sha = snapshot_tree(repo)
    ts = time.time()
    snap = {
        "tree": tree_sha,
        "timestamp": ts,
        "label": label,
    }
    snap_dir = repo / VAULT_DIR / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{int(ts)}.json"
    snap_path.write_text(json.dumps(snap, indent=2) + "\n")
    return tree_sha


def list_snapshots(repo: Path | None = None) -> list[dict]:
    """List all snapshots ordered newest first."""
    if repo is None:
        repo = find_repo()
    if repo is None:
        return []
    snap_dir = repo / VAULT_DIR / "snapshots"
    if not snap_dir.exists():
        return []
    snaps = []
    for f in sorted(snap_dir.glob("*.json"), reverse=True):
        snaps.append(json.loads(f.read_text()))
    return snaps
