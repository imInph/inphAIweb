"""Backup and restore of the whole site: the database, uploads, knowledge files and (optionally)
the generated images and videos. A restore is checked and set aside, then swapped in at the next
start, so nothing is replaced under a running server."""
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
import zipfile
import store

FOLDERS = ('uploads', 'knowledge', 'outputs')
PENDING = store.DATA / 'restore-pending'
STORED = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4', '.webm'}  # already compressed


def make(include_media):
    """Write a backup .zip into DATA and return its path (the caller deletes it once sent)."""
    store.DATA.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=store.DATA, suffix='.db', delete=False) as copy:
        copy_path = copy.name
    zip_file = tempfile.NamedTemporaryFile(dir=store.DATA, suffix='.zip', delete=False)
    zip_file.close()
    try:
        # SQLite's backup API: a consistent copy even while the site is writing.
        source, target = sqlite3.connect(store.DB_PATH), sqlite3.connect(copy_path)
        with target:
            source.backup(target)
        version = target.execute('PRAGMA user_version').fetchone()[0]
        source.close()
        target.close()
        with zipfile.ZipFile(zip_file.name, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('backup.json', json.dumps({'app': 'inphAI', 'created_at': time.time(),
                                                        'schema': version, 'media': include_media}))
            archive.write(copy_path, 'inphai.db')
            for folder in FOLDERS:
                if folder == 'outputs' and not include_media:
                    continue
                root = store.DATA / folder
                for path in sorted(root.rglob('*')) if root.exists() else []:
                    if path.is_file():
                        archive.write(path, f'{folder}/{path.relative_to(root).as_posix()}',
                                      zipfile.ZIP_STORED if path.suffix.lower() in STORED else zipfile.ZIP_DEFLATED)
    except Exception:
        Path(zip_file.name).unlink(missing_ok=True)
        raise
    finally:
        Path(copy_path).unlink(missing_ok=True)
    return zip_file.name


def safe_name(name):
    """A member's path inside DATA, or None if it isn't one a backup would contain."""
    parts = name.split('/')
    if name == 'inphai.db':
        return name
    if len(parts) < 2 or parts[0] not in FOLDERS or any(p in ('', '.', '..') or '\\' in p for p in parts[1:]):
        return None
    return name


def stage(path):
    """Check an uploaded backup and unpack it into restore-pending/. Returns what it holds."""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError("That isn't a .zip file")
    with archive:
        names = archive.namelist()
        if 'inphai.db' not in names:
            raise ValueError("That isn't an inphAI backup (it has no inphai.db)")
        with archive.open('inphai.db') as db:
            if db.read(16) != b'SQLite format 3\x00':
                raise ValueError("The backup's inphai.db isn't a SQLite database")
        shutil.rmtree(PENDING, ignore_errors=True)
        PENDING.mkdir(parents=True)
        counts = {folder: 0 for folder in FOLDERS}
        try:
            for info in archive.infolist():
                name = safe_name(info.filename)
                if name is None or info.is_dir():
                    continue
                target = PENDING / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, 'wb') as out:
                    shutil.copyfileobj(source, out)
                if '/' in name:
                    counts[name.split('/')[0]] += 1
            db = sqlite3.connect(PENDING / 'inphai.db')
            try:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError("The backup's database is damaged")
                users = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            except sqlite3.DatabaseError as error:
                raise ValueError(f"The backup's database can't be read: {error}")
            finally:
                db.close()
        except Exception:
            shutil.rmtree(PENDING, ignore_errors=True)
            raise
    return {'users': users, 'files': counts['uploads'], 'knowledge_files': counts['knowledge'],
            'media_files': counts['outputs'], 'media': counts['outputs'] > 0}


def discard():
    shutil.rmtree(PENDING, ignore_errors=True)


def pending():
    return (PENDING / 'inphai.db').is_file()


def apply_pending():
    """At start, before the database opens: swap a staged restore in. What it replaces is moved
    to data/before-restore-<time>/, never deleted."""
    if not pending():
        return
    keep = store.DATA / time.strftime('before-restore-%Y%m%d-%H%M%S')
    keep.mkdir(parents=True)
    for name in ('inphai.db', 'inphai.db-wal', 'inphai.db-shm'):
        if (store.DATA / name).exists():
            shutil.move(store.DATA / name, keep / name)
    shutil.move(PENDING / 'inphai.db', store.DB_PATH)
    for folder in FOLDERS:
        # A backup made without media leaves the current images and videos where they are.
        if folder == 'outputs' and not (PENDING / folder).exists():
            continue
        if (store.DATA / folder).exists():
            shutil.move(store.DATA / folder, keep / folder)
        if (PENDING / folder).exists():
            shutil.move(PENDING / folder, store.DATA / folder)
    shutil.rmtree(PENDING, ignore_errors=True)
    print(f'Restored a backup; the previous data is in {keep}', flush=True)
