"""Dolmi — live meeting subtitles into English (German by default), private and on your PC."""
import ctypes, json, math, os, queue, shutil, sys, threading, time, traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

import assistant
import inbox
import models
import stealth
from overlay import Overlay
from theme import C, FONT, MONO
from widgets import LiteButton, LiteProgress, badge

APP, VERSION = "Dolmi", "1.1"
HERE = Path(__file__).resolve().parent
ICON, LOGO = HERE / "assets" / "dolmi-2.ico", HERE / "assets" / "dolmi.png"
SETTINGS = HERE / "settings.json"
OLD_TRANSCRIPTS = HERE / "transcripts"   # where Dolmi kept files before the data folder setting

def _load_env():
    """Read KEY=VALUE lines from .env into the environment (no dependency), without overriding
    variables already set. Secrets like the Pro unlock hash live here, not in the source."""
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

def _seed_example_files():
    """First run: create vocabulary.txt / glossary.txt from the bundled *.example.txt templates."""
    for name in ("vocabulary", "glossary"):
        real, example = HERE / f"{name}.txt", HERE / f"{name}.example.txt"
        if not real.exists() and example.exists():
            shutil.copyfile(example, real)

_load_env()
DEFAULTS = {"model": "auto", "device": "auto", "font": 22, "opacity": 0.88,
            "show_german": True, "overlay": True, "overlay_geometry": None, "audio_device": "", "language": "de",
            "mode": "translate", "ai_provider": "claude", "ai_models": {}, "api_keys": {}, "ai_context": "", "ai_length": "short", "pro": "",
            "save_transcripts": True, "keep_days": 0, "privacy_seen": False, "data_folder": "",
            "invisible": False}
KEEP_OPTIONS = {"Never": 0, "After 7 days": 7, "After 30 days": 30, "After 90 days": 90}
MEETING_LINES = 8    # transcript lines sent along with each Assistant question
QUESTION_WAIT = 1000           # ms a question waits for the speaker to go on ("…your experience." "In AI.")
QUESTION_WAIT_SPEAKING = 2500  # ms while they are visibly still talking
QUESTION_MAX_WAIT = 8          # s; never hold a question longer than this
NO_AUDIO_AFTER = 8   # s of silence during a session before Dolmi says it hears nothing

def load_settings():
    try:
        s = {**DEFAULTS, **json.loads(SETTINGS.read_text(encoding="utf-8"))}
        s["ai_context"] = s["ai_context"] or s.pop("ai_notes", "")
        return s
    except (OSError, ValueError):
        return dict(DEFAULTS)

def documents_folder():
    """The user's Documents folder, wherever Windows has it (it is often moved into OneDrive)."""
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:   # 5 = CSIDL_PERSONAL
        return Path(buf.value)
    return Path.home() / "Documents"

def data_folder(settings):
    """Transcripts, summaries, saved items and the inbox: per Windows user, Documents\\Dolmi unless changed."""
    return Path(settings["data_folder"]) if settings["data_folder"] else documents_folder() / "Dolmi"

def copy_old_files(dest):
    """First run with the data folder: copy files from the old app-folder location (originals stay)."""
    if dest.exists() or not OLD_TRANSCRIPTS.exists():
        return
    dest.mkdir(parents=True)
    for f in OLD_TRANSCRIPTS.iterdir():
        if f.is_file():
            shutil.copy2(f, dest / f.name)
    print(f"Copied earlier transcripts from {OLD_TRANSCRIPTS} to {dest}")

def font(size, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)

class Dolmi(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=C["bg"])
        self.title(APP)
        self.iconbitmap(str(ICON))
        self.geometry("1040x700")
        self.minsize(820, 540)
        self.settings = load_settings()
        self.data = data_folder(self.settings)
        copy_old_files(self.data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.db = inbox.connect(self.data / "dolmi.db")
        self.chat_id = None   # current inbox chat: one per Assistant session (or per Clear)
        self.ui_q = queue.Queue()
        self.engine, self.session, self.history = None, None, []

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._sidebar()
        self.content = ctk.CTkFrame(self, fg_color=C["bg"], corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        self._topbar()
        self.pages = {"Live": self._live_page(), "Saved": self._saved_page(),
                      "Assistant": self._assistant_page(), "Inbox": self._inbox_page(), "Vocabulary": self._vocab_page(), "Models": self._models_page(),
                      "Settings": self._settings_page()}
        self.show("Live")

        self.overlay = Overlay(self, self.settings, self.bring_to_front, self.save_settings)
        # the subtitle bar is a borderless floating bar — never give it a taskbar/Alt+Tab button,
        # and re-assert it on every show (some Windows setups re-add a button on deiconify)
        self._keep_bar_off_taskbar()
        self.overlay.bind("<Map>", lambda e: self._keep_bar_off_taskbar(), add="+")
        self.lang_lbl.configure(text=f"{self.language_name()} → English")
        if not self.settings["overlay"]:
            self.overlay.withdraw()
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.ask_q, self.asks, self.ai_history = queue.Queue(), {}, []
        self.pending_q = None   # a spoken question waiting a moment for its last words
        self.history_lock = threading.Lock()
        self.unlock_tries, self.unlock_wait = 0, 0.0
        self._apply_pro()
        self._update_memory_chip()
        threading.Thread(target=self._answer_loop, daemon=True).start()
        self.mode_switch.set(self.settings["mode"].capitalize())
        self._show_privacy()
        self.level, self.last_sound, self.no_audio = 0.0, 0.0, False
        self.summary, self.last_transcript = None, None
        self._purge_transcripts()
        if not assistant.is_pro(self.settings["pro"]):
            self.settings["invisible"] = False
        self.after(400, self._apply_invisible)
        # a system-wide hotkey always brings Dolmi back, even hidden from the taskbar and buried
        self.hotkey = stealth.GlobalHotkey(
            stealth.MOD_CONTROL | stealth.MOD_ALT | stealth.MOD_SHIFT, 0x44,   # Ctrl+Alt+Shift+D
            lambda: self.after(0, self._recover_visible), name="show Dolmi (Ctrl+Alt+Shift+D)")
        self.hotkey.start()
        if not self.settings["privacy_seen"]:
            self.after(800, self.privacy_notice)
        self.load_engine()
        self.after(100, self.poll)

    # ------------------------------------------------------------ layout
    def _sidebar(self):
        side = ctk.CTkFrame(self, width=210, fg_color=C["surface"], corner_radius=0)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=18, pady=(22, 28))
        logo = ctk.CTkImage(Image.open(LOGO), size=(34, 34))
        ctk.CTkLabel(brand, image=logo, text="").pack(side="left")
        names = ctk.CTkFrame(brand, fg_color="transparent")
        names.pack(side="left", padx=10)
        ctk.CTkLabel(names, text=APP, font=font(20, "bold"), text_color=C["text"]).pack(anchor="w")
        self.lang_lbl = ctk.CTkLabel(names, text="", font=font(11), text_color=C["muted"])
        self.lang_lbl.pack(anchor="w")

        self.nav = {}
        for name, glyph in (("Live", "●"), ("Assistant", "✦"), ("Inbox", "✉"), ("Saved", "★"), ("Vocabulary", "Aa"), ("Models", "◆"), ("Settings", "⚙")):
            b = ctk.CTkButton(side, text=f"  {glyph}   {name}", anchor="w", height=40, corner_radius=8,
                              font=font(14), fg_color="transparent", hover_color=C["surface2"],
                              text_color=C["muted"], command=lambda n=name: self.show(n))
            b.pack(fill="x", padx=12, pady=2)
            self.nav[name] = b

        foot = ctk.CTkFrame(side, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=18, pady=18)
        self.engine_lbl = ctk.CTkLabel(foot, text="Loading models…", font=font(11),
                                       text_color=C["muted"], justify="left", anchor="w")
        self.engine_lbl.pack(anchor="w")
        self.privacy_lbl = ctk.CTkLabel(foot, text="", font=font(11), text_color=C["muted"])
        self.privacy_lbl.pack(anchor="w")

    def _topbar(self):
        top = ctk.CTkFrame(self.content, fg_color="transparent", height=76)
        top.grid(row=0, column=0, sticky="ew", padx=28, pady=(18, 6))
        self.page_title = ctk.CTkLabel(top, text="", font=font(24, "bold"), text_color=C["text"])
        self.page_title.pack(side="left")
        self.start_btn = ctk.CTkButton(top, text="Loading…", width=150, height=42, corner_radius=8,
                                       font=font(15, "bold"), fg_color=C["accent"],
                                       hover_color=C["accent_hover"], text_color=C["accent_text"],
                                       state="disabled", command=self.toggle_session)
        self.start_btn.pack(side="right")
        self.subs_btn = ctk.CTkButton(top, text="Subtitles", width=110, height=42, corner_radius=8,
                                      font=font(13), fg_color=C["surface"], hover_color=C["surface2"], border_width=1,
                                      border_color=C["border"], text_color=C["text"], command=self.toggle_overlay)
        self.subs_btn.pack(side="right", padx=10)
        self.pill = ctk.CTkLabel(top, text="  ◌  Loading  ", font=font(12, "bold"), corner_radius=14,
                                 fg_color=C["surface2"], text_color=C["muted"], height=28)
        self.pill.pack(side="right", padx=6)
        self.invisible_pill = ctk.CTkLabel(top, text="  🛡 Invisible · only you see this — click or Ctrl+Alt+Shift+D to turn off  ",
                                           font=font(12, "bold"), corner_radius=8, cursor="hand2",
                                           fg_color=C["accent_subtle"], text_color=C["accent"], height=28)
        self.invisible_pill.bind("<Button-1>", lambda e: self.toggle_invisible(False))
        self.meter = ctk.CTkFrame(top, fg_color="transparent")
        ctk.CTkLabel(self.meter, text="Audio", font=font(11), text_color=C["muted"]).pack(side="left", padx=(0, 6))
        self.meter_bar = ctk.CTkProgressBar(self.meter, width=70, height=6, corner_radius=3,
                                            fg_color=C["border"], progress_color=C["accent"])
        self.meter_bar.set(0)
        self.meter_bar.pack(side="left")
        self.mode_switch = ctk.CTkSegmentedButton(
            top, values=["Translate", "Assistant"], height=34, font=font(12, "bold"),
            selected_color=C["accent_select"], selected_hover_color=C["accent_select"],
            unselected_color=C["surface2"], unselected_hover_color=C["border"], fg_color=C["surface2"],
            text_color=C["text"], command=lambda v: self.set_mode(v.lower()))
        self.mode_switch.pack(side="right", padx=10)

    def _page(self):
        f = ctk.CTkFrame(self.content, fg_color="transparent")
        f.grid(row=1, column=0, sticky="nsew", padx=28, pady=(6, 24))
        return f

    def _card(self, parent):
        return ctk.CTkFrame(parent, fg_color=C["surface"], corner_radius=12,
                            border_width=1, border_color=C["border"])

    def _text(self, parent, **kw):
        t = tk.Text(parent, bg=C["surface"], fg=C["text"], insertbackground=C["text"], relief="flat",
                    bd=0, highlightthickness=0, wrap="word", padx=20, pady=16,
                    font=(FONT, 12), selectbackground=C["border"], **kw)
        sb = ctk.CTkScrollbar(parent, command=t.yview, button_color=C["border"])
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 6), pady=10)
        t.pack(fill="both", expand=True, padx=(4, 0), pady=4)
        return t

    def _switch(self, parent, on, command):
        sw = ctk.CTkSwitch(parent, text="", progress_color=C["accent"], fg_color=C["border_strong"],
                           button_color=C["muted"], button_hover_color=C["text"], command=command)
        sw.select() if on else sw.deselect()
        sw.pack(side="right")
        return sw

    def _small_btn(self, parent, text, cmd):
        return ctk.CTkButton(parent, text=text, height=32, corner_radius=8, font=font(12),
                             fg_color=C["surface"], hover_color=C["surface2"], border_width=1, border_color=C["border"],
                             text_color=C["text"], command=cmd)

    def _live_page(self):
        page = self._page()
        tools = ctk.CTkFrame(page, fg_color="transparent")
        tools.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(tools, text="Click a sentence to save it · double-click a word to save just the word",
                     font=font(12), text_color=C["muted"]).pack(side="left")
        self._small_btn(tools, "Export…", self.export).pack(side="right")
        self._small_btn(tools, "Copy all", self.copy_all).pack(side="right", padx=8)
        self.sum_btn = self._small_btn(tools, "✦ Summarize", self.summarize)

        card = self._card(page)
        card.pack(fill="both", expand=True)
        self.feed = self._text(card, cursor="hand2", spacing1=2, spacing3=2)
        self.feed.tag_configure("time", foreground=C["muted"], font=(FONT, 9))
        self.feed.tag_configure("en", font=(FONT, 14, "bold"), spacing1=12)
        self.feed.tag_configure("de", foreground=C["german"], font=(FONT, 11), spacing3=8)
        self.feed.tag_configure("hover", background=C["surface2"])
        self.feed.tag_configure("saved", background=C["saved_bg"])
        self.feed.tag_configure("empty", foreground=C["muted"], font=(FONT, 13), justify="center",
                                spacing1=120)
        self.feed.insert("end", "Press Start, then join your meeting (Teams, Zoom, Slack, Meet, any app).\n"
                                "Subtitles appear here and in the floating bar.", "empty")
        self.feed.config(state="disabled")
        self.feed.bind("<Motion>", self._feed_hover)
        self.feed.bind("<Leave>", lambda e: self.feed.tag_remove("hover", "1.0", "end"))
        self.feed.bind("<Button-1>", self._feed_click)
        self.feed.bind("<Double-Button-1>", self._feed_word)

        self.draft = ctk.CTkLabel(page, text="", font=font(14), text_color=C["draft"],
                                  anchor="w", justify="left", wraplength=700)
        self.draft.pack(fill="x", pady=(10, 0))
        self.toast = ctk.CTkLabel(page, text="", font=font(12, "bold"), text_color=C["ok"], anchor="w")
        self.toast.pack(fill="x")
        page.bind("<Configure>", lambda e: self.draft.configure(wraplength=max(e.width - 20, 200)))
        return page

    def _saved_page(self):
        page = self._page()
        tools = ctk.CTkFrame(page, fg_color="transparent")
        tools.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(tools, text="Sentences and words you saved today", font=font(12),
                     text_color=C["muted"]).pack(side="left")
        self._small_btn(tools, "Open folder", self.open_transcripts).pack(side="right")
        self._small_btn(tools, "Copy", lambda: self._copy(self.saved_txt.get("1.0", "end"))
                        ).pack(side="right", padx=8)
        card = self._card(page)
        card.pack(fill="both", expand=True)
        self.saved_txt = self._text(card)
        return page

    def _vocab_page(self):
        page = self._page()
        tabs = ctk.CTkTabview(page, fg_color=C["surface"], segmented_button_selected_color=C["accent_select"],
                              segmented_button_selected_hover_color=C["accent_select"],
                              segmented_button_unselected_color=C["surface2"], segmented_button_fg_color=C["surface2"],
                              text_color=C["text"], corner_radius=12,
                              border_width=1, border_color=C["border"])
        tabs.pack(fill="both", expand=True)
        self.vocab_boxes = {}
        for tab, file, hint in (
                ("Translation rules", "vocabulary.txt",
                 "[keep] stays unchanged · [german] German = English · [english] wrong = right"),
                ("Spelling hints", "glossary.txt",
                 "Names and terms the speech recognition should spell correctly — one per line")):
            t = tabs.add(tab)
            ctk.CTkLabel(t, text=hint, font=font(12), text_color=C["muted"]).pack(anchor="w", padx=6)
            box = ctk.CTkTextbox(t, font=ctk.CTkFont(family=MONO, size=13),
                                 fg_color=C["bg"], text_color=C["text"], corner_radius=8,
                                 border_width=1, border_color=C["border"])
            box.pack(fill="both", expand=True, padx=6, pady=8)
            path = HERE / file
            box.insert("1.0", path.read_text(encoding="utf-8") if path.exists() else "")
            self.vocab_boxes[file] = box
        bottom = ctk.CTkFrame(page, fg_color="transparent")
        bottom.pack(fill="x", pady=(10, 0))
        self.vocab_status = ctk.CTkLabel(bottom, text="", font=font(12, "bold"), text_color=C["ok"])
        self.vocab_status.pack(side="left")
        ctk.CTkButton(bottom, text="Save & apply", height=38, corner_radius=8, font=font(13, "bold"),
                      fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["accent_text"],
                      command=self.save_vocab).pack(side="right")
        return page

    def _settings_page(self):
        outer = self._page()
        page = ctk.CTkScrollableFrame(outer, fg_color="transparent")   # assistant section runs long
        page.pack(fill="both", expand=True)
        card = self._card(page)
        card.pack(fill="x")
        s = self.settings

        def row(title, subtitle):
            r = ctk.CTkFrame(card, fg_color="transparent")
            r.pack(fill="x", padx=22, pady=12)
            txt = ctk.CTkFrame(r, fg_color="transparent")
            txt.pack(side="left")
            ctk.CTkLabel(txt, text=title, font=font(14, "bold"), text_color=C["text"]).pack(anchor="w")
            sub = ctk.CTkLabel(txt, text=subtitle, font=font(12), text_color=C["muted"],
                               justify="left", anchor="w")
            sub.pack(anchor="w")
            # wrap the subtitle to the space left of the control, so a long one never pushes
            # the switch/menu off the right edge when the window isn't full-screen
            r.bind("<Configure>", lambda e, lbl=sub: lbl.configure(wraplength=max(e.width - 300, 180)))
            return r

        auto = "Auto-detect (any language)"
        names = {models.LANGUAGES[c]: c for c in sorted(models.LANGUAGES, key=models.LANGUAGES.get)}
        r = row("Spoken language", "Language people speak in the meeting — subtitles are always English")
        lang = ctk.CTkOptionMenu(r, values=[auto] + list(names), width=280, fg_color=C["surface2"],
                                 button_color=C["border"], button_hover_color=C["accent_subtle"], text_color=C["text"],
                                 dynamic_resizing=False,
                                 command=lambda v: self.set_language("auto" if v == auto else names[v]))
        lang.set(auto if s["language"] == "auto" else models.LANGUAGES[s["language"]])
        lang.pack(side="right")

        r = row("Speech model", "Download, remove and switch models in the Models tab")
        self._small_btn(r, "Manage models", lambda: self.show("Models")).pack(side="right")

        import live_subs
        default = "Default speaker (follows Windows)"
        r = row("Listen to", "The speaker or headset your meeting app plays through")
        dev = ctk.CTkOptionMenu(r, values=[default] + live_subs.output_devices(), width=280,
                                fg_color=C["surface2"], button_color=C["border"], text_color=C["text"],
                                button_hover_color=C["accent_subtle"], dynamic_resizing=False,
                                command=lambda v: self._set("audio_device", "" if v == default else v))
        dev.set(s["audio_device"] or default)
        dev.pack(side="right")

        r = row("Subtitle size", "Text size in the floating bar")
        size_lbl = ctk.CTkLabel(r, text=str(s["font"]), width=30, text_color=C["muted"])
        size_lbl.pack(side="right")
        size = ctk.CTkSlider(r, from_=12, to=48, number_of_steps=18, width=220,
                             button_color=C["accent"], button_hover_color=C["accent_hover"], progress_color=C["accent"], fg_color=C["border_strong"],
                             command=lambda v: (size_lbl.configure(text=str(int(v))),
                                                self._set("font", int(v))))
        size.set(s["font"]); size.pack(side="right", padx=8)

        r = row("Subtitle opacity", "How see-through the floating bar is")
        op = ctk.CTkSlider(r, from_=0.4, to=1.0, width=220, button_color=C["accent"], button_hover_color=C["accent_hover"],
                           progress_color=C["accent"], fg_color=C["border_strong"], command=lambda v: self._set("opacity", round(v, 2)))
        op.set(s["opacity"]); op.pack(side="right", padx=8)

        r = row("Show original line", "What was said, in the original language, under the English subtitle")
        self.german_switch = self._switch(r, s["show_german"],
                                          lambda: self._set("show_german", bool(self.german_switch.get())))

        # --- Privacy: what Dolmi keeps on disk, and for how long
        ctk.CTkLabel(page, text="Privacy", font=font(16, "bold"),
                     text_color=C["text"]).pack(anchor="w", pady=(22, 6))
        card = self._card(page)
        card.pack(fill="x")
        r = row("Save transcripts", "Write every meeting to the transcripts folder (off = live subtitles only)")
        self.save_switch = self._switch(r, s["save_transcripts"],
                                        lambda: self._set("save_transcripts", bool(self.save_switch.get())))
        r = row("Delete old files", "Transcripts, saved sentences, answers and summaries older than this")
        keep = ctk.CTkOptionMenu(r, values=list(KEEP_OPTIONS), width=280, fg_color=C["surface2"],
                                 button_color=C["border"], button_hover_color=C["accent_subtle"], text_color=C["text"],
                                 dynamic_resizing=False, command=lambda v: self.set_keep_days(KEEP_OPTIONS[v]))
        keep.set(next((k for k, v in KEEP_OPTIONS.items() if v == s["keep_days"]), "Never"))
        keep.pack(side="right")
        self.purge_lbl = ctk.CTkLabel(r, text="", font=font(12, "bold"), text_color=C["ok"])
        self.purge_lbl.pack(side="right", padx=12)
        r = row("Dolmi folder", "Transcripts, summaries, saved items and the inbox are kept here")
        self._small_btn(r, "Change…", self.change_data_folder).pack(side="right")
        self._small_btn(r, "Open", self.open_transcripts).pack(side="right", padx=8)
        self.data_lbl = ctk.CTkLabel(card, text=str(self.data), font=font(12), text_color=C["accent"], anchor="w")
        self.data_lbl.pack(fill="x", padx=22, pady=(0, 6))
        from huggingface_hub.constants import HF_HUB_CACHE
        r = row("Speech and translation models", "Downloaded once per Windows user, shared by every Dolmi install")
        self._small_btn(r, "Open", lambda: (Path(HF_HUB_CACHE).mkdir(parents=True, exist_ok=True),
                                            os.startfile(HF_HUB_CACHE))).pack(side="right")
        ctk.CTkLabel(card, text=HF_HUB_CACHE, font=font(12), text_color=C["muted"], anchor="w"
                     ).pack(fill="x", padx=22, pady=(0, 16))


        # --- Screen privacy (Pro + Assistant only): keep Dolmi out of screen shares
        self.invisible_section = ctk.CTkFrame(page, fg_color="transparent")
        ctk.CTkLabel(self.invisible_section, text="Screen privacy", font=font(16, "bold"),
                     text_color=C["text"]).pack(anchor="w", pady=(22, 6))
        card = self._card(self.invisible_section)
        card.pack(fill="x")
        r = row("Invisible mode", "Hide Dolmi from screen shares, recordings and the taskbar")
        self.invisible_switch = self._switch(r, s["invisible"],
                                             lambda: self.toggle_invisible(bool(self.invisible_switch.get())))
        desc = ctk.CTkLabel(card, text="For when you share your screen with clients and don't want your "
                                "transcripts, inbox or this window shown. The content is excluded from the capture, so a "
                                "shared screen sees straight through it — no black box. The “🛡 Invisible” badge in "
                                "Dolmi’s top bar is your indicator; only you can see it. Press Ctrl+Alt+Shift+D any time "
                                "to turn this off. Does not hide Dolmi from Task Manager.",
                            font=font(12), text_color=C["muted"], anchor="w", justify="left", wraplength=720)
        desc.pack(fill="x", padx=22, pady=(0, 14))
        # e.width is physical px; CTk scales wraplength by the display factor, so divide it back out
        sc = ctk.ScalingTracker.get_widget_scaling(desc)
        outer.bind("<Configure>", lambda e, l=desc: l.configure(wraplength=max(e.width / sc - 90, 160)), add="+")

        # --- Dolmi Pro: the Assistant is unlocked once per Windows user with a password
        ctk.CTkLabel(page, text="Dolmi Pro", font=font(16, "bold"),
                     text_color=C["text"]).pack(anchor="w", pady=(22, 6))
        card = self.pro_card = self._card(page)
        card.pack(fill="x")
        r = row("Assistant", "AI answers to technical questions asked in meetings — for Pro users")
        self.pro_btn = ctk.CTkButton(r, text="Unlock Assistant", width=170, height=36, corner_radius=8,
                                     font=font(13, "bold"), fg_color=C["accent"], hover_color=C["accent_hover"],
                                     text_color=C["accent_text"], command=self.unlock_dialog)
        self.pro_btn.pack(side="right")
        self.pro_status = ctk.CTkLabel(r, text="", font=font(12, "bold"), text_color=C["ok"])
        self.pro_status.pack(side="right", padx=12)

        # --- Assistant settings: provider, model, encrypted API key (only once unlocked)
        self.ai_section = ctk.CTkFrame(page, fg_color="transparent")
        ctk.CTkLabel(self.ai_section, text="Assistant", font=font(16, "bold"),
                     text_color=C["text"]).pack(anchor="w", pady=(22, 6))
        card = self._card(self.ai_section)
        card.pack(fill="x")
        labels = {v["label"]: k for k, v in assistant.PROVIDERS.items()}
        r = row("Provider", "Which AI answers the questions (question text is sent to this provider)")
        prov = ctk.CTkOptionMenu(r, values=list(labels), width=280, fg_color=C["surface2"],
                                 button_color=C["border"], button_hover_color=C["accent_subtle"], text_color=C["text"],
                                 dynamic_resizing=False, command=lambda v: self._set_provider(labels[v]))
        prov.set(assistant.PROVIDERS[s["ai_provider"]]["label"])
        prov.pack(side="right")

        r = row("Model", "Fast models answer in 1-3 s; you can type any model name")
        self.ai_model = ctk.CTkEntry(r, width=280, fg_color=C["surface2"], border_color=C["border"])
        self.ai_model.pack(side="right")

        r = row("API key", "Stored encrypted for your Windows account only")
        self._small_btn(r, "Test", self.test_assistant).pack(side="right", padx=(8, 0))
        self._small_btn(r, "Save", self.save_assistant).pack(side="right", padx=(8, 0))
        self.ai_key = ctk.CTkEntry(r, width=280, show="•", fg_color=C["surface2"], border_color=C["border"])
        self.ai_key.pack(side="right")
        self.ai_status = ctk.CTkLabel(card, text="", font=font(12, "bold"), text_color=C["muted"],
                                      anchor="w", justify="left", wraplength=700)
        self.ai_status.pack(fill="x", padx=22, pady=(0, 14))
        self._fill_assistant_fields()


        ctk.CTkLabel(page, text=f"{APP} {VERSION} · Whisper + Opus-MT, running locally",
                     font=font(11), text_color=C["muted"]).pack(anchor="w", pady=14)
        return outer

    # ------------------------------------------------------------ assistant
    def _assistant_page(self):
        page = self._page()
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)

        # left: the conversation
        left = ctk.CTkFrame(page, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        tools = ctk.CTkFrame(left, fg_color="transparent")
        tools.pack(fill="x", pady=(0, 10))
        self.ai_hint = ctk.CTkLabel(tools, text="", font=font(12, "bold"), corner_radius=12, height=26,
                                    fg_color=C["surface2"], text_color=C["muted"])
        self.ai_hint.pack(side="left")
        self.memory_chip = ctk.CTkLabel(tools, text="", font=font(12, "bold"), corner_radius=12, height=26,
                                        fg_color=C["surface2"], text_color=C["muted"])
        self.memory_chip.pack(side="left", padx=8)
        self._small_btn(tools, "Clear  (Ctrl+L)", self.clear_answers).pack(side="right")
        self._small_btn(tools, "Copy last answer", self.copy_last_answer).pack(side="right", padx=8)

        card = self._card(left)
        card.pack(fill="both", expand=True)
        self.answers = self._text(card, spacing1=2, spacing3=3)
        self.answers.tag_configure("q", foreground=C["accent"], font=(FONT, 13, "bold"), spacing1=18, spacing3=6)
        self.answers.tag_configure("meta", foreground=C["muted"], font=(FONT, 9))
        self.answers.tag_configure("code", font=(MONO, 11), background=C["bg"],
                                   lmargin1=14, lmargin2=14, rmargin=14)
        self.answers.tag_configure("fence", foreground=C["muted"], font=(MONO, 9))
        self.answers.tag_configure("err", foreground=C["draft"])
        self.answers.tag_configure("empty", foreground=C["muted"], font=(FONT, 12), justify="center",
                                   spacing1=6)
        self.answers.tag_configure("empty_title", foreground=C["text"], font=(FONT, 15, "bold"),
                                   justify="center", spacing1=90, spacing3=8)
        self._answers_empty_state()

        ask_row = ctk.CTkFrame(left, fg_color="transparent")
        ask_row.pack(fill="x", pady=(10, 0))
        self.ask_entry = ctk.CTkEntry(ask_row, height=42, corner_radius=8, font=font(13),
                                      placeholder_text="Type a follow-up or a new question and press Enter…",
                                      fg_color=C["surface"], border_color=C["border"])
        self.ask_entry.pack(side="left", fill="x", expand=True)
        self.ask_entry.bind("<Return>", lambda e: self._ask_typed())
        ctk.CTkButton(ask_row, text="Ask", width=92, height=42, corner_radius=8, font=font(13, "bold"),
                      fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["accent_text"],
                      command=self._ask_typed).pack(side="left", padx=(10, 0))

        # right: what the assistant should know
        panel = self._card(page)
        panel.grid(row=0, column=1, sticky="ns")
        inner = ctk.CTkFrame(panel, fg_color="transparent", width=300)
        inner.pack(fill="both", expand=True, padx=18, pady=16)
        ctk.CTkLabel(inner, text="Context", font=font(15, "bold"), text_color=C["text"]).pack(anchor="w")
        ctk.CTkLabel(inner, text="Who you are, the project, the stack and what\nthe meeting is about. "
                                 "Sent with every question.", font=font(12), text_color=C["muted"],
                     justify="left").pack(anchor="w", pady=(2, 8))
        self.context_box = ctk.CTkTextbox(inner, width=300, font=font(12), fg_color=C["bg"], wrap="word",
                                          border_width=1, border_color=C["border"], corner_radius=8)
        self.context_box.pack(fill="both", expand=True)
        self.context_box.insert("1.0", self.settings["ai_context"])
        self.context_box.bind("<KeyRelease>", self._context_typed)   # saved as you type, no button needed

        ctk.CTkLabel(inner, text="Answer length", font=font(12, "bold"),
                     text_color=C["text"]).pack(anchor="w", pady=(14, 4))
        self.length_switch = ctk.CTkSegmentedButton(
            inner, values=["Short", "Detailed"], font=font(12, "bold"), selected_color=C["accent_select"],
            selected_hover_color=C["accent_select"], unselected_color=C["surface2"], fg_color=C["surface2"],
            unselected_hover_color=C["border"], text_color=C["text"],
            command=lambda v: self._set("ai_length", v.lower()))
        self.length_switch.set(self.settings["ai_length"].capitalize())
        self.length_switch.pack(fill="x")

        ctk.CTkLabel(inner, text=f"Remembers the last {assistant.HISTORY_MESSAGES // 2} questions "
                                 "and answers, so follow-ups like “and how does that scale?” work.",
                     font=font(11), text_color=C["muted"], justify="left",
                     wraplength=290).pack(anchor="w", pady=(14, 0))
        self.context_status = ctk.CTkLabel(inner, text="", font=font(12, "bold"), text_color=C["ok"])
        self.context_status.pack(side="left", pady=(14, 0))
        ctk.CTkButton(inner, text="Save context", width=130, height=36, corner_radius=8, font=font(13, "bold"),
                      fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["accent_text"],
                      command=self.save_context).pack(side="right", pady=(14, 0))
        self.bind("<Control-l>", lambda e: self.current_page == "Assistant" and self.clear_answers())
        return page

    def _answers_empty_state(self):
        t = self.answers
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("end", "Ask anything technical\n", "empty_title")
        t.insert("end", "Switch to Assistant mode and press Start — English questions in the meeting\n"
                        "are answered here as they're asked. Or type one below.\n\n", "empty")
        t.insert("end", "Try: “What's the difference between RAG and fine-tuning?”", "empty")
        t.config(state="disabled")

    def _update_memory_chip(self):
        n = len(self.ai_history) // 2
        self.memory_chip.configure(text=f"  Memory: {n} of {assistant.HISTORY_MESSAGES // 2}  ",
                                   text_color=C["accent"] if n else C["muted"])

    def _context_typed(self, _):
        if getattr(self, "_context_job", None):
            self.after_cancel(self._context_job)
        self._context_job = self.after(800, self.save_context)

    def save_context(self):
        self.settings["ai_context"] = self.context_box.get("1.0", "end").strip()
        self.save_settings()
        self.context_status.configure(text="✓ Saved")
        self.after(2500, lambda: self.context_status.configure(text=""))

    # --- Pro unlock
    def _apply_pro(self):
        """Show or hide everything Assistant-related depending on the unlock."""
        unlocked = assistant.is_pro(self.settings["pro"])
        if unlocked:
            self.nav["Assistant"].pack(fill="x", padx=12, pady=2, after=self.nav["Live"])
            self.nav["Inbox"].pack(fill="x", padx=12, pady=2, after=self.nav["Assistant"])
            self.mode_switch.pack(side="right", padx=10, after=self.pill)
            self.sum_btn.pack(side="right")
            self.ai_section.pack(fill="x", after=self.pro_card)
            self.invisible_section.pack(fill="x", before=self.pro_card)
            self.pro_btn.pack_forget()
            self.pro_status.configure(text="✓ Unlocked on this PC")
        else:
            self.nav["Assistant"].pack_forget()
            self.nav["Inbox"].pack_forget()
            self.mode_switch.pack_forget()
            self.sum_btn.pack_forget()
            self.ai_section.pack_forget()
            self.invisible_section.pack_forget()
            self.pro_status.configure(text="")
            if self.settings["mode"] == "assistant":
                self.settings["mode"] = "translate"
            if self.settings["invisible"]:   # screen privacy is a Pro feature; locking Pro turns it off
                self.toggle_invisible(False)
        return unlocked

    def unlock_dialog(self):
        win = ctk.CTkToplevel(self, fg_color=C["bg"])
        win.title("Unlock Dolmi Pro")
        win.geometry("420x250")
        win.resizable(False, False)
        win.transient(self)
        win.after(250, lambda: (win.iconbitmap(str(ICON)), win.grab_set(), entry.focus_set()))
        ctk.CTkLabel(win, text="Unlock the Assistant", font=font(18, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=26, pady=(24, 2))
        ctk.CTkLabel(win, text="Enter the Pro password. You only need to do this once on this PC.",
                     font=font(12), text_color=C["muted"]).pack(anchor="w", padx=26)
        entry = ctk.CTkEntry(win, show="•", height=40, corner_radius=8, font=font(14),
                             fg_color=C["surface"], border_color=C["border"], placeholder_text="Password")
        entry.pack(fill="x", padx=26, pady=(16, 6))
        msg = ctk.CTkLabel(win, text="", font=font(12, "bold"), text_color=C["live"])
        msg.pack(anchor="w", padx=26)

        def attempt(_=None):
            if self.unlock_tries >= 5 and time.time() < self.unlock_wait:
                msg.configure(text=f"Too many attempts — wait {int(self.unlock_wait - time.time())} s")
                return
            if assistant.check_password(entry.get()):
                self.settings["pro"] = assistant.pro_unlock_value()
                self.save_settings()
                self._apply_pro()
                win.destroy()
                self.flash("✓ Assistant unlocked — it's in the sidebar and the top bar")
                return
            self.unlock_tries += 1
            if self.unlock_tries >= 5:
                self.unlock_wait = time.time() + 30
            entry.delete(0, "end")
            msg.configure(text="Wrong password")
        entry.bind("<Return>", attempt)
        ctk.CTkButton(win, text="Unlock", height=38, corner_radius=8, font=font(13, "bold"),
                      fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["accent_text"],
                      command=attempt).pack(anchor="e", padx=26, pady=(8, 0))

    def _provider(self):
        return self.settings["ai_provider"]

    def _ai_model(self):
        return self.settings["ai_models"].get(self._provider()) or assistant.PROVIDERS[self._provider()]["model"]

    def _ai_key(self):
        return assistant.decrypt_key(self.settings["api_keys"].get(self._provider(), ""))

    def _fill_assistant_fields(self):
        p = assistant.PROVIDERS[self._provider()]
        self.ai_model.delete(0, "end"); self.ai_model.insert(0, self._ai_model())
        self.ai_key.delete(0, "end"); self.ai_key.insert(0, self._ai_key())
        self.ai_key.configure(placeholder_text=f"{p['key_hint']}  (from {p['console']})")
        has = bool(self._ai_key())
        self.ai_status.configure(text=f"✓ Key saved for {p['label']}" if has else
                                 f"No key yet — paste your {p['label']} API key and press Save",
                                 text_color=C["ok"] if has else C["muted"])
        self._update_ai_hint()

    def _update_ai_hint(self):
        p = assistant.PROVIDERS[self._provider()]
        has = bool(self._ai_key())
        self.ai_hint.configure(text=f"  ● {p['label']} · {self._ai_model()}  " if has
                               else "  No API key — Settings → Assistant  ",
                               text_color=C["ok"] if has else C["draft"])

    def _set_provider(self, key):
        self.settings["ai_provider"] = key
        self.save_settings()
        self._fill_assistant_fields()
        self._show_privacy()

    def save_assistant(self):
        key, model = self.ai_key.get().strip(), self.ai_model.get().strip()
        self.settings["api_keys"][self._provider()] = assistant.encrypt_key(key)
        self.settings["ai_models"][self._provider()] = model or assistant.PROVIDERS[self._provider()]["model"]
        self.save_settings()
        self._fill_assistant_fields()

    def test_assistant(self):
        self.save_assistant()
        self.ai_status.configure(text="Testing…", text_color=C["muted"])
        provider, model, key = self._provider(), self._ai_model(), self._ai_key()

        def work():
            try:
                reply = "".join(assistant.stream_answer(provider, model, key, "Reply with just: OK"))
                self.ui_q.put(("ask_test", f"✓ Connected — {model} replied: {reply.strip()[:40]}", ""))
            except assistant.AssistantError as e:
                self.ui_q.put(("ask_test", f"✗ {e}", "err"))
        threading.Thread(target=work, daemon=True).start()

    def _keep_bar_off_taskbar(self):
        try:
            stealth.mark_toolwindow(self.overlay)
        except Exception as e:
            print(f"Could not keep the subtitle bar off the taskbar: {e}")

    def _recover_visible(self):
        """Global hotkey (Ctrl+Alt+Shift+D): always get Dolmi back — visible, on the taskbar, in front."""
        if self.settings["invisible"]:
            self.toggle_invisible(False)
        self.deiconify()
        self.lift()
        self.focus_force()
        self.show("Settings")
        self.flash("Invisible mode is off — Dolmi is visible again")

    def toggle_invisible(self, on):
        self.settings["invisible"] = on
        if hasattr(self, "invisible_switch"):
            self.invisible_switch.select() if on else self.invisible_switch.deselect()
        self.save_settings()
        self._apply_invisible()

    def _invisible_windows(self):
        """Every Dolmi window that follows invisible mode: main, subtitle bar, open dialogs.
        The main window and dialogs also leave the taskbar and Alt+Tab, so a whole-screen share
        shows nothing of Dolmi. The in-app "Invisible" badge is the indicator — only you see it,
        and Ctrl+Alt+Shift+D always brings Dolmi back."""
        wins = [(self, True), (self.overlay, False)]   # overlay is borderless, never on the taskbar
        wins += [(w, True) for w in self.winfo_children() if isinstance(w, ctk.CTkToplevel) and w.winfo_exists()]
        return wins

    def _apply_invisible(self):
        on = self.settings["invisible"]
        ok = True
        for win, taskbar in self._invisible_windows():
            try:
                ok = stealth.apply(win, on, taskbar=taskbar) and ok
            except Exception as e:
                print(f"Invisible mode could not be applied to a window: {e}")
                ok = False
        if on and not ok:   # never pretend it worked: a shared screen would still show Dolmi
            for win, taskbar in self._invisible_windows():
                try:
                    stealth.apply(win, False, taskbar=taskbar)
                except Exception:
                    pass
            self.settings["invisible"] = False
            self.save_settings()
            if hasattr(self, "invisible_switch"):
                self.invisible_switch.deselect()
            self.invisible_pill.pack_forget()
            self.flash("Invisible mode needs Windows 10 (2020) or newer — it stayed off", C["draft"])
            return
        if on:
            # The main window is off the taskbar/Alt+Tab now, so you can't click back to it over a
            # meeting. The floating bar is the only surface you can see but clients can't — keep it up
            # so Assistant answers and subtitles are still readable while invisible.
            self._bar_auto_shown = not self.settings["overlay"]
            if self._bar_auto_shown:
                self.overlay.reveal()
            self.flash("Invisible mode on — answers show on the floating bar (only you see it). "
                       "Ctrl+Alt+Shift+D to exit.")
        elif getattr(self, "_bar_auto_shown", False):   # restore the user's subtitle preference
            self.overlay.hide()
            self._bar_auto_shown = False
        self.invisible_pill.pack(side="right", padx=6, after=self.pill) if on else self.invisible_pill.pack_forget()

    def _show_privacy(self):
        self.privacy_lbl.configure(
            text="Questions go to " + assistant.PROVIDERS[self._provider()]["label"]
            if self.settings["mode"] == "assistant" else "100% local · nothing uploaded")

    def set_mode(self, mode):
        self.settings["mode"] = mode
        self.save_settings()
        self.mode_switch.set(mode.capitalize())
        self._show_privacy()
        if self.engine:
            self.engine.language = "en" if mode == "assistant" else self.settings["language"]
        if mode == "assistant":
            self.draft.configure(text="")   # no half-finished subtitle left behind on the Live page
            self.show("Assistant")
            self.overlay.idle("Assistant — listening for English questions" if self.session
                              else "Assistant — press Start in Dolmi")
            if not self._ai_key():
                self.flash("Add an API key in Settings → Assistant", C["draft"])
        else:
            self.show("Live")
            self.overlay.idle("Listening…" if self.session else "Ready — press Start in Dolmi")

    def _ask_typed(self):
        q = self.ask_entry.get().strip()
        if q:
            self.ask_entry.delete(0, "end")
            self.ask(q, "typed")

    # --- spoken questions: speakers pause mid-question and repeat themselves
    def _collect_question(self, text):
        if self.pending_q:
            self.pending_q["text"] += " " + text
        elif assistant.is_question(text):
            self.pending_q = {"text": text, "since": time.time(), "job": None}
        else:
            return
        self.overlay.show_answer(self.pending_q["text"], "…")   # heard it: shown right away
        self._wait_for_question(QUESTION_WAIT)

    def _wait_for_question(self, ms):
        p = self.pending_q
        if p["job"]:
            self.after_cancel(p["job"])
        p["job"] = self.after(0 if time.time() - p["since"] > QUESTION_MAX_WAIT else ms,
                              self._send_pending_question)

    def _send_pending_question(self):
        if not self.pending_q:
            return
        p, self.pending_q = self.pending_q, None
        if p["job"]:
            self.after_cancel(p["job"])
        recent = [self.asks[i]["q"].lower() for i in sorted(self.asks)[-3:]]
        if p["text"].lower() not in recent:   # the same question said twice is answered once
            self.ask(p["text"])

    def ask(self, question, source="spoken"):
        qid = len(self.asks)
        # what was said right before, minus the question itself, so "what do you think about that?" works
        meeting = [en for t, de, en in self.history[-MEETING_LINES - 1:] if en != question][-MEETING_LINES:]
        self.asks[qid] = {"q": question, "a": "", "meeting": meeting, "source": source}
        self.ask_q.put(qid)

    def _answer_loop(self):
        """One answer at a time, in the order questions were asked, with recent Q&A as memory."""
        while True:
            qid = self.ask_q.get()
            question = self.asks[qid]["q"]
            self.ui_q.put(("ask_start", qid, ""))
            try:
                with self.history_lock:
                    history = list(self.ai_history)
                answer = ""
                for piece in assistant.stream_answer(
                        self._provider(), self._ai_model(), self._ai_key(), question,
                        context=self.settings["ai_context"], history=history,
                        length=self.settings["ai_length"], meeting=self.asks[qid]["meeting"]):
                    answer += piece
                    self.ui_q.put(("ask_chunk", qid, piece))
                # Remember it here, not in the UI: the next queued question may start before
                # the UI has caught up, and it must already see this exchange.
                with self.history_lock:
                    self.ai_history += [{"role": "user", "content": question},
                                        {"role": "assistant", "content": answer.strip()}]
                    del self.ai_history[:-assistant.HISTORY_MESSAGES]
                self.ui_q.put(("ask_done", qid, ""))
            except assistant.AssistantError as e:
                self.ui_q.put(("ask_done", qid, str(e)))
            except Exception as e:
                traceback.print_exc()
                self.ui_q.put(("ask_done", qid, f"Unexpected error: {e}"))

    def on_answer(self, kind, qid, text):
        if kind == "ask_test":
            self.ai_status.configure(text=qid, text_color=C["live"] if text else C["ok"])
            self._update_ai_hint()
            return
        t, item = self.answers, self.asks[qid]
        t.config(state="normal")
        if kind == "ask_start":
            if t.tag_ranges("empty_title"):
                t.delete("1.0", "end")
            t.insert("end", f"{datetime.now():%H:%M:%S}\n", "meta")
            t.insert("end", f"{item['q']}\n", "q")
            t.mark_set(f"a{qid}", "end-1c"); t.mark_gravity(f"a{qid}", "left")
            self.overlay.show_answer(item["q"], "Thinking…")
        elif kind == "ask_chunk":
            item["a"] += text
            t.insert("end", text)
            self.overlay.show_answer(item["q"], item["a"])
        else:   # ask_done
            if text:
                t.insert("end", f"⚠ {text}", "err")
            self._tag_code(qid)
            t.insert("end", "\n")
            answer = item["a"].strip()
            self.overlay.show_answer(item["q"], answer + (f"\n\n⚠ {text}" if text and answer else text), done=True)
            self._update_memory_chip()
            if answer or text:
                self._save_answer(item, answer, text)
        t.config(state="disabled")
        t.see("end")

    def _tag_code(self, qid):
        self._tag_fences(self.answers, f"a{qid}")

    @staticmethod
    def _tag_fences(t, pos):
        """Monospace for ```fenced``` code from `pos` on; the fence lines themselves are dimmed."""
        inside, start = False, None
        while True:
            pos = t.search("```", pos, "end")
            if not pos:
                return
            t.tag_add("fence", pos, f"{pos} lineend")
            if inside:
                t.tag_add("code", start, f"{pos} lineend")
            else:
                start = pos
            inside = not inside
            pos = f"{pos}+3c"

    def _save_answer(self, item, answer, error):
        """Every finished answer goes into the current inbox chat."""
        if not self.settings["save_transcripts"]:
            return
        if self.chat_id is None:
            self.chat_id = inbox.start_chat(self.db, self._provider(), self._ai_model())
        inbox.add_message(self.db, self.chat_id, item["q"], answer, error, item["source"])

    def copy_last_answer(self):
        done = [a for a in self.asks.values() if a["a"]]
        if done:
            self._copy(done[-1]["a"])
            self.flash("Answer copied")

    def clear_answers(self):
        """Clears the conversation and the assistant's memory of it."""
        with self.history_lock:
            self.ai_history.clear()
        self.chat_id = None   # the next answer starts a new inbox chat
        self._update_memory_chip()
        self._answers_empty_state()

    # ------------------------------------------------------------ inbox
    def _inbox_page(self):
        page = self._page()
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(0, weight=1)
        left = self._card(page)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        self.inbox_search = ctk.CTkEntry(left, width=290, height=36, corner_radius=8, font=font(12),
                                         placeholder_text="Search questions and answers",
                                         fg_color=C["surface2"], border_color=C["border"])
        self.inbox_search.pack(fill="x", padx=12, pady=12)
        self.inbox_search.bind("<KeyRelease>", lambda e: self.refresh_inbox())
        self.inbox_list = ctk.CTkScrollableFrame(left, width=290, fg_color="transparent")
        self.inbox_list.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        right = self._card(page)
        right.grid(row=0, column=1, sticky="nsew")
        head = ctk.CTkFrame(right, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(16, 0))
        self._small_btn(head, "Delete", self.delete_chat).pack(side="right")
        self._small_btn(head, "Copy", lambda: self.inbox_selected and (
            self._copy(inbox.as_markdown(self.db, self.inbox_selected)), self.flash("Chat copied"))
        ).pack(side="right", padx=8)
        self.inbox_title = ctk.CTkLabel(head, text="", font=font(15, "bold"), text_color=C["text"],
                                        anchor="w", justify="left")
        self.inbox_title.pack(side="left", fill="x", expand=True)
        self.inbox_view = self._text(right, spacing1=2, spacing3=3)
        t = self.inbox_view
        t.tag_configure("q", foreground=C["accent"], font=(FONT, 13, "bold"), spacing1=18, spacing3=6)
        t.tag_configure("meta", foreground=C["muted"], font=(FONT, 9))
        t.tag_configure("code", font=(MONO, 11), background=C["bg"], lmargin1=14, lmargin2=14, rmargin=14)
        t.tag_configure("fence", foreground=C["muted"], font=(MONO, 9))
        t.tag_configure("err", foreground=C["draft"])
        t.config(state="disabled")
        self.inbox_selected, self.inbox_rows = None, {}
        return page

    def refresh_inbox(self):
        for w in self.inbox_list.winfo_children():
            w.destroy()
        chats = inbox.list_chats(self.db, self.inbox_search.get())
        self.inbox_rows = {}
        if not chats:
            tk.Label(self.inbox_list, text="No conversations yet.\nEvery Assistant session is saved here.",
                     font=(FONT, 10), fg=C["muted"], bg=C["surface"], justify="left").pack(anchor="w", padx=10, pady=10)
            self.inbox_selected = None
            self._show_chat(None)
            return
        for c in chats:
            row = tk.Frame(self.inbox_list, bg=C["surface"], cursor="hand2", padx=10, pady=8)
            row.pack(fill="x", pady=1)
            title = c["title"] if len(c["title"]) <= 42 else c["title"][:40] + "…"
            n = c["questions"]
            tk.Label(row, text=title, font=(FONT, 10, "bold"), fg=C["text"], bg=C["surface"], anchor="w").pack(fill="x")
            tk.Label(row, text=f"{inbox.local(c['started_at']):%a %d %b, %H:%M} · {n} question{'s' * (n != 1)}",
                     font=(FONT, 9), fg=C["muted"], bg=C["surface"], anchor="w").pack(fill="x")
            for w in (row, *row.winfo_children()):
                w.bind("<Button-1>", lambda e, i=c["id"]: self._show_chat(i))
            self.inbox_rows[c["id"]] = row
        self._show_chat(self.inbox_selected if self.inbox_selected in self.inbox_rows else chats[0]["id"])

    def _show_chat(self, chat_id):
        self.inbox_selected = chat_id
        for i, row in self.inbox_rows.items():
            bg = C["accent_subtle"] if i == chat_id else C["surface"]
            for w in (row, *row.winfo_children()):
                w.config(bg=bg)
        t = self.inbox_view
        t.config(state="normal")
        t.delete("1.0", "end")
        msgs = inbox.messages(self.db, chat_id) if chat_id else []
        self.inbox_title.configure(text=f"{inbox.local(msgs[0]['asked_at']):%A %d %B %Y, %H:%M}" if msgs else "")
        for m in msgs:
            how = "typed" if m["source"] == "typed" else "heard in the meeting"
            t.insert("end", f"{inbox.local(m['asked_at']):%H:%M:%S} · {how}\n", "meta")
            t.insert("end", f"{m['question']}\n", "q")
            t.insert("end", f"{m['answer']}\n")
            if m["error"]:
                t.insert("end", f"⚠ {m['error']}\n", "err")
        self._tag_fences(t, "1.0")
        t.config(state="disabled")
        t.see("1.0")

    def delete_chat(self):
        if not self.inbox_selected:
            return
        if messagebox.askyesno("Delete chat", "Delete this conversation for good?", parent=self):
            inbox.delete_chat(self.db, self.inbox_selected)
            if self.inbox_selected == self.chat_id:
                self.chat_id = None
            self.inbox_selected = None
            self.refresh_inbox()

    def _models_page(self):
        page = self._page()
        self.pc = models.system_info()
        gpu = "NVIDIA GPU ready" if self.pc["gpu"] else "no usable NVIDIA GPU (CPU mode)"
        ctk.CTkLabel(page, text=f"This PC:  {self.pc['ram_gb']} GB RAM  ·  {self.pc['cores']} CPU threads  ·  {gpu}",
                     font=font(12), text_color=C["muted"]).pack(anchor="w", pady=(0, 10))
        self.models_list = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.models_list.pack(fill="both", expand=True)
        self.downloads = {}     # model key -> True while downloading
        self.model_cards = {}   # built on demand: translator cards only when relevant
        for job in (models.SPEECH, models.TRANSLATE):
            tk.Label(self.models_list, text=job, font=(FONT, 13, "bold"), fg=C["text"],
                     bg=C["bg"]).pack(anchor="w", pady=(12, 6))
            if job == models.SPEECH:
                for m in (m for m in models.CATALOG if m.job == job):
                    self.model_cards[m.key] = self._model_card(m)
        return page

    def _model_card(self, m):
        S = C["surface"]
        card = self._card(self.models_list)
        card.pack(fill="x", pady=5, padx=(0, 8))
        body = tk.Frame(card, bg=S)   # one rounded CTk frame; everything inside is plain Tk
        body.pack(fill="x", padx=18, pady=14)
        body.grid_columnconfigure(0, weight=1)

        head = tk.Frame(body, bg=S)
        head.grid(row=0, column=0, sticky="w")
        tk.Label(head, text=m.name, font=(FONT, 13, "bold"), fg=C["text"], bg=S).pack(side="left", padx=(0, 12))
        badges = tk.Frame(head, bg=S)
        badges.pack(side="left")
        tk.Label(body, text=m.note, font=(FONT, 10), fg=C["muted"], bg=S, anchor="w", justify="left",
                 wraplength=640).grid(row=1, column=0, sticky="w", pady=(4, 8))

        stats = tk.Frame(body, bg=S)
        stats.grid(row=2, column=0, sticky="w")
        size = f"{m.size_mb / 1000:.1f} GB" if m.size_mb >= 1000 else f"{m.size_mb} MB"
        dots = lambda n: "●" * n + "○" * (5 - n)
        verdict, color = models.fit(m, self.pc)
        for label, value, fg in (("Size", size, C["text"]), ("Accuracy", dots(m.accuracy), C["accent"]),
                                 ("Speed (CPU)", dots(m.speed), C["accent"]), ("On this PC", verdict, C[color])):
            cell = tk.Frame(stats, bg=S)
            cell.pack(side="left", padx=(0, 28))
            tk.Label(cell, text=label, font=(FONT, 9), fg=C["muted"], bg=S).pack(anchor="w")
            tk.Label(cell, text=value, font=(FONT, 11, "bold"), fg=fg, bg=S).pack(anchor="w")

        actions = tk.Frame(body, bg=S)
        actions.grid(row=0, column=1, rowspan=3, sticky="e")
        btn_font = (FONT, 10, "bold")
        LiteButton(actions, "ⓘ  Info", lambda: self.model_info(m), font=btn_font).pack(side="left", padx=3)
        main = LiteButton(actions, "", font=btn_font, width=11)
        main.pack(side="left", padx=3)
        use = LiteButton(actions, "Use", lambda: self.use_model(m), style="primary", font=btn_font)
        bar = LiteProgress(body)
        return dict(card=card, badges=badges, main=main, use=use, bar=bar)

    def _badge(self, parent, text, color):
        badge(parent, text, C[color], (FONT, 9, "bold"))

    def _recommended(self):
        ok = [m for m in models.CATALOG if m.job == models.SPEECH and models.fit(m, self.pc)[1] == "ok"]
        return max(ok, key=lambda m: (m.accuracy, m.speed)).key if ok else "tiny"

    def _relevant_translators(self):
        """Translator cards worth showing: the one for the chosen language, the fallback, installed ones."""
        lang = self.settings["language"]
        keep = {models.translator_key(lang)} if lang != "auto" else {"opus-mul"}
        return [m for m in models.CATALOG if m.job == models.TRANSLATE
                and (m.key in keep or m.key in self.downloads or models.is_installed(m))]

    def refresh_models(self):
        shown = self._relevant_translators()
        for key, w in self.model_cards.items():
            if models.BY_KEY[key].job == models.TRANSLATE:
                w["card"].pack_forget()
        for m in shown:
            if m.key not in self.model_cards:
                self.model_cards[m.key] = self._model_card(m)
            else:
                self.model_cards[m.key]["card"].pack(fill="x", pady=5, padx=(0, 8))
        rec = self._recommended()
        active = self.engine.model_name if self.engine else None
        for key, w in self.model_cards.items():
            m = models.BY_KEY[key]
            busy = key in self.downloads
            installed = not busy and models.is_installed(m)
            in_use = key == active or (self.engine is not None and key in self.engine.translators)
            for child in w["badges"].winfo_children():
                child.destroy()
            if busy:
                self._badge(w["badges"], "Downloading…", "draft")
            else:
                self._badge(w["badges"], "✓ Installed" if installed else "Not installed",
                            "ok" if installed else "muted")
            if in_use:
                self._badge(w["badges"], "● In use", "accent")
            if key == rec and m.job == models.SPEECH:
                self._badge(w["badges"], "★ Recommended for this PC", "accent")

            if installed:
                w["main"].set(text="In use" if in_use else "Uninstall", enabled=not in_use,
                              command=lambda m=m: self.uninstall_model(m))
            else:
                w["main"].set(text="0%" if busy else "↓  Download", enabled=not busy,
                              command=lambda m=m: self.download_model(m))
            show_use = installed and m.job == models.SPEECH and key != active
            w["use"].pack(side="left", padx=3) if show_use else w["use"].pack_forget()
            if busy:
                w["bar"].grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
            else:
                w["bar"].grid_remove()

    def download_model(self, m):
        self.downloads[m.key] = True

        def work():
            try:
                models.download(m)
                self.ui_q.put(("model_done", m.key, ""))
            except Exception as e:
                traceback.print_exc()
                self.ui_q.put(("model_done", m.key, str(e)))
        threading.Thread(target=work, daemon=True).start()
        self.refresh_models()
        self._track_download(m)

    def _track_download(self, m):
        if m.key not in self.downloads:
            return
        done = min(models.downloaded_mb(m) / m.size_mb, 1)
        self.model_cards[m.key]["bar"].set(done)
        self.model_cards[m.key]["main"].set(text=f"{done:.0%}")
        self.after(500, self._track_download, m)

    def uninstall_model(self, m):
        models.uninstall(m)
        self.flash(f"{m.name} removed ({m.size_mb:,} MB freed)")
        self.refresh_models()

    def language_name(self):
        code = self.settings["language"]
        return "Any language" if code == "auto" else models.LANGUAGES[code]

    def set_language(self, code):
        self.settings["language"] = code
        self.save_settings()
        self.lang_lbl.configure(text=f"{self.language_name()} → English")
        self.overlay.set_language(code)
        if self.session:
            self.flash("Applies after you stop and start again", C["draft"])
            return
        self.load_engine()
        self.flash(f"Switching to {self.language_name()}… (first time downloads its translator)")

    def use_model(self, m):
        if self.session:
            self.flash("Stop the session first, then switch models", C["draft"])
            return
        self.settings["model"] = m.key
        self.save_settings()
        self.load_engine()
        self.flash(f"Loading {m.name}…")
        self.refresh_models()

    def model_info(self, m):
        win = ctk.CTkToplevel(self, fg_color=C["bg"])
        win.title(m.name)
        win.geometry("480x500")
        win.transient(self)
        win.after(250, lambda: win.iconbitmap(str(ICON)))
        ctk.CTkLabel(win, text=m.name, font=font(20, "bold"), text_color=C["text"]).pack(anchor="w", padx=24, pady=(22, 2))
        ctk.CTkLabel(win, text=m.job, font=font(12), text_color=C["muted"]).pack(anchor="w", padx=24)
        card = self._card(win)
        card.pack(fill="both", expand=True, padx=20, pady=16)
        gpu_text = {"no": "Not needed",
                    "optional": f"Optional — {m.vram_gb} GB+ VRAM makes it faster",
                    "recommended": f"Recommended — NVIDIA, {m.vram_gb} GB+ VRAM",
                    "required": f"Required for live use — NVIDIA, {m.vram_gb} GB+ VRAM"}[m.gpu]
        verdict, color = models.fit(m, self.pc)
        dots = lambda n: "●" * n + "○" * (5 - n)
        rows = [("Download size", f"{m.size_mb:,} MB"), ("RAM needed", f"{m.ram_gb} GB free"),
                ("CPU (live use)", f"{m.min_cores}+ threads"), ("GPU", gpu_text),
                ("Accuracy", dots(m.accuracy)), ("Speed (CPU)", dots(m.speed)),
                ("This PC", f"{self.pc['ram_gb']} GB RAM · {self.pc['cores']} threads · "
                            f"{'GPU ready' if self.pc['gpu'] else 'CPU only'}"),
                ("Source", m.repo)]
        for label, value in rows:
            r = ctk.CTkFrame(card, fg_color="transparent")
            r.pack(fill="x", padx=18, pady=4)
            ctk.CTkLabel(r, text=label, font=font(12), text_color=C["muted"], width=130, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=value, font=font(12, "bold"), text_color=C["text"], anchor="w",
                         wraplength=260, justify="left").pack(side="left")
        ctk.CTkLabel(card, text=f"On this PC: {verdict}", font=font(14, "bold"),
                     text_color=C[color]).pack(anchor="w", padx=18, pady=(12, 4))
        ctk.CTkLabel(card, text=m.note, font=font(12), text_color=C["muted"], wraplength=400,
                     justify="left").pack(anchor="w", padx=18, pady=(0, 14))

    # ------------------------------------------------------------ navigation
    def show(self, name):
        self.current_page = name
        for n, p in self.pages.items():
            p.grid_remove() if n != name else p.grid()
        for n, b in self.nav.items():
            on = n == name
            b.configure(fg_color=C["accent_subtle"] if on else "transparent",
                        text_color=C["accent"] if on else C["muted"])
        self.page_title.configure(text=name)
        if name == "Saved":
            self.refresh_saved()
        if name == "Models":
            self.refresh_models()
        if name == "Inbox":
            self.refresh_inbox()

    def bring_to_front(self):
        self.deiconify(); self.lift(); self.focus_force()

    # ------------------------------------------------------------ settings
    def save_settings(self):
        SETTINGS.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        self.subs_btn.configure(text="Hide subtitles" if self.settings["overlay"] else "Show subtitles")
        if hasattr(self, "german_switch"):
            self.german_switch.select() if self.settings["show_german"] else self.german_switch.deselect()

    def _set(self, key, value):
        self.settings[key] = value
        self.overlay.apply_settings()
        self.save_settings()

    def toggle_overlay(self):
        if self.settings["overlay"]:
            self.overlay.hide()
        else:
            self.overlay.reveal()

    # ------------------------------------------------------------ engine & session
    def load_engine(self):
        if self.session:
            self.flash("Stop the session before reloading models")
            return
        self.engine = None
        self.set_status("loading")

        def work():
            import live_subs
            try:
                eng = live_subs.Engine(self.settings["model"], self.settings["device"],
                                       self.settings["language"])
                self.ui_q.put(("ready", eng, f"{eng.device.upper()} · {eng.model_name}"))
            except BaseException as e:   # SystemExit included: show it instead of dying silently
                traceback.print_exc()
                self.ui_q.put(("error", f"Could not load models: {e}", ""))
        threading.Thread(target=work, daemon=True).start()

    def toggle_session(self):
        if self.session:
            self.stop_session()
        else:
            self.start_session()

    def start_session(self):
        import live_subs
        self.data.mkdir(exist_ok=True)
        stop, audio_q = threading.Event(), queue.Queue()
        tr = self.last_transcript = live_subs.Transcript(self.data, save=self.settings["save_transcripts"])

        def capture():
            try:
                live_subs.loopback_stream(audio_q, stop, self.settings["audio_device"] or None,
                                          on_level=self._on_level)
            except Exception as e:
                traceback.print_exc()
                self.ui_q.put(("error", f"Can't record audio: {e}", ""))

        threading.Thread(target=capture, daemon=True).start()
        threading.Thread(target=live_subs.worker, args=(self.engine, audio_q, self.ui_q, tr, stop),
                         daemon=True).start()
        self.session = {"stop": stop, "transcript": tr}
        self.engine.language = "en" if self.settings["mode"] == "assistant" else self.settings["language"]
        self.history.clear()
        self.chat_id = None   # each session is its own inbox chat
        self.feed.config(state="normal"); self.feed.delete("1.0", "end"); self.feed.config(state="disabled")
        self.overlay.idle("Listening…")
        self.level, self.last_sound, self.no_audio = 0.0, time.time(), False
        if not self.settings["overlay"]:   # subtitles are the point of pressing Start
            self.overlay.reveal()
        self.set_status("live")

    def stop_session(self):
        if not self.session:
            return
        self.session["stop"].set()
        md = self.session["transcript"].md
        saved = f"Transcript saved: {md.name}" if md else "Session ended (saving transcripts is off)"
        if assistant.is_pro(self.settings["pro"]) and len(self.history) >= 3:
            saved += " · ✦ Summarize turns it into notes and action items"
        self.flash(saved)
        self.session = None
        self.draft.configure(text="")
        self._send_pending_question()
        if self.overlay.answer_q is None:   # keep the last answer readable after Stop
            self.overlay.idle("Stopped — press Start in Dolmi")
        self.set_status("ready")

    def set_status(self, state):
        pill = {"loading": ("◌  Loading models", C["muted"]),
                "ready": ("●  Ready", C["ok"]),
                "live": ("●  Live · no audio", C["draft"]) if self.no_audio else ("●  Live", C["live"]),
                "error": ("!  Problem", C["draft"])}[state]
        self.pill.configure(text=f"  {pill[0]}  ", text_color=pill[1])
        live = state == "live"
        self.start_btn.configure(
            state="normal" if state in ("ready", "live") else "disabled",
            text={"live": "■  Stop", "ready": "▶  Start"}.get(state, "Loading…"),
            fg_color=C["live"] if live else C["accent"],
            hover_color=C["live_hover"] if live else C["accent_hover"],
            text_color="white" if live else C["accent_text"])
        self.overlay.set_live(live)
        if live:
            self.meter.pack(side="right", padx=(12, 4), after=self.pill)
        else:
            self.meter.pack_forget()

    def poll(self):
        try:
            while True:
                kind, a, b = self.ui_q.get_nowait()
                if kind == "ready":
                    self.engine = a
                    self.engine_lbl.configure(text=f"Engine: {b}")
                    self.set_status("ready")
                    self.overlay.idle("Ready — press Start in Dolmi")
                elif kind == "error":
                    self.set_status("error" if not self.engine else "ready")
                    self.flash(a, C["draft"])
                elif kind == "model_done":
                    self.downloads.pop(a, None)
                    self.flash(f"Download failed: {b}" if b else f"{models.BY_KEY[a].name} downloaded",
                               C["draft"] if b else None)
                    self.refresh_models()
                elif kind.startswith("ask_"):
                    self.on_answer(kind, a, b)
                elif kind.startswith("sum_"):
                    self.on_summary(kind, a)
                elif not self.session:
                    continue   # late results after Stop
                elif self.settings["mode"] == "assistant":
                    # No subtitles in Assistant mode: what's said is kept only as meeting context
                    # for answers and the summary, and questions are answered.
                    if kind == "final":
                        self.history.append((datetime.now().strftime("%H:%M:%S"), a, b))
                        self._collect_question(b)
                    elif b and self.pending_q:   # still talking: let the sentence finish first
                        self._wait_for_question(QUESTION_WAIT_SPEAKING)
                elif kind == "final":
                    self.add_sentence(a, b)
                    self.overlay.show_final(a, b)
                    self.draft.configure(text="")
                elif b:
                    self.draft.configure(text=f"{b} …")
                    self.overlay.show_draft(a, b)
        except queue.Empty:
            pass
        self._update_meter()
        self.after(100, self.poll)

    # ------------------------------------------------------------ audio meter
    def _on_level(self, rms):   # audio thread; a float assignment is safe to share
        self.level = rms

    def _update_meter(self):
        if not self.session:
            return
        db = 20 * math.log10(max(self.level, 1e-6))
        self.meter_bar.set(min(max((db + 60) / 50, 0), 1))   # -60 dB = silent … -10 dB = loud
        now = time.time()
        if self.level > 0.006:   # same threshold the engine treats as sound
            self.last_sound = now
        silent = now - self.last_sound > NO_AUDIO_AFTER
        if silent == self.no_audio:
            return
        self.no_audio = silent
        self.set_status("live")
        if silent:
            device = self.settings["audio_device"] or "the default speaker"
            self.flash(f"No sound from {device} for {NO_AUDIO_AFTER} s. Is the meeting playing there? "
                       "Settings → Listen to", C["draft"])
            if not self.history and self.overlay.answer_q is None:
                self.overlay.idle("No sound yet — check Settings → Listen to in Dolmi")
        elif not self.history and self.overlay.answer_q is None:
            self.overlay.idle("Listening…")

    # ------------------------------------------------------------ live feed
    def add_sentence(self, de, en):
        i = len(self.history)
        self.history.append((datetime.now().strftime("%H:%M:%S"), de, en))
        tag = f"s{i}"
        at_bottom = self.feed.yview()[1] > 0.98
        self.feed.config(state="normal")
        self.feed.insert("end", f"{self.history[i][0]}\n", ("time", tag))
        self.feed.insert("end", f"{en}\n", ("en", tag))
        if self.settings["show_german"]:
            self.feed.insert("end", f"{de}\n", ("de", tag))
        self.feed.config(state="disabled")
        if at_bottom:
            self.feed.see("end")

    def _sentence_at(self, e):
        tags = [t for t in self.feed.tag_names(f"@{e.x},{e.y}") if t[0] == "s" and t[1:].isdigit()]
        return int(tags[0][1:]) if tags else None

    def _feed_hover(self, e):
        self.feed.tag_remove("hover", "1.0", "end")
        i = self._sentence_at(e)
        if i is not None:
            self.feed.tag_add("hover", f"s{i}.first", f"s{i}.last")

    def _feed_click(self, e):   # delayed so a double-click (word) doesn't also save the sentence
        i = self._sentence_at(e)
        if i is not None:
            self._pending = self.after(280, lambda: self.save_sentence(i))

    def _feed_word(self, e):
        if getattr(self, "_pending", None):
            self.after_cancel(self._pending); self._pending = None
        word = self.feed.get(f"@{e.x},{e.y} wordstart", f"@{e.x},{e.y} wordend").strip(" .,;:!?\"'()…")
        if word:
            self._save(word)
            self.flash(f"★ Saved word: {word}")
        return "break"

    def save_sentence(self, i):
        self._pending = None
        t, de, en = self.history[i]
        self._save(f"{en}  \n  <sub>{de}</sub>")
        self.feed.tag_add("saved", f"s{i}.first", f"s{i}.last")
        self.flash("★ Sentence saved")

    def _save(self, text):
        import live_subs
        self.data.mkdir(exist_ok=True)
        live_subs.save_snippet(self.data, text)

    def refresh_saved(self):
        path = self.data / f"saved_{datetime.now():%Y-%m-%d}.md"
        self.saved_txt.config(state="normal")
        self.saved_txt.delete("1.0", "end")
        self.saved_txt.insert("1.0", path.read_text(encoding="utf-8") if path.exists()
                              else "Nothing saved yet today.\n\nOn the Live page, click a sentence "
                                   "or double-click a word to save it here.")
        self.saved_txt.config(state="disabled")

    def copy_all(self):
        self._copy("\n\n".join(f"[{t}] {en}\n{de}" for t, de, en in self.history))
        self.flash("Transcript copied")

    def _copy(self, text):
        self.clipboard_clear(); self.clipboard_append(text.strip())

    def export(self):
        tr = self.session["transcript"] if self.session else None
        files = sorted(self.data.glob("meeting_*.md")) if self.data.exists() else []
        src = (tr.md if tr else None) or (files[-1] if files else None)
        if not src:
            self.flash("No transcript yet", C["draft"])
            return
        dest = filedialog.asksaveasfilename(parent=self, initialfile=src.name, defaultextension=".md",
                                            filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
        if dest:
            shutil.copyfile(src, dest)
            self.flash(f"Exported to {Path(dest).name}")

    def save_vocab(self):
        for file, box in self.vocab_boxes.items():
            (HERE / file).write_text(box.get("1.0", "end").rstrip() + "\n", encoding="utf-8")
        if self.engine:
            self.engine.reload_terms()
        self.vocab_status.configure(text="✓ Saved — used from the next sentence on")
        self.after(4000, lambda: self.vocab_status.configure(text=""))

    def flash(self, message, color=None):
        self.toast.configure(text=message, text_color=color or C["ok"])
        self.after(3500, lambda: self.toast.configure(text=""))

    # ------------------------------------------------------------ privacy
    # ------------------------------------------------------------ meetings (saved transcripts)
    def _meetings_page(self):
        page = self._page()
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(0, weight=1)
        left = self._card(page)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(head, text="Past meetings", font=font(12, "bold"), text_color=C["muted"]).pack(side="left")
        self._small_btn(head, "Folder", self.open_transcripts).pack(side="right")
        self.meetings_list = ctk.CTkScrollableFrame(left, width=290, fg_color="transparent")
        self.meetings_list.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        right = self._card(page)
        right.grid(row=0, column=1, sticky="nsew")
        rhead = ctk.CTkFrame(right, fg_color="transparent")
        rhead.pack(fill="x", padx=20, pady=(16, 0))
        self._small_btn(rhead, "Delete", self.delete_meeting).pack(side="right")
        self._small_btn(rhead, "Export…", self.export_meeting).pack(side="right", padx=8)
        self._small_btn(rhead, "Copy", lambda: (self._copy(self.meetings_view.get("1.0", "end")),
                        self.flash("Transcript copied"))).pack(side="right", padx=(0, 8))
        self.meetings_title = ctk.CTkLabel(rhead, text="", font=font(15, "bold"), text_color=C["text"],
                                           anchor="w", justify="left")
        self.meetings_title.pack(side="left", fill="x", expand=True)
        self.meetings_view = self._text(right)
        self.meetings_view.config(state="disabled")
        self.meetings_selected, self.meetings_rows = None, {}
        return page

    def _meeting_files(self):
        return sorted(self.data.glob("meeting_*.md"), reverse=True) if self.data.exists() else []

    def _meeting_label(self, path):
        """A friendly (day, time) from a meeting_YYYY-MM-DD_HH-MM.md filename."""
        try:
            dt = datetime.strptime(path.stem.removeprefix("meeting_"), "%Y-%m-%d_%H-%M")
            return f"{dt:%a %d %b %Y}", f"{dt:%H:%M}"
        except ValueError:
            return path.stem, ""

    def refresh_meetings(self):
        for w in self.meetings_list.winfo_children():
            w.destroy()
        files = self._meeting_files()
        self.meetings_rows = {}
        if not files:
            tk.Label(self.meetings_list, text="No meetings yet.\nEvery session is saved here automatically.",
                     font=(FONT, 10), fg=C["muted"], bg=C["surface"], justify="left").pack(anchor="w", padx=10, pady=10)
            self.meetings_selected = None
            self._show_meeting(None)
            return
        for p in files:
            day, tm = self._meeting_label(p)
            row = tk.Frame(self.meetings_list, bg=C["surface"], cursor="hand2", padx=10, pady=8)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{day}  {tm}".strip(), font=(FONT, 10, "bold"), fg=C["text"],
                     bg=C["surface"], anchor="w").pack(fill="x")
            kb = max(1, p.stat().st_size // 1024)
            tk.Label(row, text=f"{kb} KB", font=(FONT, 9), fg=C["muted"], bg=C["surface"], anchor="w").pack(fill="x")
            for w in (row, *row.winfo_children()):
                w.bind("<Button-1>", lambda e, path=p: self._show_meeting(path))
            self.meetings_rows[p] = row
        current = self.meetings_selected if self.meetings_selected in self.meetings_rows else files[0]
        self._show_meeting(current)

    def _show_meeting(self, path):
        self.meetings_selected = path
        for p, row in self.meetings_rows.items():
            bg = C["surface2"] if p == path else C["surface"]
            row.configure(bg=bg)
            for w in row.winfo_children():
                w.configure(bg=bg)
        self.meetings_view.config(state="normal")
        self.meetings_view.delete("1.0", "end")
        if path is None:
            self.meetings_title.configure(text="")
            self.meetings_view.config(state="disabled")
            return
        day, tm = self._meeting_label(path)
        self.meetings_title.configure(text=f"{day}  {tm}".strip())
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            raw = f"Could not read this transcript: {e}"
        # plain, readable view: drop the markdown markers used in the saved file
        text = raw.replace("<sub>", "").replace("</sub>", "").replace("**", "")
        self.meetings_view.insert("1.0", text)
        self.meetings_view.config(state="disabled")

    def delete_meeting(self):
        p = self.meetings_selected
        if not p:
            return
        if not messagebox.askyesno("Delete meeting?", f"Delete this transcript?\n\n{p.name}\n\n"
                                   "This cannot be undone.", parent=self):
            return
        for f in (p, p.with_suffix(".srt")):
            try:
                f.unlink()
            except OSError:
                pass
        self.meetings_selected = None
        self.refresh_meetings()
        self.flash("Meeting deleted")

    def export_meeting(self):
        p = self.meetings_selected
        if not p:
            self.flash("No meeting selected", C["draft"])
            return
        dest = filedialog.asksaveasfilename(parent=self, initialfile=p.name, defaultextension=".md",
                                            filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
        if dest:
            shutil.copyfile(p, dest)
            self.flash(f"Exported to {Path(dest).name}")

    def open_transcripts(self):
        self.data.mkdir(exist_ok=True)
        os.startfile(self.data)

    def _purge_transcripts(self):
        import live_subs
        days = self.settings["keep_days"]
        return live_subs.delete_old_files(self.data, days) + inbox.delete_older_than(self.db, days)

    def change_data_folder(self):
        if self.session:
            self.flash("Stop the session before changing the folder", C["draft"])
            return
        chosen = filedialog.askdirectory(parent=self, initialdir=self.data, title="Folder for Dolmi's files")
        if not chosen or Path(chosen) == self.data:
            return
        new, old = Path(chosen), self.data
        move = messagebox.askyesno("Move your files?", f"Move Dolmi's files and the inbox from\n{old}\nto\n{new}?\n\n"
                                   "Choose No to start empty there (the old files stay where they are).", parent=self)
        self.db.close()
        if move:
            import live_subs
            names = [f for pattern in live_subs.DOLMI_FILES + ("dolmi.db",) for f in old.glob(pattern)]
            for f in names:
                if not (new / f.name).exists():
                    shutil.move(str(f), new / f.name)
        self.settings["data_folder"] = str(new)
        self.save_settings()
        self.data, self.chat_id = new, None
        self.db = inbox.connect(new / "dolmi.db")
        self.data_lbl.configure(text=str(new))
        self.refresh_inbox()

    def set_keep_days(self, days):
        self._set("keep_days", days)
        n = self._purge_transcripts()
        self.purge_lbl.configure(text=f"Deleted {n} old file{'s' * (n != 1)}" if n else "")
        self.after(5000, lambda: self.purge_lbl.configure(text=""))

    def privacy_notice(self):
        """Shown once: people being transcribed should know about it."""
        win = ctk.CTkToplevel(self, fg_color=C["bg"])
        win.title("Before your first meeting")
        win.geometry("560x320")
        win.resizable(False, False)
        win.transient(self)
        win.after(250, lambda: (win.iconbitmap(str(ICON)), win.grab_set()))

        def done():
            self.settings["privacy_seen"] = True
            self.save_settings()
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", done)
        ctk.CTkLabel(win, text="Before your first meeting", font=font(18, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=26, pady=(24, 10))
        points = (
            "Let the other participants know you use live transcription. Many companies and the GDPR "
            "expect it, and some meetings may not allow it.",
            "Speech recognition and translation run on this PC. Nothing is uploaded, except Assistant "
            "questions and summaries you ask for (Pro).",
            "Transcripts are saved in the transcripts folder. Settings → Privacy turns saving off or "
            "deletes old files automatically.")
        for point in points:
            ctk.CTkLabel(win, text=f"•  {point}", font=font(13), text_color=C["text"], justify="left",
                         anchor="w", wraplength=490).pack(anchor="w", padx=26, pady=4)
        ctk.CTkLabel(win, text="Bitte informieren Sie alle Teilnehmenden, dass Sie eine Live-Transkription nutzen.",
                     font=font(12), text_color=C["muted"], justify="left", anchor="w",
                     wraplength=490).pack(anchor="w", padx=26, pady=(10, 0))
        ctk.CTkButton(win, text="Got it", width=120, height=38, corner_radius=8, font=font(13, "bold"),
                      fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["accent_text"],
                      command=done).pack(anchor="e", padx=26, pady=(18, 0))

    # ------------------------------------------------------------ meeting summary (Pro)
    def summarize(self):
        if self.summary and not self.summary["done"] and self.summary["window"].winfo_exists():
            self.summary["window"].lift()
            return
        if not self.history:
            self.flash("Nothing to summarize yet — run a session first", C["draft"])
            return
        if not self._ai_key():
            self.flash("Add an API key in Settings → Assistant first", C["draft"])
            return
        transcript = "\n".join(f"[{t}] {en}" for t, de, en in self.history)
        tr = self.last_transcript
        stamp = tr.md.stem.removeprefix("meeting_") if tr and tr.md else f"{datetime.now():%Y-%m-%d_%H-%M}"
        self.summary = {"text": "", "done": False, "file": self.data / f"summary_{stamp}.md"}
        self._summary_window(len(self.history))
        provider, model, key, context = self._provider(), self._ai_model(), self._ai_key(), self.settings["ai_context"]

        def work():
            try:
                for piece in assistant.stream_summary(provider, model, key, transcript, context):
                    self.ui_q.put(("sum_chunk", piece, ""))
                self.ui_q.put(("sum_done", "", ""))
            except assistant.AssistantError as e:
                self.ui_q.put(("sum_done", str(e), ""))
            except Exception as e:
                traceback.print_exc()
                self.ui_q.put(("sum_done", f"Unexpected error: {e}", ""))
        threading.Thread(target=work, daemon=True).start()

    def _summary_window(self, sentences):
        sm = self.summary
        win = sm["window"] = ctk.CTkToplevel(self, fg_color=C["bg"])
        win.title("Meeting summary")
        win.geometry("720x680")
        win.minsize(480, 400)
        win.after(250, lambda: win.iconbitmap(str(ICON)))
        if self.settings["invisible"]:
            win.after(120, lambda: stealth.apply(win, True))
        head = ctk.CTkFrame(win, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 4))
        ctk.CTkLabel(head, text="Meeting summary", font=font(18, "bold"), text_color=C["text"]).pack(side="left")
        self._small_btn(head, "Copy", lambda: (self._copy(sm["text"]),
                                               sm["status"].configure(text="✓ Copied"))).pack(side="right")
        ctk.CTkLabel(win, text=f"{sentences} sentences sent to {assistant.PROVIDERS[self._provider()]['label']} "
                               f"· {self._ai_model()}", font=font(12), text_color=C["muted"]
                     ).pack(anchor="w", padx=24)
        sm["status"] = ctk.CTkLabel(win, text="", font=font(12, "bold"), text_color=C["ok"], anchor="w")
        sm["status"].pack(side="bottom", fill="x", padx=24, pady=(0, 14))
        card = self._card(win)
        card.pack(fill="both", expand=True, padx=24, pady=12)
        box = sm["box"] = self._text(card)
        box.tag_configure("h", font=(FONT, 14, "bold"), spacing1=14, spacing3=4)
        box.tag_configure("err", foreground=C["live"], font=(FONT, 12, "bold"))
        box.insert("end", "Writing the summary…")
        box.config(state="disabled")

    def on_summary(self, kind, text):
        sm = self.summary
        alive = sm["window"].winfo_exists()
        box = sm["box"]
        if kind == "sum_chunk":
            sm["text"] += text
            if alive:
                box.config(state="normal")
                if len(sm["text"]) == len(text):   # the first piece replaces the placeholder
                    box.delete("1.0", "end")
                box.insert("end", text)
                box.config(state="disabled")
                box.see("end")
            return
        sm["done"] = True
        note = ""
        if sm["text"].strip() and self.settings["save_transcripts"]:
            self.data.mkdir(exist_ok=True)
            sm["file"].write_text(sm["text"].strip() + "\n", encoding="utf-8")
            note = f"✓ Saved as {sm['file'].name}"
        if not alive:
            return
        box.config(state="normal")
        box.delete("1.0", "end")
        for line in sm["text"].strip().splitlines():   # Markdown headings shown as bold text; Copy keeps the Markdown
            if line.startswith("#"):
                box.insert("end", line.lstrip("# ") + "\n", "h")
            else:
                box.insert("end", line + "\n")
        if text:
            box.insert("end", f"⚠ {text}", "err")
        box.config(state="disabled")
        sm["status"].configure(text=note)

    def quit_app(self):
        if self.session:
            self.session["stop"].set()
        self.save_settings()
        self.destroy()

def main():
    if sys.stdout is None:   # started with pythonw: keep a log instead of losing errors
        log = open(HERE / "dolmi.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        print(f"\n--- {APP} started {datetime.now():%Y-%m-%d %H:%M:%S}")
    os.chdir(HERE)
    _seed_example_files()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")   # Windows without dev mode: harmless
    try:   # own taskbar entry and icon instead of Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Dolmi.App")
    except (AttributeError, OSError):
        pass
    ctk.set_appearance_mode("light")
    Dolmi().mainloop()

if __name__ == "__main__":
    main()
