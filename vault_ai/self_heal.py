"""
vault_ai.self_heal
~~~~~~~~~~~~~~~~~~~
Self-Healing Commits: pre-save hook that runs linters and sends errors
to Ollama for automatic fix proposals.
"""
from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path
from typing import List, Tuple

from vault_ai.llm import ask
from vault_ai.utils import find_repo, is_ignored


# ---------------------------------------------------------------------------
# Linter runners
# ---------------------------------------------------------------------------

def _run_flake8(file_path: Path) -> List[str]:
    """Run flake8 on a single file, return list of error strings."""
    if not shutil.which("flake8"):
        return []
    try:
        result = subprocess.run(
            ["flake8", "--max-line-length=120", "--format=default", str(file_path)],
            capture_output=True, text=True, timeout=15,
        )
        if result.stdout.strip():
            return result.stdout.strip().splitlines()
        return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _run_eslint(file_path: Path) -> List[str]:
    """Run eslint on a single JS/TS file, return list of error strings."""
    if not shutil.which("eslint"):
        return []
    try:
        result = subprocess.run(
            ["eslint", "--format=compact", str(file_path)],
            capture_output=True, text=True, timeout=15,
        )
        if result.stdout.strip():
            return result.stdout.strip().splitlines()
        return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


_LINTER_MAP = {
    ".py": _run_flake8,
    ".js": _run_eslint,
    ".ts": _run_eslint,
    ".jsx": _run_eslint,
    ".tsx": _run_eslint,
}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def lint_working_tree(repo: Path | None = None) -> List[Tuple[str, List[str]]]:
    """
    Lint all supported files in the working tree.
    Returns [(relative_path, [error_line, ...]), ...] for files with errors.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return []

    results: List[Tuple[str, List[str]]] = []

    for dirpath, dirnames, filenames in os.walk(repo):
        dp = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_ignored(dp / d, repo)]
        for fname in filenames:
            fp = dp / fname
            if is_ignored(fp, repo):
                continue
            ext = fp.suffix.lower()
            linter = _LINTER_MAP.get(ext)
            if linter:
                errors = linter(fp)
                if errors:
                    rel = str(fp.relative_to(repo))
                    results.append((rel, errors))
    return results


def propose_fix(file_path: str, errors: List[str], source_code: str) -> str | None:
    """
    Send lint errors + source code to AI and get a fix proposal.
    Returns a markdown-formatted fix suggestion or None.
    """
    prompt = f"""\
You are a code-fixing assistant. A linter found these errors in `{file_path}`:

{chr(10).join(errors[:10])}

Here is the file content:
```
{source_code[:6000]}
```

Propose a minimal fix. Show ONLY the corrected code block, no explanations.
"""
    return ask(prompt)


def run_self_heal(repo: Path | None = None) -> bool:
    """
    Run linters on the working tree. If errors found, propose AI fixes.
    Returns True if clean (or user skipped), False if errors unresolved.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return True

    lint_results = lint_working_tree(repo)
    if not lint_results:
        return True

    print(f"\n  🩺  Self-Heal: found lint errors in {len(lint_results)} file(s):\n")
    for rel_path, errors in lint_results:
        print(f"  ── {rel_path} ({len(errors)} error{'s' if len(errors) > 1 else ''}) ──")
        for e in errors[:5]:
            print(f"    {e}")
        if len(errors) > 5:
            print(f"    … and {len(errors) - 5} more")

    print("\n  🧠  Asking AI for fix proposals...")
    for rel_path, errors in lint_results:
        try:
            source = (repo / rel_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fix = propose_fix(rel_path, errors, source)
        if fix:
            print(f"\n  💡  Proposed fix for {rel_path}:")
            for line in fix.splitlines()[:20]:
                print(f"    {line}")
        else:
            print(f"  ⚠  AI unavailable for {rel_path} — fix manually.")

    return False
