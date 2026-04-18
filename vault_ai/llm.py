"""
vault_ai.llm
~~~~~~~~~~~~~
Modular AI Switchboard: Routes AI generation to the configured engine.
Defaults to local Ollama.
Adapters: Ollama, OpenAI, Gemini, Custom Script.
Features: Strict JSON format enforcement across all adapters.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
import urllib.error

from vault_ai.config import (
    get_active_ai, get_api_key, get_ollama_settings
)

# ---------------------------------------------------------------------------
# Format Enforcer
# ---------------------------------------------------------------------------

JSON_GUARDRAIL = (
    "\n\nOUTPUT INSTRUCTION: You MUST output ONLY valid JSON. "
    "Do not include any markdown formatting blocks (like ```json), explanations, or trailing text. "
    "The root structure must match the requester's target exactly."
)

def _enforce_json_prompt(prompt: str, json_only: bool) -> str:
    """If JSON is strictly required, append the guardrail logic."""
    if json_only:
        return prompt + JSON_GUARDRAIL
    return prompt

# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

def _ask_ollama(prompt: str, json_only: bool) -> str | None:
    """Ollama Adapter"""
    stored_url, stored_model = get_ollama_settings()
    url = os.environ.get("OLLAMA_URL", stored_url)
    model = os.environ.get("OLLAMA_MODEL", stored_model)
    
    payload_dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if json_only:
        payload_dict["format"] = "json"

    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        f"{url}/api/generate",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
            return body.get("response", "").strip() or None
    except Exception:
        return None


def _ask_openai(prompt: str, json_only: bool) -> str | None:
    """OpenAI Adapter"""
    api_key = os.environ.get("OPENAI_API_KEY") or get_api_key("openai")
    if not api_key:
        print("  ⚠  OpenAI configuration active, but no API key found in env or config.")
        return None
        
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    
    payload_dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if json_only:
        payload_dict["response_format"] = {"type": "json_object"}

    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
            return body["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  ⚠  OpenAI API Error: {e}")
        return None


def _ask_gemini(prompt: str, json_only: bool) -> str | None:
    """Gemini Adapter"""
    api_key = os.environ.get("GEMINI_API_KEY") or get_api_key("gemini")
    if not api_key:
        print("  ⚠  Gemini configuration active, but no API key found in env or config.")
        return None
        
    # Using REST API directly to keep dependencies zero/low
    model = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    payload_dict = {
        "contents": [{"parts": [{"text": prompt}]}],
    }
    if json_only:
        payload_dict["generationConfig"] = {"responseMimeType": "application/json"}

    payload = json.dumps(payload_dict).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
            return body["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"  ⚠  Gemini API Error: {e}")
        return None


def _ask_custom(prompt: str, json_only: bool, script_path: str | None) -> str | None:
    """Custom Script Adapter"""
    if not script_path or not os.path.exists(script_path):
        print(f"  ⚠  Custom AI script not found: {script_path}")
        return None
        
    env = os.environ.copy()
    if json_only:
        env["VAULT_REQUIRE_JSON"] = "1"
        
    try:
        # Pass the prompt via stdin
        process = subprocess.run(
            [script_path],
            input=prompt,
            text=True,
            capture_output=True,
            env=env,
            timeout=120
        )
        if process.returncode != 0:
            print(f"  ⚠  Custom AI script failed (exit {process.returncode}): {process.stderr}")
            return None
        return process.stdout.strip()
    except Exception as e:
        print(f"  ⚠  Custom AI script exception: {e}")
        return None


# ---------------------------------------------------------------------------
# Switchboard Interface
# ---------------------------------------------------------------------------

def ask(prompt: str, json_only: bool = False, prefer: str | None = None) -> str | None:
    """
    Ask an LLM a question based on active configuration.
    Strictly enforces JSON schema if json_only=True.
    """
    agent_type, custom_path = get_active_ai()
    
    # Optional override
    if prefer:
        agent_type = prefer

    prompt = _enforce_json_prompt(prompt, json_only)

    if agent_type == "openai":
        ans = _ask_openai(prompt, json_only)
    elif agent_type == "gemini":
        ans = _ask_gemini(prompt, json_only)
    elif agent_type == "custom":
        ans = _ask_custom(prompt, json_only, custom_path)
    else:
        ans = _ask_ollama(prompt, json_only)

    try:
        from vault_ai.audit import log_audit
        from vault_ai.utils import find_repo
        repo = find_repo()
        if repo and ans:
            log_audit(repo, "AI Prompt Generated", "Switchboard Intercept", prompt, agent_type, "N/A")
    except ImportError:
        pass

    return ans


def check_ai_readiness() -> tuple[bool, str | None]:
    """
    Checks if the currently configured AI engine is ready to use.
    Returns (is_ready, error_message/instructions).
    """
    agent_type, _ = get_active_ai()
    
    if agent_type == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or get_api_key("gemini")
        if not key:
            return False, (
                "  ⚠  Missing Gemini API Key.\n"
                "  👉  To fix this, run:  vault setup\n"
                "      Then select 'Gemini' and provide your key."
            )
    elif agent_type == "openai":
        key = os.environ.get("OPENAI_API_KEY") or get_api_key("openai")
        if not key:
            return False, (
                "  ⚠  Missing OpenAI API Key.\n"
                "  👉  To fix this, run:  vault setup\n"
                "      Then select 'OpenAI' and provide your key."
            )
    elif agent_type == "ollama":
        # We can't easily check if it's running without a request, 
        # but we can warn about the default setup.
        pass
        
    return True, None
