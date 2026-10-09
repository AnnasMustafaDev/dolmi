"""Dolmi (webview UI) — entry point.

A pywebview/WebView2 window hosting ui/web/index.html, bridged to the core modules at the
repo root (live_subs, assistant, inbox, models, stealth).

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
        logs = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Dolmi"
        logs.mkdir(parents=True, exist_ok=True)
        log = open(logs / "dolmi.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    else:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    print(f"\n--- Dolmi (webview) started {datetime.now():%Y-%m-%d %H:%M:%S}")


def main():
    _setup_output()
    os.chdir(ROOT)                                            # core resolves relative paths from here
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:  # own taskbar entry + icon instead of inheriting pythonw's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Dolmi.App")
    except (AttributeError, OSError):
        pass

    from ui import app as app_mod
    import stealth

    api = app_mod.Api()
    window = webview.create_window(
        "Dolmi", url=str((WEB / "index.html").as_uri()), js_api=api,
        width=1180, height=780, min_size=(900, 600), background_color="#F2ECDF",
        text_select=True)   # captions, transcripts and answers must be selectable/copyable
    api._window = window

    def on_start():
        # apply a saved "invisible" setting once the window really exists (it has no HWND before)
        try:
            window.events.shown.wait(10)
        except Exception:
            pass
        if api._settings.get("invisible"):
            api.toggle_invisible(True)
        # a system-wide hotkey always brings Dolmi back, even hidden from the taskbar
        stealth.GlobalHotkey(
            stealth.MOD_CONTROL | stealth.MOD_ALT | stealth.MOD_SHIFT, 0x44,  # Ctrl+Alt+Shift+D
            api.recover, name="show Dolmi (Ctrl+Alt+Shift+D)").start()

    icon = ROOT / "assets" / "dolmi-2.ico"
    webview.start(on_start, gui="edgechromium", private_mode=False, icon=str(icon))


if __name__ == "__main__":
    main()
