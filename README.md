# Dolmi

**Live meeting subtitles, translated to English — private and running entirely on your PC.**

Dolmi listens to whatever plays through your speakers (Teams, Zoom, Meet, Slack, a browser, anything), transcribes the speech, and shows a live English translation in a floating subtitle bar. German is the default spoken language, with 27+ other languages selectable. Nothing is uploaded — speech recognition and translation run locally.

> Windows only. Dolmi runs on the signed python.org Python rather than as a packaged `.exe`, because Windows Smart App Control blocks unsigned executables and PyTorch DLLs.

![Dolmi live transcript](assets/screenshots/live.png)

<p align="center"><em>Live view — English subtitles appear as people speak, with the original line kept too. Click a sentence to save it.</em></p>

![Dolmi floating subtitle bar](assets/screenshots/overlay.png)

<p align="center"><em>The floating, always-on-top subtitle bar — drag to move, resize from any edge, fits any text size.</em></p>

---

## Features

- **Live subtitles** in a draggable, resizable, always-on-top bar — with the original line underneath, adjustable text size and opacity, and the last few sentences kept on screen.
- **Any meeting app or browser** — Dolmi captures system audio (WASAPI loopback), so you still hear the meeting and need no virtual audio cable.
- **Any language → English** — 27+ spoken languages, auto-detect, German by default.
- **Transcripts** saved per meeting as Markdown + SRT (with real timings); click a sentence or double-click a word to save it.
- **Vocabulary & glossary** — teach Dolmi your names, products and terms so they're spelled and translated correctly, editable in-app and applied without a restart.
- **Models tab** — download, remove and switch speech models; each shows its size, accuracy and whether your PC can run it.

---

## How it works

```
App plays audio ─► WASAPI loopback capture (you still hear it)
                ─► Silero VAD splits on pauses
                ─► faster-whisper (speech → text, CTranslate2 int8)
                ─► Opus-MT (source language → English, CTranslate2)
                ─► floating subtitle bar  +  transcript (.md + .srt)
```

| Part | Choice |
|---|---|
| Audio capture | `pyaudiowpatch` (WASAPI loopback) |
| Speech recognition | `faster-whisper` — `small` on CPU, `large-v3-turbo` on an NVIDIA GPU |
| Translation | Helsinki-NLP Opus-MT, run with CTranslate2 (no PyTorch) |
| UI | CustomTkinter; floating bar in plain Tk |

See [SPECS.md](SPECS.md) for the full technical details, models, languages and system requirements.

---

## Screenshots

**Models tab** — download, switch and remove speech models, each labelled for your PC:

![Dolmi Models](assets/screenshots/models.png)

---

## Setup

1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/) (tick *Add to PATH*).
2. Clone this repo (or download it).
3. Double-click **`start.bat`**. It creates a virtual environment, installs dependencies (~5–10 min the first time), and adds a **Dolmi** shortcut to the Desktop and Start Menu.
4. Open Dolmi, press **▶ Start**, and join your meeting — no audio settings to change.

On first run, `vocabulary.txt` and `glossary.txt` are created from the bundled `*.example.txt` templates; edit them with your own terms. Copy `.env.example` to `.env` for optional local configuration — `.env` is gitignored and never committed.

---

## Privacy

Speech recognition and translation run entirely on your PC — nothing is uploaded.

Transcribing other people processes their personal data, so tell meeting participants you are using live transcription. Dolmi shows a one-time reminder and lets you turn off saving or auto-delete old files in Settings → Privacy. *(Not legal advice.)*

---

## Tests

```bash
python -m pytest tests/
```

The helper tests cover the transcript, vocabulary and question-detection logic. They have no external dependencies and don't touch the network.

---

## License

MIT — see [LICENSE](LICENSE).
