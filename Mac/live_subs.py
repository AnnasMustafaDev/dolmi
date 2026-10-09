"""
Dolmi engine: live German -> English subtitles for any meeting app on macOS, 100% free & local.

v2 improvements over v1:
  * Core Audio process tap   -> no virtual cable, you still HEAR the meeting
  * Pause-based segmentation -> no more sentences cut in half every 2 s
  * Live "draft" line        -> text appears ~1 s after speech, finalized on pause
  * faster-whisper (int8)    -> ~4x faster than openai-whisper on CPU
  * Hallucination filter     -> drops Whisper's classic German silence ghosts
  * Real timestamps          -> correct SRT + readable Markdown transcript

The window and subtitle bar live in src/ (webview UI). Run: ./start.command or Dolmi.app.
"""
import os, queue, re, threading, time
from datetime import datetime
from pathlib import Path

import numpy as np

import models

# ---------------------------------------------------------------- settings
SR = 16000                 # Whisper sample rate
DRAFT_EVERY = 1.0          # s between live draft updates
PAUSE_TO_FINALIZE = 0.6    # s of silence that ends a sentence
MAX_UTTERANCE = 12.0       # s, force-finalize long monologues
MIN_SENTENCE = 3.0         # s, a draft ending in . ? ! this long is finalized without a pause
SILENCE_RMS = 0.006        # energy threshold (raise if noisy)

HALLUCINATIONS = [         # typical Whisper output on silence / noise
    "untertitel", "amara.org", "zdf", "vielen dank fürs zuschauen",
    "danke fürs zuschauen", "bis zum nächsten mal", "copyright",
    "swr", "wdr", "tschüss", "abonniert",
    "thanks for watching", "thank you for watching", "subscribe to", "sous-titrage",
]

# --------------------------------------------------------------- capture
SYSTEM_AUDIO = ""          # audio_device value for "everything the Mac plays" (the default)

def audio_helper():
    """The dolmi-audio binary (Core Audio process tap); built from audio/dolmi-audio.swift."""
    import sys
    for d in (Path(__file__).resolve().parent / "audio", Path(getattr(sys, "_MEIPASS", "")) / "audio"):
        if (d / "dolmi-audio").is_file():
            return d / "dolmi-audio"
    raise RuntimeError("the system audio helper is missing - run ./start.command once to build it")

def output_devices():
    """Inputs Dolmi can listen to besides all Mac audio: microphones and virtual devices (BlackHole...)."""
    import sounddevice as sd
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]

def loopback_stream(out_q: queue.Queue, stop: threading.Event, device=None, on_level=None):
    """Capture everything the Mac plays (device None) or one input device by name.
    on_level(rms) is called about 10x a second so the UI can show that audio is arriving."""
    def feed(a, rate):
        # cheap resample to 16 kHz (fine for speech)
        idx = np.linspace(0, len(a) - 1, int(len(a) * SR / rate)).astype(np.int64)
        out_q.put(a[idx])
        if on_level:
            on_level(float(np.sqrt(np.mean(a * a))) if len(a) else 0.0)

    if device:
        import sounddevice as sd
        info = next((d for d in sd.query_devices() if d["max_input_channels"] > 0 and device in d["name"]), None)
        if info is None:
            raise RuntimeError(f"input '{device}' not found - pick another in Settings")
        rate, ch = int(info["default_samplerate"]), min(int(info["max_input_channels"]), 2)
        print(f"🎧 Capturing: {info['name']}  ({rate} Hz, {ch} ch)")
        with sd.InputStream(device=info["index"], channels=ch, samplerate=rate, dtype="float32",
                            blocksize=int(rate * 0.1),
                            callback=lambda data, *_: feed(data.mean(axis=1).copy(), rate)):
            stop.wait()
        return

    import subprocess
    proc = subprocess.Popen([str(audio_helper())], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        header = proc.stdout.readline().decode(errors="replace").split()
        if len(header) != 3 or header[0] != "DOLMI-AUDIO":
            raise RuntimeError(proc.stderr.read().decode(errors="replace").strip() or "audio helper failed")
        rate = int(header[1])
        print(f"🎧 Capturing: all Mac audio  ({rate} Hz, mono)")
        block = int(rate * 0.1) * 4            # 100 ms of float32
        while not stop.is_set():
            data = proc.stdout.read(block)
            if not data:
                raise RuntimeError(proc.stderr.read().decode(errors="replace").strip() or "audio helper stopped")
            feed(np.frombuffer(data[: len(data) // 4 * 4], dtype=np.float32), rate)
    finally:
        proc.terminate()
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:
            proc.kill()

# ---------------------------------------------------------------- engines
class Vocabulary:
    """Applies vocabulary.txt. Locked terms travel through the translator as X0X-style
    placeholders (Opus-MT copies those unchanged), then become the wanted English."""
    def __init__(self, path):
        self.lock, self.fix = [], []   # (pattern, english)
        section = None
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        for line in (l.strip() for l in lines):
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                section = line.strip("[]").lower()
                continue
            src, _, dst = (s.strip() for s in line.partition("="))
            pat = re.compile(rf"(?<!\w){re.escape(src)}(?!\w)", re.I)
            if section in ("keep", "german"):
                self.lock.append((pat, dst or src))
            elif section == "english":
                self.fix.append((pat, dst))
        self.lock.sort(key=lambda p: -len(p[0].pattern))   # "New York" before "New"

    def protect(self, de):
        slots = []
        for pat, en in self.lock:
            def slot(_, en=en):
                slots.append(en)
                return f"X{len(slots) - 1}X"
            de = pat.sub(slot, de)
        return de, slots

    def finish(self, en, slots=()):
        for i, term in enumerate(slots):
            en = re.sub(rf"X\s*{i}\s*X", lambda _: term, en)
        for pat, good in self.fix:
            en = pat.sub(lambda _: good, en)
        return en

class Engine:
    def __init__(self, model, device, language="de"):
        from faster_whisper import WhisperModel
        if device == "auto":   # CTranslate2 has no Metal backend: Whisper runs on the CPU cores
            device = "cpu"
        ctype = "int8"
        if model == "auto":
            model = models.auto_speech()
        print(f"📥 Whisper '{model}' on {device} ({ctype})")
        self.model_name, self.device = model, device
        self.asr = WhisperModel(model, device=device, compute_type=ctype)
        self.language = language   # spoken language: "de" or "en"
        self.translators = {}      # model key -> (translator, source spm, target spm)
        # Load (and on first use download) the translator up front
        self._translator(models.translator_key(language), download=True)
        self.context = ""   # previous sentence -> better names/terms
        self.reload_terms()

    def reload_terms(self):
        """Re-read glossary.txt (Whisper spelling hints) and vocabulary.txt (translation rules)."""
        cfg = Path(os.environ.get("DOLMI_CONFIG") or Path(__file__).parent)
        g = cfg / "glossary.txt"
        lines = g.read_text(encoding="utf-8").splitlines() if g.exists() else []
        self.glossary = ", ".join(l.strip() for l in lines if l.strip() and not l.startswith("#"))
        self.terms = {w.lower() for w in re.findall(r"\w+", self.glossary) if len(w) > 2}
        self.vocab = Vocabulary(cfg / "vocabulary.txt")

    def _translator(self, key, download=False):
        """Loaded translator for a model key; never downloads mid-meeting unless asked."""
        if key in self.translators:
            return self.translators[key]
        m = models.BY_KEY[key]
        if not models.is_installed(m):
            if not download:
                return None
            models.download(m)
        import ctranslate2, sentencepiece as spm
        from huggingface_hub import snapshot_download
        d = Path(snapshot_download(m.repo, local_files_only=True))
        self.translators[key] = (
            ctranslate2.Translator(str(d), device=self.device, compute_type="int8"),
            spm.SentencePieceProcessor(model_file=str(d / "source.spm")),
            spm.SentencePieceProcessor(model_file=str(d / "target.spm")))
        print(f"📥 Translator {m.name}")
        return self.translators[key]

    def transcribe(self, audio):
        """Speech -> (text in the spoken language, language code)."""
        segs, info = self.asr.transcribe(
            audio, language=self.language,
            beam_size=5, vad_filter=True, condition_on_previous_text=False,
            # spelling hints go in as hotwords; only the previous sentence is the prompt
            hotwords=self.glossary or None, initial_prompt=self.context[-200:] or None)
        text = collapse_repeats(" ".join(s.text.strip() for s in segs).strip())
        return ("" if echoes_terms(text, self.terms) else text), info.language

    def translate(self, text, lang):
        key = models.translator_key(lang if lang in models.LANGUAGES else self.language)
        t = self._translator(key)
        if t is None:
            return f"[{lang}] {text}"   # the translator for this direction isn't installed yet
        mt, sp_src, sp_tgt = t
        text, slots = self.vocab.protect(text)
        toks = sp_src.encode(text, out_type=str)[:400] + ["</s>"]
        res = mt.translate_batch([toks], beam_size=2, max_decoding_length=256)
        out = [tok for tok in res[0].hypotheses[0] if tok not in ("</s>", "<pad>")]
        return self.vocab.finish(sp_tgt.decode(out), slots)

def collapse_repeats(text: str) -> str:
    """Whisper sometimes loops: 'He's a good man. He's a good man.' -> said once."""
    return re.sub(r"(\b.{8,}?)(?:[\s,.!?]+\1\b)+", r"\1", text, flags=re.I)

def echoes_terms(text: str, terms: set) -> bool:
    """On silence or music Whisper can read its hint list back: 'Acme, Alice, Bob, Carol, …'.
    A comma list of mostly one- or two-word items that contains our own terms is that echo."""
    parts = [x for x in re.split(r"[,;]", text) if x.strip()]
    hits = sum(w.lower() in terms for w in re.findall(r"\w+", text))
    return len(parts) >= 5 and hits >= 2 and sum(len(x.split()) <= 2 for x in parts) >= 0.7 * len(parts)

def is_junk(t: str) -> bool:
    low = t.lower()
    return len(re.sub(r"\W", "", low)) < 3 or any(h in low for h in HALLUCINATIONS)

# ------------------------------------------------------------ transcript
class Transcript:
    def __init__(self, folder="transcripts", save=True):
        self.save = save
        if not save:   # privacy setting: live subtitles only, nothing written to disk
            self.md = self.srt = None
            return
        Path(folder).mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        self.md = Path(folder) / f"meeting_{stamp}.md"
        self.srt = Path(folder) / f"meeting_{stamp}.srt"
        self.t0 = time.time()
        self.n = 0
        self.md.write_text(f"# Meeting {stamp}\n\n", encoding="utf-8")

    @staticmethod
    def _ts(s):
        h, r = divmod(s, 3600); m, s = divmod(r, 60)
        return f"{int(h):02}:{int(m):02}:{int(s):02},{int((s % 1) * 1000):03}"

    def add(self, start, end, de, en):   # appended live -> nothing lost on crash
        if not self.save:
            return
        self.n += 1
        with self.md.open("a", encoding="utf-8") as f:
            f.write(f"**{datetime.now():%H:%M:%S}** {en}  \n<sub>{de}</sub>\n\n")
        with self.srt.open("a", encoding="utf-8") as f:
            f.write(f"{self.n}\n{self._ts(start - self.t0)} --> "
                    f"{self._ts(end - self.t0)}\n{en}\n\n")

# --------------------------------------------------------------- worker
def speech_gap(audio):
    """Seconds since the last speech at the end of `audio`, or None if it holds no speech.
    Uses Silero VAD rather than loudness, so music or background noise doesn't count as talking."""
    if np.sqrt(np.mean(audio ** 2)) < SILENCE_RMS:   # cheap early-out for real silence
        return None
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    ts = get_speech_timestamps(audio, VadOptions(min_speech_duration_ms=100,
                                                 min_silence_duration_ms=150, speech_pad_ms=30))
    return (len(audio) - ts[-1]["end"]) / SR if ts else None

def worker(eng, audio_q, ui_q, tr, stop, detect=speech_gap):
    buf, recent = np.zeros(0, np.float32), np.zeros(0, np.float32)
    utt_start, last_voice, last_draft = None, 0.0, 0.0
    while not stop.is_set():
        try:
            chunk = audio_q.get(timeout=0.2)
        except queue.Empty:
            continue
        # drain backlog so we never fall behind real time
        while not audio_q.empty():
            chunk = np.concatenate([chunk, audio_q.get_nowait()])
        now = time.time()
        recent = np.concatenate([recent, chunk])[-SR:]   # VAD needs ~1 s of context
        gap = detect(recent)
        voiced = gap is not None and gap < len(chunk) / SR + 0.1
        if voiced:
            last_voice = now - gap
            if utt_start is None:
                utt_start = now
        if utt_start is None:
            continue
        buf = np.concatenate([buf, chunk])
        dur = len(buf) / SR
        pause = now - last_voice

        finalize = pause >= PAUSE_TO_FINALIZE or dur >= MAX_UTTERANCE
        if not finalize and (now - last_draft < DRAFT_EVERY or dur <= 0.8):
            continue   # not time for a draft yet
        de, lang = eng.transcribe(buf)
        last_draft = now
        # Fast talkers barely pause: a draft that already ends a sentence is final too
        finalize = finalize or (dur >= MIN_SENTENCE and de.rstrip().endswith((".", "?", "!")))
        if not finalize:
            if de and not is_junk(de):
                ui_q.put(("draft", de, eng.translate(de, lang)))
            continue
        if de and not is_junk(de):
            en = eng.translate(de, lang)
            ui_q.put(("final", de, en))
            tr.add(utt_start, now, de, en)
            eng.context = de
        buf, utt_start = np.zeros(0, np.float32), None
        ui_q.put(("draft", "", ""))

# ---------------------------------------------------------------- saving
def save_snippet(folder, text):
    """Append a sentence or word the user clicked to transcripts/saved_<date>.md."""
    path = Path(folder) / f"saved_{datetime.now():%Y-%m-%d}.md"
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- **{datetime.now():%H:%M}** {text}\n")
    return path

DOLMI_FILES = ("meeting_*", "saved_*", "assistant_*", "summary_*")

def delete_old_files(folder, days, now=None):
    """Delete Dolmi's own transcript files older than `days` (0 = keep forever). Returns how many."""
    folder = Path(folder)
    if not days or not folder.exists():
        return 0
    cutoff = (now or time.time()) - days * 86400
    deleted = 0
    for f in {f for pattern in DOLMI_FILES for f in folder.glob(pattern)}:
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except OSError as e:
                print(f"Could not delete old transcript {f.name}: {e}")
    if deleted:
        print(f"Deleted {deleted} transcript files older than {days} days")
    return deleted

if __name__ == "__main__":   # the UI lives in src/main.py
    import runpy
    runpy.run_path(str(Path(__file__).with_name("src") / "main.py"), run_name="__main__")
