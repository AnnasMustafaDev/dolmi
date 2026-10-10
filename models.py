"""Dolmi model catalog: what each model does, what it needs, and whether this PC has/can run it."""
import ctypes, os, sys, types
from dataclasses import dataclass
from pathlib import Path

# faster-whisper imports PyAV (FFmpeg) at startup, but only uses it to decode audio *files*;
# Dolmi passes raw samples. Smart App Control can block PyAV's unsigned FFmpeg DLLs at any time
# (it blocked libvpx here on 2026-10-05), so fall back to an empty stand-in instead of crashing.
try:
    import av  # noqa: F401
except ImportError as e:
    print(f"PyAV unavailable ({e}); not needed for live audio, continuing without it")
    sys.modules["av"] = types.ModuleType("av")

@dataclass(frozen=True)
class Model:
    key: str            # name passed to faster-whisper, or "opus-<lang>" for translators
    name: str
    repo: str
    job: str
    size_mb: int        # download size from Hugging Face
    ram_gb: int         # free RAM needed while running
    min_cores: int      # for live use on CPU
    gpu: str            # "no" | "optional" | "recommended" | "required"
    vram_gb: int
    accuracy: int       # 1-5, relative, German speech
    speed: int          # 1-5, relative, on CPU
    note: str

SPEECH, TRANSLATE = "Speech recognition (speech → text)", "Translation (German ↔ English)"

CATALOG = [
    Model("tiny", "Whisper Tiny", "Systran/faster-whisper-tiny", SPEECH, 78, 1, 2, "no", 0, 1, 5,
          "Fastest, for very weak PCs. Makes many mistakes in German."),
    Model("base", "Whisper Base", "Systran/faster-whisper-base", SPEECH, 148, 1, 2, "no", 0, 2, 4,
          "Fast and light. Noticeably less accurate than Small."),
    Model("small", "Whisper Small", "Systran/faster-whisper-small", SPEECH, 486, 2, 4, "no", 0, 3, 3,
          "Recommended for laptops without NVIDIA GPU. Good balance of speed and accuracy."),
    Model("medium", "Whisper Medium", "Systran/faster-whisper-medium", SPEECH, 1531, 4, 8, "optional", 4, 4, 2,
          "More accurate. On CPU it needs a strong processor and adds 2-4 s delay."),
    Model("large-v3-turbo", "Whisper Large v3 Turbo", "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
          SPEECH, 1622, 6, 12, "recommended", 4, 5, 2,
          "Near best accuracy, much faster than Large v3. Best with an NVIDIA GPU."),
    Model("large-v3", "Whisper Large v3", "Systran/faster-whisper-large-v3", SPEECH, 3091, 8, 16, "required", 6, 5, 1,
          "Highest accuracy. Too slow for live subtitles without an NVIDIA GPU."),
]

# Dolmi translates between German and English: German speech gets English subtitles, English
# speech gets German subtitles. Each direction has its own Opus-MT translator (CTranslate2, int8).
LANGUAGES = {"de": "German", "en": "English"}
SUBTITLES = {"de": "en", "en": "de"}          # spoken language -> subtitle language
TRANSLATOR_SIZES = {"de": 152, "en": 152}

def translator_key(lang):
    """Model key of the translator for a spoken language ("opus-de" = German → English)."""
    return f"opus-{lang}" if lang in TRANSLATOR_SIZES else None

for _code, _mb in TRANSLATOR_SIZES.items():
    _src, _dst = LANGUAGES[_code], LANGUAGES[SUBTITLES[_code]]
    CATALOG.append(Model(
        f"opus-{_code}", f"Opus-MT {_src} → {_dst}", f"gaudi/opus-mt-{_code}-{SUBTITLES[_code]}-ctranslate2",
        TRANSLATE, _mb, 1, 2, "no", 0, 4, 5, f"Translates {_src} sentences into {_dst} in ~0.06 s, even on CPU."))
BY_KEY = {m.key: m for m in CATALOG}

# ------------------------------------------------------------------ this PC
class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

def system_info():
    m = _MemStatus(); m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {"ram_gb": round(m.ullTotalPhys / 2**30), "cores": os.cpu_count() or 1,
            "gpu": gpu_ready()}

def gpu_ready():
    """NVIDIA GPU that CTranslate2 can actually use (card + CUDA 12 libraries)."""
    import ctypes.util, ctranslate2
    return ctranslate2.get_cuda_device_count() > 0 and all(
        ctypes.util.find_library(l) for l in ("cublas64_12", "cudnn64_9"))

def fit(model, pc):
    """(verdict, colour key) for how well this PC runs the model live."""
    if pc["ram_gb"] < model.ram_gb + 2:
        return "Not enough RAM", "live"
    if pc["gpu"]:
        return "Runs well (GPU)", "ok"
    if model.gpu == "required":
        return "Needs NVIDIA GPU", "live"
    if pc["cores"] < model.min_cores or model.gpu == "recommended":
        return "Slow on this PC", "draft"
    if model.gpu == "optional":
        return "Works, 2-4 s delay", "draft"
    return "Runs well", "ok"

# ------------------------------------------------------------------ install state
def _cache_dir(model):
    from huggingface_hub.constants import HF_HUB_CACHE
    return Path(HF_HUB_CACHE) / ("models--" + model.repo.replace("/", "--"))

def is_installed(model):
    # faster-whisper fetches only the files it needs, so check for the weights, not a full snapshot
    snapshots = _cache_dir(model) / "snapshots"
    return snapshots.exists() and any((s / "model.bin").exists() for s in snapshots.iterdir())

def downloaded_mb(model):
    # Without symlink support (most Windows PCs) files end up in snapshots/, not blobs/
    root = _cache_dir(model)
    if not root.exists():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink()) / 1e6

def download(model):
    if model.job == SPEECH:   # same files faster-whisper loads
        from faster_whisper.utils import download_model
        download_model(model.key)
    else:
        from huggingface_hub import snapshot_download
        snapshot_download(model.repo)

def uninstall(model):
    from huggingface_hub import scan_cache_dir
    for repo in scan_cache_dir().repos:
        if repo.repo_id == model.repo:
            scan_cache_dir().delete_revisions(*(r.commit_hash for r in repo.revisions)).execute()
