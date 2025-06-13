# LCH

`lm_clipboard_hotkey.py` interacts with LM Studio via a keyboard shortcut and
copies the answer back to your clipboard. Models are loaded on demand using the
Just‑in‑Time feature introduced in LM Studio 0.3.6.

This script was designed for Windows. On Linux hotkeys rely on the
`keyboard` package, which needs access to `/dev/uinput` and often root
privileges. Even with these, hotkey support may be unreliable.

## What's new 

- **System prompt** via `-s/--system-prompt` or `-f/--system-prompt-file`.
- **Auto-load**: missing models are loaded automatically on the first request.
- **Timeout**: set `LMSTUDIO_TIMEOUT` to tweak the wait time (default 60s).
- **Model selection** via `--model-id`.
- **Auto-paste**: use `--auto-paste` to paste with OS defaults (override with `--paste-keys`).
- **Auto-copy**: use `--auto-copy` to copy the selection before sending.
- **Explain**: sample hotkey to explain the meaning of the clipboard text.
- **Run once**: use `--run-hotkey N` to trigger hotkey `N` then exit.

## Quick start

```bash
# Inline system prompt + auto-load (default)
python lm_clipboard_hotkey.py -s "Always answer in French."

# Specify the loading strategy
python lm_clipboard_hotkey.py --load-strategy jit        # (default)
python lm_clipboard_hotkey.py --load-strategy cli        # uses lms.exe
python lm_clipboard_hotkey.py --load-strategy off        # do nothing
python lm_clipboard_hotkey.py --auto-paste              # paste using OS default
python lm_clipboard_hotkey.py --paste-keys "ctrl+shift+v" # custom combo
python lm_clipboard_hotkey.py --auto-copy               # copy selection
python lm_clipboard_hotkey.py --model-id MyModel        # override config
python lm_clipboard_hotkey.py --run-hotkey 1            # run #1 and exit
```

### Dependencies

```
pip install requests pyperclip keyboard colorama
# install the CLI: https://lmstudio.ai/docs/cli
```

### Configuration

Copy `config.example.json` to `config.json` and edit the values
to suit your environment. The `config.json` file is ignored by Git so
that your personal settings are preserved on updates.
You may also define `LMSTUDIO_TIMEOUT` to adjust the maximum wait time
for the initial model load.
