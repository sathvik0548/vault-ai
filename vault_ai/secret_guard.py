"""
vault_ai.secret_guard
~~~~~~~~~~~~~~~~~~~~~
Pre-commit secret detection engine.

Scans the working tree for API keys, tokens, passwords, private keys,
and high-entropy strings before any commit is finalised.

Usage (programmatic):
    from vault_ai.secret_guard import scan_working_tree, SecretFinding
    findings = scan_working_tree(repo)

Usage (CLI via cli.py):
    vault scan
    vault save          # automatically scans; blocks if secrets found
    vault save --force  # bypass the guard
"""
from __future__ import annotations

import math
import re
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo, is_ignored

# ---------------------------------------------------------------------------
# Regex ruleset — ordered from most-specific to least-specific
# Each rule is (display_name, compiled_pattern)
# ---------------------------------------------------------------------------
_RULES: list[tuple[str, re.Pattern]] = [
    # Cloud providers
    ("AWS Access Key ID",      re.compile(r"AKIA[0-9A-Z]{16}", re.I)),
    ("AWS Secret Access Key",  re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+]{40}['\"]?")),
    ("GCP API Key",            re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("Azure Connection String",re.compile(r"DefaultEndpointsProtocol=https;AccountName=[^;]+;AccountKey=[A-Za-z0-9+/=]+")),

    # Version-control tokens
    ("GitHub Token",           re.compile(r"gh[pousr]_[0-9A-Za-z]{36,}")),
    ("GitLab Token",           re.compile(r"glpat-[0-9A-Za-z\-_]{20,}")),

    # Generic API / Bearer tokens
    ("Generic API Key",        re.compile(r"(?i)api[_\-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{20,}['\"]?")),
    ("Bearer Token",           re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_]{20,}")),

    # Database URLs (contain credentials)
    ("DB Connection String",   re.compile(r"(?i)(postgres|mysql|mongodb|redis)://[^:]+:[^@]+@")),

    # Private keys
    ("RSA Private Key",        re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),

    # Passwords / secrets in assignment form
    ("Password Assignment",    re.compile(r"(?i)(password|passwd|secret|token)\s*[=:]\s*['\"][^'\"]{8,}['\"]")),

    # Stripe keys
    ("Stripe Secret Key",      re.compile(r"sk_live_[0-9A-Za-z]{24,}")),
    ("Stripe Test Key",        re.compile(r"sk_test_[0-9A-Za-z]{24,}")),

    # Slack tokens
    ("Slack Token",            re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),

    # Sendgrid
    ("SendGrid API Key",       re.compile(r"SG\.[A-Za-z0-9\-_]{22}\.[A-Za-z0-9\-_]{43}")),

    # JWT (bare secret, not encoded)
    ("JWT Secret",             re.compile(r"(?i)jwt[_\-]?secret\s*[=:]\s*['\"][^'\"]{16,}['\"]")),
]

# Binary file extensions to skip
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".xz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".pyc", ".pyo",
    ".pkl", ".model", ".pt", ".pth", ".bin", ".db", ".sqlite",
}

# Safe env-var names whose values are never secrets
_SAFE_ENV_VARS = {"PATH", "HOME", "USER", "SHELL", "TERM", "LANG", "LC_ALL"}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SecretFinding:
    file_path: str          # relative to repo root
    line_num: int
    rule_name: str
    snippet: str            # redacted snippet for display
    is_high_entropy: bool = False

    def __str__(self) -> str:
        tag = "🔑" if not self.is_high_entropy else "🎲"
        return (
            f"  {tag}  {self.file_path}:{self.line_num} "
            f"[{self.rule_name}]  …{self.snippet}…"
        )


# ---------------------------------------------------------------------------
# Entropy scorer
# ---------------------------------------------------------------------------

def _shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of a string (bits per character)."""
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


# Heuristic: any 20+ char token-like word with entropy ≥ 4.5 bits is suspicious
_ENTROPY_THRESHOLD = 4.5
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9/+_\-]{20,}")


def _high_entropy_tokens(line: str) -> list[str]:
    """Return substrings that look like random high-entropy tokens."""
    return [
        m.group()
        for m in _TOKEN_PATTERN.finditer(line)
        if _shannon_entropy(m.group()) >= _ENTROPY_THRESHOLD
    ]


# ---------------------------------------------------------------------------
# Allowlist helpers
# ---------------------------------------------------------------------------

_INLINE_ALLOW_PATTERN = re.compile(r"vault-ok", re.I)
_ALLOW_FILE_NAME = ".vault-secrets-allow"


def _load_allowlist(repo: Path) -> set[str]:
    """Load path patterns (one per line) from .vault-secrets-allow."""
    allow_path = repo / _ALLOW_FILE_NAME
    if not allow_path.exists():
        return set()
    lines = allow_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return {l.strip() for l in lines if l.strip() and not l.startswith("#")}


def _is_path_allowed(rel_path: str, allowlist: set[str]) -> bool:
    for pattern in allowlist:
        if pattern in rel_path or rel_path.endswith(pattern):
            return True
    return False


# ---------------------------------------------------------------------------
# Core scanners
# ---------------------------------------------------------------------------

def scan_text(
    text: str,
    file_path: str,
    *,
    check_entropy: bool = True,
) -> List[SecretFinding]:
    """Scan a block of text and return all findings."""
    findings: List[SecretFinding] = []

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        # Skip inline-allowed lines
        if _INLINE_ALLOW_PATTERN.search(raw_line):
            continue

        # Rule-based scan
        for rule_name, pattern in _RULES:
            if pattern.search(raw_line):
                snippet = raw_line.strip()[:60]
                findings.append(SecretFinding(
                    file_path=file_path,
                    line_num=lineno,
                    rule_name=rule_name,
                    snippet=snippet,
                ))

        # Entropy scan (skip lines already caught by rules)
        if check_entropy:
            caught_line = any(p.search(raw_line) for _, p in _RULES)
            if not caught_line:
                for token in _high_entropy_tokens(raw_line):
                    snippet = raw_line.strip()[:60]
                    findings.append(SecretFinding(
                        file_path=file_path,
                        line_num=lineno,
                        rule_name="High-Entropy Token",
                        snippet=snippet,
                        is_high_entropy=True,
                    ))

    return findings


def scan_file(path: Path, rel_path: str) -> List[SecretFinding]:
    """Scan a single file. Returns [] for binary files."""
    if path.suffix.lower() in _BINARY_EXTS:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    return scan_text(text, rel_path)


def scan_working_tree(
    repo: Optional[Path] = None,
) -> List[SecretFinding]:
    """
    Walk the working tree and scan every non-ignored, non-binary file.
    Returns a (possibly empty) list of SecretFinding objects.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return []

    allowlist = _load_allowlist(repo)
    findings: List[SecretFinding] = []

    for dirpath, dirnames, filenames in os.walk(repo):
        dir_path = Path(dirpath)

        # Prune ignored dirs in-place
        dirnames[:] = [
            d for d in dirnames
            if not is_ignored(dir_path / d, repo)
        ]

        for fname in filenames:
            file_path = dir_path / fname
            if is_ignored(file_path, repo):
                continue
            rel_path = str(file_path.relative_to(repo))
            if _is_path_allowed(rel_path, allowlist):
                continue
            findings.extend(scan_file(file_path, rel_path))

    return findings


# ---------------------------------------------------------------------------
# Pretty-printer
# ---------------------------------------------------------------------------

def print_findings(findings: List[SecretFinding], repo: Optional[Path] = None) -> None:
    """Print a user-friendly report of secret findings."""
    if not findings:
        print("  ✓  Secret Guard: No secrets detected.")
        return

    print(f"\n  🛡  Secret Guard found {len(findings)} potential secret(s):\n")
    for f in findings:
        print(str(f))

    print()
    print("  To suppress a specific line:  add  # vault-ok  at the end of the line")
    print(f"  To allow a whole file:  add its path to  {_ALLOW_FILE_NAME}")
    print("  To commit anyway (⚠ risky):  vault save --force")
    print()


def ai_verify_secrets(findings: List[SecretFinding], repo: Path) -> List[SecretFinding]:
    """
    Send suspicious lines to AI for secondary verification.
    Returns only the findings the AI confirms as real secrets.
    """
    if not findings:
        return []

    try:
        from vault_ai.llm import ask
    except ImportError:
        return findings  # can't verify, return all

    confirmed = []
    for f in findings:
        prompt = (
            f"Is this line likely to contain a real secret/credential or is it a false positive?\n"
            f"Rule matched: {f.rule_name}\n"
            f"Line: {f.snippet}\n\n"
            f"Reply with ONLY 'REAL' or 'FALSE_POSITIVE'."
        )
        result = ask(prompt)
        if result and "FALSE_POSITIVE" in result.upper():
            continue
        confirmed.append(f)

    return confirmed if confirmed else findings


def auto_move_to_private(findings: List[SecretFinding], repo: Path) -> int:
    """
    Auto-move detected secret lines to .vault_private file.
    Returns count of secrets moved.
    """
    private_path = repo / ".vault_private"
    moved = 0
    entries = []

    for f in findings:
        entries.append(f"# {f.rule_name} found in {f.file_path}:{f.line_num}")
        entries.append(f"# {f.snippet}")
        entries.append("")
        moved += 1

    if entries:
        mode = "a" if private_path.exists() else "w"
        with open(private_path, mode) as fp:
            fp.write("\n".join(entries) + "\n")

    return moved
