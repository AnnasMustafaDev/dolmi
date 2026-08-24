"""Dolmi's floating caption bar — a frameless, always-on-top window that mirrors the live
subtitles. Created on demand (Api.show_overlay); excluded from screen capture in invisible mode.
Its × button hides it; move/resize are handled in JS and applied here via the js_api.
"""
from pathlib import Path

import webview

WEB = Path(__file__).resolve().parent / "web"
MIN_W, MIN_H = 360, 110


class OverlayApi:
    """Exposed to overlay.html. State is private: pywebview exposes public attributes to the page."""

    def __init__(self, on_hide=None):
        self._window = None
        self._on_hide = on_hide

    def geometry(self):
        """Window size in *logical* pixels — the same units resize() takes and that the page's
        mouse deltas use, so resizing stays correct at 125 %/150 % display scaling."""
        try:
            w = self._window
            if w and w.width:
                return {"w": int(w.width), "h": int(w.height)}
        except Exception:
            pass
        return {"w": 640, "h": 200}

    def resize(self, w, h):
        try:
            if self._window:
                self._window.resize(max(int(w), MIN_W), max(int(h), MIN_H))
        except Exception:
            pass
        return True

    def hide(self):
        try:
            if self._window:
                self._window.hide()
        except Exception:
            pass
        if self._on_hide:
            self._on_hide()
        return True


