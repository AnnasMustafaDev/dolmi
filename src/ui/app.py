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


class Api:
    def __init__(self):
        config.load_env()
        config.seed_example_files()
        self._settings = config.load_settings()
        self._settings_lock = threading.Lock()
        self._data = config.data_folder(self._settings)
        self._data.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data / "dolmi.db"
        self._purge_old()

        self._window = None                  # set by main
        self._overlay = None
        self._overlay_ready = False          # page loaded -> safe to evaluate_js without blocking
        self._overlay_visible = False
        self._engine = None
        self._engine_lock = threading.Lock() # one engine load at a time
        self._session = None                 # {"token", "stop", "transcript"} while starting/listening
        self._lock = threading.Lock()        # guards session transitions
        self._history = []                   # [(hh:mm:ss, de, en)] this session
        self._hist_lock = threading.Lock()
        self._last_transcript = None
        self._ai_history = []
        self._chat_id = None                 # inbox chat the next answer is saved into
        self._asks = {}
        self._ids = itertools.count()
        self._level = 0.0
        self._ui_q = queue.Queue()
        self._ask_q = queue.Queue()
        self._ov_q = queue.Queue()
        for loop in (self._pump, self._answer_loop, self._level_loop, self._overlay_loop):
            threading.Thread(target=loop, daemon=True).start()

    # ------------------------------------------------------------------ plumbing
    def _emit(self, fn, *args):
        w = self._window
        if not w:
            return
        try:
            w.evaluate_js(f"window.dolmi && window.dolmi.{fn}(" + ",".join(_js(a) for a in args) + ")")
        except Exception as e:
            print(f"emit {fn} failed: {e}")

    def _ov(self, fn, *args):
        """Queue a caption for the overlay, only once it's loaded and shown (evaluate_js on an
        unloaded or hidden window blocks for up to 15 s and would stall the caption pump)."""
        if self._overlay and self._overlay_ready and self._overlay_visible:
            self._ov_q.put((fn, args))

    def _overlay_loop(self):
        while True:
            fn, args = self._ov_q.get()
            ov = self._overlay
            if ov and self._overlay_visible:
                ov.emit(fn, *args)

    def _pump(self):
        """Drain ui_q (fed by the worker and answer threads) and push to the page."""
        while True:
            kind, a, b = self._ui_q.get()
            try:
                if kind in ("final", "draft") and not self._session:
                    continue                          # late result after Stop
                if kind == "error":
                    self._emit("onError", a)
                elif kind == "final":
                    with self._hist_lock:
                        self._history.append((datetime.now().strftime("%H:%M:%S"), a, b))
                    self._emit("onLine", datetime.now().strftime("%H:%M"), a, b)
                    self._ov("line", a, b)
                elif kind == "draft":
                    self._emit("onDraft", a, b)
                    self._ov("draft", a, b)
                elif kind == "ask_start":
                    self._emit("onAsk", a)
                elif kind == "ask_chunk":
                    self._emit("onAnswerChunk", a, b)
                elif kind == "ask_done":
                    self._emit("onAnswerDone", a, b)
                elif kind == "sum_chunk":
                    self._emit("onSummaryChunk", a)
                elif kind == "sum_done":
                    self._emit("onSummaryDone", a)
            except Exception as e:
                print(f"pump {kind} failed: {e}")

    def _level_loop(self):
        while True:
            time.sleep(0.1)
            s = self._session
            if s and s.get("stop"):
                self._emit("onLevel", round(float(self._level), 4))

    def _purge_old(self):
        """'Delete after N days': transcript/summary/saved files AND inbox chats (0 = keep all)."""
        days = self._settings["keep_days"]
        try:
            live_subs.delete_old_files(self._data, days)
        except Exception as e:
            print(f"file retention failed: {e}")
        try:   # short-lived connection: sqlite handles are per-thread
            db = inbox.connect(self._db_path)
            inbox.delete_older_than(db, days)
            db.close()
        except Exception as e:
            print(f"inbox retention failed: {e}")

    # ------------------------------------------------------------------ state / settings
    def _provider(self):
        p = self._settings["ai_provider"]
        return p if p in assistant.PROVIDERS else next(iter(assistant.PROVIDERS))

    def _ai_model(self):
        return self._settings["ai_models"].get(self._provider()) or assistant.PROVIDERS[self._provider()]["model"]

    def _ai_key(self):
        return assistant.decrypt_key(self._settings["api_keys"].get(self._provider(), ""))

    def _engine_label(self):
        e = self._engine
        return f"{e.device.upper()} · {e.model_name}" if e else "not loaded"

    def state(self):
        s = self._settings
        return {
            "language": s["language"], "showGerman": s["show_german"], "invisible": s["invisible"],
            "saveTranscripts": s["save_transcripts"], "keepDays": s["keep_days"],
            "provider": self._provider(), "providers": {k: v["label"] for k, v in assistant.PROVIDERS.items()},
            "model": self._ai_model(), "hasKey": bool(self._ai_key()),
            "audioDevice": s["audio_device"], "speechModel": s["model"], "overlay": bool(s.get("overlay")),
            "theme": s.get("theme", "paper"), "listening": bool(self._session),
            "engine": self._engine_label(), "dataFolder": str(self._data),
        }

    def set_setting(self, key, value):
        if key not in SETTABLE:
            return False
        with self._settings_lock:
            self._settings[key] = value
            config.save_settings(self._settings)
        if key == "keep_days":
            threading.Thread(target=self._purge_old, daemon=True).start()
        return True

    def set_key(self, key):
        with self._settings_lock:
            self._settings["api_keys"][self._provider()] = assistant.encrypt_key((key or "").strip())
            config.save_settings(self._settings)
        return True

    def set_provider(self, provider):
        if provider in assistant.PROVIDERS:
            with self._settings_lock:
                self._settings["ai_provider"] = provider
                config.save_settings(self._settings)
        return self.state()

    def test_key(self):
        try:
            reply = "".join(assistant.stream_answer(self._provider(), self._ai_model(), self._ai_key(),
                                                     "Reply with just: OK"))
            return {"ok": True, "message": f"Connected — {self._ai_model()} replied: {reply.strip()[:40]}"}
        except assistant.AssistantError as e:
            return {"ok": False, "message": str(e)}
        except Exception as e:
            return {"ok": False, "message": f"Unexpected error: {e}"}

    def devices(self):
        try:
            return live_subs.output_devices()
        except Exception as e:
            print(f"devices failed: {e}")
            return []

    # ------------------------------------------------------------------ live captions
    def start_listening(self):
        with self._lock:
            if self._session:
                return True
            token = object()
            self._session = {"token": token, "stop": None, "transcript": None}
        threading.Thread(target=self._begin, args=(token,), daemon=True).start()
        return True

    def _alive(self, token):
        s = self._session
        return s is not None and s.get("token") is token

    def _begin(self, token):
        with self._engine_lock:                       # a second Start waits here, then reuses it
            if not self._alive(token):
                return
            if not self._engine:
                self._emit("onEngine", "loading models…")
                try:
                    self._engine = live_subs.Engine(self._settings["model"], self._settings["device"],
                                                    self._settings["language"])
                except Exception as e:
                    with self._lock:
                        if self._alive(token):
                            self._session = None
                    self._emit("onError", f"Could not load models: {e}")
                    self._emit("onStopped")
                    return
                self._emit("onEngine", self._engine_label())
        with self._lock:
            if not self._alive(token):                # stopped while the models loaded
                return
            self._engine.language = self._settings["language"]
            with self._hist_lock:
                self._history = []
            self._chat_id, self._ai_history = None, []
            stop, audio_q = threading.Event(), queue.Queue()
            tr = live_subs.Transcript(self._data, save=self._settings["save_transcripts"])
            self._last_transcript = tr
            self._session.update(stop=stop, transcript=tr)

        def capture():
            try:
                live_subs.loopback_stream(audio_q, stop, self._settings["audio_device"] or None,
                                          on_level=self._on_level)
            except Exception as e:
                # no audio means no captions: end the session instead of sitting on "Translating"
                self._emit("onError", f"Can't record audio: {e}")
                if self._alive(token):
                    self.stop_listening()
                else:
                    stop.set()

        threading.Thread(target=capture, daemon=True).start()
        threading.Thread(target=live_subs.worker, args=(self._engine, audio_q, self._ui_q, tr, stop),
                         daemon=True).start()
        self._emit("onListening", True, self._engine_label())
        if self._settings.get("overlay"):             # subtitles are the point of pressing Start
            self.show_overlay()
            self._emit("onBar", True)

    def stop_listening(self):
        with self._lock:
            s, self._session = self._session, None
        if s and s.get("stop"):
            s["stop"].set()
        self._level = 0.0
        tr = s.get("transcript") if s else None
        self._emit("onListening", False, tr.md.name if tr and tr.md else "")
        return True

    def _on_level(self, rms):
        self._level = rms

    # ------------------------------------------------------------------ meetings
    def _meeting_path(self, name):
        """A meeting file inside the data folder, or None (blocks ..\\ and absolute paths)."""
        try:
            base = self._data.resolve()
            p = (base / str(name)).resolve()
        except (OSError, ValueError):
            return None
        if p.parent != base or p.suffix != ".md" or not p.name.startswith("meeting_"):
            return None
        return p

    def list_meetings(self):
        out = []
        for p in sorted(self._data.glob("meeting_*.md"), reverse=True):
            try:
                dt = datetime.strptime(p.stem.removeprefix("meeting_"), "%Y-%m-%d_%H-%M")
                day, tm = f"{dt:%a %d %b %Y}", f"{dt:%H:%M}"
            except ValueError:
                day, tm = p.stem, ""
            try:
                lines = p.read_text(encoding="utf-8").count("<sub>")
            except OSError:
                lines = 0
            out.append({"name": p.name, "day": day, "time": tm, "lines": lines})
        return out

    def read_meeting(self, name):
        p = self._meeting_path(name)
        if not p or not p.exists():
            return []
        # Transcript.add writes "**HH:MM:SS** {en}  \n<sub>{de}</sub>" -> groups are (ts, en, de)
        return [{"ts": ts.strip(), "de": de.strip(), "en": en.strip()}
                for ts, en, de in _MEETING_RE.findall(p.read_text(encoding="utf-8"))]

    def delete_meeting(self, name):
        p = self._meeting_path(name)
        if p:
            for f in (p, p.with_suffix(".srt")):
                try:
                    f.unlink()
                except OSError:
                    pass
        return True

    def open_data_folder(self):
        import os
        try:
            self._data.mkdir(exist_ok=True)
            os.startfile(self._data)
        except OSError as e:
            print(f"open folder failed: {e}")
        return True

    # ------------------------------------------------------------------ vocabulary
    def read_vocab(self):
        return {"vocabulary": self._read_file("vocabulary.txt"), "glossary": self._read_file("glossary.txt")}

    def _read_file(self, name):
        p = config.ROOT / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def save_vocab(self, vocabulary=None, glossary=None):
        """Write only the files the page actually sent (None = leave that file alone)."""
        if vocabulary is not None:
            (config.ROOT / "vocabulary.txt").write_text(vocabulary.rstrip() + "\n", encoding="utf-8")
        if glossary is not None:
            (config.ROOT / "glossary.txt").write_text(glossary.rstrip() + "\n", encoding="utf-8")
        if self._engine:
            self._engine.reload_terms()
        return True

    # ------------------------------------------------------------------ assistant + inbox
    def ask(self, question):
        question = (question or "").strip()
        if not question:
            return -1
        qid = next(self._ids)
        with self._hist_lock:
            meeting = [en for _, _, en in self._history[-9:] if en != question][-8:]
        self._asks[qid] = {"q": question, "meeting": meeting}
        self._ask_q.put(qid)
        return qid

    def _answer_loop(self):
        db = None                                  # this thread's own sqlite connection
        while True:
            qid = self._ask_q.get()
            item = self._asks.pop(qid, None)
            if not item:
                continue
            try:
                self._ui_q.put(("ask_start", qid, ""))
                answer, err = "", ""
                try:
                    history = list(self._ai_history)
                    for piece in assistant.stream_answer(
                            self._provider(), self._ai_model(), self._ai_key(), item["q"],
                            context=self._settings["ai_context"], history=history,
                            length=self._settings["ai_length"], meeting=item["meeting"]):
                        answer += piece
                        self._ui_q.put(("ask_chunk", qid, piece))
                    self._ai_history = (self._ai_history + [
                        {"role": "user", "content": item["q"]},
                        {"role": "assistant", "content": answer.strip()}])[-assistant.HISTORY_MESSAGES:]
                except assistant.AssistantError as e:
                    err = str(e)
                except Exception as e:
                    err = f"Unexpected error: {e}"
                self._ui_q.put(("ask_done", qid, err))
                if self._settings["save_transcripts"] and (answer or err):
                    if db is None:
                        db = inbox.connect(self._db_path)
                    if self._chat_id is None:
                        self._chat_id = inbox.start_chat(db, self._provider(), self._ai_model())
                    inbox.add_message(db, self._chat_id, item["q"], answer, err, "typed")
                    self._emit("onChatsChanged", self._chat_id)
            except Exception as e:                 # never let one bad answer kill the loop
                print(f"answer loop: {e}")

