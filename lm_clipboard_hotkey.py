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
import ctypes
import sys

import keyboard  # Handles keyboard shortcuts
import pyperclip  # Clipboard
import requests  # HTTP
from colorama import Fore, Style  # Console colors

from settings import JIT_LOADING_TIMEOUT

CONFIG_FILE: Final[Path] = Path(__file__).with_name("config.json")
EXAMPLE_FILE: Final[Path] = Path(__file__).with_name("config.example.json")
TIMEOUT: Final[int] = 120  # s
JIT_TIMEOUT: Final[int] = JIT_LOADING_TIMEOUT

if os.name == "nt":
    DEFAULT_PASTE_KEYS: Final[str] = "ctrl+v"
elif sys.platform == "darwin":
    DEFAULT_PASTE_KEYS: Final[str] = "command+v"
else:
    DEFAULT_PASTE_KEYS: Final[str] = "ctrl+shift+v"

# -------------------- Utils -------------------- #

def debug(msg: str, *, color: str = "") -> None:
    col = getattr(Fore, color.upper(), "")
    print(f"{col}{msg}{Style.RESET_ALL}")


def release_left_click() -> None:
    """Release the left mouse button on Windows."""
    if os.name == "nt":
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)


def warn_linux_privileges() -> None:
    """Warn about Linux hotkey requirements."""
    if os.name == "nt":
        return
    if not Path("/dev/uinput").exists():
        debug("[WARN] /dev/uinput missing; hotkeys may fail", color="yellow")
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        debug("[WARN] Hotkeys may require root privileges on Linux", color="yellow")


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


def read_prompt_file(path_str: str) -> str:
    """Return the contents of *path_str*.

    If *path_str* is relative, it is resolved against the directory of this
    script."""

    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path.read_text(encoding="utf-8").strip()

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
    system_prompt: str | None,
    load_strategy: str,
    auto_paste: bool,
    auto_copy: bool,
    paste_keys: str,
    model_id: str,
    prompt_file: str | None = None,
) -> None:
    release_left_click()
    if auto_copy:
        keyboard.release("ctrl")
        keyboard.release("shift")
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
            keyboard.press_and_release(paste_keys)
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
        help="Paste the answer using the OS default after copying.",
    )

    parser.add_argument(
        "--paste-keys",
        default=DEFAULT_PASTE_KEYS,
        help="Key combo to paste when auto-paste is active.",
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

    parser.add_argument(
        "-r",
        "--run-hotkey",
        type=int,
        metavar="N",
        help="Run hotkey N from config and exit.",
    )

    args = parser.parse_args()

    ensure_config()
    warn_linux_privileges()
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

    if args.run_hotkey:
        idx = args.run_hotkey - 1
        if idx < 0 or idx >= len(hotkeys):
            debug(f"[ERROR] Invalid hotkey index {args.run_hotkey}", color="red")
            return
        hk = hotkeys[idx]
        prompt_file = hk.get("prompt_file")
        system_prompt = None
        if prompt_file:
            try:
                system_prompt = read_prompt_file(prompt_file)
            except Exception as exc:
                debug(f"[ERROR] Reading {prompt_file}: {exc}", color="red")
                return
        hk_model = hk.get("model", MODEL_NAME)
        handle_hotkey(system_prompt, args.load_strategy, args.auto_paste, args.auto_copy, args.paste_keys, hk_model, prompt_file)
        return
    for hk in hotkeys[:6]:
        keys = hk.get("keys")
        prompt_file = hk.get("prompt_file")
        if not keys:
            continue
        system_prompt = None
        if prompt_file:
            try:
                system_prompt = read_prompt_file(prompt_file)
                preview = (system_prompt[:60] + "…") if len(system_prompt) > 60 else system_prompt
                desc = f"{keys} → {prompt_file} : {preview!r}"
            except Exception as exc:
                debug(f"[ERROR] Reading {prompt_file}: {exc}", color="red")
                continue
        else:
            desc = f"{keys} → clipboard"

        hk_model = hk.get("model")
        if hk_model and hk_model != MODEL_NAME:
            debug(f"[CONFIG] {desc} (model: {hk_model})", color="blue")
        else:
            debug(f"[CONFIG] {desc}", color="blue")

        keyboard.add_hotkey(
            keys,
            lambda sp=system_prompt, pf=prompt_file, m=hk.get("model", MODEL_NAME): threading.Thread(
                target=handle_hotkey,
                args=(sp, args.load_strategy, args.auto_paste, args.auto_copy, args.paste_keys, m, pf),
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
