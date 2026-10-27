"""Knowledge: read documents, split them into overlapping chunks, embed them, and find the passages
that answer a question (keyword and vector ranks combined)."""
from collections import OrderedDict
from html.parser import HTMLParser
import io
import json
import re
import threading
import xml.etree.ElementTree as ElementTree
import zipfile
import numpy
import llm
import settings
import store
import uploads

CHUNK_CHARS = 1500
OVERLAP_CHARS = 200
BATCH = 16
wake = threading.Event()
vector_cache = {}            # knowledge id -> (chunk count, ids, normalised matrix)
cache_lock = threading.Lock()


# Reading documents

class TextOnly(HTMLParser):
    """The readable text of an HTML page: no scripts, styles, navigation or forms."""
    SKIP = {'script', 'style', 'noscript', 'nav', 'header', 'footer', 'form', 'svg', 'template', 'aside'}
    BLOCK = {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article', 'pre', 'blockquote'}

    def __init__(self):
        super().__init__()
        self.parts, self.skipping, self.title, self.in_title = [], 0, '', False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append('\n')
        self.in_title = tag == 'title'

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1
        elif tag in self.BLOCK:
            self.parts.append('\n')
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skipping:
            self.parts.append(data)

    def text(self):
        text = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(self.parts))
        return re.sub(r'\n\s*\n+', '\n\n', text).strip()


def html_text(html):
    parser = TextOnly()
    parser.feed(html)
    return parser.text(), parser.title.strip()


def docx_text(data):
    """A .docx is a zip; the words are in word/document.xml, one <w:p> per paragraph."""
    w = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            root = ElementTree.fromstring(archive.read('word/document.xml'))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise ValueError('Could not read this Word document')
    paragraphs = [''.join(node.text or '' for node in p.iter(f'{w}t')) for p in root.iter(f'{w}p')]
    return '\n\n'.join(p for p in paragraphs if p.strip())


def extract_text(name, data):
    lower = name.lower()
    if data.startswith(b'%PDF'):
        return uploads.pdf_text(data), 'application/pdf'
    if data[:2] == b'PK' and lower.endswith('.docx'):
        return docx_text(data), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    text = uploads.plain_text(data)
    if lower.endswith(('.html', '.htm')) or text.lstrip()[:15].lower().startswith(('<!doctype html', '<html')):
        return html_text(text)[0], 'text/html'
    return text, 'text/plain'


# Chunks and embeddings

def chunk_text(text, size=CHUNK_CHARS, overlap=OVERLAP_CHARS):
    """Pieces of about `size` characters, broken at paragraphs (or sentences), overlapping a little."""
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    pieces = []
    for paragraph in paragraphs:
        while len(paragraph) > size:
            cut = max(paragraph.rfind('. ', 0, size), paragraph.rfind('\n', 0, size))
            cut = cut + 1 if cut > size // 2 else size
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            pieces.append(paragraph)
    chunks, current = [], ''
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            current = current[-overlap:].split(' ', 1)[-1] + '\n\n' + piece if overlap else piece
        else:
            current = f'{current}\n\n{piece}' if current else piece
    if current:
        chunks.append(current)
    return chunks


def task_prefix(model, kind):
    # nomic-embed-text is trained with these task prefixes and finds better matches with them.
    if 'nomic-embed' in model:
        return 'search_query: ' if kind == 'query' else 'search_document: '
    return ''


def embed(texts, kind='document'):
    """Unit-length float32 vectors for these texts, from the embedding model in Settings."""
    config = settings.get()
    backend, model = config['embed_backend'], config['embed_model']
    prefix = task_prefix(model, kind)
    vectors = []
    for start in range(0, len(texts), BATCH):
        batch = [prefix + text for text in texts[start:start + BATCH]]
        try:
            if backend == 'ollama':
                with llm.request('ollama', '/api/embed', {'model': model, 'input': batch}, timeout=300) as response:
                    vectors += json.load(response)['embeddings']
            else:
                with llm.request('lmstudio', '/v1/embeddings', {'model': model, 'input': batch}, timeout=300) as response:
                    vectors += [item['embedding'] for item in sorted(json.load(response)['data'], key=lambda d: d['index'])]
        except Exception as error:
            raise RuntimeError(f'{llm.describe(backend, error)}. Is the embedding model "{model}" installed? '
                               f'(Models page; or change it in Settings → Knowledge.)')
    matrix = numpy.asarray(vectors, dtype=numpy.float32)
    norms = numpy.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / numpy.where(norms == 0, 1, norms)


# Indexing (one worker thread)

def index_document(document_id):
    document = store.get_document(document_id)
    if document is None:
        return
    store.set_document_status(document_id, 'indexing')
    try:
        data = (store.KNOWLEDGE / document['path']).read_bytes()
        text, _ = extract_text(document['name'], data)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError('There is no text in this document')
        vectors = embed(chunks)
        store.save_chunks(document_id, document['knowledge_id'], chunks, [v.tobytes() for v in vectors])
        store.set_document_status(document_id, 'ready', chunks=len(chunks))
    except Exception as error:
        store.set_document_status(document_id, 'error', error=str(error))
    with cache_lock:
        vector_cache.pop(document['knowledge_id'], None)


def worker():
    while True:
        document_id = store.next_queued_document()
        if document_id is None:
            wake.wait(timeout=60)
            wake.clear()
            continue
        try:
            index_document(document_id)
        except Exception as error:
            print(f'Indexing document {document_id} failed: {error}', flush=True)
            store.set_document_status(document_id, 'error', error=str(error))


def start_worker():
    threading.Thread(target=worker, name='knowledge-index', daemon=True).start()


def add_document(knowledge_id, file):
    """Save an uploaded file into a collection and queue it for indexing."""
    data = file.read(uploads.MAX_BYTES + 1)
    if len(data) > uploads.MAX_BYTES:
        raise ValueError(f'Files can be at most {uploads.MAX_BYTES // (1024 * 1024)} MB')
    if not data:
        raise ValueError('That file is empty')
    name = uploads.SAFE_NAME.sub('_', (file.filename or 'document').rsplit('/', 1)[-1]).strip() or 'document'
    _, mime = extract_text(name, data)   # refuses files it can't read, before anything is saved
    folder = store.KNOWLEDGE / str(knowledge_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    stem, suffix, n = path.stem, path.suffix, 1
    while path.exists():
        n += 1
        path = folder / f'{stem} ({n}){suffix}'
    path.write_bytes(data)
    document_id = store.add_document(knowledge_id, path.name, mime, len(data), f'{knowledge_id}/{path.name}')
    wake.set()
    return document_id


# Retrieval

def vectors_for(knowledge_id):
    """Every chunk vector of one collection as a matrix, cached until the collection changes."""
    rows = store.chunk_vectors([knowledge_id])
    with cache_lock:
        cached = vector_cache.get(knowledge_id)
        if cached and cached[0] == len(rows):
            return cached[1], cached[2]
    ids = numpy.array([row['id'] for row in rows], dtype=numpy.int64)
    matrix = numpy.stack([numpy.frombuffer(row['embedding'], dtype=numpy.float32) for row in rows]) if rows else None
    with cache_lock:
        vector_cache[knowledge_id] = (len(rows), ids, matrix)
    return ids, matrix


def fuse(rankings, k=60):
    """Reciprocal rank fusion: each list adds 1/(k + rank) to an id's score."""
    scores = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0) + 1 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


def retrieve(knowledge_ids, query, top_k=None):
    """The best passages for this query across the collections, each with its source."""
    top_k = top_k or settings.get('rag_top_k')
    query = query.strip()[:4000]
    if not knowledge_ids or not query:
        return []
    by_vector = []
    question = embed([query], kind='query')[0]
    for knowledge_id in knowledge_ids:
        ids, matrix = vectors_for(knowledge_id)
        if matrix is None or matrix.shape[1] != question.shape[0]:
            continue
        scores = matrix @ question
        best = numpy.argsort(-scores)[:top_k * 4]
        by_vector += [(float(scores[i]), int(ids[i])) for i in best]
    by_vector = [chunk_id for _, chunk_id in sorted(by_vector, reverse=True)]
    by_keyword = store.keyword_chunks(knowledge_ids, query, top_k * 4)
    chosen = fuse([by_vector, by_keyword])[:top_k]
    found = store.chunks_by_id(chosen)
    return [found[i] for i in chosen if i in found]


# Large chat attachments: answer from their best passages instead of the whole text.

LARGE_FILE_CHARS = 24_000
attachment_cache = OrderedDict()   # attachment id -> (chunks, vectors)


def passages_from_text(attachment_id, text, query, top_k=8):
    if attachment_id not in attachment_cache:
        chunks = chunk_text(text)
        attachment_cache[attachment_id] = (chunks, embed(chunks))
        while len(attachment_cache) > 32:
            attachment_cache.popitem(last=False)
    chunks, vectors = attachment_cache[attachment_id]
    scores = vectors @ embed([query], kind='query')[0]
    best = sorted(numpy.argsort(-scores)[:top_k])   # keep the document's order
    return [chunks[i] for i in best]
