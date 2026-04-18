"""
vault_ai.diff_engine
Semantic Diff: AST-based analysis for Python files, text fallback for the rest.
"""
from __future__ import annotations


import ast
import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from vault_ai.utils import find_repo, read_object, get_head

# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class DiffEntry:
    category: str          # added | removed | modified | moved | renamed
    kind: str              # function | class | variable | line
    name: str
    detail: str = ""

@dataclass
class DiffResult:
    file_path: str
    entries: list[DiffEntry] = field(default_factory=list)
    unified: str = ""      # raw unified diff for fallback / token input

    @property
    def summary_lines(self) -> list[str]:
        lines = []
        for e in self.entries:
            tag = {
                "added": "＋", "removed": "－", "modified": "✎",
                "moved": "⇄", "renamed": "↻",
            }.get(e.category, "•")
            detail = f" — {e.detail}" if e.detail else ""
            lines.append(f"  {tag} [{e.kind}] {e.name}{detail}")
        return lines


# ---------------------------------------------------------------------------
# Text diff (universal fallback)
# ---------------------------------------------------------------------------

def text_diff(old: str, new: str, filename: str = "") -> str:
    """Return a unified diff string."""
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{filename}", tofile=f"b/{filename}",
    ))


# ---------------------------------------------------------------------------
# AST-based semantic diff (Python files)
# ---------------------------------------------------------------------------

def _extract_definitions(source: str) -> dict:
    """
    Parse Python source and extract top-level function/class definitions.
    Returns {name: {"type": "function"|"class", "source": <str>, "lineno": N}}
    """
    defs = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return defs

    lines = source.splitlines()

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = (node.end_lineno or node.lineno)
            src = "\n".join(lines[node.lineno - 1 : end])
            defs[node.name] = {
                "type": "function",
                "source": src,
                "lineno": node.lineno,
                "args": [a.arg for a in node.args.args],
            }
        elif isinstance(node, ast.ClassDef):
            end = (node.end_lineno or node.lineno)
            src = "\n".join(lines[node.lineno - 1 : end])
            methods = [
                n.name for n in ast.iter_child_nodes(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            defs[node.name] = {
                "type": "class",
                "source": src,
                "lineno": node.lineno,
                "methods": methods,
            }
    return defs


def ast_diff(old_source: str, new_source: str) -> list[DiffEntry]:
    """
    Compare two Python sources at the AST level.
    Detects: added, removed, modified, moved (line change), renamed (same body, new name).
    """
    old_defs = _extract_definitions(old_source)
    new_defs = _extract_definitions(new_source)
    entries: list[DiffEntry] = []

    old_names = set(old_defs)
    new_names = set(new_defs)

    # --- Simple added / removed ---
    purely_added = new_names - old_names
    purely_removed = old_names - new_names

    # --- Detect renames: removed name whose body appears under an added name ---
    renames_found: set[str] = set()
    for rname in list(purely_removed):
        old_src = old_defs[rname]["source"]
        # Strip the def/class line and compare body
        old_body = "\n".join(old_src.splitlines()[1:]).strip()
        for aname in list(purely_added):
            new_src = new_defs[aname]["source"]
            new_body = "\n".join(new_src.splitlines()[1:]).strip()
            if old_body and old_body == new_body:
                entries.append(DiffEntry(
                    "renamed", old_defs[rname]["type"], rname,
                    f"→ {aname}",
                ))
                renames_found.add(rname)
                purely_added.discard(aname)
                break

    purely_removed -= renames_found

    for name in sorted(purely_added):
        entries.append(DiffEntry("added", new_defs[name]["type"], name))

    for name in sorted(purely_removed):
        entries.append(DiffEntry("removed", old_defs[name]["type"], name))

    # --- Modified or moved (present in both) ---
    for name in sorted(old_names & new_names):
        old_d = old_defs[name]
        new_d = new_defs[name]
        if old_d["source"] == new_d["source"]:
            if old_d["lineno"] != new_d["lineno"]:
                entries.append(DiffEntry(
                    "moved", old_d["type"], name,
                    f"line {old_d['lineno']} → {new_d['lineno']}",
                ))
        else:
            entries.append(DiffEntry(
                "modified", old_d["type"], name,
                f"at line {new_d['lineno']}",
            ))

    return entries


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def semantic_diff(old_content: str, new_content: str,
                  filename: str = "") -> DiffResult:
    """
    Pick the best diff strategy based on file extension.
    .py  → AST diff + unified text
    else → unified text only
    """
    unified = text_diff(old_content, new_content, filename)
    result = DiffResult(file_path=filename, unified=unified)

    if filename.endswith(".py"):
        result.entries = ast_diff(old_content, new_content)

    # If AST found nothing but text diff exists, report generic modification
    if unified and not result.entries:
        added = sum(1 for l in unified.splitlines() if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in unified.splitlines() if l.startswith("-") and not l.startswith("---"))
        if added or removed:
            result.entries.append(DiffEntry(
                "modified", "line", filename,
                f"+{added} / −{removed} lines",
            ))

    return result


# ---------------------------------------------------------------------------
# Working-tree diff (compare current files vs last commit)
# ---------------------------------------------------------------------------

def diff_working_tree(repo=None) -> list[DiffResult]:
    """
    Compare every tracked file in the working tree against the last commit.
    Returns a list of DiffResult objects.
    """
    from vault_ai.utils import find_repo, get_head, read_object, is_ignored

    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return []

    head_sha = get_head(repo)
    if head_sha is None:
        print("  (no commits yet — nothing to diff against)")
        return []

    # Reconstruct committed file map from tree
    _, commit_payload = read_object(repo, head_sha)
    commit_data = json.loads(commit_payload)
    tree_sha = commit_data["tree"]
    committed_files = _flatten_tree(repo, tree_sha, Path(""))

    results = []

    # Walk current working tree
    current_files: dict[str, str] = {}
    for fpath in sorted(repo.rglob("*")):
        if not fpath.is_file() or is_ignored(fpath, repo):
            continue
        rel = str(fpath.relative_to(repo))
        current_files[rel] = fpath.read_text(errors="replace")

    all_paths = set(committed_files.keys()) | set(current_files.keys())

    for rel_path in sorted(all_paths):
        old = committed_files.get(rel_path, "")
        new = current_files.get(rel_path, "")
        if old == new:
            continue
        result = semantic_diff(old, new, rel_path)
        if result.entries:
            results.append(result)

    return results


def _flatten_tree(repo: Path, tree_sha: str, prefix: Path) -> dict[str, str]:
    """Recursively flatten a tree object into {relative_path: file_content}."""
    _, payload = read_object(repo, tree_sha)
    entries = json.loads(payload)
    files: dict[str, str] = {}
    for entry in entries:
        child_path = prefix / entry["name"]
        if entry["mode"] == "blob":
            _, blob_data = read_object(repo, entry["sha"])
            files[str(child_path)] = blob_data.decode(errors="replace")
        elif entry["mode"] == "tree":
            files.update(_flatten_tree(repo, entry["sha"], child_path))
    return files
