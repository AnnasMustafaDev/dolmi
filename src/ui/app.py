"""Dolmi webview UI — the JS bridge (Api exposed as window.pywebview.api).

Reuses the existing core (live_subs, assistant, inbox, config) untouched. Python pushes to the page by
calling window.dolmi.<fn>(...) through evaluate_js; the page calls Python through
window.pywebview.api.<method>.

All state is held in underscore attributes on purpose: pywebview exposes every *public* attribute of
the js_api object to page JavaScript (recursively), which would hand the page the window object, the
Whisper model, the settings with the encrypted keys, and the database handle.
"""
import ctypes
import itertools
import json
import queue
import re
import threading
import time
from datetime import datetime

import config
import assistant
import inbox
import live_subs

# Win32 bits for invisible mode (stealth.py's helpers are tkinter-only; these act on a raw HWND)
WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
_SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
_u = ctypes.windll.user32

_MEETING_RE = re.compile(r"\*\*(.+?)\*\*\s+(.*?)\s*\n<sub>(.*?)</sub>", re.S)
# settings the page may change through set_setting (provider, keys and invisible have their own calls)
SETTABLE = {"language", "show_german", "save_transcripts", "keep_days", "audio_device", "model",
            "overlay", "theme", "ai_length", "ai_context"}


def _js(v):
    return json.dumps(v)


def _native_hwnd(win, title):
    """Top-level HWND for a pywebview window: .native first, window title as the fallback."""
    try:
        native = getattr(win, "native", None)
        if native is not None:
            return int(native.Handle.ToInt32())
    except Exception:
        pass
    try:
        return _u.FindWindowW(None, title) if win else 0
    except Exception:
        return 0


def _set_toolwindow(hwnd, on):
    """Off the taskbar and Alt+Tab (on) or back on them (off)."""
    if not hwnd:
        return
    try:
        ex = _u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        want = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW if on else (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
        if want != ex:
            _u.SetWindowLongW(hwnd, GWL_EXSTYLE, want)
            _u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP)
    except Exception as e:
        print(f"invisible: taskbar style failed ({e})")


def _set_capture(hwnd, hidden):
    """Leave the window out of screen shares/recordings (hidden) or include it again."""
    if not hwnd:
        return False
    try:
        return bool(_u.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if hidden else WDA_NONE))
    except Exception as e:
        print(f"invisible: capture affinity failed ({e})")
        return False


