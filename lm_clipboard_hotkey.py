#!/usr/bin/env python3
"""
lm_clipboard_hotkey.py — Interagit avec LM Studio via un raccourci clavier.

Nouveautés v1.3
===============
✔︎ **Prompt système** via `-s/--system-prompt` ou `-f/--system-prompt-file` (inchangé).
✔︎ **Auto‑load** : si le modèle n’est pas chargé, le script peut :
     • tenter un JIT‑load (requête d’amorçage)
     • ou invoquer `lms load` (CLI) quand disponible.

Usage rapide
------------
```bash
# Prompt système inline + auto‑load (défaut)
python lm_clipboard_hotkey.py -s "Réponds toujours en français."

# Spécifier la stratégie de chargement
python lm_clipboard_hotkey.py --load-strategy jit        # (par défaut)
python lm_clipboard_hotkey.py --load-strategy cli        # utilise lms.exe
python lm_clipboard_hotkey.py --load-strategy off        # ne tente rien
```

Dépendances :
    pip install requests pyperclip keyboard colorama
    # et installer le CLI : https://lmstudio.ai/docs/cli
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Final, List

import keyboard  # Gère les raccourcis clavier
import pyperclip  # Presse-papier
import requests  # HTTP
from colorama import Fore, Style  # Couleurs console

LM_STUDIO_HOST: Final[str] = "http://10.0.0.211:1234"
MODEL_NAME: Final[str] = "qwen3b-30b-a3b"
TIMEOUT: Final[int] = 120  # s
HOTKEY: Final[str] = "ctrl+shift+1"

# -------------------- Utils -------------------- #

def debug(msg: str, *, color: str = "") -> None:
    col = getattr(Fore, color.upper(), "")
    print(f"{col}{msg}{Style.RESET_ALL}")

# -------------------- Chargement modèle -------------------- #

def is_model_loaded() -> bool:
    """Retourne True si MODEL_NAME est déjà chargé (en fonction de JIT)."""
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
    """Force un JIT load en envoyant une requête bidon avec TTL."""
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
        "ttl": 300,  # 5 min pour ne pas bloquer mémoire
    }
    try:
        requests.post(f"{LM_STUDIO_HOST}/v1/chat/completions", json=payload, timeout=TIMEOUT)
        return is_model_loaded()
    except Exception as exc:
        debug(f"[ERREUR] JIT load a échoué: {exc}", color="red")
        return False


def cli_load_model() -> bool:
    """Tente `lms load MODEL_NAME` via le CLI."""
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
    """Selon *strategy*, s’assure que le modèle est en mémoire."""
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

def load_system_prompt(args: argparse.Namespace) -> str:
    if args.system_prompt_file:
        path = Path(args.system_prompt_file).expanduser().resolve()
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            debug(f"[ERREUR] Lecture du fichier {path}: {exc}", color="red")
            return ""
    if args.system_prompt:
        return args.system_prompt.strip()
    return os.getenv("LM_SYSTEM_PROMPT", "").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Hotkey → LM Studio", formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-s", "--system-prompt", help="Prompt système inline.")
    group.add_argument("-f", "--system-prompt-file", help="Fichier texte contenant le prompt système.")

    parser.add_argument(
        "--load-strategy",
        choices=["jit", "cli", "off"],
        default="jit",
        help="Stratégie de chargement automatique du modèle si absent.",
    )

    args = parser.parse_args()
    system_prompt = load_system_prompt(args)

    if system_prompt:
        preview = (system_prompt[:60] + "…") if len(system_prompt) > 60 else system_prompt
        debug(f"[CONFIG] Prompt système : {preview!r}\n", color="blue")

    debug("LM Studio Hotkey actif ! (Ctrl+C pour quitter)\n", color="magenta")

    keyboard.add_hotkey(HOTKEY, lambda: threading.Thread(target=handle_hotkey, args=(system_prompt, args.load_strategy), daemon=True).start())

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        debug("Arrêt du script…", color="cyan")


if __name__ == "__main__":
    main()
