#!/usr/bin/env python3
"""Send clipboard content to LM Studio when a hotkey is pressed."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Final, List

import keyboard  # Handles keyboard shortcuts
import pyperclip  # Clipboard
import requests  # HTTP
from colorama import Fore, Style  # Console colors

CONFIG_FILE: Final[Path] = Path(__file__).with_name("config.json")
TIMEOUT: Final[int] = 120  # s

# -------------------- Utils -------------------- #

def debug(msg: str, *, color: str = "") -> None:
    col = getattr(Fore, color.upper(), "")
    print(f"{col}{msg}{Style.RESET_ALL}")


def load_config(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        debug(f"[ERROR] Loading {path}: {exc}", color="red")
        return {}

# -------------------- Model loading -------------------- #

def is_model_loaded() -> bool:
    """Return True if MODEL_NAME is already loaded (depends on JIT)."""
    try:
        r = requests.get(f"{LM_STUDIO_HOST}/v1/models", timeout=10)
        r.raise_for_status()
        data = r.json()
        models: List[str] = [m["id"] if isinstance(m, dict) else m for m in data.get("data", [])]
        return any(MODEL_NAME in mid for mid in models)
    except Exception as exc:
        debug(f"[WARN] Unable to check loaded models: {exc}", color="yellow")
        return False


def jit_load_model(system_prompt: str | None = None) -> bool:
    """Force a JIT load by sending a dummy request with TTL."""
    debug("[INFO] Trying JIT load…", color="cyan")
    dummy_user = "(warm‑up)"
    headers = {"Content-Type": "application/json"}
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": dummy_user})
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": 1,
        "stream": False,
        "ttl": 300,  # 5 min to avoid hogging memory
    }
    try:
        requests.post(f"{LM_STUDIO_HOST}/v1/chat/completions", json=payload, timeout=TIMEOUT)
        return is_model_loaded()
    except Exception as exc:
        debug(f"[ERROR] JIT load failed: {exc}", color="red")
        return False


def cli_load_model() -> bool:
    """Try `lms load MODEL_NAME` via the CLI."""
    debug("[INFO] Trying to load via lms CLI…", color="cyan")
    try:
        completed = subprocess.run([
            "lms", "load", MODEL_NAME, "-y"
        ], check=False, capture_output=True, text=True, shell=True)
        if completed.returncode == 0:
            return True
        debug(completed.stderr or completed.stdout, color="yellow")
    except FileNotFoundError:
        debug("[WARN] lms.exe not found in PATH.", color="yellow")
    return False


def ensure_model_loaded(strategy: str, system_prompt: str | None = None) -> None:
    """Ensure the model is loaded according to *strategy*."""
    if is_model_loaded():
        return
    if strategy == "off":
        debug("[INFO] Model not loaded and strategy=off — continuing.", color="yellow")
        return
    loaded = False
    if strategy == "jit":
        loaded = jit_load_model(system_prompt)
    elif strategy == "cli":
        loaded = cli_load_model()
    if not loaded:
        debug("[ERROR] Could not load the model automatically.", color="red")

# -------------------- Inference -------------------- #

def query_lm(prompt: str, system_prompt: str | None = None) -> str:
    headers = {"Content-Type": "application/json"}
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
    }
    try:
        response = requests.post(f"{LM_STUDIO_HOST}/v1/chat/completions", headers=headers, json=payload, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        debug(f"[ERROR] LM Studio request failed: {exc}", color="red")
        return ""

# -------------------- Hotkey -------------------- #

def handle_hotkey(system_prompt: str, load_strategy: str) -> None:
    prompt = pyperclip.paste()
    if not prompt.strip():
        debug("[INFO] Clipboard empty.", color="yellow")
        return

    ensure_model_loaded(load_strategy, system_prompt)

    debug("[INFO] Sending to LM Studio…", color="cyan")
    answer = query_lm(prompt, system_prompt)

    if answer:
        pyperclip.copy(answer)
        debug("[OK] Answer copied ✔\n", color="green")
    else:
        debug("[KO] No answer.\n", color="red")

# -------------------- Main -------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Hotkey → LM Studio", formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument(
        "--load-strategy",
        choices=["jit", "cli", "off"],
        default="jit",
        help="Strategy to auto-load the model if missing.",
    )

    args = parser.parse_args()

    config = load_config(CONFIG_FILE)

    env_host = os.getenv("LM_STUDIO_HOST")
    if env_host:
        lm_host = env_host
    else:
        host = config.get("host", "127.0.0.1")
        port = config.get("port", 1234)
        lm_host = f"http://{host}:{port}"

    model = os.getenv("MODEL_NAME", config.get("model", "model"))

    global LM_STUDIO_HOST, MODEL_NAME
    LM_STUDIO_HOST = lm_host
    MODEL_NAME = model

    debug(f"[CONFIG] Host: {LM_STUDIO_HOST}", color="blue")
    debug(f"[CONFIG] Model: {MODEL_NAME}\n", color="blue")

    hotkeys = config.get("hotkeys")
    if not hotkeys:
        env_hotkey = os.getenv("HOTKEY")
        if env_hotkey:
            hotkeys = [{"keys": env_hotkey, "prompt_file": "prompts/default.txt"}]
        else:
            hotkeys = [{"keys": "ctrl+shift+1", "prompt_file": "prompts/default.txt"}]
        debug("[INFO] Default hotkeys loaded.", color="yellow")
    for hk in hotkeys[:5]:
        keys = hk.get("keys")
        prompt_file = hk.get("prompt_file")
        if not keys or not prompt_file:
            continue
        try:
            system_prompt = Path(prompt_file).expanduser().read_text(encoding="utf-8").strip()
            preview = (system_prompt[:60] + "…") if len(system_prompt) > 60 else system_prompt
            debug(f"[CONFIG] {keys} → {prompt_file} : {preview!r}", color="blue")
        except Exception as exc:
            debug(f"[ERROR] Reading {prompt_file}: {exc}", color="red")
            continue

        keyboard.add_hotkey(
            keys,
            lambda sp=system_prompt: threading.Thread(
                target=handle_hotkey,
                args=(sp, args.load_strategy),
                daemon=True,
            ).start(),
        )

    debug("LM Studio Hotkey active! (Ctrl+C to quit)\n", color="magenta")

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        debug("Stopping script…", color="cyan")


if __name__ == "__main__":
    main()
