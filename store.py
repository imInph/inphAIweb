"""SQLite storage: chats, their messages, and generated images/videos."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DB_PATH = DATA / 'inphai.db'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    backend TEXT,
    model TEXT,
    system_prompt TEXT NOT NULL DEFAULT '',
    temperature REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    thinking TEXT NOT NULL DEFAULT '',
    model TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    prompt TEXT NOT NULL,
    settings TEXT NOT NULL,
    file TEXT NOT NULL,
    seconds REAL,
    created_at REAL NOT NULL
);
'''
CHAT_FIELDS = ('title', 'backend', 'model', 'system_prompt', 'temperature')


@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def init():
    DATA.mkdir(exist_ok=True)
    with connect() as db:
        db.execute('PRAGMA journal_mode = WAL')
        db.executescript(SCHEMA)


def list_chats():
    with connect() as db:
        rows = db.execute('SELECT id, title, backend, model, updated_at FROM chats ORDER BY updated_at DESC')
        return [dict(row) for row in rows]


def create_chat(fields):
    now = time.time()
    fields = {key: fields[key] for key in CHAT_FIELDS if key in fields}
    with connect() as db:
        cursor = db.execute(
            f'INSERT INTO chats ({", ".join(fields)}{", " if fields else ""}created_at, updated_at) '
            f'VALUES ({"?, " * len(fields)}?, ?)', (*fields.values(), now, now))
        return cursor.lastrowid


def get_chat(chat_id):
    with connect() as db:
        chat = db.execute('SELECT * FROM chats WHERE id = ?', (chat_id,)).fetchone()
        if chat is None:
            return None
        messages = db.execute('SELECT id, role, content, thinking, model, created_at FROM messages '
                              'WHERE chat_id = ? ORDER BY id', (chat_id,))
        return {**dict(chat), 'messages': [dict(row) for row in messages]}


def update_chat(chat_id, fields, touch=False):
    fields = {key: fields[key] for key in CHAT_FIELDS if key in fields}
    if touch:
        fields['updated_at'] = time.time()
    if not fields:
        return
    with connect() as db:
        db.execute(f'UPDATE chats SET {", ".join(f"{key} = ?" for key in fields)} WHERE id = ?',
                   (*fields.values(), chat_id))


def delete_chat(chat_id):
    with connect() as db:
        return db.execute('DELETE FROM chats WHERE id = ?', (chat_id,)).rowcount > 0


def add_message(chat_id, role, content, thinking='', model=None):
    now = time.time()
    with connect() as db:
        cursor = db.execute('INSERT INTO messages (chat_id, role, content, thinking, model, created_at) '
                            'VALUES (?, ?, ?, ?, ?, ?)', (chat_id, role, content, thinking, model, now))
        db.execute('UPDATE chats SET updated_at = ? WHERE id = ?', (now, chat_id))
        return cursor.lastrowid


def drop_trailing_replies(chat_id):
    """Remove the assistant messages after the last user message (for regenerate)."""
    with connect() as db:
        db.execute('DELETE FROM messages WHERE chat_id = ? AND role = ? AND id > '
                   '(SELECT COALESCE(MAX(id), 0) FROM messages WHERE chat_id = ? AND role = ?)',
                   (chat_id, 'assistant', chat_id, 'user'))


def add_media(kind, prompt, settings, file, seconds):
    with connect() as db:
        cursor = db.execute('INSERT INTO media (kind, prompt, settings, file, seconds, created_at) '
                            'VALUES (?, ?, ?, ?, ?, ?)',
                            (kind, prompt, json.dumps(settings), file, seconds, time.time()))
        return cursor.lastrowid


def media_row(row):
    return {**dict(row), 'settings': json.loads(row['settings']), 'url': f"/media/{row['file']}"}


def list_media(kind):
    with connect() as db:
        rows = db.execute('SELECT * FROM media WHERE kind = ? ORDER BY id DESC', (kind,))
        return [media_row(row) for row in rows]


def get_media(media_id):
    with connect() as db:
        row = db.execute('SELECT * FROM media WHERE id = ?', (media_id,)).fetchone()
        return media_row(row) if row else None


def delete_media(media_id):
    with connect() as db:
        return db.execute('DELETE FROM media WHERE id = ?', (media_id,)).rowcount > 0
