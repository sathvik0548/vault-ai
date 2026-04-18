"""
vault_ai.utils
Shared helpers: hashing, compression, repo discovery, config.
"""
from __future__ import annotations


import hashlib
import zlib
import json
import os
from pathlib import Path
from vault_ai import VAULT_DIR


# ---------------------------------------------------------------------------
# Hashing & Object I/O
# ---------------------------------------------------------------------------

def hash_object(data: bytes, obj_type: str = "blob") -> tuple[str, bytes]:
    """Return (sha256_hex, raw_store_bytes) for a given payload."""
    header = f"{obj_type} {len(data)}\0".encode()
    store = header + data
    sha = hashlib.sha256(store).hexdigest()
    return sha, store


def write_object(repo: Path, sha: str, raw: bytes) -> Path:
    """Compress *raw* and persist under objects/<2-char>/<rest>."""
    obj_dir = repo / VAULT_DIR / "objects" / sha[:2]
    obj_dir.mkdir(parents=True, exist_ok=True)
    obj_path = obj_dir / sha[2:]
    if not obj_path.exists():
        obj_path.write_bytes(zlib.compress(raw))
    return obj_path


def read_object(repo: Path, sha: str) -> tuple[str, bytes]:
    """
    Read & decompress an object.  Returns (obj_type, payload).
    """
    obj_path = repo / VAULT_DIR / "objects" / sha[:2] / sha[2:]
    raw = zlib.decompress(obj_path.read_bytes())
    # Parse header: "<type> <size>\0<data>"
    null_idx = raw.index(b"\0")
    header = raw[:null_idx].decode()
    obj_type, _ = header.split(" ", 1)
    payload = raw[null_idx + 1:]
    return obj_type, payload


# ---------------------------------------------------------------------------
# Repo discovery
# ---------------------------------------------------------------------------

def find_repo(start: str = ".") -> Path | None:
    """Walk up directory tree to find the repo root containing .vault/."""
    current = Path(start).resolve()
    while True:
        if (current / VAULT_DIR).is_dir():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def get_head(repo: Path) -> str | None:
    """Return the SHA the current HEAD points to (or None)."""
    head_path = repo / VAULT_DIR / "HEAD"
    content = head_path.read_text().strip()
    if content.startswith("ref: "):
        ref_path = repo / VAULT_DIR / content[5:]
        if ref_path.exists():
            return ref_path.read_text().strip() or None
        return None
    return content or None


def update_ref(repo: Path, ref: str, sha: str):
    """Write *sha* into the given ref file (e.g. refs/heads/main)."""
    ref_path = repo / VAULT_DIR / ref
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(sha + "\n")


def get_current_branch(repo: Path) -> str:
    """Return the current branch name (e.g. 'main')."""
    head_path = repo / VAULT_DIR / "HEAD"
    content = head_path.read_text().strip()
    if content.startswith("ref: "):
        return content.split("/")[-1]
    return "(detached)"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(repo: Path) -> dict:
    cfg_path = repo / VAULT_DIR / "config.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text())
    return {}


def save_config(repo: Path, config: dict):
    cfg_path = repo / VAULT_DIR / "config.json"
    cfg_path.write_text(json.dumps(config, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Ignore helpers
# ---------------------------------------------------------------------------

DEFAULT_IGNORE = {".vault", "__pycache__", ".git", ".DS_Store", "node_modules", ".env", ".vault_private", ".vault-secrets-allow"}


def is_ignored(path: Path, repo: Path) -> bool:
    """Check if *path* should be ignored."""
    parts = path.relative_to(repo).parts
    for part in parts:
        if part in DEFAULT_IGNORE:
            return True
    return False
