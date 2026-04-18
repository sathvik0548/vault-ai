"""
vault_ai.search
~~~~~~~~~~~~~~~~
Semantic Search & Expert Discovery using ChromaDB vector index.
Indexes every commit and author metadata.
Allows natural language queries like "Who worked on the database?"

Graceful degradation: if ChromaDB is not installed, returns a helpful error.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, get_head, read_object


_CHROMA_DIR = "search_index"

# ---------------------------------------------------------------------------
# ChromaDB wrapper (graceful degradation)
# ---------------------------------------------------------------------------

def _has_chromadb() -> bool:
    try:
        import chromadb
        return True
    except ImportError:
        return False


def _get_collection(repo: Path):
    """Get or create a ChromaDB collection for commit search."""
    import chromadb
    db_path = str(repo / VAULT_DIR / _CHROMA_DIR)
    client = chromadb.PersistentClient(path=db_path)
    return client.get_or_create_collection(
        name="commits",
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def index_commits(repo: Path | None = None) -> int:
    """
    Walk the entire commit chain and index each commit into ChromaDB.
    Returns the number of commits indexed.
    """
    if not _has_chromadb():
        print("  ⚠  ChromaDB not installed. Run: pip install chromadb")
        return 0

    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return 0

    collection = _get_collection(repo)
    sha = get_head(repo)
    count = 0

    while sha:
        _, payload = read_object(repo, sha)
        data = json.loads(payload)

        # Check if already indexed
        existing = collection.get(ids=[sha[:16]])
        if existing and existing["ids"]:
            sha = data.get("parent")
            continue

        doc = (
            f"Commit: {data['message']}\n"
            f"Author: {data.get('author', 'unknown')}\n"
            f"Tree: {data['tree'][:10]}\n"
            f"Date: {time.strftime('%Y-%m-%d', time.localtime(data['timestamp']))}"
        )

        collection.add(
            ids=[sha[:16]],
            documents=[doc],
            metadatas=[{
                "sha": sha,
                "author": data.get("author", "unknown"),
                "message": data["message"],
                "timestamp": str(data["timestamp"]),
            }],
        )
        count += 1
        sha = data.get("parent")

    return count


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_commits(
    query: str,
    repo: Path | None = None,
    n_results: int = 5,
) -> List[dict]:
    """
    Search commits using natural language.
    Returns a list of matching commit metadata dicts.
    """
    if not _has_chromadb():
        print("  ⚠  ChromaDB not installed. Run: pip install chromadb")
        return []

    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return []

    collection = _get_collection(repo)
    if collection.count() == 0:
        # Auto-index if empty
        indexed = index_commits(repo)
        if indexed == 0:
            print("  ⚠  No commits to search.")
            return []

    results = collection.query(
        query_texts=[query],
        n_results=min(n_results, collection.count()),
    )

    matches = []
    if results and results["metadatas"]:
        for meta in results["metadatas"][0]:
            matches.append(meta)
    return matches


def print_search_results(query: str, results: List[dict]) -> None:
    """Pretty-print search results for the CLI."""
    if not results:
        print(f'  ⚠  No results for "{query}".')
        return

    print(f'\n  🔍  Search results for "{query}":\n')
    for i, r in enumerate(results, 1):
        sha = r.get("sha", "?")[:10]
        author = r.get("author", "?")
        message = r.get("message", "?")
        ts = r.get("timestamp", "")
        if ts:
            try:
                ts = time.strftime("%Y-%m-%d", time.localtime(float(ts)))
            except (ValueError, OSError):
                ts = "?"
        print(f"  {i}. [{sha}] {ts} by {author} — {message}")
    print()


# ---------------------------------------------------------------------------
# Fallback search (no ChromaDB — simple substring match)
# ---------------------------------------------------------------------------

def simple_search(query: str, repo: Path | None = None, limit: int = 10) -> List[dict]:
    """Fallback: substring search through commit messages (no vector DB needed)."""
    if repo is None:
        repo = find_repo()
    if repo is None:
        return []

    sha = get_head(repo)
    results = []
    query_lower = query.lower()

    while sha and len(results) < limit:
        _, payload = read_object(repo, sha)
        data = json.loads(payload)
        msg = data.get("message", "").lower()
        author = data.get("author", "").lower()

        if query_lower in msg or query_lower in author:
            results.append({
                "sha": sha,
                "author": data.get("author", "unknown"),
                "message": data["message"],
                "timestamp": str(data["timestamp"]),
            })
        sha = data.get("parent")

    return results
