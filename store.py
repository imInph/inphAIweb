"""SQLite storage: chats, their messages and attachments, settings, and generated images/videos."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DB_PATH = DATA / 'inphai.db'
UPLOADS = DATA / 'uploads'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    backend TEXT,
    model TEXT,
    system_prompt TEXT NOT NULL DEFAULT '',
    temperature REAL,
    options TEXT NOT NULL DEFAULT '{}',
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
    stats TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    path TEXT NOT NULL,
    text TEXT,
    truncated INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS attachments_message ON attachments(message_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
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
CHAT_FIELDS = ('title', 'backend', 'model', 'system_prompt', 'options')
# Added after the first version: (table, column, definition).
COLUMNS = [('chats', 'options', "TEXT NOT NULL DEFAULT '{}'"), ('messages', 'stats', 'TEXT')]


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
        for table, column, definition in COLUMNS:
            if column not in {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        # The old per-chat temperature now lives in options.
        db.execute("UPDATE chats SET options = json_object('temperature', temperature), temperature = NULL "
                   "WHERE temperature IS NOT NULL AND options = '{}'")
        # Uploads that never made it into a message (the page was closed first).
        stale = db.execute('SELECT path FROM attachments WHERE message_id IS NULL AND created_at < ?',
                           (time.time() - 86400,)).fetchall()
        db.execute('DELETE FROM attachments WHERE message_id IS NULL AND created_at < ?', (time.time() - 86400,))
    remove_files(row['path'] for row in stale)


def remove_files(paths):
    for path in paths:
        file = UPLOADS / path
        file.unlink(missing_ok=True)
        try:
            file.parent.rmdir()
        except OSError:
            pass


def list_chats():
    with connect() as db:
        rows = db.execute('SELECT id, title, backend, model, updated_at FROM chats ORDER BY updated_at DESC')
        return [dict(row) for row in rows]


def chat_columns(fields):
    fields = {key: fields[key] for key in CHAT_FIELDS if key in fields}
    if 'options' in fields:
        fields['options'] = json.dumps(fields['options'])
    return fields


def create_chat(fields):
    now = time.time()
    fields = chat_columns(fields)
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
        messages = [{**dict(row), 'stats': json.loads(row['stats']) if row['stats'] else None, 'attachments': []}
                    for row in db.execute('SELECT id, role, content, thinking, model, stats, created_at FROM messages '
                                          'WHERE chat_id = ? ORDER BY id', (chat_id,))]
        by_id = {message['id']: message for message in messages}
        for row in db.execute('SELECT a.* FROM attachments a JOIN messages m ON m.id = a.message_id '
                              'WHERE m.chat_id = ? ORDER BY a.created_at', (chat_id,)):
            by_id[row['message_id']]['attachments'].append(attachment_row(row))
        chat = dict(chat)
        chat.pop('temperature', None)
        return {**chat, 'options': json.loads(chat['options']), 'messages': messages}


def update_chat(chat_id, fields, touch=False):
    fields = chat_columns(fields)
    if touch:
        fields['updated_at'] = time.time()
    if not fields:
        return
    with connect() as db:
        db.execute(f'UPDATE chats SET {", ".join(f"{key} = ?" for key in fields)} WHERE id = ?',
                   (*fields.values(), chat_id))


def delete_chat(chat_id):
    with connect() as db:
        paths = [row['path'] for row in db.execute('SELECT a.path FROM attachments a JOIN messages m '
                                                   'ON m.id = a.message_id WHERE m.chat_id = ?', (chat_id,))]
        deleted = db.execute('DELETE FROM chats WHERE id = ?', (chat_id,)).rowcount > 0
    remove_files(paths)
    return deleted


def add_message(chat_id, role, content, thinking='', model=None, stats=None, attachments=()):
    now = time.time()
    with connect() as db:
        cursor = db.execute('INSERT INTO messages (chat_id, role, content, thinking, model, stats, created_at) '
                            'VALUES (?, ?, ?, ?, ?, ?, ?)', (chat_id, role, content, thinking, model,
                                                             json.dumps(stats) if stats else None, now))
        db.executemany('UPDATE attachments SET message_id = ? WHERE id = ? AND message_id IS NULL',
                       [(cursor.lastrowid, attachment) for attachment in attachments])
        db.execute('UPDATE chats SET updated_at = ? WHERE id = ?', (now, chat_id))
        return cursor.lastrowid


def attachment_row(row):
    row = dict(row)
    row.pop('text', None)
    return {**row, 'truncated': bool(row['truncated']), 'url': f"/api/uploads/{row['id']}"}


def add_attachment(attachment_id, kind, name, mime, size, path, text=None, truncated=False):
    with connect() as db:
        db.execute('INSERT INTO attachments (id, kind, name, mime, size, path, text, truncated, created_at) '
                   'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (attachment_id, kind, name, mime, size, path, text, int(truncated), time.time()))
    return get_attachment(attachment_id)


def get_attachment(attachment_id, with_text=False):
    with connect() as db:
        row = db.execute('SELECT * FROM attachments WHERE id = ?', (attachment_id,)).fetchone()
    if row is None:
        return None
    return {**attachment_row(row), 'text': row['text']} if with_text else attachment_row(row)


def unattached(ids):
    """The ids that exist and aren't part of a message yet."""
    if not ids:
        return []
    with connect() as db:
        rows = db.execute(f'SELECT id FROM attachments WHERE message_id IS NULL AND id IN ({", ".join("?" * len(ids))})',
                          list(ids))
        found = {row['id'] for row in rows}
    return [i for i in ids if i in found]


def message_attachments(message_id):
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM attachments WHERE message_id = ? ORDER BY created_at',
                                                (message_id,))]


def delete_unattached(attachment_id):
    with connect() as db:
        row = db.execute('SELECT path FROM attachments WHERE id = ? AND message_id IS NULL', (attachment_id,)).fetchone()
        if row is None:
            return False
        db.execute('DELETE FROM attachments WHERE id = ?', (attachment_id,))
    remove_files([row['path']])
    return True


def drop_trailing_replies(chat_id):
    """Remove the assistant messages after the last user message (for regenerate)."""
    with connect() as db:
        db.execute('DELETE FROM messages WHERE chat_id = ? AND role = ? AND id > '
                   '(SELECT COALESCE(MAX(id), 0) FROM messages WHERE chat_id = ? AND role = ?)',
                   (chat_id, 'assistant', chat_id, 'user'))


def get_settings(defaults):
    with connect() as db:
        saved = {row['key']: json.loads(row['value']) for row in db.execute('SELECT key, value FROM settings')}
    return {key: saved.get(key, value) for key, value in defaults.items()}


def save_settings(fields):
    with connect() as db:
        db.executemany('INSERT INTO settings (key, value) VALUES (?, ?) '
                       'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                       [(key, json.dumps(value)) for key, value in fields.items()])


def delete_all_chats():
    with connect() as db:
        paths = [row['path'] for row in db.execute('SELECT path FROM attachments WHERE message_id IS NOT NULL')]
        deleted = db.execute('DELETE FROM chats').rowcount
    remove_files(paths)
    return deleted


def counts():
    with connect() as db:
        one = lambda sql, *args: db.execute(sql, args).fetchone()[0]
        return {'chats': one('SELECT COUNT(*) FROM chats'), 'messages': one('SELECT COUNT(*) FROM messages'),
                'files': one('SELECT COUNT(*) FROM attachments WHERE message_id IS NOT NULL'),
                'images': one('SELECT COUNT(*) FROM media WHERE kind = ?', 'image'),
                'videos': one('SELECT COUNT(*) FROM media WHERE kind = ?', 'video')}


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
