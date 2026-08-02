"""Dolmi subtitle bar: always on top, drag to move, drag any edge to resize."""
import re
import tkinter as tk

import customtkinter as ctk

from theme import FONT, OVERLAY as C

RESIZE_CURSORS = {"e": "size_we", "w": "size_we", "n": "size_ns", "s": "size_ns",
                  "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw"}
EDGE, MIN_W, MIN_H = 8, 360, 110   # logical px, multiplied by display scaling
HISTORY = 12                        # previous sentences kept above the live line (as many as fit)
MIN_ANSWER_PT = 10                  # Assistant answers shrink down to this size to fit, then scroll

def plain(markdown):
    """Assistant answers are Markdown; the bar shows them as plain lines with • bullets."""
    lines, code = [], False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            code = not code
            continue
        if code:   # keep indentation: Python needs it to read right
            lines.append(line.rstrip())
            continue
        line = re.sub(r"^#+\s*", "", re.sub(r"^[-*]\s+(\[ \]\s*)?", "• ", line.strip()))
        lines.append(line.replace("**", "").replace("`", ""))
    return "\n".join(l for l in lines if l)   # no blank lines: every line of the bar counts

class Overlay(tk.Toplevel):
    def __init__(self, master, settings, on_open_app, on_change):
        super().__init__(master)
        self.settings, self.on_change = settings, on_change
        # CustomTkinter makes the process DPI-aware, so plain tk sizes are physical pixels
        self.k = ctk.ScalingTracker.get_window_scaling(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", settings["opacity"])
        self.configure(bg=C["bg"], highlightthickness=1, highlightbackground=C["border"])
        sw, sh = self.winfo_screenwidth() * self.k, self.winfo_screenheight() * self.k
        self.geometry(settings.get("overlay_geometry")
                      or f"{int(sw * .62)}x{self.px(230)}+{int(sw * .19)}+{int(sh - self.px(320))}")
        self.min_w, self.min_h = self.px(MIN_W), self.px(MIN_H)
        self.minsize(self.min_w, self.min_h)

        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x", padx=10, pady=(6, 0))
        self.dot = tk.Label(bar, text="●", fg=C["muted"], bg=C["bg"], font=self.font(9))
        self.dot.pack(side="left")
        tk.Label(bar, text="Dolmi", fg=C["muted"], bg=C["bg"], font=self.font(9, "bold")).pack(side="left", padx=(4, 0))
        for text, cmd in (("✕", self.hide), ("↗", on_open_app), ("", self.toggle_german),
                          ("A+", lambda: self.bump_font(2)), ("A−", lambda: self.bump_font(-2))):
            tool = self._tool(bar, text, cmd)
            tool.pack(side="right", padx=1)
            if cmd == self.toggle_german:
                self.lang_tool = tool   # shows the spoken language; toggles the original line
        self.set_language(settings.get("language", "de"))

        # Subtitles stack from the bottom: original line, live line, then older sentences above
        self.body = tk.Frame(self, bg=C["bg"])
        self.body.pack(fill="both", expand=True, pady=(0, 8))
        self.de = self._line(C["german"], -9)
        self.cur = self._line(C["text"], 0, "bold")
        self.hist = [self._line(C["text"], -3), self._line(C["muted"], -5)] + \
                    [self._line(C["faint"], -6) for _ in range(HISTORY - 2)]
        self.history = []   # finished English sentences, newest last

        # Assistant answers: the whole answer, as large as fits the bar (scrolls only at the minimum size)
        self.answer = tk.Text(self, bg=C["bg"], fg=C["text"], relief="flat", bd=0, highlightthickness=0,
                              wrap="word", padx=self.px(16), pady=0, cursor="fleur", state="disabled")
        self.answer.tag_configure("q", foreground=C["german"])
        self.answer.bindtags((str(self.answer), str(self), "all"))   # no text selection: dragging moves the bar
        self.answer.bind("<MouseWheel>", lambda e: self.answer.yview_scroll(-e.delta // 120, "units"))
        self.answer_q, self.answer_size = None, None
        self.apply_settings()
        self.idle("Ready — press Start in Dolmi")

        self._drag = {}
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: self._remember())
        self.bind("<Motion>", lambda e: self.config(cursor=RESIZE_CURSORS.get(self._edge(e), "fleur")))
        self.bind("<Configure>", self._rewrap)
        self.bind("<Escape>", lambda e: self.hide())

    # --- sizing
    def px(self, n):
        return round(n * self.k)

    def font(self, pt, weight="normal"):
        return (FONT, -round(pt * 96 / 72 * self.k), weight)

    # --- widgets
    def _tool(self, parent, text, cmd):
        b = tk.Label(parent, text=text, fg=C["muted"], bg=C["bg"], font=self.font(10, "bold"),
                     padx=7, cursor="hand2")
        b.bind("<Enter>", lambda e: b.config(fg=C["text"], bg=C["surface2"]))
        b.bind("<Leave>", lambda e: b.config(fg=C["muted"], bg=C["bg"]))
        b.bind("<ButtonRelease-1>", lambda e: cmd())
        return b

    def _line(self, color, size_delta, weight="normal"):
        l = tk.Label(self.body, fg=color, bg=C["bg"], justify="left", anchor="w")
        l.size_delta, l.weight, l.full = size_delta, weight, ""
        return l

    # --- content
    def show_final(self, de, en):
        self._show_subtitles()
        self.history = (self.history + [en])[-HISTORY:]
        self._put(self.cur, "")
        self._put(self.de, de)
        self._render()

    def show_draft(self, de, en):
        self._show_subtitles()
        self.cur.config(fg=C["draft"])
        self._put(self.cur, en + " …")
        self._put(self.de, de)
        self._render()

    def idle(self, message):
        self._show_subtitles()
        self.history = []
        self.cur.config(fg=C["muted"])
        self._put(self.cur, message)
        self._put(self.de, "")
        self._render()

    def show_answer(self, question, answer, done=False):
        """The Assistant's whole answer under its question, shrunk only as far as needed to fit.
        While it streams the size only goes down; once done it is fitted fresh, so it ends as large as fits."""
        if not self.answer.winfo_ismapped():
            self.body.pack_forget()
            self.answer.pack(fill="both", expand=True, pady=(4, 8))
        new = question != self.answer_q
        self.answer_q = question
        t = self.answer
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("end", question + "\n", "q")
        t.insert("end", plain(answer))
        t.config(state="disabled")
        self._fit_answer(reset=new or done)

    def _show_subtitles(self):
        if self.answer.winfo_ismapped():
            self.answer.pack_forget()
            self.body.pack(fill="both", expand=True, pady=(0, 8))
            self.answer_q = None

    def _fit_answer(self, reset=False):
        # While an answer streams in it only grows, so the size only ever goes down from where it is
        size = self.settings["font"] if reset or self.answer_size is None else self.answer_size
        t = self.answer
        while True:
            t.config(font=self.font(size, "bold"), spacing3=self.px(4))
            t.tag_configure("q", font=self.font(max(size - 6, 9)))
            t.update_idletasks()
            height = t.count("1.0", "end", "update", "ypixels")   # an int, a 1-tuple or None by Python version
            height = (height[0] if isinstance(height, tuple) else height) or 0
            if size <= MIN_ANSWER_PT or height <= t.winfo_height():
                break
            size -= 1
        self.answer_size = size
        t.yview_moveto(0)

    @staticmethod
    def _put(label, text):
        label.full = text   # the label may show only the end of it when it doesn't fit
        label.config(text=text)

    @staticmethod
    def _fit(label, room):
        """Show as much of the label's text as fits `room` px; the oldest words go first, like live captions."""
        label.config(text=label.full)
        if label.winfo_reqheight() <= room:
            return True
        words = label.full.split()
        lo, hi = 1, len(words)   # smallest start word that still fits
        while lo < hi:
            mid = (lo + hi) // 2
            label.config(text="… " + " ".join(words[mid:]))
            if label.winfo_reqheight() <= room:
                hi = mid
            else:
                lo = mid + 1
        if lo >= len(words):
            return False   # not even one line of room
        label.config(text="… " + " ".join(words[lo:]))
        return label.winfo_reqheight() <= room

    def _render(self):
        """Fill the bar bottom-up for the current size and font: the newest line always shows (trimmed
        from the front if it's too long), then the original-language line, then older sentences."""
        self.update_idletasks()
        for l in [self.de, self.cur] + self.hist:
            l.pack_forget()
        for i, label in enumerate(self.hist):   # newest finished sentence first
            self._put(label, self.history[-1 - i] if i < len(self.history) else "")
        room = self.body.winfo_height() or self.winfo_height()
        stack = [l for l in [self.cur] + self.hist if l.full]
        shown = []
        if stack and self._fit(stack[0], room):
            shown.append(stack[0])
            room -= stack[0].winfo_reqheight()
        if self.settings["show_german"] and self.de.full and self._fit(self.de, room):
            shown.insert(0, self.de)
            room -= self.de.winfo_reqheight()
        for label in stack[1:]:
            if label.winfo_reqheight() > room:
                break
            room -= label.winfo_reqheight()
            shown.append(label)
        for l in shown:
            l.pack(side="bottom", fill="x", padx=16)

    def set_language(self, code):
        self.lang_tool.config(text="ORIG" if code == "auto" else code.upper())

    def set_live(self, live):
        self.dot.config(fg=C["live"] if live else C["muted"])

    # --- settings
    def apply_settings(self):
        s = self.settings
        for l in [self.cur, self.de] + self.hist:
            l.config(font=self.font(max(s["font"] + l.size_delta, 9), l.weight))
        self.attributes("-alpha", s["opacity"])
        self._render()
        if self.answer.winfo_ismapped():
            self._fit_answer(reset=True)

    def bump_font(self, delta):
        self.settings["font"] = min(max(self.settings["font"] + delta, 12), 48)
        self.apply_settings(); self.on_change()

    def toggle_german(self):
        self.settings["show_german"] = not self.settings["show_german"]
        self.apply_settings(); self.on_change()

    def hide(self):
        self.withdraw()
        self.settings["overlay"] = False
        self.on_change()

    def reveal(self):
        self.deiconify(); self.lift()
        self.settings["overlay"] = True
        self.on_change()

    # --- move / resize
    def _edge(self, e):
        x, y = e.x_root - self.winfo_rootx(), e.y_root - self.winfo_rooty()
        ns = "n" if y < EDGE else "s" if y > self.winfo_height() - EDGE else ""
        we = "w" if x < EDGE else "e" if x > self.winfo_width() - EDGE else ""
        return ns + we

    def _press(self, e):
        self._drag = dict(mode=self._edge(e), x=e.x_root, y=e.y_root, gx=self.winfo_x(),
                          gy=self.winfo_y(), w=self.winfo_width(), h=self.winfo_height())

    def _motion(self, e):
        d = self._drag
        if not d:
            return
        m, dx, dy = d["mode"], e.x_root - d["x"], e.y_root - d["y"]
        x, y, w, h = d["gx"], d["gy"], d["w"], d["h"]
        if not m:
            self.geometry(f"+{x + dx}+{y + dy}")
            return
        if "e" in m: w = max(w + dx, self.min_w)
        if "s" in m: h = max(h + dy, self.min_h)
        if "w" in m: w = max(d["w"] - dx, self.min_w); x = d["gx"] + d["w"] - w
        if "n" in m: h = max(d["h"] - dy, self.min_h); y = d["gy"] + d["h"] - h
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _remember(self):
        self.settings["overlay_geometry"] = self.geometry()
        self.on_change()

    def _rewrap(self, e):
        # Windows sends <Configure> for every pixel the bar moves; only a size change needs work,
        # otherwise each drag step repacks all lines and the bar flickers.
        if e.widget is not self or (e.width, e.height) == getattr(self, "_size", None):
            return
        self._size = (e.width, e.height)
        for l in [self.cur, self.de] + self.hist:
            l.config(wraplength=max(e.width - self.px(36), 100))
        if not getattr(self, "_render_queued", False):   # one repack per burst of resize events
            self._render_queued = True
            self.after_idle(self._render_once)

    def _render_once(self):
        self._render_queued = False
        self._render()
        if self.answer.winfo_ismapped():
            self._fit_answer(reset=True)
