"""LAT-1: per-line latency log (no text) and its p50/p95 report."""
import importlib.util
import json
import threading
import time

import streaming
from voice import reader

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def rows_in(folder):
    return [json.loads(x) for f in folder.glob("latency-*.jsonl") for x in f.read_text("utf-8").splitlines()]


def final_meta(utt, end):
    return {"id": utt, "session": 7, "utt": utt, "end": end, "trace": {"speech_end": end}}


def test_a_line_without_voice_is_written_at_once_with_iso_times_and_ms(tmp_path):
    log = streaming.LatencyLog(tmp_path, clock=lambda: 1_790_000_010.0)
    log.draft_shown(7, 3, 1_790_000_000.5)
    log.final_shown(final_meta(3, 1_790_000_000.0), "Wir fangen gleich an.", "We start soon.", 1_790_000_001.2)
    [row] = rows_in(tmp_path)
    assert row["utt"] == 3 and row["line"] == 3
    assert row["speech_end"].endswith("Z") and row["speech_end"].startswith("2026-")
    assert row["speech_end_to_german_shown_ms"] == 500 and row["speech_end_to_english_shown_ms"] == 1200
    assert row["de_words"] == 4 and row["en_words"] == 3
    assert "Wir" not in json.dumps(row)   # no text, only counts


def test_a_voiced_line_waits_for_the_voice_and_records_its_times(tmp_path):
    log = streaming.LatencyLog(tmp_path, clock=lambda: 1_790_000_002.0)
    log.final_shown(final_meta(4, 1_790_000_000.0), "Gut.", "Good.", 1_790_000_001.0, voice=True)
    assert rows_in(tmp_path) == []
    log.voice_spoken(7, 4, 1_790_000_001.5, 1_790_000_002.0, last=False)
    assert rows_in(tmp_path) == []                     # more of that line is still queued
    log.voice_spoken(7, 4, 1_790_000_002.0, 1_790_000_003.0, last=True)
    [row] = rows_in(tmp_path)
    assert row["english_shown_to_voice_start_ms"] == 500 and row["voice_start_to_voice_end_ms"] == 1500
    assert row["speech_end_to_voice_end_ms"] == 3000


def test_flush_writes_lines_the_voice_never_read(tmp_path):
    log = streaming.LatencyLog(tmp_path, clock=lambda: 1_790_000_002.0)
    log.final_shown(final_meta(5, 1_790_000_000.0), "Ja.", "Yes.", 1_790_000_001.0, voice=True)
    log.flush()
    [row] = rows_in(tmp_path)
    assert "voice_start" not in row and row["utt"] == 5


def test_reader_reports_tagged_lines():
    class TTS:
        name = "fake"

        def speak(self, text):
            pass

    seen, done = [], threading.Event()
    r = reader.Reader(TTS())
    r.on_spoken = lambda tag, start, end, last: (seen.append((tag, last, end >= start)), done.set())
    r.start()
    r.offer("Hello there.", tag=(7, 1))
    assert done.wait(2)
    r.stop()
    assert seen == [((7, 1), True, True)]


def test_pump_writes_a_row_for_each_final(api, tmp_path):
    import config
    api._session = {"token": object(), "stop": None, "transcript": None}
    sid = api._session_id()
    api._ui_q.put(("final", "Wir fangen an.", "We start.", {"id": 1, "session": sid, "utt": 1, "rev": 1,
                                                             "start": time.time() - 2, "end": time.time() - 1,
                                                             "trace": {"speech_end": time.time() - 1}}))
    threading.Thread(target=api._pump, daemon=True).start()
    deadline = time.time() + 3
    while time.time() < deadline and not rows_in(config.LOGS):
        time.sleep(0.05)
    [row] = rows_in(config.LOGS)
    assert row["utt"] == 1 and row["de_words"] == 3 and row["speech_end_to_english_shown_ms"] >= 900


def test_report_prints_p50_and_p95_per_stage(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("latency_report", ROOT / "eval" / "latency_report.py")
    rep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rep)
    f = tmp_path / "latency-2026-10-26.jsonl"
    f.write_text("\n".join(json.dumps({"speech_end_to_english_shown_ms": v}) for v in range(1, 101)) + "\n{bad",
                 encoding="utf-8")
    assert rep.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert "100 lines" in out and "speech_end_to_english_shown" in out
    assert out.split("speech_end_to_english_shown")[1].split()[1:] == ["50", "95"]
