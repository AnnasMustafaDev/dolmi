# Dolmi

**Live meeting subtitles, translated to English — private and running entirely on your PC.**

Dolmi listens to whatever plays through your speakers (Teams, Zoom, Meet, Slack, a browser, anything), transcribes the speech, and shows a live English translation in a floating subtitle bar. German is the default spoken language, with 27+ other languages selectable. Speech recognition and translation run locally and your audio never leaves your PC; only the optional Assistant sends text to the AI provider you choose ([privacy policy](PRIVACY.md)).

> Windows 10 (2004+) / 11. MIT licence. The installer ships the official, PSF-signed Python runtime as its launcher instead of a packaged `.exe`, because Windows Smart App Control blocks unsigned executables.

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
| UI | HTML/CSS in a native window (pywebview + WebView2); the subtitle bar is a frameless, transparent webview |
| Installer | Inno Setup `setup.exe` with the bundled, PSF-signed embeddable Python |

See [SPECS.md](SPECS.md) for the full technical details, models, languages and system requirements.

---

## Screenshots

**Models tab** — download, switch and remove speech models, each labelled for your PC:

![Dolmi Models](assets/screenshots/models.png)

---

## Install

1. Download **`Dolmi-Setup-x.y.z.exe`** from [Releases](https://github.com/AnnasMustafaDev/dolmi/releases).
2. Run it. No admin rights are needed: it installs for your user, and offers "all users" if you prefer.
3. Optional during setup: tick **GPU speech recognition** if you have an NVIDIA card (downloads about 1.3 GB). Dolmi then uses Whisper large-v3-turbo on the GPU.
4. Open Dolmi, press **▶ Start**, and join your meeting. The first start downloads the speech model once; *Settings → This PC & models* shows its progress and what your PC can run.

The installer isn't code-signed yet, so Windows may show *"Windows protected your PC"*. Click **More info → Run anyway**. On PCs with **Smart App Control** turned on, unsigned installers can be blocked entirely.

Uninstall from *Settings → Apps*. Your transcripts (`Documents\Dolmi`) and settings (`%APPDATA%\Dolmi`) are kept.

### Run from source

1. Install **Python 3.12** from [python.org](https://www.python.org/downloads/) (tick *Add to PATH*), then clone this repo.
2. Double-click **`start.bat`**. It creates a virtual environment, installs dependencies, adds Desktop and Start Menu shortcuts, and starts Dolmi.

Build the installer yourself with Inno Setup 6.7+ (`winget install -e --id JRSoftware.InnoSetup`): `powershell -ExecutionPolicy Bypass -File installeruild.ps1`.

---

## Privacy

No account, no telemetry, no analytics. Speech recognition and translation run entirely on your PC. Dolmi uses the network only for the optional Assistant and summaries (your chosen AI provider), to download models once (Hugging Face), and for the optional GPU add-on at setup (PyPI). Details: [PRIVACY.md](PRIVACY.md).

Transcribing other people processes their personal data, so tell meeting participants you are using live transcription. Dolmi shows a one-time reminder and lets you turn off saving or auto-delete old files in Settings. *(Not legal advice.)*

### Code signing policy

Every installer is built by GitHub Actions from a tagged commit in this repository ([release workflow](.github/workflows/release.yml)). The workflow installs, launches, upgrades and uninstalls each build before it publishes the release. Releases are **not code-signed yet**. Signing is planned through [SignPath Foundation](https://signpath.org), and this section will name the certificate once that's approved.

| Role | Who |
|---|---|
| Committers and reviewers | [Annas Mustafa](https://github.com/AnnasMustafaDev) |
| Approvers (approve each signed release) | [Annas Mustafa](https://github.com/AnnasMustafaDev) |

Dolmi never transfers information to other networked systems unless the user asks for it, as described in the [privacy policy](PRIVACY.md).

---

## Tests

```bash
venv\Scripts\python.exe -m pytest tests/test_helpers.py
venv\Scripts\python.exe tests/test_webview_bridge.py
```

The helper tests cover the transcript, vocabulary and question-detection logic. The bridge tests exercise the webview API headlessly: captions, meetings, vocabulary, chats, archive, settings and models. Neither touches the network.

`installer	est-install.ps1` installs, launches, upgrades and uninstalls a built `setup.exe`. It runs on GitHub's Windows runners for every release (*Actions → Installer test* runs it on demand). On a PC with Smart App Control on, it stops safely, because Windows blocks unsigned installers there.

---

## License

MIT — see [LICENSE](LICENSE).
