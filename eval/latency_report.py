"""p50/p95 per stage of one day's latency log (LAT-1).

    venv\\Scripts\\python.exe eval\\latency_report.py              # today (UTC)
    venv\\Scripts\\python.exe eval\\latency_report.py 2026-10-26   # that day
    venv\\Scripts\\python.exe eval\\latency_report.py path\\to\\latency-2026-10-26.jsonl

The file is %LOCALAPPDATA%\\Dolmi\\latency-YYYY-MM-DD.jsonl (~/Library/Logs/Dolmi on a Mac): one row per
final line with its stage deltas in ms (keys ending in _ms). No text is in it.
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def percentile(values, q):
    """Nearest-rank percentile of `values` (q in 0..100)."""
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))
    return s[k]


def stages(rows):
    """{stage key: [ms, ...]} over every *_ms field in `rows`."""
    out = {}
    for row in rows:
        for k, v in row.items():
            if k.endswith("_ms") and isinstance(v, (int, float)):
                out.setdefault(k, []).append(v)
    return out


def read_rows(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue                 # a line cut off by a crash: skip it
    return rows


def report(rows):
    lines = [f"{len(rows)} lines", f"{'stage':40} {'n':>5} {'p50 ms':>8} {'p95 ms':>8}"]
    for k, v in sorted(stages(rows).items()):
        lines.append(f"{k[:-3]:40} {len(v):5} {percentile(v, 50):8} {percentile(v, 95):8}")
    return "\n".join(lines)


def default_path(day):
    import dolmi_platform
    return Path(dolmi_platform.log_dir()) / f"latency-{day}.jsonl"


def main(argv):
    arg = argv[0] if argv else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = Path(arg) if arg.endswith(".jsonl") else default_path(arg)
    if not path.exists():
        print(f"No latency log at {path}")
        return 1
    print(report(read_rows(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
