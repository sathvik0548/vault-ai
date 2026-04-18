import json
from pathlib import Path

def get_policy(repo: Path) -> dict:
    """Read the .vault_policy file if it exists."""
    policy_file = repo / ".vault_policy"
    if policy_file.exists():
        try:
            return json.loads(policy_file.read_text())
        except json.JSONDecodeError:
            print("  ⚠  Failed to parse .vault_policy JSON. Bypassing policy.")
    return {}

def validate_policy(repo: Path, pre_commit_files: list[Path] = None) -> bool:
    """
    Apply rules strictly.
    Returns True if passed or no rules. Returns False if breached.
    """
    policy = get_policy(repo)
    if not policy:
        return True
    
    if pre_commit_files is None:
        # scan all files
        # simplified strategy: we scan everything in the tree that is not ignored
        pass
    
    # We will enforce logic via text parsing for simplistic policies.
    # Example constraints: "no_console_logs"
    breaches = []
    
    no_logs = policy.get("no_console_logs", False)
    req_tests = policy.get("require_test_files", False)
    
    if no_logs:
        for file in repo.rglob("*.py"):
            if ".vault" in file.parts or ".git" in file.parts or file.parts[-1] == "cli.py":
                continue
            if not file.is_file(): continue
            content = file.read_text(errors="ignore")
            if "print(" in content or "console.log(" in content:
                breaches.append(f"{file.name}: contains print/console logger.")
                
    if req_tests:
        has_test = any(f.name.startswith("test_") for f in repo.rglob("*.py") if ".vault" not in f.parts)
        if not has_test:
            breaches.append("Policy requires at least one unit test script, but none found.")
            
    if breaches:
        print("  \033[91m🚨  POLICY-AS-CODE BREACH DETECTED:\033[0m")
        for b in breaches:
            print(f"      - {b}")
        return False
        
    return True
