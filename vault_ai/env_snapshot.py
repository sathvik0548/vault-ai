"""
vault_ai.env_snapshot
~~~~~~~~~~~~~~~~~~~~~
Environmental Snapshotting — captures WHO ran the code and WHERE.

On every `vault save`, a sidecar JSON is written to .vault/env_snaps/<sha>.env.json
capturing:
    - Python version & platform
    - All installed packages (via importlib.metadata — zero dependencies)
    - Safe subset of environment variables
    - CPU arch & hostname

CLI usage (via cli.py):
    vault env show              → latest commit's env snapshot
    vault env diff <sha>        → diff current env vs a historic snapshot
"""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, get_head

# Env vars that are safe to capture (never contain secrets)
_SAFE_ENV_VARS = [
    "PATH", "VIRTUAL_ENV", "CONDA_DEFAULT_ENV", "CONDA_PREFIX",
    "PYENV_VERSION", "PIPENV_ACTIVE", "POETRY_ACTIVE",
    "NODE_ENV", "JAVA_HOME", "GOPATH",
    "LANG", "LC_ALL", "TZ", "TERM",
    "SHELL", "USER", "HOME",
]

_ENV_SNAPS_DIR = "env_snaps"


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass
class EnvSnapshot:
    python_version: str
    platform_system: str     # e.g. "Darwin", "Linux", "Windows"
    platform_release: str
    cpu_arch: str
    hostname: str
    packages: Dict[str, str]            # {package_name: version}
    env_vars: Dict[str, str]            # {VAR: value}
    vault_ai_version: str = "0.1.0"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "EnvSnapshot":
        return EnvSnapshot(**d)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _installed_packages() -> Dict[str, str]:
    """Return {name: version} for all packages visible to the current Python."""
    try:
        from importlib.metadata import packages_distributions, version
        # Use importlib.metadata — zero external deps
        pkgs: Dict[str, str] = {}
        try:
            # Python 3.9+
            from importlib.metadata import distributions
            for dist in distributions():
                name = dist.metadata.get("Name", "")
                ver  = dist.metadata.get("Version", "")
                if name:
                    pkgs[name.lower()] = ver
        except Exception:
            pass
        return dict(sorted(pkgs.items()))
    except Exception:
        return {}


def _safe_env_vars() -> Dict[str, str]:
    """Return a safe subset of os.environ."""
    result: Dict[str, str] = {}
    for key in _SAFE_ENV_VARS:
        val = os.environ.get(key)
        if val is not None:
            result[key] = val
    return result


def capture(repo: Path | None = None) -> EnvSnapshot:
    """Capture the current environment and return an EnvSnapshot."""
    snap = EnvSnapshot(
        python_version=sys.version,
        platform_system=platform.system(),
        platform_release=platform.release(),
        cpu_arch=platform.machine(),
        hostname=socket.gethostname(),
        packages=_installed_packages(),
        env_vars=_safe_env_vars(),
    )

    # Also capture dependency file versions if present
    if repo is None:
        from vault_ai.utils import find_repo
        repo = find_repo()
    if repo:
        req_file = repo / "requirements.txt"
        if req_file.exists():
            try:
                for line in req_file.read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        if "==" in line:
                            name, ver = line.split("==", 1)
                            snap.packages[name.strip().lower()] = ver.strip()
                        elif line:
                            snap.packages[line.lower()] = "any"
            except OSError:
                pass

        pkg_json = repo / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text())
                for dep_key in ("dependencies", "devDependencies"):
                    deps = data.get(dep_key, {})
                    for name, ver in deps.items():
                        snap.packages[f"npm:{name}"] = ver
            except (OSError, json.JSONDecodeError):
                pass

    return snap


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _snaps_dir(repo: Path) -> Path:
    d = repo / VAULT_DIR / _ENV_SNAPS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_env_snapshot(repo: Path, commit_sha: str, snapshot: Optional[EnvSnapshot] = None) -> None:
    """Write a sidecar env snapshot for the given commit SHA."""
    if snapshot is None:
        snapshot = capture()
    snap_path = _snaps_dir(repo) / f"{commit_sha}.env.json"
    snap_path.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")


def load_env_snapshot(repo: Path, commit_sha: str) -> Optional[EnvSnapshot]:
    """Load the env snapshot for a given commit SHA. Returns None if not found."""
    snap_path = _snaps_dir(repo) / f"{commit_sha}.env.json"
    if not snap_path.exists():
        return None
    try:
        return EnvSnapshot.from_dict(json.loads(snap_path.read_text(encoding="utf-8")))
    except Exception:
        return None


def latest_snapshot(repo: Path) -> Tuple[Optional[str], Optional[EnvSnapshot]]:
    """Return (commit_sha, snapshot) for the most recent commit that has one."""
    sha = get_head(repo)
    if sha is None:
        return None, None
    snap = load_env_snapshot(repo, sha)
    return sha, snap


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

@dataclass
class EnvDiff:
    added_packages: Dict[str, str]    = field(default_factory=dict)  # new in current
    removed_packages: Dict[str, str]  = field(default_factory=dict)  # gone vs snap
    changed_packages: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # name: (old, new)
    python_changed: bool = False
    python_old: str = ""
    python_new: str = ""
    env_var_changes: Dict[str, Tuple[Optional[str], Optional[str]]] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return (
            not self.added_packages and
            not self.removed_packages and
            not self.changed_packages and
            not self.python_changed and
            not self.env_var_changes
        )


def diff_envs(old: EnvSnapshot, new: EnvSnapshot) -> EnvDiff:
    """Compare two EnvSnapshot objects and return a structured diff."""
    result = EnvDiff()

    # Python version
    if old.python_version != new.python_version:
        result.python_changed = True
        result.python_old = old.python_version.split(" ")[0]
        result.python_new = new.python_version.split(" ")[0]

    # Packages
    old_pkgs = old.packages
    new_pkgs = new.packages
    all_keys = set(old_pkgs) | set(new_pkgs)
    for k in all_keys:
        in_old = k in old_pkgs
        in_new = k in new_pkgs
        if in_old and in_new:
            if old_pkgs[k] != new_pkgs[k]:
                result.changed_packages[k] = (old_pkgs[k], new_pkgs[k])
        elif in_new:
            result.added_packages[k] = new_pkgs[k]
        else:
            result.removed_packages[k] = old_pkgs[k]

    # Env vars
    all_env_keys = set(old.env_vars) | set(new.env_vars)
    for k in all_env_keys:
        ov = old.env_vars.get(k)
        nv = new.env_vars.get(k)
        if ov != nv:
            result.env_var_changes[k] = (ov, nv)

    return result


# ---------------------------------------------------------------------------
# Pretty-printer
# ---------------------------------------------------------------------------

def print_snapshot(snap: EnvSnapshot, sha: Optional[str] = None) -> None:
    """Print a human-readable env snapshot."""
    label = f" (commit {sha[:10]})" if sha else ""
    print(f"\n  🌍  Environment Snapshot{label}")
    print(f"  {'─'*45}")
    print(f"  Python   : {snap.python_version.splitlines()[0]}")
    print(f"  OS       : {snap.platform_system} {snap.platform_release} ({snap.cpu_arch})")
    print(f"  Host     : {snap.hostname}")
    print(f"  Packages : {len(snap.packages)} installed")
    if snap.packages:
        samples = list(snap.packages.items())[:6]
        for name, ver in samples:
            print(f"             {name}=={ver}")
        if len(snap.packages) > 6:
            print(f"             … and {len(snap.packages) - 6} more")
    if snap.env_vars:
        print(f"  Env Vars : {', '.join(f'{k}' for k in list(snap.env_vars)[:4])} …")
    print()


def print_diff(diff: EnvDiff) -> None:
    """Print a human-readable diff between two env snapshots."""
    if diff.is_clean:
        print("  ✓  Environment is identical to the stored snapshot.")
        return

    print("\n  🔄  Environment Diff vs stored snapshot:")
    print(f"  {'─'*45}")

    if diff.python_changed:
        print(f"  🐍  Python: {diff.python_old} → {diff.python_new}")

    if diff.added_packages:
        print(f"\n  ＋ Packages added ({len(diff.added_packages)}):")
        for n, v in sorted(diff.added_packages.items()):
            print(f"      {n}=={v}")

    if diff.removed_packages:
        print(f"\n  － Packages removed ({len(diff.removed_packages)}):")
        for n, v in sorted(diff.removed_packages.items()):
            print(f"      {n}=={v}")

    if diff.changed_packages:
        print(f"\n  ✎  Packages changed ({len(diff.changed_packages)}):")
        for n, (ov, nv) in sorted(diff.changed_packages.items()):
            print(f"      {n}: {ov} → {nv}")

    if diff.env_var_changes:
        print(f"\n  🌐  Env var changes ({len(diff.env_var_changes)}):")
        for k, (ov, nv) in sorted(diff.env_var_changes.items()):
            print(f"      {k}: {ov!r} → {nv!r}")

    print()
