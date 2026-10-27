"""SQLite storage: users and sessions, chats with their messages and attachments, settings,
generated images/videos and the GPU job queue. Everything a user owns is looked up by its owner."""
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


def has_column(db, table, column):
    return column in {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}


def add_column(db, table, column, definition):
    if not has_column(db, table, column):
        db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def migrate_1(db):
    """Model options per chat (the old temperature moves in), reply stats, attachments."""
    add_column(db, 'chats', 'options', "TEXT NOT NULL DEFAULT '{}'")
    add_column(db, 'messages', 'stats', 'TEXT')
    db.execute("UPDATE chats SET options = json_object('temperature', temperature), temperature = NULL "
               "WHERE temperature IS NOT NULL AND options = '{}'")
    db.executescript('''
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
CREATE INDEX IF NOT EXISTS attachments_message ON attachments(message_id);''')


def migrate_2(db):
    """User accounts: users, sessions, an owner on every chat, upload and file, and the GPU queue."""
    db.executescript('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    disabled INTEGER NOT NULL DEFAULT 0,
    prefs TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    settings TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    completed_steps INTEGER NOT NULL DEFAULT 0,
    total_steps INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    media_id INTEGER,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, id);''')
    # Rows from before accounts have no owner; the first admin gets them (see claim_orphans).
    for table in ('chats', 'media', 'attachments'):
        add_column(db, table, 'user_id', 'INTEGER REFERENCES users(id) ON DELETE CASCADE')


# Applied in order; PRAGMA user_version records how many have run. Append, never edit or reorder.
# Each must be safe to run twice: executescript commits as it goes, so a crash can leave one half done.
MIGRATIONS = [migrate_1, migrate_2]
CHAT_FIELDS = ('title', 'backend', 'model', 'system_prompt', 'options')
USER_FIELDS = ('username', 'password_hash', 'role', 'disabled')


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
        version = db.execute('PRAGMA user_version').fetchone()[0]
        for number, migration in enumerate(MIGRATIONS[version:], version + 1):
            migration(db)
            db.execute(f'PRAGMA user_version = {number}')
        # A job can't still be running after a restart; queued ones carry on.
        db.execute("UPDATE jobs SET state = 'error', error = 'The server restarted during this job', "
                   "finished_at = ? WHERE state = 'running'", (time.time(),))
        db.execute('DELETE FROM sessions WHERE expires_at < ?', (time.time(),))
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


def one(db, sql, *args):
    return db.execute(sql, args).fetchone()[0]


# Users and sessions

def user_row(row):
    if row is None:
        return None
    row = dict(row)
    row.pop('password_hash', None)
    return {**row, 'disabled': bool(row['disabled']), 'prefs': json.loads(row['prefs'])}


def count_users():
    with connect() as db:
        return one(db, 'SELECT COUNT(*) FROM users')


def create_user(username, password_hash, role='user'):
    with connect() as db:
        return db.execute('INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)',
                          (username, password_hash, role, time.time())).lastrowid


def get_user(user_id):
    with connect() as db:
        return user_row(db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone())


def find_login(username):
    """The user with this name, including the password hash (for checking a login only)."""
    with connect() as db:
        row = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    return dict(row) if row else None


def password_hash(user_id):
    with connect() as db:
        row = db.execute('SELECT password_hash FROM users WHERE id = ?', (user_id,)).fetchone()
    return row['password_hash'] if row else None


def list_users():
    with connect() as db:
        rows = db.execute('SELECT u.*, (SELECT COUNT(*) FROM chats WHERE user_id = u.id) AS chats, '
                          '(SELECT COUNT(*) FROM media WHERE user_id = u.id) AS media FROM users u ORDER BY u.id')
        return [user_row(row) for row in rows]


def update_user(user_id, fields):
    fields = {key: fields[key] for key in USER_FIELDS if key in fields}
    if not fields:
        return
    with connect() as db:
        db.execute(f'UPDATE users SET {", ".join(f"{key} = ?" for key in fields)} WHERE id = ?',
                   (*fields.values(), user_id))
        if fields.get('disabled') or 'password_hash' in fields:
            db.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))


def save_prefs(user_id, prefs):
    with connect() as db:
        db.execute('UPDATE users SET prefs = ? WHERE id = ?', (json.dumps(prefs), user_id))


def count_admins():
    with connect() as db:
        return one(db, "SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0")


def delete_user(user_id):
    """Remove a user with everything they own. Returns the media files to delete from disk."""
    with connect() as db:
        uploads = [row['path'] for row in db.execute('SELECT path FROM attachments WHERE user_id = ?', (user_id,))]
        media = [row['file'] for row in db.execute('SELECT file FROM media WHERE user_id = ?', (user_id,))]
        db.execute('DELETE FROM media WHERE user_id = ?', (user_id,))
        db.execute('DELETE FROM attachments WHERE user_id = ?', (user_id,))
        db.execute('DELETE FROM chats WHERE user_id = ?', (user_id,))
        db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    remove_files(uploads)
    return media


def claim_orphans(user_id):
    """Give everything made before accounts existed to the first admin."""
    with connect() as db:
        for table in ('chats', 'media', 'attachments', 'jobs'):
            db.execute(f'UPDATE {table} SET user_id = ? WHERE user_id IS NULL', (user_id,))


def add_session(token_hash, user_id, expires_at):
    with connect() as db:
        db.execute('INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)',
                   (token_hash, user_id, time.time(), expires_at))


def session_user(token_hash):
    """The active user behind a session, and when the session expires."""
    with connect() as db:
        row = db.execute('SELECT u.*, s.expires_at AS session_expires FROM sessions s JOIN users u ON u.id = s.user_id '
                         'WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0',
                         (token_hash, time.time())).fetchone()
    if row is None:
        return None, None
    row = dict(row)
    return user_row({k: v for k, v in row.items() if k != 'session_expires'}), row['session_expires']


def extend_session(token_hash, expires_at):
    with connect() as db:
        db.execute('UPDATE sessions SET expires_at = ? WHERE token_hash = ?', (expires_at, token_hash))


def delete_session(token_hash):
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE token_hash = ?', (token_hash,))


# Chats and messages

def list_chats(user_id):
    with connect() as db:
        rows = db.execute('SELECT id, title, backend, model, updated_at FROM chats WHERE user_id = ? '
                          'ORDER BY updated_at DESC', (user_id,))
        return [dict(row) for row in rows]


def chat_columns(fields):
    fields = {key: fields[key] for key in CHAT_FIELDS if key in fields}
    if 'options' in fields:
        fields['options'] = json.dumps(fields['options'])
    return fields


def create_chat(user_id, fields):
    now = time.time()
    fields = {**chat_columns(fields), 'user_id': user_id, 'created_at': now, 'updated_at': now}
    with connect() as db:
        return db.execute(f'INSERT INTO chats ({", ".join(fields)}) VALUES ({", ".join("?" * len(fields))})',
                          tuple(fields.values())).lastrowid


def get_chat(chat_id, user_id):
    with connect() as db:
        chat = db.execute('SELECT * FROM chats WHERE id = ? AND user_id = ?', (chat_id, user_id)).fetchone()
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


def update_chat(chat_id, user_id, fields, touch=False):
    fields = chat_columns(fields)
    if touch:
        fields['updated_at'] = time.time()
    if not fields:
        return
    with connect() as db:
        db.execute(f'UPDATE chats SET {", ".join(f"{key} = ?" for key in fields)} WHERE id = ? AND user_id = ?',
                   (*fields.values(), chat_id, user_id))


def delete_chat(chat_id, user_id):
    with connect() as db:
        paths = [row['path'] for row in db.execute('SELECT a.path FROM attachments a JOIN messages m '
                                                   'ON m.id = a.message_id WHERE m.chat_id = ?', (chat_id,))]
        deleted = db.execute('DELETE FROM chats WHERE id = ? AND user_id = ?', (chat_id, user_id)).rowcount > 0
    if deleted:
        remove_files(paths)
    return deleted


def delete_all_chats(user_id):
    with connect() as db:
        paths = [row['path'] for row in db.execute('SELECT path FROM attachments WHERE user_id = ? '
                                                   'AND message_id IS NOT NULL', (user_id,))]
        deleted = db.execute('DELETE FROM chats WHERE user_id = ?', (user_id,)).rowcount
    remove_files(paths)
    return deleted


def add_message(chat_id, role, content, thinking='', model=None, stats=None, attachments=()):
    """chat_id must already be checked against its owner."""
    now = time.time()
    with connect() as db:
        cursor = db.execute('INSERT INTO messages (chat_id, role, content, thinking, model, stats, created_at) '
                            'VALUES (?, ?, ?, ?, ?, ?, ?)', (chat_id, role, content, thinking, model,
                                                             json.dumps(stats) if stats else None, now))
        db.executemany('UPDATE attachments SET message_id = ? WHERE id = ? AND message_id IS NULL',
                       [(cursor.lastrowid, attachment) for attachment in attachments])
        db.execute('UPDATE chats SET updated_at = ? WHERE id = ?', (now, chat_id))
        return cursor.lastrowid


def drop_trailing_replies(chat_id):
    """Remove the assistant messages after the last user message (for regenerate)."""
    with connect() as db:
        db.execute('DELETE FROM messages WHERE chat_id = ? AND role = ? AND id > '
                   '(SELECT COALESCE(MAX(id), 0) FROM messages WHERE chat_id = ? AND role = ?)',
                   (chat_id, 'assistant', chat_id, 'user'))


# Attachments

def attachment_row(row):
    row = dict(row)
    row.pop('text', None)
    return {**row, 'truncated': bool(row['truncated']), 'url': f"/api/uploads/{row['id']}"}


def add_attachment(user_id, attachment_id, kind, name, mime, size, path, text=None, truncated=False):
    with connect() as db:
        db.execute('INSERT INTO attachments (id, user_id, kind, name, mime, size, path, text, truncated, created_at) '
                   'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (attachment_id, user_id, kind, name, mime, size, path, text, int(truncated), time.time()))
    return get_attachment(attachment_id, user_id)


def get_attachment(attachment_id, user_id):
    with connect() as db:
        row = db.execute('SELECT * FROM attachments WHERE id = ? AND user_id = ?', (attachment_id, user_id)).fetchone()
    return attachment_row(row) if row else None


def unattached(ids, user_id):
    """The ids that are this user's and aren't part of a message yet."""
    if not ids:
        return []
    with connect() as db:
        rows = db.execute(f'SELECT id FROM attachments WHERE message_id IS NULL AND user_id = ? '
                          f'AND id IN ({", ".join("?" * len(ids))})', (user_id, *ids))
        found = {row['id'] for row in rows}
    return [i for i in ids if i in found]


def message_attachments(message_id):
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM attachments WHERE message_id = ? ORDER BY created_at',
                                                (message_id,))]


def delete_unattached(attachment_id, user_id):
    with connect() as db:
        row = db.execute('SELECT path FROM attachments WHERE id = ? AND user_id = ? AND message_id IS NULL',
                         (attachment_id, user_id)).fetchone()
        if row is None:
            return False
        db.execute('DELETE FROM attachments WHERE id = ?', (attachment_id,))
    remove_files([row['path']])
    return True


# Settings (server-wide)

def get_settings(defaults):
    with connect() as db:
        saved = {row['key']: json.loads(row['value']) for row in db.execute('SELECT key, value FROM settings')}
    return {key: saved.get(key, value) for key, value in defaults.items()}


def get_setting_raw(key):
    with connect() as db:
        row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return json.loads(row['value']) if row else None


def save_settings(fields):
    with connect() as db:
        db.executemany('INSERT INTO settings (key, value) VALUES (?, ?) '
                       'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                       [(key, json.dumps(value)) for key, value in fields.items()])


def counts(user_id):
    with connect() as db:
        return {'chats': one(db, 'SELECT COUNT(*) FROM chats WHERE user_id = ?', user_id),
                'messages': one(db, 'SELECT COUNT(*) FROM messages m JOIN chats c ON c.id = m.chat_id '
                                    'WHERE c.user_id = ?', user_id),
                'files': one(db, 'SELECT COUNT(*) FROM attachments WHERE user_id = ? AND message_id IS NOT NULL', user_id),
                'images': one(db, "SELECT COUNT(*) FROM media WHERE kind = 'image' AND user_id = ?", user_id),
                'videos': one(db, "SELECT COUNT(*) FROM media WHERE kind = 'video' AND user_id = ?", user_id)}


def upload_bytes(user_id):
    with connect() as db:
        return one(db, 'SELECT COALESCE(SUM(size), 0) FROM attachments WHERE user_id = ?', user_id)


# Generated images and videos

def add_media(user_id, kind, prompt, settings, file, seconds):
    with connect() as db:
        return db.execute('INSERT INTO media (user_id, kind, prompt, settings, file, seconds, created_at) '
                          'VALUES (?, ?, ?, ?, ?, ?, ?)',
                          (user_id, kind, prompt, json.dumps(settings), file, seconds, time.time())).lastrowid


def media_row(row):
    return {**dict(row), 'settings': json.loads(row['settings']), 'url': f"/media/{row['file']}"}


def list_media(kind, user_id):
    with connect() as db:
        rows = db.execute('SELECT * FROM media WHERE kind = ? AND user_id = ? ORDER BY id DESC', (kind, user_id))
        return [media_row(row) for row in rows]


def get_media(media_id, user_id):
    with connect() as db:
        row = db.execute('SELECT * FROM media WHERE id = ? AND user_id = ?', (media_id, user_id)).fetchone()
        return media_row(row) if row else None


def media_owner(file):
    with connect() as db:
        row = db.execute('SELECT user_id FROM media WHERE file = ?', (file,)).fetchone()
    return row['user_id'] if row else None


def delete_media(media_id, user_id):
    with connect() as db:
        return db.execute('DELETE FROM media WHERE id = ? AND user_id = ?', (media_id, user_id)).rowcount > 0


# The GPU job queue

JOB_FIELDS = ('state', 'stage', 'completed_steps', 'total_steps', 'error', 'media_id', 'started_at', 'finished_at')


def job_row(row):
    return {**dict(row), 'settings': json.loads(row['settings'])} if row else None


def add_job(user_id, kind, settings):
    with connect() as db:
        return db.execute('INSERT INTO jobs (user_id, kind, settings, total_steps, created_at) VALUES (?, ?, ?, ?, ?)',
                          (user_id, kind, json.dumps(settings), settings.get('steps', 0), time.time())).lastrowid


def get_job(job_id):
    with connect() as db:
        return job_row(db.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone())


def next_queued_job():
    with connect() as db:
        return job_row(db.execute("SELECT * FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1").fetchone())


def update_job(job_id, fields):
    fields = {key: fields[key] for key in JOB_FIELDS if key in fields}
    with connect() as db:
        db.execute(f'UPDATE jobs SET {", ".join(f"{key} = ?" for key in fields)} WHERE id = ?',
                   (*fields.values(), job_id))


def cancel_queued_job(job_id):
    with connect() as db:
        return db.execute("UPDATE jobs SET state = 'cancelled', finished_at = ? WHERE id = ? AND state = 'queued'",
                          (time.time(), job_id)).rowcount > 0


def user_jobs(user_id, since):
    """This user's waiting and running jobs, plus the ones that ended after `since`."""
    with connect() as db:
        rows = db.execute("SELECT * FROM jobs WHERE user_id = ? AND (state IN ('queued', 'running') "
                          "OR finished_at > ?) ORDER BY id", (user_id, since))
        return [job_row(row) for row in rows]


def queue_ahead(job_id):
    """How many jobs will run before this one (the running one included)."""
    with connect() as db:
        return one(db, "SELECT COUNT(*) FROM jobs WHERE state = 'running' OR (state = 'queued' AND id < ?)", job_id)


def running_job():
    with connect() as db:
        return job_row(db.execute("SELECT * FROM jobs WHERE state = 'running' LIMIT 1").fetchone())
