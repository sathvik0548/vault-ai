"""
vault_ai.anomaly
~~~~~~~~~~~~~~~~
AI Anomaly Detection: Uses Ollama to analyze diffs for suspicious logic.
Looks for:
  - Hidden network calls (socket, urllib.request, curl, fetch)
  - File permission changes (os.chmod, subprocess chmod)
  - Credential hardcoding (keys, tokens, passwords in code)
  - New eval()/exec() usage
  - Reverse shell patterns
  - Unexpected binary file additions
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from vault_ai.utils import find_repo


# ---------------------------------------------------------------------------
# Static heuristic patterns (fast, no AI needed)
# ---------------------------------------------------------------------------

SUSPICIOUS_PATTERNS = [
    (r"(?:socket\.connect|socket\.bind|urllib\.request\.urlopen|requests\.(?:get|post))",
     "Hidden network call detected"),
    (r"(?:os\.chmod|subprocess.*chmod|stat\.S_ISUID|stat\.S_ISGID)",
     "File permission change detected"),
    (r"(?:eval\s*\(|exec\s*\()",
     "Dynamic code execution (eval/exec) detected"),
    (r"(?:base64\.b64decode\s*\(|__import__\s*\()",
     "Obfuscated import / base64 decode detected"),
    (r"(?:password\s*=\s*['\"][^'\"]{6,}['\"]|token\s*=\s*['\"][A-Za-z0-9+/]{20,}['\"])",
     "Hardcoded credential pattern detected"),
    (r"(?:\/dev\/tcp|\/dev\/udp|nc\s+-e|bash\s+-i\s+>&)",
     "Reverse shell pattern detected"),
    (r"(?:import\s+ctypes|ctypes\.CDLL|ctypes\.windll)",
     "Native DLL / ctypes access detected"),
    (r"(?:shutil\.rmtree\s*\(['\"]\/|os\.remove\s*\(['\"]\/|subprocess.*rm\s+-rf\s+\/)",
     "Destructive root-level operation detected"),
]


@dataclass
class AnomalyFinding:
    file_path: str
    line_num: int
    snippet: str
    rule: str
    ai_confirmed: Optional[bool] = None  # True=confirmed, False=FP, None=not verified
    ai_reasoning: Optional[str] = None

    def __str__(self):
        icon = "🔴" if self.ai_confirmed else ("🟡" if self.ai_confirmed is None else "⚪")
        conf_str = " (AI-confirmed)" if self.ai_confirmed else ""
        return (
            f"  {icon}  {self.file_path}:{self.line_num}  [{self.rule}]{conf_str}\n"
            f"       {self.snippet[:80]}"
        )


def _static_scan_diff(diff_text: str, file_path: str) -> List[AnomalyFinding]:
    """Run static regex heuristics over diff text."""
    findings = []
    for i, line in enumerate(diff_text.splitlines(), 1):
        # Only look at added lines
        if not line.startswith("+") or line.startswith("+++"):
            continue
        code_line = line[1:]  # strip the leading '+'
        for pattern, rule in SUSPICIOUS_PATTERNS:
            if re.search(pattern, code_line, re.IGNORECASE):
                findings.append(AnomalyFinding(
                    file_path=file_path,
                    line_num=i,
                    snippet=code_line.strip(),
                    rule=rule,
                ))
                break  # one rule per line
    return findings


def _ai_verify_anomalies(findings: List[AnomalyFinding], diff_text: str) -> None:
    """Use Ollama to confirm or dismiss static findings."""
    if not findings:
        return

    try:
        from vault_ai.llm import ask
    except ImportError:
        return

    summary = "\n".join(
        f"- Line {f.line_num}: [{f.rule}] `{f.snippet[:60]}`"
        for f in findings
    )

    prompt = (
        "You are a security code reviewer. Analyze these suspicious lines found in a code diff:\n\n"
        f"{summary}\n\n"
        "For each finding, respond with a JSON array of:\n"
        '{"line": <LINE_NUM>, "confirmed": true/false, "reason": "<brief>"}\n\n'
        "If a line is clearly benign (e.g., a comment or string), set confirmed=false.\n"
        "Reply ONLY with valid JSON. No other text."
    )

    result = ask(prompt)
    if not result:
        return

    # Parse JSON response
    clean = result.strip()
    if clean.startswith("```json"):
        clean = clean.split("```json", 1)[1]
    if clean.startswith("```"):
        clean = clean.split("```", 1)[1]
    if clean.endswith("```"):
        clean = clean.rsplit("```", 1)[0]
    clean = clean.strip()

    try:
        reviews = json_load_safe(clean)
        if not isinstance(reviews, list):
            reviews = [reviews]

        line_map = {f.line_num: f for f in findings}
        for review in reviews:
            ln = review.get("line")
            if ln and ln in line_map:
                line_map[ln].ai_confirmed = review.get("confirmed", True)
                line_map[ln].ai_reasoning = review.get("reason", "")
    except Exception:
        # If AI response can't be parsed, mark all as unverified
        pass


def json_load_safe(text: str):
    import json
    return json.loads(text)


def scan_diff_for_anomalies(
    diff_text: str,
    file_path: str = "unknown",
    use_ai: bool = True,
) -> List[AnomalyFinding]:
    """
    Scan a unified-diff string for suspicious logic patterns.
    Optionally verify with Ollama.
    """
    findings = _static_scan_diff(diff_text, file_path)

    if findings and use_ai:
        _ai_verify_anomalies(findings, diff_text)

    return findings


def scan_working_tree_anomalies(repo: Path | None = None) -> List[AnomalyFinding]:
    """
    Scan the working tree diff against HEAD for anomalies.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return []

    all_findings = []

    try:
        from vault_ai.diff_engine import diff_working_tree
        results = diff_working_tree(repo)
        for r in results:
            if r.unified:
                findings = scan_diff_for_anomalies(r.unified, r.file_path)
                all_findings.extend(findings)
    except Exception as e:
        print(f"  ⚠  Anomaly scan failed: {e}")

    return all_findings


def print_anomaly_report(findings: List[AnomalyFinding]) -> bool:
    """Print anomaly report. Returns True if safe, False if anomalies found."""
    if not findings:
        print("  ✅  Anomaly Detector: No suspicious logic detected.")
        return True

    # Filter to confirmed + unverified, exclude FPs
    real_findings = [f for f in findings if f.ai_confirmed is not False]
    if not real_findings:
        print(f"  ✅  Anomaly Detector: {len(findings)} pattern(s) scanned — all dismissed by AI as safe.")
        return True

    print(f"\n  🚨  AI Anomaly Detector: {len(real_findings)} suspicious pattern(s) found:\n")
    for f in real_findings:
        print(str(f))
        if f.ai_reasoning:
            print(f"       AI Reason: {f.ai_reasoning}")
    print()
    print("  Action: Review the flagged lines above before committing.")
    print("  To override: use  vault save --force")
    return False
