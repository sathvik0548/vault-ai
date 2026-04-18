import json
import time
from pathlib import Path
from vault_ai import VAULT_DIR

def log_audit(repo: Path, action: str, system_prompt: str, user_prompt: str, provider: str, confidence: str = "N/A"):
    """
    Appends an AI transaction to the hidden audit trail log for team accountability.
    """
    audit_file = repo / VAULT_DIR / ".audit_log"
    
    entry = {
        "timestamp": time.time(),
        "action": action,
        "provider_model": provider,
        "confidence": confidence,
        "system_prompt": system_prompt[:500] + ("..." if len(system_prompt)>500 else ""),
        "user_prompt": user_prompt[:500] + ("..." if len(user_prompt)>500 else "")
    }
    
    # Append
    with open(audit_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
