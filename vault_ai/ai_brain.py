"""
vault_ai.ai_brain
AI-powered commit messages & change summaries.
Uses the unified llm.ask() interface (Ollama-first, Gemini fallback).
"""
from __future__ import annotations

import json
from vault_ai.llm import ask


# ---------------------------------------------------------------------------
# Commit message generation
# ---------------------------------------------------------------------------

_COMMIT_PROMPT = """\
You are a senior software engineer writing a concise, professional Git-style \
commit message. Given the following diff, write:
1. A short subject line (≤72 chars, imperative mood).
2. A blank line.
3. An optional body (≤3 bullet points) only if the change is non-trivial.

Reply with ONLY the commit message, nothing else.

--- DIFF ---
{diff}
"""


def generate_commit_message(diff_text: str) -> str | None:
    """
    Send a cleaned diff to the AI and return a professional commit message.
    Returns None if AI is unavailable — caller should fall back to manual input.
    """
    # Truncate huge diffs to keep token cost low
    max_chars = 12_000
    cleaned = diff_text[:max_chars]
    if len(diff_text) > max_chars:
        cleaned += "\n... (truncated)"

    prompt = _COMMIT_PROMPT.format(diff=cleaned)
    return ask(prompt)


# ---------------------------------------------------------------------------
# Human-readable change summary
# ---------------------------------------------------------------------------

def summarize_changes(diff_entries: list) -> str | None:
    """
    Given a list of DiffEntry-like objects, produce a natural-language summary.
    Returns None if AI is unavailable.
    """
    description = json.dumps(
        [{"category": e.category, "kind": e.kind,
          "name": e.name, "detail": e.detail} for e in diff_entries],
        indent=2,
    )

    prompt = (
        "Summarize the following code changes in 2-3 sentences for a developer. "
        "Be specific about what was changed and why it might matter.\n\n"
        f"{description}"
    )

    return ask(prompt)
