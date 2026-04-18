# Vault-AI

**An AI-native, human-centric Version Control System** — built to replace Git's complexity with clarity, safety, and semantic understanding.

## Installation (Universal One-Line)

### macOS / Linux
```bash
brew tap sathvik0548/tap && brew install vault-ai
```

### Windows (PowerShell)
```powershell
iwr -useb https://github.com/sathvik0548/vault-ai/releases/latest/download/vault-ai-windows-x64.exe -OutFile vault.exe
```

## Quickstart


```bash
# Initialize a repository
python cli.py init

# Make changes, then commit directly (no staging!)
python cli.py save -m "initial project setup"

# Or let AI write the commit message for you
export GEMINI_API_KEY="your-key-here"
python cli.py save

# View semantic diff (AST-aware for Python files)
python cli.py diff

# View commit history
python cli.py history

# Scan for leaked secrets before committing
python cli.py scan

# View the environment snapshot of the last commit
python cli.py env show
```

## Commands

| Command | Description |
|---|---|
| `vault init` | Initialize a new `.vault/` repository |
| `vault save [-m "msg"]` | Direct commit — no staging area. AI writes the message if `-m` is omitted |
| `vault save --force` | Commit even if Secret Guard detects potential secrets |
| `vault diff [-v]` | Semantic diff vs last commit. `-v` for verbose unified output |
| `vault history [-n 20]` | Show commit log |
| `vault scan` | Scan working tree for API keys, tokens, passwords (no commit) |
| `vault env show [sha]` | Show environment snapshot for a commit |
| `vault env diff [sha]` | Compare current environment vs a stored snapshot |
| `vault undo last` | Revert last commit *(scaffold)* |
| `vault create branch <name>` | Create a new branch |
| `vault snapshot [-l "label"]` | Take a time-machine snapshot |
| `vault sync [--url URL]` | Push objects to remote server |

## Internal Data Model

```
.vault/
├── HEAD                  # Points to current branch (e.g. ref: refs/heads/main)
├── config.json           # Repository config & metadata
├── objects/              # Content-Addressable Storage pool
│   └── [00-ff]/          # 2-char SHA-256 prefix buckets
│       └── <rest>        # Zlib-compressed object data
├── refs/
│   └── heads/            # Branch pointers (each file = branch tip SHA)
├── snapshots/            # Time-machine lightweight snapshots
│   └── <timestamp>.json  # {tree, timestamp, label}
└── env_snaps/            # Environmental Snapshots (sidecar per commit)
    └── <sha>.env.json    # {python_version, packages, os, env_vars}
```

### Object Types

| Type | Purpose | Created By |
|---|---|---|
| **blob** | Raw file content | `vault save` |
| **tree** | Directory listing → maps names to blob/tree SHAs | `vault save` |
| **commit** | Snapshot metadata: tree SHA, parent, author, timestamp, message | `vault save` |

### 🛡 Secret Guard

Pre-commit security scanner that blocks accidental commits of secrets.

**Pattern Detection:**
- AWS keys (`AKIA...`), GCP keys (`AIza...`), Azure connection strings
- GitHub tokens (`ghp_/ghs_/gho_...`), GitLab tokens (`glpat-...`)
- Stripe keys, Slack tokens, SendGrid keys
- Private PEM keys, database connection strings, password assignments
- JWT secrets, Bearer tokens, generic API keys

**Entropy Scoring:**
- Shannon entropy analysis catches high-entropy tokens (≥ 4.5 bits/char) that regex might miss

**Allowlisting:**
- Add `# vault-ok` at the end of any line to suppress that warning
- Add file paths to `.vault-secrets-allow` to exempt entire files
- Use `vault save --force` to bypass all checks (⚠ use with caution)

### 🌍 Environmental Snapshotting

Every `vault save` silently captures a sidecar JSON recording:
- **Python version** and platform info
- **Installed packages** with exact versions (via `importlib.metadata`)
- **Safe environment variables** (`PATH`, `VIRTUAL_ENV`, `CONDA_PREFIX`, etc.)
- **System info** — OS, CPU architecture, hostname

Use `vault env diff <sha>` to spot environment drift between commits.

### Semantic Diff Engine

For **Python files**, diffs are AST-aware:
- ＋ Function/class **added**
- － Function/class **removed**
- ✎ Function/class **modified**
- ⇄ Function/class **moved** (line number changed)
- ↻ Function/class **renamed** (body unchanged, name different)

For all other files, a standard unified text diff is used.

### AI Brain

When `GEMINI_API_KEY` is set, `vault save` (without `-m`) sends a cleaned diff to Gemini and proposes a professional commit message. The user can accept, reject, or edit before committing.

## Architecture

```
cli.py                    # Entry point — human-centric argparse CLI
vault_ai/
├── __init__.py            # Package marker, exports VAULT_DIR
├── utils.py               # Hashing, compression, repo discovery, HEAD mgmt
├── store.py               # Init, tree builder, direct commit, log, branches
├── diff_engine.py         # AST semantic diff + text fallback
├── ai_brain.py            # Gemini API commit messages & summaries
├── snapshot.py            # Time-machine snapshot helpers
├── secret_guard.py        # Pre-commit secret detection (regex + entropy)
└── env_snapshot.py        # Per-commit environment capture & diff
server.py                 # Zero-dependency remote push server (http.server)
```

## License
MIT
