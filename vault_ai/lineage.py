import hashlib
from pathlib import Path
import json
from vault_ai import VAULT_DIR

EXTENSIONS = [".bin", ".onnx", ".pth", ".csv", ".jsonl", ".parquet", ".pt", ".h5"]

def track_lineage(repo: Path):
    """
    Scans the repository for large external datasets and AI weights.
    Generates a lineage linked-list stored in .vault/metadata/.lineage
    to guarantee exact reproducible states for external assets.
    """
    metadata_dir = repo / VAULT_DIR / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    lineage_file = metadata_dir / "lineage.json"
    
    lineage_data = []
    
    for ext in EXTENSIONS:
        for file in repo.rglob(f"*{ext}"):
            if ".vault" in file.parts:
                continue
            if not file.is_file():
                continue
            
            # Hash asset
            h = hashlib.sha256()
            with open(file, "rb") as f:
                for chunk in iter(lambda: f.read(81920), b""):
                    h.update(chunk)
                    
            lineage_data.append({
                "path": str(file.relative_to(repo)),
                "sha256": h.hexdigest(),
                "size_bytes": file.stat().st_size
            })
            
    if lineage_data:
        lineage_file.write_text(json.dumps(lineage_data, indent=2))
        return lineage_data
    return []

def print_lineage_report(lineage_data: list):
    """Prints the tracked lineage objects to UI."""
    if not lineage_data:
        print("  (no AI models or datasets detected for lineage linking)")
        return
    print("  🧬  Object Lineage Detected & Linked:")
    for l in lineage_data:
        print(f"       🔗 {l['path']} [SHA: {l['sha256'][:8]}...]")
