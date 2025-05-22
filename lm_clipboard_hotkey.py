#!/usr/bin/env python3
"""
lm_clipboard_hotkey.py — Interacts with LM Studio through a keyboard shortcut.

What's new in v1.3
==================
✔︎ **System prompt** via `-s/--system-prompt` or `-f/--system-prompt-file` (unchanged).
✔︎ **Auto-load**: if the model isn't loaded, the script can:
     • attempt a JIT load (warm-up request)
     • or call `lms load` (CLI) when available.

Quick start
-----------
```bash
# Inline system prompt + auto-load (default)
python lm_clipboard_hotkey.py -s "Always answer in French."

# Specify the loading strategy
python lm_clipboard_hotkey.py --load-strategy jit        # (default)
python lm_clipboard_hotkey.py --load-strategy cli        # uses lms.exe
python lm_clipboard_hotkey.py --load-strategy off        # do nothing
```

Dependencies:
    pip install requests pyperclip keyboard colorama
    # and install the CLI: https://lmstudio.ai/docs/cli
"""
from __future__ import annotations

import argparse
import json
import subprocess
import threading
from pathlib import Path
from typing import Final, List

import keyboard  # Handles keyboard shortcuts
import pyperclip  # Clipboard
import requests  # HTTP
from colorama import Fore, Style  # Console colors

CONFIG_FILE: Final[Path] = Path(__file__).with_name("config.json")
LM_STUDIO_HOST: str = "http://127.0.0.1:1234"
MODEL_NAME: str = "model"
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
        debug(f"[ERREUR] Chargement de {path}: {exc}", color="red")
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
        debug(f"[WARN] Impossible de vérifier les modèles chargés: {exc}", color="yellow")
        return False


def jit_load_model(system_prompt: str | None = None) -> bool:
    """Force a JIT load by sending a dummy request with TTL."""
    debug("[INFO] Tentative de JIT load…", color="cyan")
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
        debug(f"[ERREUR] JIT load a échoué: {exc}", color="red")
        return False


def cli_load_model() -> bool:
    """Try `lms load MODEL_NAME` via the CLI."""
    debug("[INFO] Tentative de chargement via lms CLI…", color="cyan")
    try:
        completed = subprocess.run([
            "lms", "load", MODEL_NAME, "-y"
        ], check=False, capture_output=True, text=True, shell=True)
        if completed.returncode == 0:
            return True
        debug(completed.stderr or completed.stdout, color="yellow")
    except FileNotFoundError:
        debug("[WARN] lms.exe non trouvé dans PATH.", color="yellow")
    return False


def ensure_model_loaded(strategy: str, system_prompt: str | None = None) -> None:
    """Ensure the model is loaded according to *strategy*."""
    if is_model_loaded():
        return
    if strategy == "off":
        debug("[INFO] Modèle non chargé et stratégie=off — on continue.", color="yellow")
        return
    loaded = False
    if strategy == "jit":
        loaded = jit_load_model(system_prompt)
    elif strategy == "cli":
        loaded = cli_load_model()
    if not loaded:
        debug("[ERREUR] Impossible de charger le modèle automatiquement.", color="red")

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
        debug(f"[ERREUR] Requête LM Studio: {exc}", color="red")
        return ""

# -------------------- Hotkey -------------------- #

def handle_hotkey(system_prompt: str, load_strategy: str) -> None:
    prompt = pyperclip.paste()
    if not prompt.strip():
        debug("[INFO] Presse‑papier vide.", color="yellow")
        return

    ensure_model_loaded(load_strategy, system_prompt)

    debug("[INFO] Envoi à LM Studio…", color="cyan")
    answer = query_lm(prompt, system_prompt)

    if answer:
        pyperclip.copy(answer)
        debug("[OK] Réponse copiée ✔\n", color="green")
    else:
        debug("[KO] Aucune réponse.\n", color="red")

# -------------------- Main -------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Hotkey → LM Studio", formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument(
        "--load-strategy",
        choices=["jit", "cli", "off"],
        default="jit",
        help="Stratégie de chargement automatique du modèle si absent.",
    )

    args = parser.parse_args()

    config = load_config(CONFIG_FILE)

    host = config.get("host", "127.0.0.1")
    port = config.get("port", 1234)
    model = config.get("model", "model")

    global LM_STUDIO_HOST, MODEL_NAME
    LM_STUDIO_HOST = f"http://{host}:{port}"
    MODEL_NAME = model

    debug(f"[CONFIG] Host : {LM_STUDIO_HOST}", color="blue")
    debug(f"[CONFIG] Modèle : {MODEL_NAME}\n", color="blue")

    hotkeys = config.get("hotkeys", [])
    if not hotkeys:
        debug("[WARN] Aucune hotkey définie.", color="yellow")
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
            debug(f"[ERREUR] Lecture de {prompt_file}: {exc}", color="red")
            continue

        keyboard.add_hotkey(
            keys,
            lambda sp=system_prompt: threading.Thread(
                target=handle_hotkey,
                args=(sp, args.load_strategy),
                daemon=True,
            ).start(),
        )

    debug("LM Studio Hotkey actif ! (Ctrl+C pour quitter)\n", color="magenta")

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        debug("Arrêt du script…", color="cyan")


if __name__ == "__main__":
    main()
