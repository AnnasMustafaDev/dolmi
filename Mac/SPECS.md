# Dolmi 1.0 — App specification

**Dolmi** shows live English subtitles for German speech in any meeting app — Teams, Zoom, Slack huddles, Google Meet, Webex — or any audio playing on the Mac. Everything runs locally: no cloud, no account, nothing uploaded.

## At a glance

| | |
|---|---|
| Version | 1.0 |
| Platform | macOS 14.2 Sonoma or later, Apple Silicon (Intel builds from source) |
| Languages | German speech → English subtitles (default), or English speech → German subtitles. Set in Settings → Spoken language |
| Delay | Draft text ~1 s after speech; final sentence ~1–2.5 s after the speaker pauses (CPU, `small` model) |
| Translation speed | ~0.06 s per sentence on CPU |
| Privacy | 100% offline after the first model download |
| Install | Open `Dolmi-x.y.z.dmg` → drag Dolmi to Applications |
| Install location | `/Applications/Dolmi.app` · uninstall by moving it to the Bin |

## System requirements

| | Minimum | Recommended |
|---|---|---|
| OS | macOS 14.2 | macOS 15 or later |
| CPU | Apple M1 | Apple M3/M4 or later (tested: Apple M4, 10 cores) |
| RAM | 8 GB | 16 GB |
| Disk | 2 GB free | 4 GB free (if using a large model) |
| GPU | Not used — CTranslate2 has no Metal backend, so Whisper runs on the CPU cores | — |
| Python | Bundled in Dolmi.app; 3.12 to run from source (`brew install python@3.12`) | 3.12 |
| Internet | Only for installation and first model download | — |
| Audio | Any speakers/headphones; Dolmi records what the Mac plays (Core Audio process tap, asks for *System Audio Recording* permission once), or a microphone chosen in Settings → Listen to | — |

Disk use: app + components ~305 MB · speech model `small` 464 MB · translator 146 MB · optional `large-v3-turbo` 1.6 GB.

## How it works

```
Teams audio ─► Core Audio process tap (dolmi-audio helper) → 16 kHz mono
           ─► voice/pause detection (energy threshold + Silero VAD)
           ─► Whisper speech recognition (German text)
           ─► vocabulary: lock names/terms
           ─► Opus-MT translation (English text)
           ─► vocabulary: restore terms, fix known mistakes
           ─► Dolmi window + subtitle bar + transcript files (.md + .srt)
```

## Models

| Model | Job | Source | Size | Licence |
|---|---|---|---|---|
| Whisper `small` (CTranslate2, int8) | German speech → German text, default on CPU | OpenAI Whisper via `Systran/faster-whisper-small` | 464 MB | MIT |
| Whisper `medium` (optional) | More accurate, slower | `Systran/faster-whisper-medium` | ~1.5 GB | MIT |
| Whisper `large-v3-turbo` (optional) | Best accuracy, live only on Pro/Max/Ultra chips | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | 1.6 GB | MIT |
| Opus-MT de→en (CTranslate2) | German text → English | `gaudi/opus-mt-de-en-ctranslate2` | 152 MB | CC-BY 4.0 |
| Opus-MT en→de (CTranslate2) | English text → German; downloaded when English is picked | `gaudi/opus-mt-en-de-ctranslate2` | 152 MB | CC-BY 4.0 |
| Silero VAD | Skips silence before recognition | Bundled with faster-whisper | < 2 MB | MIT |

Models download from Hugging Face and are cached in `~/.cache/huggingface`. The **Models** tab in Dolmi lists every model with its size, accuracy and speed ratings and how well it runs on this Mac (based on RAM and CPU cores), marks the recommended one, and lets the user download, switch (**Use**) and uninstall models. The model in use and the translator can't be uninstalled.

## Technologies

| Area | Technology |
|---|---|
| Language | Python 3.12 |
| Speech recognition | faster-whisper 1.2 (CTranslate2 inference engine, int8 on the CPU with Apple Accelerate) |
| Translation | CTranslate2 4.8 + SentencePiece tokenizer |
| Audio capture | `audio/dolmi-audio` — a small Swift helper using a Core Audio process tap (macOS 14.2+); sounddevice (PortAudio) for microphones |
| Signal processing | NumPy (resampling, energy detection) |
| UI | HTML/CSS in a native window — pywebview 6 on WKWebView (Cocoa) — bridged to Python (`src/ui/app.py`); "Bridge" design with Paper (light) and Ink (dark) themes; the subtitle bar is a frameless, transparent, always-on-top webview |
| Fonts & icons | Fraunces, Hanken Grotesk, JetBrains Mono (SIL OFL) and Lucide icons (ISC), bundled in `src/ui/web/vendor` — nothing is loaded from the internet |
| Packaging | PyInstaller `Dolmi.app` (the source ships unchanged in `Contents/Resources/app`) in a `Dolmi-x.y.z.dmg`, signed with a local self-signed certificate so permissions survive rebuilds (`packaging/build.sh`) |

**Deliberately not used:** PyTorch and Hugging Face Transformers. They aren't needed for running these models and would add gigabytes to the app.

## Methods

- **Pause-based segmentation:** a sentence ends after 0.6 s of silence, or is cut after 12 s of continuous speech. This avoids sentences being split mid-word.
- **Draft + final text:** a quick draft is shown about every second (yellow) and replaced by the final sentence on the pause (white).
- **Real-time catch-up:** queued audio is merged, so delay never builds up when the Mac is busy.
- **Hallucination filter:** drops Whisper's typical German "silence" phrases (e.g. *"Untertitel im Auftrag des ZDF"*).
- **Context prompting:** Whisper gets the spelling-hints list and the previous sentence, so names and terms come out consistently.
- **Vocabulary locking:** terms in `[keep]` / `[german]` are swapped for placeholders (`X0X`) before translation and restored afterwards, so names like *Acme* or *Project Falcon* are never translated. `[english]` rules fix known mistranslations.
- **Crash-safe transcripts:** every sentence is appended to disk immediately (Markdown + SRT with real timestamps).
- **Automatic device choice:** always the CPU on a Mac (`small` model); Medium and larger can be picked by hand.

## Features

- Main window: Live transcript, Saved items, Vocabulary editor, Settings
- Click a sentence / double-click a word to save it (`transcripts\saved_<date>.md`)
- Copy all, Export transcript
- Vocabulary and spelling hints editable in the app, applied without restart
- Always-on-top subtitle bar: move, resize from any edge, text size, German line on/off, opacity
- Settings and subtitle-bar position remembered (`settings.json`)
- Audio meter in the top bar while live; warns after 8 s with no sound (wrong speaker selected)
- **Dolmi folder**: transcripts and saved items live in `~/Documents/Dolmi` per Mac user; Settings → Privacy shows it and can move it (Change…). Models live in the per-user Hugging Face cache, also shown there
- Errors logged to `dolmi.log`

## Files

| File | Purpose |
|---|---|
| `src/main.py` | Entry point: the native window, logging, invisible mode at launch, recovery hotkey |
| `src/ui/app.py` | The JS bridge (`window.pywebview.api`): live captions, meetings, assistant, settings, models |
| `src/ui/overlay.py` | Subtitle bar window |
| `src/ui/web/` | The UI: `index.html` (main window), `overlay.html` (subtitle bar), bundled fonts/icons |
| `src/config.py` | Settings and where files live (`~/Library/Application Support/Dolmi`, `~/Documents/Dolmi`) |
| `cloud_speech.py` | Optional cloud captions: Gemini (hear + translate), OpenAI or Cloudflare Whisper (hear; Opus-MT translates) |
| `inbox.py` | Local SQLite store (`dolmi.db`) |
| `stealth.py` | Invisible mode (`NSWindow.sharingType`, Dock hiding) and the ⌃⌥⇧D hotkey (Carbon) |
| `models.py` | Model catalog, Mac check, download / uninstall |
| `assistant.py` | Optional cloud helper (Claude / OpenAI / Gemini / NVIDIA / Cloudflare), keys encrypted with a Keychain secret |
| `live_subs.py` | Engine: capture, recognition, translation, transcripts |
| `vocabulary.txt` | Translation rules (in `~/Library/Application Support/Dolmi`) |
| `glossary.txt` | Spelling hints for speech recognition (in `~/Library/Application Support/Dolmi`) |
| `audio/` | `dolmi-audio.swift`: the system-audio capture helper |
| `packaging/` | `build.sh`, `dolmi.spec` (PyInstaller), `launcher.py` |
| `transcripts/` | Meeting transcripts and saved items (kept on uninstall) |

## Known limits

- Only German and English: other spoken languages are not translated.
- Doesn't tell speakers apart.
- Accuracy drops with overlapping speakers, bad audio or fast speech; the CPU `small` model is the main limit.
- A bare "Chris" can't be resolved to Chris1 / Chris2; only full names are labelled.
- Records from the default output device at start; restart the session after switching headphones/speakers.

## Privacy

Audio and text never leave the Mac. Transcribing colleagues is processing personal data, so tell meeting participants you're using it; Dolmi shows a one-time reminder. (Not legal advice.)

Settings → Privacy: turn saving transcripts off (live subtitles only), or delete Dolmi's files (meeting, saved) automatically after 7, 30 or 90 days. Other files in the folder are never touched.
