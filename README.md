# LCH

`lm_clipboard_hotkey.py` interacts with LM Studio via a keyboard shortcut and
copies the answer back to your clipboard.

## What's new 

- **System prompt** via `-s/--system-prompt` or `-f/--system-prompt-file`.
- **Auto-load**: if the model isn't loaded, the script can attempt a JIT warm-up
  or call `lms load` when available.
- **Auto-paste**: use `--auto-paste` to send Ctrl+V after copying.

## Quick start

```bash
# Inline system prompt + auto-load (default)
python lm_clipboard_hotkey.py -s "Always answer in French."

# Specify the loading strategy
python lm_clipboard_hotkey.py --load-strategy jit        # (default)
python lm_clipboard_hotkey.py --load-strategy cli        # uses lms.exe
python lm_clipboard_hotkey.py --load-strategy off        # do nothing
python lm_clipboard_hotkey.py --auto-paste              # send Ctrl+V
```

### Dependencies

```
pip install requests pyperclip keyboard colorama
# install the CLI: https://lmstudio.ai/docs/cli
```
