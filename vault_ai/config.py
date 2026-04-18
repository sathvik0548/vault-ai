"""
vault_ai.config
~~~~~~~~~~~~~~~
Configuration manager for Vault-AI settings (primarily AI Engine Switchboard).
Stores settings as JSON in `.vault/config.json`.
"""
from __future__ import annotations

import json
from pathlib import Path

from vault_ai import VAULT_DIR
from vault_ai.utils import find_repo


def _get_config_path(repo: Path | None = None) -> Path | None:
    if repo is None:
        repo = find_repo()
    if repo is None:
        return None
    return repo / VAULT_DIR / "config.json"


def load_config(repo: Path | None = None) -> dict:
    """Load config.json from .vault, creating default if missing."""
    cfg_path = _get_config_path(repo)
    if not cfg_path:
        return {"agent": "ollama"}

    if not cfg_path.exists():
        default_cfg = {"agent": "ollama"}
        cfg_path.write_text(json.dumps(default_cfg, indent=2))
        return default_cfg

    try:
        return json.loads(cfg_path.read_text())
    except json.JSONDecodeError:
        return {"agent": "ollama"}


def save_config(config_data: dict, repo: Path | None = None) -> bool:
    """Save config.json to .vault."""
    cfg_path = _get_config_path(repo)
    if not cfg_path:
        return False
    
    cfg_path.write_text(json.dumps(config_data, indent=2))
    return True


def get_active_ai(repo: Path | None = None) -> tuple[str, str | None]:
    """
    Returns (agent_type, custom_script_path).
    Agent types: 'ollama' (default), 'gemini', 'openai', 'custom'.
    """
    cfg = load_config(repo)
    agent_type = cfg.get("agent", "ollama")
    custom_path = cfg.get("custom_agent_path")
    return agent_type, custom_path


def set_active_ai(agent_type: str, custom_path: str | None = None, repo: Path | None = None) -> bool:
    """Change the active AI brain."""
    cfg = load_config(repo)
    cfg["agent"] = agent_type
    if custom_path:
        cfg["custom_agent_path"] = custom_path
    elif "custom_agent_path" in cfg and agent_type != "custom":
        del cfg["custom_agent_path"]
        
    return save_config(cfg, repo)


def get_api_key(provider: str, repo: Path | None = None) -> str | None:
    """Retrieve API key from config."""
    cfg = load_config(repo)
    return cfg.get(f"{provider.lower()}_api_key")


def set_api_key(provider: str, key: str, repo: Path | None = None) -> bool:
    """Store API key in config."""
    cfg = load_config(repo)
    cfg[f"{provider.lower()}_api_key"] = key
    return save_config(cfg, repo)


def get_ollama_settings(repo: Path | None = None) -> tuple[str, str]:
    """Returns (url, model) for Ollama."""
    cfg = load_config(repo)
    url = cfg.get("ollama_url", "http://localhost:11434")
    model = cfg.get("ollama_model", "llama3.2")
    return url, model


def set_ollama_settings(url: str | None = None, model: str | None = None, repo: Path | None = None) -> bool:
    """Store Ollama settings in config."""
    cfg = load_config(repo)
    if url:
        cfg["ollama_url"] = url
    if model:
        cfg["ollama_model"] = model
    return save_config(cfg, repo)
