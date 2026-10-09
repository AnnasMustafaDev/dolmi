"""Settings, .env and data-folder helpers for Dolmi's webview UI.

Reads the same settings.json, .env, vocabulary.txt and glossary.txt as the existing tkinter app, so
both UIs share one configuration and one data folder.
"""
import copy
import ctypes
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # repo root (dolmi/)
SETTINGS = ROOT / "settings.json"

DEFAULTS = {
    "model": "auto", "device": "auto", "font": 22, "opacity": 0.88,
    "show_german": True, "overlay": True, "overlay_geometry": None, "audio_device": "", "language": "de",
    "mode": "translate", "ai_provider": "claude", "ai_models": {}, "api_keys": {}, "ai_context": "",
    "ai_length": "short", "pro": "",
    "save_transcripts": True, "keep_days": 0, "privacy_seen": False, "data_folder": "",
    "invisible": False, "theme": "paper",
}


def load_env():
    """Read KEY=VALUE lines from .env into os.environ without overriding what's already set."""
    import os
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def seed_example_files():
    """First run: create vocabulary.txt / glossary.txt from the bundled *.example.txt templates."""
    for name in ("vocabulary", "glossary"):
        real, example = ROOT / f"{name}.txt", ROOT / f"{name}.example.txt"
        if not real.exists() and example.exists():
            shutil.copyfile(example, real)


def load_settings():
    s = copy.deepcopy(DEFAULTS)   # nested dicts (api_keys, ai_models) must never alias DEFAULTS
    try:
        s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
        s["ai_context"] = s["ai_context"] or s.pop("ai_notes", "")
    except (OSError, ValueError) as e:
        if SETTINGS.exists():
            print(f"settings.json unreadable, using defaults: {e}")
    return s


def save_settings(s):
    """Atomic write: a crash or concurrent save can never leave a truncated settings.json
    (which would silently drop the saved API keys on the next load)."""
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS)


def documents_folder() -> Path:
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # 5 = CSIDL_PERSONAL
        return Path(buf.value)
    return Path.home() / "Documents"


def data_folder(settings) -> Path:
    return Path(settings["data_folder"]) if settings.get("data_folder") else documents_folder() / "Dolmi"
