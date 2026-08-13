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


