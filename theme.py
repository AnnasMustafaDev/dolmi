"""Dolmi colours and font, shared by the main window and the subtitle bar."""
import ctypes
from pathlib import Path

# "Paper Light": warm off-white canvas, white cards, one deep-teal accent. Every text pairing ≥ 4.5:1 (WCAG AA).
C = {
    "bg": "#F4F4F1",        # window background
    "surface": "#FFFFFF",   # sidebar, cards
    "surface2": "#EFEFEB",  # hover, inputs, secondary buttons
    "border": "#E3E3DE",
    "border_strong": "#CFCFC8",
    "text": "#16181D",
    "muted": "#4B505C",
    "accent": "#0F766E",    # Dolmi teal, deep enough for white text on it
    "accent_hover": "#115E59",
    "accent_text": "#FFFFFF",
    "accent_subtle": "#E1F3F0",   # selected nav item
    "accent_select": "#C4E6DF",   # selected segment / tab
    "draft": "#9A5B00",     # not-yet-final subtitle
    "german": "#2B5FA3",
    "live": "#C81E3A",
    "live_hover": "#A3172F",
    "ok": "#11804F",
    "saved_bg": "#E1F3F0",
}

# The subtitle bar floats over videos and meeting windows, so it stays dark whatever the app theme is.
OVERLAY = {
    "bg": "#12151C", "surface2": "#1F2430", "border": "#353B49",
    "text": "#ECEEF3", "muted": "#A3AABB", "faint": "#7A8294",
    "german": "#7DA7D9", "draft": "#FBBF24", "live": "#F43F5E", "ok": "#34D399",
}

def _load_geist():
    """Registers the bundled Geist font for this process only (no install, no admin)."""
    fonts = Path(__file__).resolve().parent / "assets" / "fonts"
    try:
        added = [ctypes.windll.gdi32.AddFontResourceExW(str(f), 0x10, 0)   # 0x10 = FR_PRIVATE
                 for f in fonts.glob("Geist-*.ttf")]
    except (AttributeError, OSError) as e:
        print(f"Geist font not loaded ({e}); using Segoe UI")
        return "Segoe UI"
    return "Geist" if added and all(added) else "Segoe UI"

FONT = _load_geist()
MONO = "Cascadia Mono"
