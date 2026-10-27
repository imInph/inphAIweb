"""Chat attachments: images go to vision models, text and PDFs are read into the message."""
import base64
import io
from pathlib import Path
import re
import uuid
import store

MAX_BYTES = 25 * 1024 * 1024
MAX_CHARS = 200_000
# Recognised by their first bytes, not the name or what the browser claims.
IMAGE_TYPES = [(b'\x89PNG\r\n\x1a\n', 'image/png'), (b'\xff\xd8\xff', 'image/jpeg')]
SAFE_NAME = re.compile(r'[^\w.\- ]+')


def image_type(head):
    for magic, mime in IMAGE_TYPES:
        if head.startswith(magic):
            return mime
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


def pdf_text(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError('Reading PDFs needs pypdf: run bash scripts/setup.sh app')
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or '' for page in reader.pages]
    except Exception as error:
        raise ValueError(f'Could not read this PDF ({error})')
    text = '\n\n'.join(f'[Page {i}]\n{page.strip()}' for i, page in enumerate(pages, 1) if page.strip())
    if not text:
        raise ValueError('This PDF has no text in it (a scan?). Send the pages as images instead.')
    return text


def plain_text(data):
    if b'\x00' in data[:8192]:
        raise ValueError("This file isn't text, an image (PNG, JPEG, WebP) or a PDF")
    for encoding in ('utf-8-sig', 'utf-16'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('latin-1')


def save(user_id, file):
    """Store one uploaded file (a werkzeug FileStorage) and return its attachment record."""
    data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f'Files can be at most {MAX_BYTES // (1024 * 1024)} MB')
    if not data:
        raise ValueError('That file is empty')
    name = SAFE_NAME.sub('_', Path(file.filename or 'file').name).strip() or 'file'
    mime, text, truncated = image_type(data[:16]), None, False
    if mime:
        kind = 'image'
    else:
        kind = 'text'
        if data.startswith(b'%PDF'):
            mime, text = 'application/pdf', pdf_text(data)
        else:
            mime, text = 'text/plain', plain_text(data)
        if len(text) > MAX_CHARS:
            text, truncated = text[:MAX_CHARS], True
    attachment_id = uuid.uuid4().hex
    folder = store.UPLOADS / attachment_id
    folder.mkdir(parents=True)
    (folder / name).write_bytes(data)
    return store.add_attachment(user_id, attachment_id, kind, name, mime, len(data), f'{attachment_id}/{name}', text, truncated)


def file_text(attachment, question):
    """A file's text for the model. A very long one is cut down to the passages that best match
    the question (when an embedding model is there to find them; else its beginning)."""
    import knowledge
    text = attachment['text']
    if len(text) <= knowledge.LARGE_FILE_CHARS:
        return text, ''
    if question.strip():
        try:
            passages = knowledge.passages_from_text(attachment['id'], text, question)
            return '\n[...]\n'.join(passages), ' excerpts="the passages that best match the question"'
        except Exception as error:
            print(f'Could not search {attachment["name"]}: {error}', flush=True)
    return text[:knowledge.LARGE_FILE_CHARS], ' truncated="true"'


def with_files(content, attachments):
    """The message as the model sees it: file contents first, then what was typed."""
    files = [a for a in attachments if a['kind'] == 'text']
    if not files:
        return content
    blocks = []
    for a in files:
        text, note = file_text(a, content)
        marker = note or (' truncated="true"' if a['truncated'] else '')
        blocks.append(f'<file name="{a["name"]}"{marker}>\n{text}\n</file>')
    return '\n\n'.join(blocks + ([content] if content.strip() else []))


def images(attachments):
    return [(a['mime'], base64.b64encode((store.UPLOADS / a['path']).read_bytes()).decode())
            for a in attachments if a['kind'] == 'image' and (store.UPLOADS / a['path']).is_file()]
