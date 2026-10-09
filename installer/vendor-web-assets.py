"""Vendor the web UI's fonts and icons into src/ui/web/vendor so Dolmi works offline and contacts
no font/CDN server at runtime. Re-run only to change versions; the output is committed.

    venv\\Scripts\\python.exe installer\\vendor-web-assets.py
"""
import re
import urllib.request
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "ui" / "web"
OUT = WEB / "vendor"
LUCIDE = "https://unpkg.com/lucide@0.469.0/dist/umd/lucide.min.js"
FONTS_CSS = ("https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,600;"
             "1,9..144,500&family=Hanken+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600&display=swap")
KEEP_SUBSETS = ("latin", "latin-ext")   # covers German (ä ö ü ß) and most European text
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")      # makes Google Fonts serve woff2


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
        return r.read()


def main():
    (OUT / "fonts").mkdir(parents=True, exist_ok=True)
    (OUT / "lucide.min.js").write_bytes(get(LUCIDE))

    css = get(FONTS_CSS).decode("utf-8")
    blocks = re.findall(r"/\* ([\w-]+) \*/\s*(@font-face\s*\{.*?\})", css, re.S)
    kept = []
    for subset, block in blocks:
        if subset not in KEEP_SUBSETS:
            continue
        family = re.search(r"font-family:\s*'([^']+)'", block).group(1).replace(" ", "")
        style = re.search(r"font-style:\s*(\w+)", block).group(1)
        weight = re.search(r"font-weight:\s*([\d ]+);", block).group(1).replace(" ", "-")
        url = re.search(r"url\((https://[^)]+\.woff2)\)", block).group(1)
        name = f"{family}-{style}-{weight}-{subset}.woff2"
        (OUT / "fonts" / name).write_bytes(get(url))
        kept.append(f"/* {subset} */\n" + block.replace(url, f"fonts/{name}"))
    (OUT / "fonts.css").write_text("/* Vendored from Google Fonts (SIL OFL 1.1) by installer/vendor-web-assets.py */\n"
                                   + "\n".join(kept) + "\n", encoding="utf-8")
    (OUT / "LICENSES.txt").write_text(
        "Fonts (fonts/): Fraunces, Hanken Grotesk, JetBrains Mono — SIL Open Font License 1.1\n"
        "  https://openfontlicense.org\n"
        "Icons (lucide.min.js): Lucide 0.469.0 — ISC License, https://lucide.dev/license\n", encoding="utf-8")
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"vendored {len(kept)} font faces + Lucide into {OUT} ({size // 1024} KB)")


if __name__ == "__main__":
    main()
