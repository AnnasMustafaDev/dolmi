"""Dolmi inbox: every Assistant conversation, kept in a local SQLite file (dolmi.db, never uploaded).

One chat per Assistant session (or per Clear); each message is one question with its answer.
Times are stored in UTC as ISO 8601 and shown in local time by the UI.
"""
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id         INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    provider   TEXT NOT NULL DEFAULT '',
    model      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    id       INTEGER PRIMARY KEY,
    chat_id  INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    asked_at TEXT NOT NULL,
    question TEXT NOT NULL,
    answer   TEXT NOT NULL DEFAULT '',
    error    TEXT NOT NULL DEFAULT '',
    source   TEXT NOT NULL DEFAULT 'spoken'   -- 'spoken' or 'typed'
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id);
"""

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def local(iso):
    """'2026-10-06T11:30:00+00:00' -> datetime in the PC's time zone."""
    return datetime.fromisoformat(iso).astimezone()

def connect(path):
    new = not Path(path).exists()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    if new:
        import_markdown(db, Path(path).parent)   # answers saved as files before the inbox existed
    return db

def start_chat(db, provider="", model="", started_at=None):
    cur = db.execute("INSERT INTO chats (started_at, provider, model) VALUES (?, ?, ?)",
                     (started_at or now(), provider, model))
    db.commit()
    return cur.lastrowid

def add_message(db, chat_id, question, answer, error="", source="spoken", asked_at=None):
    db.execute("INSERT INTO messages (chat_id, asked_at, question, answer, error, source) VALUES (?, ?, ?, ?, ?, ?)",
               (chat_id, asked_at or now(), question, answer, error, source))
    db.commit()

def list_chats(db, search=""):
    """Newest first, with the first question as the title. Chats without messages are left out."""
    like = f"%{search.strip()}%"
    return db.execute("""
        SELECT c.id, c.started_at, c.provider, c.model, COUNT(m.id) AS questions,
               (SELECT question FROM messages WHERE chat_id = c.id ORDER BY id LIMIT 1) AS title
        FROM chats c JOIN messages m ON m.chat_id = c.id
        WHERE ? = '%%' OR c.id IN (SELECT chat_id FROM messages WHERE question LIKE ? OR answer LIKE ?)
        GROUP BY c.id ORDER BY c.started_at DESC, c.id DESC""", (like, like, like)).fetchall()

def messages(db, chat_id):
    return db.execute("SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()

def delete_chat(db, chat_id):
    db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    db.commit()

def delete_older_than(db, days):
    """Privacy setting 'Delete old files'; 0 keeps everything. Returns how many chats were deleted."""
    if not days:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    n = db.execute("DELETE FROM chats WHERE started_at < ?", (cutoff,)).rowcount
    db.commit()
    return n

def as_markdown(db, chat_id):
    lines = []
    for m in messages(db, chat_id):
        lines.append(f"### {local(m['asked_at']):%Y-%m-%d %H:%M} — {m['question']}\n\n{m['answer']}")
        if m["error"]:
            lines.append(f"⚠ {m['error']}")
    return "\n\n".join(lines) + "\n"

def import_markdown(db, folder):
    """One-time import of the assistant_<date>.md files Dolmi wrote before the inbox existed."""
    for f in sorted(Path(folder).glob("assistant_*.md")):
        day = f.stem.removeprefix("assistant_")
        parts = re.split(r"^### (\d\d:\d\d:\d\d) — (.*)$", f.read_text(encoding="utf-8"), flags=re.M)
        entries = [(parts[i], parts[i + 1].strip(), parts[i + 2].strip()) for i in range(1, len(parts) - 2, 3)]
        if not entries:
            continue
        stamp = lambda t: datetime.fromisoformat(f"{day}T{t}").astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")
        chat = start_chat(db, started_at=stamp(entries[0][0]))
        for t, q, a in entries:
            add_message(db, chat, q, a, asked_at=stamp(t))
