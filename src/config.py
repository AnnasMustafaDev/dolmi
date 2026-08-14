"""Settings, .env and data-folder helpers for the webview Dolmi — ported UI-free from app.py.

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


