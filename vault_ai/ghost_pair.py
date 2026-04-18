"""
vault_ai.ghost_pair
~~~~~~~~~~~~~~~~~~~~
Ghost Pairing: detect duplicate/similar functions before committing.
Compares new function structures against the local object store.
Warns if >80% similarity is found.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, is_ignored


# ---------------------------------------------------------------------------
# AST fingerprinting
# ---------------------------------------------------------------------------

@dataclass
class FuncFingerprint:
    name: str
    file_path: str
    arg_count: int
    node_count: int           # total AST nodes in body
    control_flow: tuple       # ordered tuple of (If, For, While, Return, ...)
    call_names: tuple         # sorted function call names

    @property
    def structure_key(self) -> tuple:
        """Structural identity for comparison (ignores name)."""
        return (self.arg_count, self.node_count, self.control_flow, self.call_names)


def _count_nodes(node: ast.AST) -> int:
    """Count all AST nodes under a given root."""
    return sum(1 for _ in ast.walk(node))


def _extract_control_flow(node: ast.AST) -> tuple:
    """Extract ordered control-flow statement types."""
    flow = []
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.Return,
                              ast.Try, ast.With, ast.Raise, ast.Assert)):
            flow.append(type(child).__name__)
    return tuple(flow)


def _extract_call_names(node: ast.AST) -> tuple:
    """Extract sorted set of function call names."""
    calls = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                calls.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                calls.add(child.func.attr)
    return tuple(sorted(calls))


def fingerprint_functions(source: str, file_path: str) -> List[FuncFingerprint]:
    """Parse Python source and return fingerprints for all top-level functions."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    fps = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fps.append(FuncFingerprint(
                name=node.name,
                file_path=file_path,
                arg_count=len(node.args.args),
                node_count=_count_nodes(node),
                control_flow=_extract_control_flow(node),
                call_names=_extract_call_names(node),
            ))
    return fps


# ---------------------------------------------------------------------------
# Similarity scoring
# ---------------------------------------------------------------------------

def similarity(a: FuncFingerprint, b: FuncFingerprint) -> float:
    """
    Compute structural similarity between two function fingerprints.
    Returns a float from 0.0 to 1.0.
    """
    score = 0.0
    total = 4.0

    # Arg count match
    if a.arg_count == b.arg_count:
        score += 1.0

    # Node count similarity (within 20% tolerance)
    if a.node_count > 0 and b.node_count > 0:
        ratio = min(a.node_count, b.node_count) / max(a.node_count, b.node_count)
        score += ratio

    # Control flow match
    if a.control_flow == b.control_flow:
        score += 1.0
    elif a.control_flow and b.control_flow:
        common = set(a.control_flow) & set(b.control_flow)
        union = set(a.control_flow) | set(b.control_flow)
        if union:
            score += len(common) / len(union)

    # Call names overlap (Jaccard similarity)
    if a.call_names or b.call_names:
        a_set = set(a.call_names)
        b_set = set(b.call_names)
        union = a_set | b_set
        if union:
            score += len(a_set & b_set) / len(union)
    else:
        score += 1.0  # both empty → same

    return score / total


# ---------------------------------------------------------------------------
# Working-tree scan
# ---------------------------------------------------------------------------

def _collect_existing_fingerprints(repo: Path) -> List[FuncFingerprint]:
    """Scan all Python files in the repo for function fingerprints."""
    fps: List[FuncFingerprint] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dp = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not is_ignored(dp / d, repo)]
        for fname in filenames:
            fp = dp / fname
            if fp.suffix != ".py" or is_ignored(fp, repo):
                continue
            try:
                source = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = str(fp.relative_to(repo))
            fps.extend(fingerprint_functions(source, rel))
    return fps


@dataclass
class GhostWarning:
    new_func: str
    new_file: str
    existing_func: str
    existing_file: str
    similarity_pct: float


def check_ghost_pairs(
    repo: Path | None = None,
    threshold: float = 0.80,
) -> List[GhostWarning]:
    """
    Check for ghost pairs: new functions that are >80% similar to existing ones.
    Should be called before save to warn the user.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return []

    all_fps = _collect_existing_fingerprints(repo)
    if len(all_fps) < 2:
        return []

    warnings: List[GhostWarning] = []
    seen_pairs: set = set()

    for i, a in enumerate(all_fps):
        for j, b in enumerate(all_fps):
            if i >= j:
                continue
            # Skip same function in same file
            if a.file_path == b.file_path and a.name == b.name:
                continue
            pair_key = (min(a.name, b.name), max(a.name, b.name),
                        min(a.file_path, b.file_path), max(a.file_path, b.file_path))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            sim = similarity(a, b)
            if sim >= threshold:
                warnings.append(GhostWarning(
                    new_func=a.name,
                    new_file=a.file_path,
                    existing_func=b.name,
                    existing_file=b.file_path,
                    similarity_pct=sim * 100,
                ))

    return warnings


def print_ghost_warnings(warnings: List[GhostWarning]) -> None:
    """Print ghost pairing warnings."""
    if not warnings:
        return
    print(f"\n  👻  Ghost Pairing: found {len(warnings)} similar function pair(s):\n")
    for w in warnings:
        print(f"    {w.new_file}:{w.new_func}  ↔  {w.existing_file}:{w.existing_func}"
              f"  ({w.similarity_pct:.0f}% similar)")
    print("\n  Consider refactoring duplicates into shared utilities.\n")
