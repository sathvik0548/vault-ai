"""
vault_ai.story
~~~~~~~~~~~~~~
Story Mode: aggregate recent commits and generate a structured Markdown report.
Usage: vault story [--days 7]
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from vault_ai import VAULT_DIR
from vault_ai.llm import ask
from vault_ai.utils import find_repo, get_head, read_object


def _collect_commits(repo: Path, days: int = 7) -> list[dict]:
    """Walk the commit chain and collect commits from the last N days."""
    sha = get_head(repo)
    if sha is None:
        return []

    cutoff = time.time() - (days * 86400)
    commits = []

    while sha:
        _, payload = read_object(repo, sha)
        data = json.loads(payload)
        if data["timestamp"] < cutoff:
            break
        commits.append({
            "sha": sha[:10],
            "message": data["message"],
            "author": data.get("author", "unknown"),
            "timestamp": time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(data["timestamp"])
            ),
        })
        sha = data.get("parent")

    return commits


def generate_story(repo: Path | None = None, days: int = 7) -> str | None:
    """
    Generate a structured Markdown report from recent commits.
    Uses Ollama/Gemini to create a narrative summary.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return None

    commits = _collect_commits(repo, days)
    if not commits:
        print(f"  ⚠  No commits found in the last {days} day(s).")
        return None

    # Build a summary of all commits for the AI
    commit_log = "\n".join(
        f"- [{c['sha']}] {c['timestamp']} by {c['author']}: {c['message']}"
        for c in commits
    )

    prompt = f"""\
You are a technical writer. Given the following commit log from the last {days} days, \
write a structured Markdown report titled "## Development Story".

Include:
1. **Summary**: A 2-3 sentence high-level overview
2. **Key Changes**: Bullet points of the most important work
3. **Contributors**: Who did what (based on author field)
4. **Timeline**: A brief chronological narrative

Commit log:
{commit_log}

Write ONLY the Markdown report, no other text.
"""

    report = ask(prompt)
    if report:
        return report

    # Fallback: generate a basic report without AI
    lines = [
        f"## Development Story (Last {days} Days)\n",
        f"**{len(commits)} commits** from {commits[-1]['timestamp']} to {commits[0]['timestamp']}\n",
        "### Commit Log\n",
    ]
    for c in commits:
        lines.append(f"- `{c['sha']}` {c['timestamp']} — {c['message']}")
    return "\n".join(lines)


def print_story(report: str) -> None:
    """Print the story report to stdout."""
    print()
    for line in report.splitlines():
        print(f"  {line}")
    print()


def export_story_pdf(report: str, output_path: str | None = None) -> str | None:
    """
    Export the story report as a PDF file.
    Uses a zero-dependency minimal PDF generator (no external libraries needed).
    Returns the output file path.
    """
    if output_path is None:
        output_path = f"vault_story_{int(time.time())}.pdf"

    # Strip markdown formatting for clean PDF text
    clean_lines = []
    for line in report.splitlines():
        # Convert markdown headers to uppercase text
        if line.startswith("## "):
            clean_lines.append(line[3:].upper())
            clean_lines.append("=" * len(line[3:]))
        elif line.startswith("### "):
            clean_lines.append(line[4:])
            clean_lines.append("-" * len(line[4:]))
        elif line.startswith("**") and line.endswith("**"):
            clean_lines.append(line.strip("*"))
        else:
            clean_lines.append(line.replace("**", "").replace("`", ""))

    text = "\n".join(clean_lines)

    # Minimal PDF 1.4 generator (zero dependencies)
    # PDF is a structured text format; we manually build the byte stream
    pdf_lines = []
    pdf_lines.append(b"%PDF-1.4")

    # Object 1: Catalog
    pdf_lines.append(b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj")

    # Object 2: Pages
    pdf_lines.append(b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj")

    # Object 4: Font
    pdf_lines.append(b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Courier>>endobj")

    # Build page content stream
    content_lines = []
    content_lines.append("BT")
    content_lines.append("/F1 10 Tf")
    content_lines.append("50 750 Td")
    content_lines.append("12 TL")

    # Title
    content_lines.append("(Vault-AI Development Story) Tj T*")
    content_lines.append("(================================) Tj T*")
    content_lines.append("() Tj T*")

    # Sanitize and add text lines
    for line in text.splitlines()[:55]:  # limit to one page
        safe = (line
                .replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
                .replace("\t", "    "))
        if len(safe) > 80:
            safe = safe[:77] + "..."
        content_lines.append(f"({safe}) Tj T*")

    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    # Object 5: Content stream
    pdf_lines.append(f"5 0 obj<</Length {len(stream)}>>stream\n".encode() + stream + b"\nendstream endobj")

    # Object 3: Page
    pdf_lines.append(b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
                     b"/Contents 5 0 R/Resources<</Font<</F1 4 0 R>>>>>>endobj")

    # Build full PDF
    body = b"\n".join(pdf_lines)

    # Cross-reference table (simplified)
    xref_offset = len(body) + 1
    xref = b"\nxref\n0 6\n"
    xref += b"0000000000 65535 f \n" * 6

    trailer = f"\ntrailer<</Size 6/Root 1 0 R>>\nstartxref\n{xref_offset}\n%%EOF".encode()

    with open(output_path, "wb") as f:
        f.write(body + xref + trailer)

    return output_path

