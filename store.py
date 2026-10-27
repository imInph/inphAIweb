"""SQLite storage: users and sessions, chats with their messages and attachments, settings,
generated images/videos and the GPU job queue. Everything a user owns is looked up by its owner."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

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


def migrate_3(db):
    """Message branches: each message has a parent, and a chat remembers which branch is shown."""
    add_column(db, 'messages', 'parent_id', 'INTEGER REFERENCES messages(id) ON DELETE CASCADE')
    add_column(db, 'chats', 'leaf_id', 'INTEGER')
    # Until now a chat was one line of messages: each one's parent is the one before it.
    db.execute("""UPDATE messages SET parent_id = (SELECT MAX(p.id) FROM messages p
                  WHERE p.chat_id = messages.chat_id AND p.id < messages.id) WHERE parent_id IS NULL""")
    db.execute('UPDATE chats SET leaf_id = (SELECT MAX(id) FROM messages WHERE chat_id = chats.id) WHERE leaf_id IS NULL')
    db.execute('CREATE INDEX IF NOT EXISTS messages_parent ON messages(parent_id)')


def migrate_4(db):
    """Organising chats (pinned, archived, folders) and full-text search over messages."""
    add_column(db, 'chats', 'pinned', 'INTEGER NOT NULL DEFAULT 0')
    add_column(db, 'chats', 'archived', 'INTEGER NOT NULL DEFAULT 0')
    db.executescript('''
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    collapsed INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);''')
    add_column(db, 'chats', 'folder_id', 'INTEGER REFERENCES folders(id) ON DELETE SET NULL')
    try:
        db.executescript('''
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE OF content ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
INSERT INTO messages_fts(messages_fts) VALUES ('rebuild');''')
    except sqlite3.OperationalError:
        pass  # an SQLite without FTS5: search falls back to LIKE


def migrate_5(db):
    """Saved prompts (/commands), personas (a model with its own instructions) and memories."""
    db.executescript('''
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    command TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (user_id, command)
);
CREATE TABLE IF NOT EXISTS personas (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT 'green',
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    system_prompt TEXT NOT NULL DEFAULT '',
    options TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);''')
    add_column(db, 'chats', 'persona_id', 'INTEGER REFERENCES personas(id) ON DELETE SET NULL')


def migrate_6(db):
    """Knowledge: collections of documents, split into chunks with embeddings, that chats can draw on."""
    db.executescript('''
CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    knowledge_id INTEGER NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    chunks INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    knowledge_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS chunks_knowledge ON chunks(knowledge_id);
CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);''')
    try:
        db.executescript('''
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;''')
    except sqlite3.OperationalError:
        pass  # without FTS5, retrieval uses the embeddings alone
    add_column(db, 'chats', 'knowledge_ids', "TEXT NOT NULL DEFAULT '[]'")
    add_column(db, 'personas', 'knowledge_ids', "TEXT NOT NULL DEFAULT '[]'")
    add_column(db, 'messages', 'sources', 'TEXT')
    # A restart mid-indexing: start those documents again.
    db.execute("UPDATE documents SET status = 'queued' WHERE status = 'indexing'")


def migrate_7(db):
    """Tools: a switch per chat, the calls a reply made, and GPU jobs that post their result into a chat."""
    add_column(db, 'chats', 'tools', 'INTEGER NOT NULL DEFAULT 0')
    add_column(db, 'messages', 'tools', 'TEXT')
    add_column(db, 'jobs', 'chat_id', 'INTEGER')
    add_column(db, 'jobs', 'message_id', 'INTEGER')


# Applied in order; PRAGMA user_version records how many have run. Append, never edit or reorder.
# Each must be safe to run twice: executescript commits as it goes, so a crash can leave one half done.
MIGRATIONS = [migrate_1, migrate_2, migrate_3, migrate_4, migrate_5, migrate_6, migrate_7]
CHAT_FIELDS = ('title', 'backend', 'model', 'system_prompt', 'options', 'pinned', 'archived', 'folder_id', 'persona_id',
               'knowledge_ids', 'tools')
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
    # Pages read from a #link have no file (an empty path); never aim unlink at UPLOADS itself.
    paths = [path for path in paths if path]
    if paths:
        with connect() as db:
            still_used = {row['path'] for row in db.execute(
                f'SELECT path FROM attachments WHERE path IN ({", ".join("?" * len(paths))})', paths)}
        paths = [path for path in paths if path not in still_used]
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
        documents = [row['path'] for row in db.execute('SELECT d.path FROM documents d JOIN knowledge k '
                                                       'ON k.id = d.knowledge_id WHERE k.user_id = ?', (user_id,))]
        db.execute('DELETE FROM chunks WHERE knowledge_id IN (SELECT id FROM knowledge WHERE user_id = ?)', (user_id,))
        db.execute('DELETE FROM media WHERE user_id = ?', (user_id,))
        db.execute('DELETE FROM attachments WHERE user_id = ?', (user_id,))
        db.execute('DELETE FROM chats WHERE user_id = ?', (user_id,))
        db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    remove_files(uploads)
    remove_document_files(documents)
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

def list_chats(user_id, archived=False):
    with connect() as db:
        rows = db.execute('SELECT id, title, backend, model, updated_at, pinned, archived, folder_id FROM chats '
                          'WHERE user_id = ? AND archived = ? ORDER BY updated_at DESC', (user_id, int(archived)))
        return [{**dict(row), 'pinned': bool(row['pinned']), 'archived': bool(row['archived'])} for row in rows]


def count_archived(user_id):
    with connect() as db:
        return one(db, 'SELECT COUNT(*) FROM chats WHERE user_id = ? AND archived = 1', user_id)


def first_exchange(chat_id):
    """The first message and the first reply to it, for writing a title."""
    with connect() as db:
        rows = db.execute('SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id LIMIT 2', (chat_id,)).fetchall()
    return [dict(row) for row in rows]


def message_count(chat_id):
    with connect() as db:
        return one(db, 'SELECT COUNT(*) FROM messages WHERE chat_id = ?', chat_id)


# Folders

def list_folders(user_id):
    with connect() as db:
        return [{**dict(row), 'collapsed': bool(row['collapsed'])}
                for row in db.execute('SELECT * FROM folders WHERE user_id = ? ORDER BY name COLLATE NOCASE', (user_id,))]


def get_folder(folder_id, user_id):
    with connect() as db:
        row = db.execute('SELECT * FROM folders WHERE id = ? AND user_id = ?', (folder_id, user_id)).fetchone()
    return dict(row) if row else None


def create_folder(user_id, name):
    with connect() as db:
        return db.execute('INSERT INTO folders (user_id, name, created_at) VALUES (?, ?, ?)',
                          (user_id, name, time.time())).lastrowid


def update_folder(folder_id, user_id, fields):
    fields = {key: fields[key] for key in ('name', 'collapsed') if key in fields}
    if fields:
        with connect() as db:
            db.execute(f'UPDATE folders SET {", ".join(f"{key} = ?" for key in fields)} WHERE id = ? AND user_id = ?',
                       (*fields.values(), folder_id, user_id))


def delete_folder(folder_id, user_id):
    """The chats inside stay; they just leave the folder."""
    with connect() as db:
        db.execute('UPDATE chats SET folder_id = NULL WHERE folder_id = ? AND user_id = ?', (folder_id, user_id))
        return db.execute('DELETE FROM folders WHERE id = ? AND user_id = ?', (folder_id, user_id)).rowcount > 0


# Search

def fts_query(text):
    """Every word must appear, as a prefix: "pyth sql" finds "Python ... SQLite"."""
    words = [w.replace('"', '') for w in text.split()]
    return ' '.join(f'"{w}"*' for w in words if w)


def like_pattern(text):
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def search(user_id, text, limit=30):
    """Chats whose title matches, then messages, with a snippet. \x01/\x02 mark the matched words."""
    text = text.strip()
    if not text:
        return []
    with connect() as db:
        titles = [dict(row) | {'message_id': None, 'snippet': None} for row in db.execute(
            "SELECT id AS chat_id, title FROM chats WHERE user_id = ? AND title LIKE ? ESCAPE '\\' "
            "ORDER BY updated_at DESC LIMIT 10",
            (user_id, like_pattern(text)))]
        has_fts = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'messages_fts'").fetchone()
        if has_fts and fts_query(text):
            rows = db.execute(
                "SELECT m.id AS message_id, m.chat_id, c.title, "
                "snippet(messages_fts, 0, char(1), char(2), '…', 14) AS snippet "
                "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid JOIN chats c ON c.id = m.chat_id "
                "WHERE messages_fts MATCH ? AND c.user_id = ? ORDER BY rank LIMIT ?",
                (fts_query(text), user_id, limit)).fetchall()
        else:
            rows = db.execute(
                "SELECT m.id AS message_id, m.chat_id, c.title, substr(m.content, 1, 160) AS snippet FROM messages m "
                "JOIN chats c ON c.id = m.chat_id WHERE c.user_id = ? AND m.content LIKE ? ESCAPE '\\' "
                "ORDER BY m.id DESC LIMIT ?", (user_id, like_pattern(text), limit)).fetchall()
    return titles + [dict(row) for row in rows]


# Export and import

def export_chat(chat_id, user_id):
    """Every message of a chat (all branches), with attachment details and their text."""
    chat = get_chat(chat_id, user_id)
    if chat is None:
        return None
    with connect() as db:
        messages = [dict(row) for row in db.execute(
            'SELECT id, parent_id, role, content, thinking, model, stats, created_at FROM messages '
            'WHERE chat_id = ? ORDER BY id', (chat_id,))]
        files = {}
        for row in db.execute('SELECT a.* FROM attachments a JOIN messages m ON m.id = a.message_id '
                              'WHERE m.chat_id = ? ORDER BY a.created_at', (chat_id,)):
            files.setdefault(row['message_id'], []).append(
                {'id': row['id'], 'kind': row['kind'], 'name': row['name'], 'mime': row['mime'], 'size': row['size'],
                 'path': row['path'], 'text': row['text'], 'truncated': bool(row['truncated'])})
    for message in messages:
        message['stats'] = json.loads(message['stats']) if message['stats'] else None
        message['attachments'] = files.get(message['id'], [])
    return {'format': 'inphai-chat', 'version': 1, 'title': chat['title'], 'backend': chat['backend'],
            'model': chat['model'], 'system_prompt': chat['system_prompt'], 'options': chat['options'],
            'created_at': chat['created_at'], 'updated_at': chat['updated_at'], 'leaf_id': chat['leaf_id'],
            'shown': [m['id'] for m in chat['messages']], 'messages': messages}


def import_chat(user_id, data, saved_files=None):
    """Recreate an exported chat for user_id (new ids throughout). saved_files maps an old
    attachment id to its new path under UPLOADS, for files that came along in an archive."""
    saved_files = saved_files or {}
    now = time.time()
    with connect() as db:
        chat_id = db.execute(
            'INSERT INTO chats (user_id, title, backend, model, system_prompt, options, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (user_id, data['title'], data.get('backend'), data.get('model'), data.get('system_prompt', ''),
             json.dumps(data.get('options') or {}), data.get('created_at', now), data.get('updated_at', now))).lastrowid
        ids = {}
        for message in sorted(data['messages'], key=lambda m: m['id']):
            new_id = db.execute(
                'INSERT INTO messages (chat_id, parent_id, role, content, thinking, model, stats, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (chat_id, ids.get(message.get('parent_id')), message['role'], message['content'],
                 message.get('thinking', ''), message.get('model'),
                 json.dumps(message['stats']) if message.get('stats') else None,
                 message.get('created_at', now))).lastrowid
            ids[message['id']] = new_id
            for file in message.get('attachments', []):
                path = saved_files.get(file.get('id'))
                if path is None and file.get('text') is None:
                    continue  # an image without its file: nothing to keep
                db.execute('INSERT INTO attachments (id, user_id, message_id, kind, name, mime, size, path, text, '
                           'truncated, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           (uuid.uuid4().hex, user_id, new_id, file['kind'], file['name'], file['mime'],
                            file.get('size', 0), path or '', file.get('text'), int(bool(file.get('truncated'))), now))
        leaf = ids.get(data.get('leaf_id')) or (max(ids.values()) if ids else None)
        db.execute('UPDATE chats SET leaf_id = ? WHERE id = ?', (leaf, chat_id))
    return chat_id


def chat_columns(fields):
    fields = {key: fields[key] for key in CHAT_FIELDS if key in fields}
    for key in ('options', 'knowledge_ids'):
        if key in fields:
            fields[key] = json.dumps(fields[key])
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
        rows = db.execute('SELECT id, parent_id, role, content, thinking, model, stats, sources, tools, created_at '
                          'FROM messages WHERE chat_id = ? ORDER BY id', (chat_id,)).fetchall()
        path = branch(rows, chat['leaf_id'])
        children = {}
        for row in rows:
            children.setdefault(row['parent_id'], []).append(row['id'])
        messages = []
        for row in path:
            siblings = children[row['parent_id']]
            messages.append({**dict(row), 'stats': json.loads(row['stats']) if row['stats'] else None,
                             'sources': json.loads(row['sources']) if row['sources'] else None,
                             'tools': json.loads(row['tools']) if row['tools'] else None,
                             'attachments': [], 'siblings': siblings, 'sibling_index': siblings.index(row['id'])})
        by_id = {message['id']: message for message in messages}
        for row in db.execute('SELECT a.* FROM attachments a JOIN messages m ON m.id = a.message_id '
                              'WHERE m.chat_id = ? ORDER BY a.created_at', (chat_id,)):
            if row['message_id'] in by_id:
                by_id[row['message_id']]['attachments'].append(attachment_row(row))
        chat = dict(chat)
        chat.pop('temperature', None)
        return {**chat, 'options': json.loads(chat['options']), 'knowledge_ids': json.loads(chat['knowledge_ids']),
                'tools': bool(chat['tools']), 'messages': messages}


def branch(rows, leaf_id):
    """The messages from the first one down to leaf_id, in order."""
    by_id = {row['id']: row for row in rows}
    path, node = [], by_id.get(leaf_id)
    while node is not None:
        path.append(node)
        node = by_id.get(node['parent_id'])
    return path[::-1]


def get_message(chat_id, message_id):
    """chat_id must already be checked against its owner."""
    with connect() as db:
        row = db.execute('SELECT * FROM messages WHERE id = ? AND chat_id = ?', (message_id, chat_id)).fetchone()
    return dict(row) if row else None


def path_to(chat_id, message_id):
    """The conversation up to and including message_id (what a model sees when answering it)."""
    with connect() as db:
        rows = db.execute('SELECT * FROM messages WHERE chat_id = ?', (chat_id,)).fetchall()
    return [dict(row) for row in branch(rows, message_id)]


def newest_leaf(chat_id, message_id):
    """Follow the newest child from message_id down to the end of that branch."""
    with connect() as db:
        while True:
            child = db.execute('SELECT MAX(id) FROM messages WHERE chat_id = ? AND parent_id = ?',
                               (chat_id, message_id)).fetchone()[0]
            if child is None:
                return message_id
            message_id = child


def set_leaf(chat_id, message_id):
    with connect() as db:
        db.execute('UPDATE chats SET leaf_id = ?, updated_at = ? WHERE id = ?', (message_id, time.time(), chat_id))


def edit_message(chat_id, message_id, content):
    with connect() as db:
        db.execute('UPDATE messages SET content = ? WHERE id = ? AND chat_id = ?', (content, message_id, chat_id))


def delete_message(chat_id, message_id):
    """Delete a message and everything after it on its branches; show a neighbouring branch instead."""
    message = get_message(chat_id, message_id)
    if message is None:
        return False
    with connect() as db:
        subtree = [row[0] for row in db.execute(
            'WITH RECURSIVE t(id) AS (SELECT ? UNION ALL SELECT m.id FROM messages m JOIN t ON m.parent_id = t.id) '
            'SELECT id FROM t', (message_id,))]
        paths = [row['path'] for row in db.execute(
            f'SELECT path FROM attachments WHERE message_id IN ({", ".join("?" * len(subtree))})', subtree)]
        db.execute('DELETE FROM messages WHERE id = ?', (message_id,))
        sibling = db.execute('SELECT MAX(id) FROM messages WHERE chat_id = ? AND parent_id IS ?',
                             (chat_id, message['parent_id'])).fetchone()[0]
    remove_files(paths)
    target = sibling if sibling is not None else message['parent_id']
    set_leaf(chat_id, newest_leaf(chat_id, target) if target is not None else None)
    return True


def copy_attachments(from_message_id, to_message_id):
    """An edited message keeps the files of the one it replaces (the same files on disk)."""
    with connect() as db:
        for row in db.execute('SELECT * FROM attachments WHERE message_id = ?', (from_message_id,)).fetchall():
            db.execute('INSERT INTO attachments (id, user_id, message_id, kind, name, mime, size, path, text, truncated, '
                       'created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                       (uuid.uuid4().hex, row['user_id'], to_message_id, row['kind'], row['name'], row['mime'],
                        row['size'], row['path'], row['text'], row['truncated'], time.time()))


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


def add_message(chat_id, parent_id, role, content, thinking='', model=None, stats=None, attachments=(), sources=None,
                tools=None):
    """Add a message under parent_id and show its branch. chat_id must already be checked against its owner."""
    now = time.time()
    with connect() as db:
        cursor = db.execute('INSERT INTO messages (chat_id, parent_id, role, content, thinking, model, stats, sources, '
                            'tools, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (chat_id, parent_id, role, content, thinking, model, json.dumps(stats) if stats else None,
                             json.dumps(sources) if sources else None, json.dumps(tools) if tools else None, now))
        db.executemany('UPDATE attachments SET message_id = ? WHERE id = ? AND message_id IS NULL',
                       [(cursor.lastrowid, attachment) for attachment in attachments])
        db.execute('UPDATE chats SET updated_at = ?, leaf_id = ? WHERE id = ?', (now, cursor.lastrowid, chat_id))
        return cursor.lastrowid


# Attachments

def attachment_row(row):
    row = dict(row)
    row.pop('text', None)
    return {**row, 'truncated': bool(row['truncated']), 'url': f"/api/uploads/{row['id']}"}


def add_attachment(user_id, attachment_id, kind, name, mime, size, path, text=None, truncated=False, message_id=None):
    with connect() as db:
        db.execute('INSERT INTO attachments (id, user_id, message_id, kind, name, mime, size, path, text, truncated, '
                   'created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (attachment_id, user_id, message_id, kind, name, mime, size, path, text, int(truncated), time.time()))
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


def add_job(user_id, kind, settings, chat_id=None, message_id=None):
    with connect() as db:
        return db.execute('INSERT INTO jobs (user_id, kind, settings, total_steps, chat_id, message_id, created_at) '
                          'VALUES (?, ?, ?, ?, ?, ?, ?)', (user_id, kind, json.dumps(settings), settings.get('steps', 0),
                                                           chat_id, message_id, time.time())).lastrowid


def set_job_message(job_id, message_id):
    with connect() as db:
        db.execute('UPDATE jobs SET message_id = ? WHERE id = ?', (message_id, job_id))


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


# Prompts, personas and memories: small per-user lists with the same shape of functions.

LIBRARY = {
    'prompts': ('command', 'title', 'content'),
    'personas': ('name', 'color', 'backend', 'model', 'system_prompt', 'options', 'knowledge_ids'),
    'memories': ('content',),
}
JSON_COLUMNS = {'options', 'knowledge_ids'}
ORDER = {'prompts': 'command', 'personas': 'name COLLATE NOCASE', 'memories': 'id'}


def library_row(row):
    row = dict(row)
    for key in JSON_COLUMNS & set(row):
        row[key] = json.loads(row[key])
    return row


def list_items(table, user_id):
    with connect() as db:
        return [library_row(row) for row in
                db.execute(f'SELECT * FROM {table} WHERE user_id = ? ORDER BY {ORDER[table]}', (user_id,))]


def get_item(table, item_id, user_id):
    with connect() as db:
        row = db.execute(f'SELECT * FROM {table} WHERE id = ? AND user_id = ?', (item_id, user_id)).fetchone()
    return library_row(row) if row else None


def item_columns(table, fields):
    fields = {key: fields[key] for key in LIBRARY[table] if key in fields}
    return {key: json.dumps(value) if key in JSON_COLUMNS else value for key, value in fields.items()}


def add_item(table, user_id, fields):
    fields = {**item_columns(table, fields), 'user_id': user_id, 'created_at': time.time()}
    with connect() as db:
        return db.execute(f'INSERT INTO {table} ({", ".join(fields)}) VALUES ({", ".join("?" * len(fields))})',
                          tuple(fields.values())).lastrowid


def update_item(table, item_id, user_id, fields):
    fields = item_columns(table, fields)
    if fields:
        with connect() as db:
            db.execute(f'UPDATE {table} SET {", ".join(f"{key} = ?" for key in fields)} WHERE id = ? AND user_id = ?',
                       (*fields.values(), item_id, user_id))


def delete_item(table, item_id, user_id):
    with connect() as db:
        return db.execute(f'DELETE FROM {table} WHERE id = ? AND user_id = ?', (item_id, user_id)).rowcount > 0


# Knowledge

KNOWLEDGE = DATA / 'knowledge'


def list_knowledge(user_id):
    with connect() as db:
        return [dict(row) for row in db.execute(
            'SELECT k.*, (SELECT COUNT(*) FROM documents d WHERE d.knowledge_id = k.id) AS documents, '
            "(SELECT COUNT(*) FROM documents d WHERE d.knowledge_id = k.id AND d.status IN ('queued', 'indexing')) AS indexing "
            'FROM knowledge k WHERE k.user_id = ? ORDER BY k.name COLLATE NOCASE', (user_id,))]


def get_knowledge(knowledge_id, user_id):
    with connect() as db:
        row = db.execute('SELECT * FROM knowledge WHERE id = ? AND user_id = ?', (knowledge_id, user_id)).fetchone()
        if row is None:
            return None
        documents = [dict(d) for d in db.execute('SELECT id, name, mime, size, status, error, chunks, created_at '
                                                 'FROM documents WHERE knowledge_id = ? ORDER BY name COLLATE NOCASE',
                                                 (knowledge_id,))]
    return {**dict(row), 'documents': documents}


def owned_knowledge(ids, user_id):
    """The ids among these that are user_id's collections."""
    if not ids:
        return []
    with connect() as db:
        found = {row['id'] for row in db.execute(
            f'SELECT id FROM knowledge WHERE user_id = ? AND id IN ({", ".join("?" * len(ids))})', (user_id, *ids))}
    return [i for i in ids if i in found]


def create_knowledge(user_id, name, description=''):
    with connect() as db:
        return db.execute('INSERT INTO knowledge (user_id, name, description, created_at) VALUES (?, ?, ?, ?)',
                          (user_id, name, description, time.time())).lastrowid


def update_knowledge(knowledge_id, user_id, fields):
    fields = {key: fields[key] for key in ('name', 'description') if key in fields}
    if fields:
        with connect() as db:
            db.execute(f'UPDATE knowledge SET {", ".join(f"{key} = ?" for key in fields)} WHERE id = ? AND user_id = ?',
                       (*fields.values(), knowledge_id, user_id))


def remove_document_files(paths):
    for path in paths:
        file = KNOWLEDGE / path
        file.unlink(missing_ok=True)
        try:
            file.parent.rmdir()
        except OSError:
            pass


def delete_knowledge(knowledge_id, user_id):
    with connect() as db:
        if not db.execute('SELECT 1 FROM knowledge WHERE id = ? AND user_id = ?', (knowledge_id, user_id)).fetchone():
            return False
        paths = [row['path'] for row in db.execute('SELECT path FROM documents WHERE knowledge_id = ?', (knowledge_id,))]
        db.execute('DELETE FROM chunks WHERE knowledge_id = ?', (knowledge_id,))
        db.execute('DELETE FROM knowledge WHERE id = ?', (knowledge_id,))
    remove_document_files(paths)
    return True


def add_document(knowledge_id, name, mime, size, path):
    with connect() as db:
        return db.execute('INSERT INTO documents (knowledge_id, name, mime, size, path, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                          (knowledge_id, name, mime, size, path, time.time())).lastrowid


def get_document(document_id):
    with connect() as db:
        row = db.execute('SELECT d.*, k.user_id FROM documents d JOIN knowledge k ON k.id = d.knowledge_id '
                         'WHERE d.id = ?', (document_id,)).fetchone()
    return dict(row) if row else None


def delete_document(document_id, user_id):
    document = get_document(document_id)
    if document is None or document['user_id'] != user_id:
        return False
    with connect() as db:
        db.execute('DELETE FROM chunks WHERE document_id = ?', (document_id,))
        db.execute('DELETE FROM documents WHERE id = ?', (document_id,))
    remove_document_files([document['path']])
    return True


def next_queued_document():
    with connect() as db:
        row = db.execute("SELECT id FROM documents WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
    return row['id'] if row else None


def set_document_status(document_id, status, error=None, chunks=None):
    with connect() as db:
        if chunks is None:
            db.execute('UPDATE documents SET status = ?, error = ? WHERE id = ?', (status, error, document_id))
        else:
            db.execute('UPDATE documents SET status = ?, error = ?, chunks = ? WHERE id = ?',
                       (status, error, chunks, document_id))


def save_chunks(document_id, knowledge_id, texts, embeddings):
    with connect() as db:
        db.execute('DELETE FROM chunks WHERE document_id = ?', (document_id,))
        db.executemany('INSERT INTO chunks (document_id, knowledge_id, position, text, embedding) VALUES (?, ?, ?, ?, ?)',
                       [(document_id, knowledge_id, i, text, vector) for i, (text, vector) in enumerate(zip(texts, embeddings))])


def chunk_vectors(knowledge_ids):
    """(chunk ids, embedding blobs) of every indexed chunk in these collections."""
    if not knowledge_ids:
        return []
    with connect() as db:
        return db.execute(f'SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL AND knowledge_id IN '
                          f'({", ".join("?" * len(knowledge_ids))})', list(knowledge_ids)).fetchall()


def keyword_chunks(knowledge_ids, query, limit):
    """Chunk ids ranked by FTS5 (any of the words), best first; [] without FTS5."""
    words = [w.replace('"', '') for w in query.split() if len(w) > 2]
    if not knowledge_ids or not words:
        return []
    with connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks_fts'").fetchone():
            return []
        match = ' OR '.join(f'"{w}"' for w in words[:30])
        return [row[0] for row in db.execute(
            f'SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ? '
            f'AND c.knowledge_id IN ({", ".join("?" * len(knowledge_ids))}) ORDER BY rank LIMIT ?',
            (match, *knowledge_ids, limit))]


def chunks_by_id(ids):
    if not ids:
        return {}
    with connect() as db:
        rows = db.execute(f'SELECT c.id, c.text, c.position, c.document_id, d.name AS document, k.name AS knowledge, '
                          f'k.id AS knowledge_id FROM chunks c JOIN documents d ON d.id = c.document_id '
                          f'JOIN knowledge k ON k.id = c.knowledge_id WHERE c.id IN ({", ".join("?" * len(ids))})', list(ids))
        return {row['id']: dict(row) for row in rows}
