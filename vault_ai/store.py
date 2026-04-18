"""
vault_ai.store
Content-Addressable Storage: init, tree snapshots, direct commits.
No staging area — everything flows straight from working tree to commit.
"""
from __future__ import annotations


import json
import time
import os
from pathlib import Path
from vault_ai import VAULT_DIR
from vault_ai.utils import (
    hash_object, write_object, read_object,
    find_repo, get_head, update_ref, get_current_branch,
    is_ignored, save_config,
)


# ---------------------------------------------------------------------------
# Repository Init
# ---------------------------------------------------------------------------

def init_repo(path: str = ".") -> bool:
    """Create a fresh .vault/ repository structure."""
    repo = Path(path).resolve()
    vault = repo / VAULT_DIR

    if vault.exists():
        print(f"  ⚠  Vault-AI repository already exists at {vault}")
        return False

    (vault / "objects").mkdir(parents=True)
    (vault / "refs" / "heads").mkdir(parents=True)
    (vault / "snapshots").mkdir(parents=True)         # for time-machine
    (vault / "HEAD").write_text("ref: refs/heads/main\n")
    save_config(repo, {"created": time.time(), "version": "0.1.0"})

    print(f"  ✦  Initialized Vault-AI repository in {vault}")
    return True


# ---------------------------------------------------------------------------
# Tree builder
# ---------------------------------------------------------------------------

def _build_tree(directory: Path, repo: Path) -> str:
    """
    Recursively hash a directory into a tree object.
    A tree object is JSON lines of the form:
        {"mode": "blob"|"tree", "name": <name>, "sha": <sha>}
    Returns the SHA of the tree.
    """
    entries = []
    for child in sorted(directory.iterdir()):
        if is_ignored(child, repo):
            continue

        if child.is_file():
            stat = child.stat()
            if stat.st_size > 50 * 1024 * 1024:
                import hashlib
                import shutil
                # Shadow Storage for files > 50MB
                h = hashlib.sha256()
                with open(child, "rb") as f:
                    for chunk in iter(lambda: f.read(81920), b""):
                        h.update(chunk)
                blob_sha = h.hexdigest()

                large_obj_dir = repo / VAULT_DIR / "objects" / "large"
                large_obj_dir.mkdir(parents=True, exist_ok=True)
                shadow_path = large_obj_dir / blob_sha

                if not shadow_path.exists():
                    shutil.copy2(child, shadow_path)

                vlink_data = json.dumps({"vlink": True, "sha": blob_sha, "size": stat.st_size}).encode()
                sha, raw = hash_object(vlink_data, "blob")
                write_object(repo, sha, raw)
                entries.append({"mode": "blob", "name": child.name, "sha": sha})
            else:
                data = child.read_bytes()
                sha, raw = hash_object(data, "blob")
                write_object(repo, sha, raw)
                entries.append({"mode": "blob", "name": child.name, "sha": sha})
        elif child.is_dir():
            subtree_sha = _build_tree(child, repo)
            entries.append({"mode": "tree", "name": child.name, "sha": subtree_sha})

    tree_data = json.dumps(entries, separators=(",", ":")).encode()
    sha, raw = hash_object(tree_data, "tree")
    write_object(repo, sha, raw)
    return sha


def snapshot_tree(repo: Path) -> str:
    """Hash the entire working tree and return the root tree SHA."""
    return _build_tree(repo, repo)


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

def create_commit(repo: Path, tree_sha: str, message: str,
                  author: str = "user") -> str:
    """
    Build a commit object and update HEAD.
    Commit payload (JSON):
        tree, parent, author, timestamp, message
    """
    parent = get_head(repo)

    commit_body = {
        "tree": tree_sha,
        "parent": parent,
        "author": author,
        "timestamp": time.time(),
        "message": message,
    }
    commit_data = json.dumps(commit_body, indent=2).encode()
    sha, raw = hash_object(commit_data, "commit")
    write_object(repo, sha, raw)

    # Update current branch ref
    head_content = (repo / VAULT_DIR / "HEAD").read_text().strip()
    if head_content.startswith("ref: "):
        update_ref(repo, head_content[5:], sha)
    else:
        (repo / VAULT_DIR / "HEAD").write_text(sha + "\n")

    return sha


def direct_commit(message: str, repo: Path | None = None, force: bool = False) -> str | None:
    """
    The hallmark of Vault-AI: skip staging, commit everything in one shot.
    1. Snapshot the whole working tree → tree object
    2. Create a commit object pointing at that tree
    3. Capture the environment as a sidecar
    4. Return the commit SHA
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return None

    # 1. Integrity Check & Lockdown
    try:
        from vault_ai.integrity import check_lockdown, verify_and_lockdown
        if check_lockdown(repo):
            print("  ✗  Commit blocked. Repository is in LOCKDOWN mode.")
            return None
        if not verify_and_lockdown(repo):
            print("  ✗  Commit aborted due to Tamper-Proof integrity violation.")
            return None
    except ImportError:
        pass

    # 2. AI Anomaly Detection
    if not force:
        try:
            from vault_ai.anomaly import scan_working_tree_anomalies, print_anomaly_report
            findings = scan_working_tree_anomalies(repo)
            if findings and not print_anomaly_report(findings):
                print("  ✗  Commit aborted due to AI Anomaly Detection. Use --force to override.")
                return None
        except ImportError:
            pass
    # 3. Policy-as-Code Validator
    try:
        from vault_ai.policy import validate_policy
        if not validate_policy(repo):
            return None
    except ImportError:
        pass

    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return None

    tree_sha = snapshot_tree(repo)
    commit_sha = create_commit(repo, tree_sha, message)
    
    # 4. Object Lineage Tracking
    try:
        from vault_ai.lineage import track_lineage, print_lineage_report
        lineage_data = track_lineage(repo)
        if lineage_data:
            print_lineage_report(lineage_data)
    except ImportError:
        pass

    short = commit_sha[:10]
    branch = get_current_branch(repo)
    print(f"  ✦  [{branch} {short}] {message}")

    # Silently attach an env snapshot to this commit
    try:
        from vault_ai.env_snapshot import save_env_snapshot
        save_env_snapshot(repo, commit_sha)
    except Exception:
        pass  # never let snapshot failure block a commit

    # 3. Dependency Audit (background/post-commit hook)
    try:
        from vault_ai.dep_audit import audit_dependencies, print_audit_report
        vulns = audit_dependencies(repo)
        if vulns:
            print_audit_report(vulns)
    except Exception:
        pass

    return commit_sha



# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def log(repo: Path | None = None, limit: int = 20):
    """Walk the commit chain from HEAD and print a human-readable log."""
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return

    sha = get_head(repo)
    if sha is None:
        print("  (no commits yet)")
        return

    count = 0
    while sha and count < limit:
        obj_type, payload = read_object(repo, sha)
        commit_data = json.loads(payload)
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(commit_data["timestamp"]))
        print(f"  {sha[:10]}  {ts}  {commit_data['message']}")
        sha = commit_data.get("parent")
        count += 1


# ---------------------------------------------------------------------------
# Branch helpers
# ---------------------------------------------------------------------------

def create_branch(name: str, repo: Path | None = None):
    """Create a new branch at the current HEAD."""
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return

    head_sha = get_head(repo)
    if head_sha is None:
        print("  ✗  Cannot create branch — no commits yet. Run `vault save` first.")
        return

    ref_path = repo / VAULT_DIR / "refs" / "heads" / name
    if ref_path.exists():
        print(f"  ⚠  Branch '{name}' already exists.")
        return

    ref_path.write_text(head_sha + "\n")
    print(f"  ✦  Created branch '{name}' at {head_sha[:10]}")


# ---------------------------------------------------------------------------
# Remote Sync (Zero-Dependency)
# ---------------------------------------------------------------------------

def push_objects(remote_url: str, repo_name: str, repo: Path | None = None):
    """
    Push all objects in .vault/objects to the remote server using standard urllib.
    """
    import urllib.request
    import urllib.parse
    
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return

    obj_dir = repo / VAULT_DIR / "objects"
    if not obj_dir.exists():
        print("  ⚠  No objects to push.")
        return

    print(f"  ☁  Syncing with remote: {remote_url}...")
    
    total_pushed = 0
    for prefix in os.listdir(obj_dir):
        prefix_path = obj_dir / prefix
        if not prefix_path.is_dir():
            continue
        for rest in os.listdir(prefix_path):
            sha = prefix + rest
            obj_path = prefix_path / rest
            
            # Simple raw POST for zero-dependency
            url = f"{remote_url.rstrip('/')}/push/{repo_name}?object_hash={sha}"
            try:
                data = obj_path.read_bytes()
                req = urllib.request.Request(url, data=data, method="POST")
                # Add content-length header for http.server robustness
                req.add_header('Content-Length', str(len(data)))
                with urllib.request.urlopen(req) as response:
                    if response.status == 200:
                        total_pushed += 1
                    else:
                        print(f"  ⚠  Failed to push {sha[:8]}: {response.status}")
            except Exception as e:
                print(f"  ✗  Connection error: {e}")
                return

    print(f"  ✓  Sync complete. Pushed {total_pushed} objects to '{repo_name}'.")


# ---------------------------------------------------------------------------
# Infinite Undo / Time-Travel Revert
# ---------------------------------------------------------------------------

def _restore_tree(repo: Path, tree_sha: str, target_dir: Path, sparse: bool = False, sparse_manifest: dict = None) -> int:
    """
    Recursively restore a tree object to the filesystem.
    Returns count of files written.
    """
    _, payload = read_object(repo, tree_sha)
    entries = json.loads(payload)
    count = 0

    if sparse and sparse_manifest is None:
        sparse_manifest = {}

    for entry in entries:
        dest = target_dir / entry["name"]
        if entry["mode"] == "blob":
            _, blob_data = read_object(repo, entry["sha"])
            
            is_vlink = False
            vlink_sha = None
            try:
                if blob_data.startswith(b'{"vlink":'):
                    vlink_info = json.loads(blob_data)
                    if vlink_info.get("vlink"):
                        is_vlink = True
                        vlink_sha = vlink_info["sha"]
            except Exception:
                pass

            if sparse:
                sparse_manifest[str(dest)] = {
                    "sha": entry["sha"],
                    "is_vlink": is_vlink,
                    "target_sha": vlink_sha if is_vlink else entry["sha"]
                }
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if is_vlink:
                    shadow_path = repo / VAULT_DIR / "objects" / "large" / vlink_sha
                    if shadow_path.exists():
                        import shutil
                        shutil.copy2(shadow_path, dest)
                    else:
                        print(f"  ⚠  Missing large file from shadow storage: {entry['name']}")
                else:
                    dest.write_bytes(blob_data)
                count += 1
        elif entry["mode"] == "tree":
            if not sparse:
                dest.mkdir(parents=True, exist_ok=True)
            count += _restore_tree(repo, entry["sha"], dest, sparse, sparse_manifest)

    if sparse and target_dir == repo:
        manifest_path = repo / VAULT_DIR / "sparse_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(sparse_manifest, f, indent=2)

    return count


def undo_last(repo: Path | None = None, sparse: bool = False) -> bool:
    """
    Revert the last commit: restore the parent commit's tree to the working directory
    and update HEAD to point at the parent. Can be run in sparse mode for lazy checkout.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return False

    head_sha = get_head(repo)
    if head_sha is None:
        print("  ⚠  No commits to undo.")
        return False

    _, payload = read_object(repo, head_sha)
    commit_data = json.loads(payload)
    parent_sha = commit_data.get("parent")

    if parent_sha is None:
        print("  ⚠  This is the first commit — nothing to revert to.")
        return False

    # Read parent commit's tree
    _, parent_payload = read_object(repo, parent_sha)
    parent_data = json.loads(parent_payload)
    parent_tree = parent_data["tree"]

    # Clean working directory (except .vault and hidden files)
    for item in repo.iterdir():
        if item.name.startswith("."):
            continue
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            import shutil
            shutil.rmtree(item)

    # Restore parent tree
    count = _restore_tree(repo, parent_tree, repo, sparse=sparse)

    # Update HEAD to parent
    head_content = (repo / VAULT_DIR / "HEAD").read_text().strip()
    if head_content.startswith("ref: "):
        update_ref(repo, head_content[5:], parent_sha)
    else:
        (repo / VAULT_DIR / "HEAD").write_text(parent_sha + "\n")

    branch = get_current_branch(repo)
    if sparse:
        print(f"  ⏪  Undid commit {head_sha[:10]} (Sparse Mode). Now at [{branch} {parent_sha[:10]}]")
    else:
        print(f"  ⏪  Undid commit {head_sha[:10]}. Now at [{branch} {parent_sha[:10]}]")
        print(f"      Restored {count} files from parent commit.")
    return True

