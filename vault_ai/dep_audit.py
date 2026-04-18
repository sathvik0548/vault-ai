"""
vault_ai.dep_audit
~~~~~~~~~~~~~~~~~~
Dependency Audit: Scans package.json / requirements.txt against the
OSV.dev open-source vulnerability database (API is free, no auth needed).
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict

from vault_ai.utils import find_repo


OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"


@dataclass
class Vulnerability:
    package: str
    version: str
    vuln_id: str
    severity: str
    summary: str


def _parse_requirements_txt(path: Path) -> Dict[str, str]:
    """Return {package: version} parsed from requirements.txt."""
    packages = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, ver = line.split("==", 1)
            packages[name.strip().lower()] = ver.strip()
        elif ">=" in line:
            name, ver = line.split(">=", 1)
            packages[name.strip().lower()] = ver.strip().split(",")[0]
        else:
            packages[line.lower()] = ""
    return packages


def _parse_package_json(path: Path) -> Dict[str, str]:
    """Return {package: version} parsed from package.json deps."""
    packages = {}
    try:
        data = json.loads(path.read_text())
        for dep_key in ("dependencies", "devDependencies"):
            for name, ver in data.get(dep_key, {}).items():
                # Strip semver prefixes like ^ and ~
                packages[name] = ver.lstrip("^~>=<").split(" ")[0]
    except Exception:
        pass
    return packages


def _build_osv_queries(packages: Dict[str, str], ecosystem: str) -> list:
    queries = []
    for name, version in packages.items():
        q: dict = {"package": {"name": name, "ecosystem": ecosystem}}
        if version:
            q["version"] = version
        queries.append(q)
    return queries


def _query_osv(queries: list) -> list:
    """Send batch query to OSV.dev; returns list of result arrays."""
    if not queries:
        return []

    payload = json.dumps({"queries": queries}).encode()
    req = urllib.request.Request(
        OSV_BATCH_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
            return body.get("results", [])
    except urllib.error.URLError as e:
        print(f"  ⚠  OSV.dev API unreachable: {e.reason}")
        return []
    except Exception as e:
        print(f"  ⚠  OSV.dev query error: {e}")
        return []


def _parse_results(
    packages: Dict[str, str],
    osv_results: list,
    ecosystem: str,
) -> List[Vulnerability]:
    """Merge OSV results back into a list of Vulnerability objects."""
    vulns = []
    pkg_names = list(packages.keys())
    for i, result in enumerate(osv_results):
        if i >= len(pkg_names):
            break
        pkg_name = pkg_names[i]
        pkg_version = packages[pkg_name]
        for vuln in result.get("vulns", []):
            severity = "UNKNOWN"
            # Try to get CVSS severity
            severities = vuln.get("severity", [])
            if severities:
                severity = severities[0].get("score", "UNKNOWN")

            vulns.append(Vulnerability(
                package=pkg_name,
                version=pkg_version,
                vuln_id=vuln.get("id", "?"),
                severity=severity,
                summary=vuln.get("summary", "No description."),
            ))
    return vulns


def audit_dependencies(repo: Path | None = None) -> List[Vulnerability]:
    """
    Scan dependency files for known vulnerabilities using OSV.dev.
    Returns a list of Vulnerability findings.
    """
    if repo is None:
        repo = find_repo()
    if repo is None:
        return []

    all_vulns: List[Vulnerability] = []

    # --- PyPI (requirements.txt) ---
    req_file = repo / "requirements.txt"
    if req_file.exists():
        py_packages = _parse_requirements_txt(req_file)
        if py_packages:
            print(f"  🔎  Auditing {len(py_packages)} Python packages against OSV.dev...")
            queries = _build_osv_queries(py_packages, "PyPI")
            results = _query_osv(queries)
            all_vulns += _parse_results(py_packages, results, "PyPI")

    # --- npm (package.json) ---
    pkg_json = repo / "package.json"
    if pkg_json.exists():
        npm_packages = _parse_package_json(pkg_json)
        if npm_packages:
            print(f"  🔎  Auditing {len(npm_packages)} npm packages against OSV.dev...")
            queries = _build_osv_queries(npm_packages, "npm")
            results = _query_osv(queries)
            all_vulns += _parse_results(npm_packages, results, "npm")

    return all_vulns


def print_audit_report(vulns: List[Vulnerability]) -> bool:
    """Print the vulnerability report. Returns True if safe, False if vulnerabilities found."""
    if not vulns:
        print("  ✅  Dependency Audit: All packages are clean (no known CVEs).")
        return True

    print(f"\n  🚨  Dependency Audit found {len(vulns)} vulnerability(-ies):\n")
    for v in vulns:
        print(f"  ⚠  {v.package}=={v.version}")
        print(f"       ID: {v.vuln_id}  |  Severity: {v.severity}")
        print(f"       {v.summary}")
    print()
    print("  Fix: Upgrade the affected packages and run `vault scan` again.")
    return False
