#!/usr/bin/env python3
"""Send clipboard content to LM Studio when a hotkey is pressed."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Final, List

import keyboard  # Handles keyboard shortcuts
import pyperclip  # Clipboard
import requests  # HTTP
from colorama import Fore, Style  # Console colors

from settings import JIT_LOADING_TIMEOUT

CONFIG_FILE: Final[Path] = Path(__file__).with_name("config.json")
EXAMPLE_FILE: Final[Path] = Path(__file__).with_name("config.example.json")
TIMEOUT: Final[int] = 120  # s
JIT_TIMEOUT: Final[int] = JIT_LOADING_TIMEOUT

# -------------------- Utils -------------------- #

def debug(msg: str, *, color: str = "") -> None:
    col = getattr(Fore, color.upper(), "")
    print(f"{col}{msg}{Style.RESET_ALL}")


def ensure_config() -> None:
    """Create CONFIG_FILE from EXAMPLE_FILE if it doesn't exist."""
    if not CONFIG_FILE.exists() and EXAMPLE_FILE.exists():
        try:
            CONFIG_FILE.write_text(EXAMPLE_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            debug(f"[INFO] Created {CONFIG_FILE.name} from example.", color="cyan")
        except Exception as exc:
            debug(f"[ERROR] Unable to create {CONFIG_FILE}: {exc}", color="red")


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


class ModelNotLoadedError(Exception):
    pass


def get_model_state(model_id: str) -> str:
    """Return the state of *model_id* via /api/v0/models."""
    try:
        r = requests.get(f"{LM_STUDIO_HOST}/api/v0/models", timeout=10)
        r.raise_for_status()
        for m in r.json():
            mid = m.get("id") or m.get("modelId")
            if model_id in (mid, m.get("name")):
                return m.get("state", "unknown")
    except Exception as exc:
        debug(f"[WARN] Checking model state failed: {exc}", color="yellow")
    return "unknown"


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

def _call_chat(payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    response = requests.post(
        f"{LM_STUDIO_HOST}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=TIMEOUT,
    )
    if response.status_code == 404 and (
        "No models loaded" in response.text
        or "Model not found" in response.text
    ):
        raise ModelNotLoadedError(response.text)
    response.raise_for_status()
    return response.json()


def query_lm(
    prompt: str,
    system_prompt: str | None = None,
    *,
    model_id: str | None = None,
) -> str:
    model_id = model_id or MODEL_NAME
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model_id, "messages": messages, "stream": False}

    deadline = time.monotonic() + JIT_TIMEOUT
    backoff = 1

    while True:
        try:
            data = _call_chat(payload)
            return data["choices"][0]["message"]["content"].strip()
        except ModelNotLoadedError:
            state = get_model_state(model_id)
            if state == "not-loaded" and time.monotonic() < deadline:
                debug(f"[INFO] Waiting for {model_id} to load…", color="cyan")
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
                continue
            if state == "not-loaded":
                debug("[ERROR] Loading model timed out", color="red")
                break
            if state == "unknown":
                debug(f"[ERROR] Model {model_id} not found", color="red")
                break
        except Exception as exc:
            debug(f"[ERROR] LM Studio request failed: {exc}", color="red")
            break
    return ""

# -------------------- Hotkey -------------------- #

def handle_hotkey(
    system_prompt: str,
    load_strategy: str,
    auto_paste: bool,
    auto_copy: bool,
    model_id: str,
    prompt_file: str | None = None,
) -> None:
    if auto_copy:
        keyboard.press_and_release("ctrl+c")
        time.sleep(0.1)
    prompt = pyperclip.paste()
    if not prompt.strip():
        debug("[INFO] Clipboard empty.", color="yellow")
        return

    ensure_model_loaded(load_strategy, system_prompt)

    if prompt_file:
        debug(f"[INFO] Sending to LM Studio ({prompt_file})…", color="cyan")
    else:
        debug("[INFO] Sending to LM Studio…", color="cyan")
    answer = query_lm(prompt, system_prompt, model_id=model_id)

    if answer:
        pyperclip.copy(answer)
        debug("[OK] Answer copied ✔\n", color="green")
        if auto_paste:
            keyboard.press_and_release("ctrl+v")
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

    parser.add_argument(
        "--auto-paste",
        action="store_true",
        help="Paste the answer with Ctrl+V after copying.",
    )

    parser.add_argument(
        "--auto-copy",
        action="store_true",
        help="Copy the current selection before sending.",
    )

    parser.add_argument(
        "--model-id",
        help="Override the model identifier",
    )

    args = parser.parse_args()

    ensure_config()
    config = load_config(CONFIG_FILE)

    env_host = os.getenv("LM_STUDIO_HOST")
    if env_host:
        lm_host = env_host
    else:
        host = config.get("host", "127.0.0.1")
        port = config.get("port", 1234)
        lm_host = f"http://{host}:{port}"

    model = os.getenv("MODEL_NAME", config.get("model", "model"))
    if args.model_id:
        model = args.model_id

    global LM_STUDIO_HOST, MODEL_NAME
    LM_STUDIO_HOST = lm_host
    MODEL_NAME = model

    debug(f"[CONFIG] Host: {LM_STUDIO_HOST}", color="blue")
    debug(f"[CONFIG] Model: {MODEL_NAME}\n", color="blue")

    hotkeys = config.get("hotkeys") or []
    if not hotkeys:
        debug("[ERROR] No hotkeys defined in config.json", color="red")
        return
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
            lambda sp=system_prompt, pf=prompt_file: threading.Thread(
                target=handle_hotkey,
                args=(sp, args.load_strategy, args.auto_paste, args.auto_copy, MODEL_NAME, pf),
                daemon=True,
            ).start(),
            suppress=True,
        )

    debug("LM Studio Hotkey active! (Ctrl+C to quit)\n", color="magenta")

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        debug("Stopping script…", color="cyan")


if __name__ == "__main__":
    main()
