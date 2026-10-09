"""Dolmi (webview UI) — entry point.

A pywebview/WKWebView window hosting ui/web/index.html, bridged to the core modules at the
Mac folder's root (live_subs, assistant, inbox, models, stealth).

Run:  ./start.command   (or venv/bin/python src/main.py for a console)
"""
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
    """The core prints status lines with emoji (📥, 🎧). Launched from Finder (Dolmi.app) there's no
    terminal, so log to a UTF-8 file; with a terminal, force UTF-8 so an emoji print never aborts
    model loading."""
    if sys.stdout is None or sys.stderr is None or not sys.stdout.isatty():
        logs = Path.home() / "Library" / "Logs" / "Dolmi"
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

    from ui import app as app_mod
    import stealth

    api = app_mod.Api()

    def make_window(x=None, y=None, width=1180, height=780):
        return webview.create_window(
            "Dolmi", url=str((WEB / "index.html").as_uri()), js_api=api, x=x, y=y,
            width=width, height=height, min_size=(900, 600), background_color="#F2ECDF",
            text_select=True)   # captions, transcripts and answers must be selectable/copyable

    window = make_window()
    api._window = window
    api._make_window = make_window   # invisible off rebuilds the window (see stealth.py)

    def on_start():
        # apply a saved "invisible" setting once the window really exists (it has no NSWindow before)
        try:
            window.events.shown.wait(10)
        except Exception:
            pass
        if api._settings.get("invisible"):
            api.toggle_invisible(True)
        # a system-wide hotkey always brings Dolmi back, even hidden from the Dock
        stealth.GlobalHotkey(
            stealth.MOD_CONTROL | stealth.MOD_ALT | stealth.MOD_SHIFT, stealth.VK_D,  # ⌃⌥⇧D
            api.recover, name="show Dolmi (⌃⌥⇧D)").start()

    webview.start(on_start, gui="cocoa", private_mode=False, icon=str(app_mod.ICON))


if __name__ == "__main__":
    main()
