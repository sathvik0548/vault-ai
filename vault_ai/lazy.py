"""
vault_ai.lazy
~~~~~~~~~~~~~
Lazy-Load Engine (Sparse Indexing): Extracts files from the compressed vault 
only when the user requests them via `vault hydrate`. Perfect for giant monorepos.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, read_object


def hydrate(path: str, repo: Path | None = None) -> bool:
    """
    Physically extract virtual/lazy files from the sparse manifest
    that belong to the given path or directory.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return False
        
    manifest_path = repo / VAULT_DIR / "sparse_manifest.json"
    if not manifest_path.exists():
        print("  ⚠  No sparse manifest found. Repository is not in lazy/sparse mode.")
        return False
        
    with open(manifest_path, "r") as f:
        manifest = json.load(f)
        
    target_path = Path(path).resolve()
    
    hydrated = 0
    missing_shadow = 0
    
    for file_path_str, info in manifest.items():
        fp = Path(file_path_str)
        
        # Check if the file is inside the target path or is exactly the target path
        try:
            fp.relative_to(target_path)
            is_target = True
        except ValueError:
            is_target = (fp == target_path)
            
        if not is_target:
            continue
            
        fp.parent.mkdir(parents=True, exist_ok=True)
        
        if info.get("is_vlink"):
            # Fetch from shadow storage
            shadow_path = repo / VAULT_DIR / "objects" / "large" / info["target_sha"]
            if shadow_path.exists():
                shutil.copy2(shadow_path, fp)
                hydrated += 1
            else:
                print(f"  ⚠  Missing large file from shadow storage for: {fp.name}")
                missing_shadow += 1
        else:
            # Fetch from standard Git-style object tree
            _, blob_data = read_object(repo, info["sha"])
            fp.write_bytes(blob_data)
            hydrated += 1
            
    if hydrated > 0:
        print(f"  💧 Hydrated {hydrated} file(s) for '{path}'.")
    elif missing_shadow == 0:
        print(f"  ✓  Nothing to hydrate in '{path}' or not present in manifest.")
        
    return True
