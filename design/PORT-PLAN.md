# Dolmi → Webview UI port (Bridge design)

Plan to replace Dolmi's tkinter UI with the approved **Bridge** design, running on a pywebview/WebView2 shell — the same architecture Talkflow (Sotto) already uses. Written 2026-10-09.

## Why this is lower-risk than it looks
Dolmi already splits cleanly into **core logic** and **UI**. The core is UI-agnostic and reused untouched; only the tkinter layer is swapped. Line counts today:

| Module | Lines | Port action |
|---|---|---|
| `live_subs.py` (Engine, Vocabulary, Transcript, worker, CUDA) | 340 | **Reuse as-is** (move under `core/`) |
| `assistant.py` (providers, DPAPI keys, build_system, stream) | 222 | **Reuse as-is** |
| `inbox.py` (SQLite chats) | 103 | **Reuse as-is** |
| `models.py` (model catalog, GPU fit, download) | 142 | **Reuse as-is** |
| `stealth.py` (capture exclusion, hotkey) | 121 | **Reuse as-is** |
| `app.py` (tkinter GUI) | 1831 | **Replace** → `ui/app.py` Api bridge (~250) |
| `overlay.py` (tkinter Toplevel) | 297 | **Replace** → `ui/overlay.py` (~80) + `overlay.html` |
| `widgets.py`, `theme.py` | 87 | **Drop** (CSS handles it) |

Net: ~2200 lines of tkinter UI become ~350 lines of Python bridge + the HTML we already designed. The 900+ lines of core logic are preserved verbatim.

## Target structure (mirrors Talkflow)
```
dolmi/
  src/
    main.py              # entry: logging, AppUserModelID, window, webview.start(icon=)
    core/
      live_subs.py        asr/translate Engine, Vocabulary, Transcript, worker  (moved)
      assistant.py        LLM providers + DPAPI                                   (moved)
      inbox.py  models.py  stealth.py                                             (moved)
    ui/
      app.py             # Api — the JS bridge (replaces all of tkinter app.py)
      overlay.py         # lazy frameless caption bar (like Talkflow)
      web/
        index.html       # the Bridge UI (from demo-final.html, wired to the bridge)
        overlay.html     # the floating subtitle bar
        dolmi.png
  assets/ … vocabulary.txt glossary.txt  (unchanged; still read by core)
  create_shortcuts.ps1   # repoint to src/main.py (venv pythonw)
```
Built and run from the source folder exactly like today (shortcuts → `venv\Scripts\pythonw.exe src\main.py`), so the install model doesn't change.

## The JS ↔ Python bridge (what the HTML calls)
`ui/app.py` exposes an `Api` object as `window.pywebview.api`. Methods map 1:1 to the demo's UI:

- **state()** → provider, model, hasKey, language, speechModel, device(`CUDA·large-v3-turbo`), showGerman, invisible, saveTranscripts, keepDays, theme, audioDevice.
- **start_listening() / stop_listening()** → spins up `live_subs.worker` on the chosen device; emits each finished line.
- **devices()** → `live_subs.output_devices()` (lazy, already off the startup path).
- **Live events pushed to JS** (Python → `window.evaluate_js`): `onLine(ts, de, en)` appends a Bridge row; `onLevel(rms)` drives the waveform; `onEngine(label)` sets the status chip.
- **Meetings**: `list_meetings()`, `read_meeting(name)` (returns DE/EN pairs), `delete_meeting(name)`, `export_meeting(name)` — over the `meeting_*.md` files.
- **Assistant**: `ask(q)` streams via `assistant.stream_answer` → `onChunk`; `summarize()` → `onSummary*`; chats saved through `inbox`.
- **Vocabulary**: `read_vocab()/save_vocab(text)` + `read_glossary()/save_glossary(text)` → write the files, then `engine.reload_terms()` live (already supported).
- **Settings**: `set_setting(key, value)`; `set_language`, `set_speech_model` (reload engine), `test_key`.
- **Overlay/stealth**: `show_overlay()/hide_overlay()` (lazy, like Talkflow), `toggle_invisible(on)` → `stealth.apply`.

### Live caption flow (the heart of it)
`live_subs.worker(...)` already produces `(start, end, de, en)` per finalized line and appends to `Transcript`. Today it pushes to a tkinter queue; the port points that same callback at `Api._emit("onLine", ts, de, en)`, which runs `window.evaluate_js` to append a Bridge row (DE left, EN right) and update the live counters. No change to recognition/translation logic.

## Phases
1. **Scaffold** — create `src/` tree, move core modules under `core/` (imports adjusted), write `main.py` + an `Api` skeleton, drop in `index.html` from `demo-final.html` wired to `window.pywebview.api`. App launches, shows the Bridge, nav works. *No real audio yet.*
2. **Live captions** — wire `start/stop_listening` → `worker` → `onLine`/`onLevel`. German→English rows stream into the bridge on the GPU. This is the demo coming alive.
3. **Meetings + Vocabulary** — list/read/delete/export transcripts; edit glossary/vocabulary with live reload.
4. **Assistant + summaries** — streaming answers, inbox history, meeting summaries.
5. **Overlay + stealth** — lazy frameless caption bar (`overlay.html`), invisible mode, global hotkey, taskbar exclusion.
6. **Settings, onboarding, polish** — model/device switch with engine reload, Paper/Ink themes, onboarding, icon via `webview.start(icon=)`, shortcut repoint, tests.

Each phase is independently runnable and testable.

## Guardrails
- **Don't break the working app.** Build the `src/` tree alongside the current tkinter `app.py`; only repoint the shortcuts (Phase 6) once the webview build is at parity. The old `app.py` stays until then as the fallback.
- **GPU stays.** `_enable_cuda_dlls()` + the `large-v3-turbo` auto-select carry over in `live_subs.py` unchanged.
- **Data is compatible.** Same `meeting_*.md`, `dolmi.db`, `vocabulary.txt`, `glossary.txt`, settings — no migration.
- **No GUI test from the sandbox.** The dev can't be driven headless here; each phase needs a quick manual launch to confirm, like Talkflow.

## Effort (rough)
Phase 1–2 (shell + live captions, the visible win): ~1 focused session. Phases 3–6: ~1–2 more. Total meaningfully less than it sounds because the core is reused and the HTML is done.
