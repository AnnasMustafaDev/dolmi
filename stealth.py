"""Screen-share privacy for Dolmi's windows (Windows only).

"Invisible mode" keeps a window fully visible to the person sitting at the PC, but leaves it
out of screen shares and recordings, and off the taskbar and Alt+Tab. It uses
SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) — the same documented Windows API password
managers and banking apps use to keep their windows out of screenshots.

It does NOT hide anything from Task Manager. Doing that needs rootkit-style process hiding
(hooking system calls or a kernel driver), which antivirus and Smart App Control treat as
malware — and it isn't needed, because screen sharing shows the shared screen, not Task Manager.
"""
import ctypes
import threading
from ctypes import wintypes

WDA_NONE = 0x00
WDA_MONITOR = 0x01             # window shows as black in captures (fallback for old Windows)
WDA_EXCLUDEFROMCAPTURE = 0x11  # window is simply absent from captures (Windows 10 2004+)
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080  # off the taskbar and Alt+Tab
WS_EX_APPWINDOW = 0x00040000   # forces a taskbar button

_user32 = ctypes.windll.user32
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.SetWindowDisplayAffinity.restype = wintypes.BOOL


def _hwnd(window):
    """The real top-level window handle behind a Tk/CustomTkinter window."""
    window.update_idletasks()
    return _user32.GetAncestor(window.winfo_id(), GA_ROOT)


def exclude_from_capture(window, on):
    """Hide `window` from screen capture (True) or show it again (False). Returns True on success.

    Only WDA_EXCLUDEFROMCAPTURE is used: the window is simply absent from the capture and whatever
    is behind it shows through. The WDA_MONITOR "black box" mode is never used, so a shared screen
    never shows a black or coloured rectangle where Dolmi is — on an unsupported Windows this just
    returns False and the caller leaves invisible mode off instead.
    """
    hwnd = _hwnd(window)
    affinity = WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE
    return bool(_user32.SetWindowDisplayAffinity(hwnd, affinity))


def hide_from_taskbar(window, on):
    """Keep `window` off the taskbar and Alt+Tab (True) or restore it (False)."""
    hwnd = _hwnd(window)
    get = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style = (get | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW if on else (get | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
    # the window must be hidden while the taskbar style changes, or Windows ignores it
    was_mapped = window.winfo_viewable()
    if was_mapped:
        window.withdraw()
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    if was_mapped:
        window.deiconify()


_SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED

def mark_toolwindow(window):
    """Force `window` off the taskbar and Alt+Tab without withdrawing it (no flicker, safe to call on
    every show). A borderless bar can otherwise pick up a taskbar button when it is re-shown."""
    hwnd = _hwnd(window)
    ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    want = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    if want != ex:
        _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, want)
    _user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP)   # tell the shell the frame changed


def apply(window, invisible, taskbar=True):
    """Apply (or clear) invisible mode on one window. `taskbar` False leaves the taskbar button alone.

    Taskbar hiding withdraws and re-shows the window, which resets its capture affinity, so the
    capture exclusion is always applied LAST.
    """
    if taskbar:
        try:
            hide_from_taskbar(window, invisible)
        except Exception as e:   # the capture exclusion is what matters; taskbar is a nicety
            print(f"Invisible mode: could not change taskbar for a window ({e})")
    return exclude_from_capture(window, invisible)


# ------------------------------------------------------------------ global hotkey (always-works escape hatch)
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x0001, 0x0002, 0x0004
MOD_NOREPEAT = 0x4000


class GlobalHotkey(threading.Thread):
    """A system-wide hotkey, so Dolmi can always be turned visible again even when it is hidden from
    the taskbar and Alt+Tab and buried behind other windows. `on_press` runs on this thread, so it
    should only hand work back to the UI thread (e.g. Tk's `after`)."""

    def __init__(self, mods, vk, on_press, name="hotkey"):
        super().__init__(daemon=True)
        self.mods, self.vk, self.on_press, self.hotkey_name = mods, vk, on_press, name
        self.registered = threading.Event()
        self.ok = False

    def run(self):
        if not _user32.RegisterHotKey(None, 1, self.mods | MOD_NOREPEAT, self.vk):
            print(f"Dolmi: could not register the {self.hotkey_name} global hotkey (another app may use it)")
            self.registered.set()
            return
        self.ok = True
        self.registered.set()
        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.on_press()
                except Exception as e:
                    print(f"Dolmi hotkey handler error: {e}")
