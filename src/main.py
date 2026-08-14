"""Dolmi (webview UI) — entry point.

The Bridge port: a pywebview/WebView2 shell hosting ui/web/index.html, bridged to the core
(live_subs/assistant/inbox/models, which stay at the repo root). The old tkinter app.py is left
intact and still runnable until this reaches parity.

Run:  venv\\Scripts\\pythonw.exe src\\main.py   (or python for a console)
"""
import ctypes
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root: core modules + assets live here
sys.path.insert(0, str(ROOT))                          # reuse live_subs, assistant, inbox, stealth, ...
sys.path.insert(0, str(Path(__file__).resolve().parent))  # src/ for the ui package

import webview

WEB = Path(__file__).resolve().parent / "ui" / "web"


def _setup_output():
    """The core prints status lines with emoji (📥, 🎧). Under pythonw there's no console, and a
    Windows console defaults to cp1252 — either way an emoji print raises 'charmap can't encode'
    and aborts model loading. Log to a UTF-8 file without a console; force UTF-8 with one."""
    if sys.stdout is None or sys.stderr is None:
        log = open(ROOT / "dolmi.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    else:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    print(f"\n--- Dolmi (webview) started {datetime.now():%Y-%m-%d %H:%M:%S}")


