"""
vault_ai.integrity
~~~~~~~~~~~~~~~~~~
Tamper-Proof History: Strict Merkle chain verification.

Verifies that every commit in the chain is cryptographically consistent:
  - SHA of stored object matches the content it was derived from
  - Commit's `parent` field correctly chains to the previous commit
  - Tree SHA inside the commit matches the actual tree object
  - Any alteration → immediate lockdown (refuse to accept new commits)
"""
from __future__ import annotations

import hashlib
import json
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, get_head, read_object


# ---------------------------------------------------------------------------
# Verification data structures
# ---------------------------------------------------------------------------

@dataclass
class IntegrityViolation:
    sha: str
    reason: str
    kind: str  # "hash_mismatch", "chain_break", "tree_missing"

    def __str__(self):
        return f"  🔴  {self.sha[:12]}  [{self.kind}]  {self.reason}"


# ---------------------------------------------------------------------------
# Low-level hash verification
# ---------------------------------------------------------------------------

def _raw_object_path(repo: Path, sha: str) -> Path:
    """Return the path to the raw object file."""
    return repo / VAULT_DIR / "objects" / sha[:2] / sha[2:]


def _verify_object_hash(repo: Path, sha: str) -> bool:
    """
    Re-hash the stored object content and compare against sha.
    Returns True if hash matches.
    """
    obj_path = _raw_object_path(repo, sha)
    if not obj_path.exists():
        return False

    raw = obj_path.read_bytes()
    try:
        d = zlib.decompressobj()
        decompressed = d.decompress(raw)
        if d.unused_data:
            return False
    except Exception:
        return False
    recomputed = hashlib.sha256(decompressed).hexdigest()
    return recomputed == sha


def _read_commit_safe(repo: Path, sha: str) -> Optional[dict]:
    """Safely read a commit object; return None on any error."""
    try:
        obj_type, payload = read_object(repo, sha)
        return json.loads(payload)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Merkle chain walker
# ---------------------------------------------------------------------------

def verify_chain(repo: Path | None = None, limit: int = 500) -> List[IntegrityViolation]:
    """
    Walk the entire commit chain from HEAD and verify:
      1. Each commit's SHA matches its content (hash integrity)
      2. Each commit's parent SHA actually exists (chain continuity)
      3. Each commit's tree SHA actually exists (tree integrity)

    Returns a list of IntegrityViolation objects.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return []

    violations = []
    sha = get_head(repo)
    if sha is None:
        return []  # Nothing to verify

    visited = set()
    count = 0

    while sha and count < limit:
        if sha in visited:
            violations.append(IntegrityViolation(
                sha=sha, kind="chain_break",
                reason="Circular reference detected in commit chain."
            ))
            break
        visited.add(sha)
        count += 1

        # 1. Verify the object hash
        if not _verify_object_hash(repo, sha):
            violations.append(IntegrityViolation(
                sha=sha, kind="hash_mismatch",
                reason="Stored content does not match SHA — commit may have been tampered with."
            ))
            # Can't trust further fields, stop walking this branch
            break

        # 2. Read commit metadata
        commit = _read_commit_safe(repo, sha)
        if commit is None:
            violations.append(IntegrityViolation(
                sha=sha, kind="hash_mismatch",
                reason="Commit object is unreadable or corrupt."
            ))
            break

        # 3. Verify tree exists
        tree_sha = commit.get("tree")
        if tree_sha and not _raw_object_path(repo, tree_sha).exists():
            violations.append(IntegrityViolation(
                sha=sha, kind="tree_missing",
                reason=f"Commit references tree {tree_sha[:12]} which does not exist."
            ))

        # 4. Verify tree hash
        if tree_sha and not _verify_object_hash(repo, tree_sha):
            violations.append(IntegrityViolation(
                sha=sha, kind="hash_mismatch",
                reason=f"Tree {tree_sha[:12]} hash mismatch — tree may have been altered."
            ))

        sha = commit.get("parent")

    return violations


# ---------------------------------------------------------------------------
# Lockdown
# ---------------------------------------------------------------------------

def write_lockdown(repo: Path, reason: str) -> None:
    """
    Write a lockdown file that prevents new commits.
    vault save will check for this file before committing.
    """
    lock_path = repo / VAULT_DIR / "LOCKDOWN"
    lock_path.write_text(
        f"VAULT-AI INTEGRITY LOCKDOWN\n{reason}\n"
        "Remove this file manually after you have investigated and restored history.\n"
    )
    print(f"\n  🔐  LOCKDOWN ENGAGED: Repository is locked.")
    print(f"      Reason: {reason}")
    print(f"      File: {lock_path}")
    print(f"      Remove '{VAULT_DIR}/LOCKDOWN' manually to unlock after investigation.\n")


def check_lockdown(repo: Path) -> bool:
    """Return True if the repository is in lockdown mode."""
    return (repo / VAULT_DIR / "LOCKDOWN").exists()


def clear_lockdown(repo: Path) -> bool:
    """Remove the lockdown file. Returns True if cleared."""
    lock_path = repo / VAULT_DIR / "LOCKDOWN"
    if lock_path.exists():
        lock_path.unlink()
        print("  ✅  Lockdown cleared. Repository is now unlocked.")
        return True
    print("  ⚠  No lockdown found.")
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_and_lockdown(repo: Path | None = None) -> bool:
    """
    Full integrity check. If violations found, engage lockdown automatically.
    Returns True if chain is clean, False if compromised.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return True

    violations = verify_chain(repo)

    if not violations:
        print("  ✅  Integrity: Merkle chain is clean — history has not been tampered with.")
        return True

    print(f"\n  🚨  INTEGRITY ALERT: {len(violations)} violation(s) detected:\n")
    for v in violations:
        print(str(v))

    write_lockdown(repo, f"{len(violations)} Merkle chain violation(s) detected.")
    return False


def print_integrity_report(violations: List[IntegrityViolation]) -> None:
    """Pretty-print a list of violations."""
    if not violations:
        print("  ✅  Integrity OK — all commits verified.")
        return
    print(f"\n  🚨  {len(violations)} integrity violation(s):\n")
    for v in violations:
        print(str(v))
    print()
