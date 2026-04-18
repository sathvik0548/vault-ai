#!/usr/bin/env python3
"""
Vault-AI CLI — Human-centric commands, zero staging area.
Intelligence & Safety layer with Ollama-first AI.

Commands:
  vault init                     Initialize a repository
  vault save [-m "msg"] [--force] Direct commit (AI writes message if omitted)
  vault diff [-v]                Semantic diff vs last commit
  vault history [-n 20]          Show commit log
  vault undo last                Revert the last commit
  vault scan                     Scan for secrets (no commit)
  vault story [--days 7]         Generate dev story from recent commits
  vault search "query"           Natural language search through commits
  vault sandbox                  Enter a virtual sandbox
  vault sandbox exit             Exit sandbox (discard changes)
  vault sandbox merge            Exit sandbox (keep changes)
  vault env show [sha]           Show environment snapshot
  vault env diff [sha]           Compare env vs stored snapshot
  vault watch                    Start file-watcher autosave daemon
  vault create branch <name>     Create a branch
  vault snapshot [-l "label"]    Take a time-machine snapshot
  vault sync [--url URL]         Push objects to remote server
"""

import argparse
import sys
from vault_ai.store import (
    init_repo, direct_commit, log, create_branch,
    push_objects, undo_last,
)
from vault_ai.diff_engine import diff_working_tree
from vault_ai.ai_brain import generate_commit_message
from vault_ai.llm import check_ai_readiness
from vault_ai.snapshot import take_snapshot
from vault_ai.utils import find_repo, get_head
from vault_ai.secret_guard import scan_working_tree, print_findings, auto_move_to_private
from vault_ai.env_snapshot import (
    load_env_snapshot, capture, diff_envs,
    print_snapshot, print_diff,
)
from vault_ai.reminders import check_large_change
from vault_ai.ghost_pair import check_ghost_pairs, print_ghost_warnings
from vault_ai.config import (
    set_active_ai, get_active_ai, set_api_key, set_ollama_settings
)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_init(args):
    init_repo()


def _cmd_save(args):
    repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return

    # --- Secret Guard pre-commit scan ---
    if not getattr(args, "force", False):
        findings = scan_working_tree(repo)
        if findings:
            print_findings(findings, repo)
            # Auto-move secrets to .vault_private
            moved = auto_move_to_private(findings, repo)
            if moved:
                print(f"  📋  Logged {moved} secret(s) to .vault_private")
            print("  ✗  Commit blocked. Fix secrets above, or use  vault save --force  to override.")
            import sys
            sys.exit(1)

    # --- Self-Healing pre-commit lint check ---
    try:
        from vault_ai.self_heal import run_self_heal
        is_clean = run_self_heal(repo)
        if not is_clean and not getattr(args, "force", False):
            print("  ⚠  Lint errors found. Fix them or use  vault save --force  to skip.")
            import sys
            sys.exit(1)
    except Exception:
        pass  # never block on self-heal failure

    # --- Ghost Pairing check ---
    ghost_warnings = check_ghost_pairs(repo)
    if ghost_warnings:
        print_ghost_warnings(ghost_warnings)

    # --- Smart Reminders ---
    check_large_change(repo)

    message = args.message
    if not message:
        # Check AI readiness
        ready, error_msg = check_ai_readiness()
        if not ready:
            print(error_msg)
            print("  ✗  Aborted — No commit message provided and AI is not configured.")
            import sys
            sys.exit(1)

        # Try AI-generated message
        print("  🧠 Generating commit message with AI...")
        diffs = diff_working_tree(repo)
        if diffs:
            combined_diff = "\n".join(d.unified for d in diffs if d.unified)
            ai_msg = generate_commit_message(combined_diff)
            if ai_msg:
                print(f"  💬 AI suggests: {ai_msg.splitlines()[0]}")
                confirm = input("  Accept? [Y/n/edit]: ").strip().lower()
                if confirm in ("", "y", "yes"):
                    message = ai_msg
                elif confirm in ("n", "no"):
                    message = input("  Enter your message: ").strip()
                else:
                    message = confirm
            else:
                print("  ⚠  AI unavailable — please provide a message manually.")
                message = input("  Enter commit message: ").strip()
        else:
            print("  (no changes detected)")
            message = input("  Enter commit message anyway: ").strip()

    if not message:
        print("  ✗  Aborted — empty commit message.")
        import sys
        sys.exit(1)

    import time
    t0 = time.time()
    result = direct_commit(message, repo, force=getattr(args, "force", False))
    t1 = time.time()
    
    if result is None:
        import sys
        sys.exit(1)
        
    cycle_ms = (t1 - t0) * 1000
    print(f"  ⏱  Cycle-Time Benchmark: Vault-AI finalized snapshot in {cycle_ms:.2f}ms")


def _cmd_sync(args):
    repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return
    repo_name = repo.name
    push_objects(args.url, repo_name, repo)


def _cmd_diff(args):
    results = diff_working_tree()
    if not results:
        print("  ✓  Working tree is clean.")
        return
    for r in results:
        print(f"\n  ╌╌ {r.file_path} ╌╌")
        for line in r.summary_lines:
            print(line)
        if args.verbose and r.unified:
            print()
            for ln in r.unified.splitlines():
                print(f"    {ln}")


def _cmd_history(args):
    log(limit=args.limit)


def _cmd_undo(args):
    if args.target == "last":
        from vault_ai.store import undo_last
        undo_last(sparse=getattr(args, "sparse", False))
    else:
        print(f"  ⚠  Unknown undo target: {args.target}")


def _cmd_create_branch(args):
    create_branch(args.name)


def _cmd_snapshot(args):
    repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return
    sha = take_snapshot(repo, label=args.label)
    if sha:
        print(f"  📸 Snapshot taken (tree {sha[:10]})")


# ---------------------------------------------------------------------------
# Secret Guard CLI
# ---------------------------------------------------------------------------

def _cmd_scan(args):
    repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return
    findings = scan_working_tree(repo)
    print_findings(findings, repo)


# ---------------------------------------------------------------------------
# Environment Snapshot CLI
# ---------------------------------------------------------------------------

def _cmd_env_show(args):
    repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return
    sha = getattr(args, "sha", None) or get_head(repo)
    if sha is None:
        print("  ⚠  No commits yet.")
        return
    snap = load_env_snapshot(repo, sha)
    if snap is None:
        print(f"  ⚠  No environment snapshot found for {sha[:10]}.")
        return
    print_snapshot(snap, sha)


def _cmd_env_diff(args):
    repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return
    sha = getattr(args, "sha", None) or get_head(repo)
    if sha is None:
        print("  ⚠  No commits yet.")
        return
    snap = load_env_snapshot(repo, sha)
    if snap is None:
        print(f"  ⚠  No snapshot found for {sha[:10]}.")
        return
    current = capture(repo)
    diff = diff_envs(snap, current)
    print(f"\n  Comparing current environment vs commit {sha[:10]}:")
    print_diff(diff)


# ---------------------------------------------------------------------------
# Config Switchboard CLI
# ---------------------------------------------------------------------------

def _cmd_config(args):
    cmd = getattr(args, "config_cmd", None)
    if not cmd:
        return

    if cmd == "list-ai":
        agent, custom = get_active_ai()
        print("\n  🤖 Available AI Adapters:")
        print(f"      {'*' if agent == 'ollama' else ' '}  Ollama (Local Default)  {'(Active)' if agent == 'ollama' else ''}")
        print(f"      {'*' if agent == 'gemini' else ' '}  Gemini (Cloud)          {'(Active)' if agent == 'gemini' else ''}")
        print(f"      {'*' if agent == 'openai' else ' '}  OpenAI (Cloud)          {'(Active)' if agent == 'openai' else ''}")
        print(f"      {'*' if agent == 'custom' else ' '}  Custom Script           {'(Active - ' + str(custom) + ')' if agent == 'custom' and custom else ''}")
        print("\n  To switch engines, use: vault config use <engine>")
        print("  To use a custom script: vault config use-custom <path>\n")

    elif cmd == "use":
        if set_active_ai(args.engine):
            print(f"  ✅  AI Engine set to: {args.engine}")

    elif cmd == "use-custom":
        import os
        import stat
        path = os.path.abspath(args.path)
        if not os.path.exists(path):
            print(f"  ✗  Could not find script at {path}")
            return
        st = os.stat(path)
        if not bool(st.st_mode & stat.S_IXUSR):
            print(f"  ⚠  Script is not executable. Run 'chmod +x {args.path}'")
        if set_active_ai("custom", path):
            print(f"  ✅  Custom AI Engine set to: {path}")

    elif cmd == "reset":
        if set_active_ai("ollama"):
            print("  ✅  AI Config reset to default (Ollama).")


def _cmd_setup(args):
    print("\n  🚀  Vault-AI Setup Wizard")
    print("  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌")
    
    # 1. AI Engine Selection
    print("\n  1. Select default AI Engine:")
    print("     [1] Ollama (Local - Default)")
    print("     [2] Gemini (Cloud)")
    print("     [3] OpenAI (Cloud)")
    choice = input("  Choice [1-3]: ").strip()
    
    engine = "ollama"
    if choice == "2":
        engine = "gemini"
    elif choice == "3":
        engine = "openai"
    
    set_active_ai(engine)
    print(f"  ✅  Default engine set to {engine}")
    
    # 2. API Keys / URLs
    if engine == "gemini":
        key = input("  Enter Gemini API Key: ").strip()
        if key:
            set_api_key("gemini", key)
            print("  ✅  Gemini API Key saved.")
    elif engine == "openai":
        key = input("  Enter OpenAI API Key: ").strip()
        if key:
            set_api_key("openai", key)
            print("  ✅  OpenAI API Key saved.")
    else:
        url = input("  Enter Ollama URL [http://localhost:11434]: ").strip()
        model = input("  Enter Ollama Model [llama3.2]: ").strip()
        set_ollama_settings(url or None, model or None)
        print("  ✅  Ollama settings saved.")
    
    print("\n  ✨ Setup complete! You can now use 'vault save' for AI-assisted commits.\n")
# ---------------------------------------------------------------------------
# Story Mode CLI
# ---------------------------------------------------------------------------

def _cmd_story(args):
    from vault_ai.story import generate_story, print_story, export_story_pdf
    report = generate_story(days=args.days)
    if report:
        print_story(report)
        if getattr(args, "pdf", False):
            pdf_path = export_story_pdf(report)
            if pdf_path:
                print(f"  📄  PDF exported to: {pdf_path}")


# ---------------------------------------------------------------------------
# Predictive Conflict Resolution CLI
# ---------------------------------------------------------------------------

def _cmd_predict(args):
    from vault_ai.conflict import predict_conflicts, print_conflict_report
    warnings = predict_conflicts(args.branch_a, args.branch_b)
    print_conflict_report(args.branch_a, args.branch_b, warnings)


def _cmd_merge(args):
    from vault_ai.merge import merge_branch
    merge_branch(args.branch)


# ---------------------------------------------------------------------------
# Project Integrity & Hacker Defense CLI
# ---------------------------------------------------------------------------

def _cmd_scan(args):
    repo = find_repo()
    if repo is None:
        print("  ✗  Not inside a Vault-AI repository.")
        return

    safe = True
    print("  🔍  Initializing Security Scan...")

    # 1. Integrity Check
    from vault_ai.integrity import verify_and_lockdown
    if not verify_and_lockdown(repo):
        safe = False

    # 2. Dependency Audit
    from vault_ai.dep_audit import audit_dependencies, print_audit_report
    vulns = audit_dependencies(repo)
    if vulns and not print_audit_report(vulns):
        safe = False

    # 3. Anomaly Detection
    from vault_ai.anomaly import scan_working_tree_anomalies, print_anomaly_report
    findings = scan_working_tree_anomalies(repo)
    if findings and not print_anomaly_report(findings):
        safe = False

    if safe:
        print("\n  ✅  System looks clean and secure!")
    else:
        print("\n  🚨  Security issues detected. See details above.")


# ---------------------------------------------------------------------------
# Visual Branch Map CLI
# ---------------------------------------------------------------------------

def _cmd_map(args):
    from vault_ai.branch_map import print_branch_map
    limit = getattr(args, "limit", 30)
    print_branch_map(limit=limit)


# ---------------------------------------------------------------------------
# Semantic Search CLI
# ---------------------------------------------------------------------------

def _cmd_search(args):
    from vault_ai.search import search_commits, simple_search, print_search_results
    results = search_commits(args.query)
    if not results:
        # Fallback to simple substring search
        results = simple_search(args.query)
    print_search_results(args.query, results)


# ---------------------------------------------------------------------------
# Virtual Sandbox CLI
# ---------------------------------------------------------------------------

def _cmd_sandbox(args):
    from vault_ai.sandbox import enter_sandbox, exit_sandbox, sandbox_status
    action = getattr(args, "action", None)
    if action == "exit":
        exit_sandbox()
    elif action == "merge":
        exit_sandbox(merge=True)
    elif action == "status":
        sandbox_status()
    else:
        enter_sandbox()


# ---------------------------------------------------------------------------
# Lazy-Load CLI
# ---------------------------------------------------------------------------

def _cmd_hydrate(args):
    from vault_ai.lazy import hydrate
    hydrate(args.path)


# ---------------------------------------------------------------------------
# File Watcher CLI
# ---------------------------------------------------------------------------

def _cmd_watch(args):
    from vault_ai.watcher import watch
    interval = getattr(args, "interval", 30)
    watch(interval=interval)


# ---------------------------------------------------------------------------
# Main / Argparse
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="vault",
        description="Vault-AI — AI-native Version Control",
    )
    sub = parser.add_subparsers(dest="command")

    # --- init ---
    sub.add_parser("init", help="Initialize a new repository")

    # --- setup ---
    sub.add_parser("setup", help="Initial configuration (AI keys, engines)")

    # --- save ---
    p_save = sub.add_parser("save", help="Commit all changes (no staging)")
    p_save.add_argument("-m", "--message", default=None,
                        help="Commit message (AI generates if omitted)")
    p_save.add_argument("--force", action="store_true",
                        help="Bypass Secret Guard and linter checks")

    # --- diff ---
    p_diff = sub.add_parser("diff", help="Semantic diff vs last commit")
    p_diff.add_argument("-v", "--verbose", action="store_true")

    # --- history ---
    p_hist = sub.add_parser("history", help="Show commit log")
    p_hist.add_argument("-n", "--limit", type=int, default=20)

    # --- scan ---
    sub.add_parser("scan", help="Scan working tree for secrets")

    # --- story ---
    p_story = sub.add_parser("story", help="Generate dev story from recent commits")
    p_story.add_argument("--days", type=int, default=7)
    p_story.add_argument("--pdf", action="store_true",
                         help="Export story as PDF")

    # --- predict (conflict resolution) ---
    p_predict = sub.add_parser("predict", help="Predict merge conflicts between branches")
    p_predict.add_argument("branch_a", help="First branch")
    p_predict.add_argument("branch_b", help="Second branch")

    # --- merge (AI auto-fixer) ---
    p_merge = sub.add_parser("merge", help="Merge a branch into the current one using AI")
    p_merge.add_argument("branch", help="Target branch to merge in")

    # --- map (visual branch graph) ---
    p_map = sub.add_parser("map", help="Visual branch map")
    p_map.add_argument("-n", "--limit", type=int, default=30,
                       help="Max commits to display")

    # --- search ---
    p_search = sub.add_parser("search", help="Search commits with natural language")
    p_search.add_argument("query", help="Search query")

    # --- sandbox ---
    p_sb = sub.add_parser("sandbox", help="Virtual sandbox for what-if experiments")
    p_sb.add_argument("action", nargs="?", default=None,
                       choices=["exit", "merge", "status"],
                       help="Sandbox action (omit to enter)")

    # --- hydrate (lazy load) ---
    p_hyd = sub.add_parser("hydrate", help="Extract lazy files from sparse manifest")
    p_hyd.add_argument("path", help="File or directory path to hydrate")

    # --- scan (security) ---
    p_scan = sub.add_parser("scan", help="Run full security suite (integrity, anomalies, deps)")

    # --- env ---
    p_env = sub.add_parser("env", help="Environment snapshot commands")
    env_sub = p_env.add_subparsers(dest="env_cmd")
    p_env_show = env_sub.add_parser("show", help="Show env snapshot")
    p_env_show.add_argument("sha", nargs="?", default=None)
    p_env_diff = env_sub.add_parser("diff", help="Diff env vs snapshot")
    p_env_diff.add_argument("sha", nargs="?", default=None)

    # --- config ---
    p_config = sub.add_parser("config", help="Vault-AI configuration (Switchboard)")
    c_sub = p_config.add_subparsers(dest="config_cmd")
    c_sub.add_parser("list-ai", help="List available AI agents")
    p_use = c_sub.add_parser("use", help="Use a specific AI agent")
    p_use.add_argument("engine", choices=["ollama", "gemini", "openai"])
    p_uc = c_sub.add_parser("use-custom", help="Use a custom agent script")
    p_uc.add_argument("path", help="Path to executable standard agent script")
    c_sub.add_parser("reset", help="Reset AI config to local Ollama")

    # --- watch ---
    p_watch = sub.add_parser("watch", help="Start file-watcher autosave daemon")
    p_watch.add_argument("-i", "--interval", type=int, default=30,
                         help="Poll interval in seconds")

    # --- undo ---
    p_undo = sub.add_parser("undo", help="Undo changes")
    p_undo.add_argument("target", choices=["last"])
    p_undo.add_argument("--sparse", action="store_true", help="Undo in sparse mode (lazy load)")

    # --- create branch ---
    p_create = sub.add_parser("create", help="Create resources")
    p_create_sub = p_create.add_subparsers(dest="resource")
    p_branch = p_create_sub.add_parser("branch", help="Create a branch")
    p_branch.add_argument("name", help="Branch name")

    # --- snapshot ---
    p_snap = sub.add_parser("snapshot", help="Take a time-machine snapshot")
    p_snap.add_argument("-l", "--label", default="manual")

    # --- sync ---
    p_sync = sub.add_parser("sync", help="Sync objects with remote server")
    p_sync.add_argument("--url", default="http://127.0.0.1:8000")

    args = parser.parse_args()

    # Route env subcommand
    if args.command == "env":
        if getattr(args, "env_cmd", None) == "show":
            _cmd_env_show(args)
        elif getattr(args, "env_cmd", None) == "diff":
            _cmd_env_diff(args)
        else:
            p_env.print_help()
        return

    if args.command == "config":
        if getattr(args, "config_cmd", None):
            _cmd_config(args)
        else:
            p_config.print_help()
        return

    dispatch = {
        "init":     _cmd_init,
        "save":     _cmd_save,
        "sync":     _cmd_sync,
        "diff":     _cmd_diff,
        "history":  _cmd_history,
        "scan":     _cmd_scan,
        "story":    _cmd_story,
        "search":   _cmd_search,
        "sandbox":  _cmd_sandbox,
        "watch":    _cmd_watch,
        "undo":     _cmd_undo,
        "create":   lambda a: _cmd_create_branch(a) if getattr(a, "resource", None) == "branch" else print(parser.format_help()),
        "snapshot": _cmd_snapshot,
        "predict":  _cmd_predict,
        "merge":    _cmd_merge,
        "map":      _cmd_map,
        "hydrate":  _cmd_hydrate,
        "scan":     _cmd_scan,
        "setup":    _cmd_setup,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
