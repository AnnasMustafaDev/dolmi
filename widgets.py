"""Lightweight Tk widgets for long lists. CustomTkinter widgets each draw on their own canvas,
which makes a scrolling list of dozens of them repaint slowly and smear; these are plain labels."""
import tkinter as tk

from theme import C

class LiteButton(tk.Label):
    """Flat button: hover highlight, disabled state, swappable command."""
    STYLES = {"primary": (C["accent"], C["accent_hover"], C["accent_text"]),
              "secondary": (C["surface2"], C["border"], C["text"])}

    def __init__(self, parent, text, command=None, style="secondary", font=None, width=None):
        self.bg, self.hover_bg, self.fg = self.STYLES[style]
        super().__init__(parent, text=text, bg=self.bg, fg=self.fg, font=font, padx=14, pady=6,
                         cursor="hand2", width=width or 0)
        self.command, self.enabled = command, True
        self.bind("<Enter>", lambda e: self.enabled and self.config(bg=self.hover_bg))
        self.bind("<Leave>", lambda e: self.config(bg=self.bg if self.enabled else C["surface"]))
        self.bind("<ButtonRelease-1>", lambda e: self.enabled and self.command and self.command())

    def set(self, text=None, enabled=None, command=None):
        if text is not None:
            self.config(text=text)
        if command is not None:
            self.command = command
        if enabled is not None:
            self.enabled = enabled
            self.config(bg=self.bg if enabled else C["surface"],
                        fg=self.fg if enabled else C["muted"], cursor="hand2" if enabled else "")

class LiteProgress(tk.Frame):
    """Thin progress bar."""
    def __init__(self, parent, height=4):
        super().__init__(parent, bg=C["surface2"], height=height)
        self.fill = tk.Frame(self, bg=C["accent"])

    def set(self, fraction):
        self.fill.place(x=0, y=0, relheight=1, relwidth=max(min(fraction, 1), 0))

def badge(parent, text, color, font):
    tk.Label(parent, text=text, bg=C["surface2"], fg=color, font=font, padx=8, pady=1).pack(side="left", padx=(0, 6))
