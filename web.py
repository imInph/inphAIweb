"""The web: search through SearXNG, read pages, and pick the passages that answer a question."""
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import knowledge
import settings
import uploads

MAX_PAGE_BYTES = 3 * 1024 * 1024
PAGE_TIMEOUT = 12
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) inphAI/1.0 (+self-hosted)'
URL_IN_TEXT = re.compile(r'#(https?://[^\s<>"\']+)')


class Blocked(Exception):
    pass


def check_host(url, allow_private):
    """Refuse addresses on this machine or its network unless allowed: otherwise anyone signed in
    could make the server fetch LM Studio, Ollama, the router..."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise Blocked('Only http and https links can be opened')
    if allow_private:
        return
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, parts.port or 443)}
    except socket.gaierror:
        raise Blocked(f'{parts.hostname} could not be found')
    for address in addresses:
        ip = ipaddress.ip_address(address.split('%')[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise Blocked(f'{parts.hostname} is on a private network; only an admin can open those')


class CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Check every redirect too, so a public page can't bounce the server onto a private address."""

    def __init__(self, allow_private):
        super().__init__()
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_host(newurl, self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page(url, allow_private=False):
    """(title, text) of a web page or PDF."""
    check_host(url, allow_private)
    opener = urllib.request.build_opener(CheckedRedirects(allow_private))
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,text/plain,application/pdf;q=0.9,*/*;q=0.5'})
    with opener.open(request, timeout=PAGE_TIMEOUT) as response:
        kind = response.headers.get_content_type()
        data = response.read(MAX_PAGE_BYTES + 1)[:MAX_PAGE_BYTES]
        charset = response.headers.get_content_charset() or 'utf-8'
    if data.startswith(b'%PDF') or kind == 'application/pdf':
        return url.rsplit('/', 1)[-1] or url, uploads.pdf_text(data)
    if kind not in ('text/html', 'application/xhtml+xml', 'text/plain', 'text/markdown'):
        raise ValueError(f"It's a {kind} file, not a page")
    text = data.decode(charset, 'replace')
    if kind == 'text/plain' or kind == 'text/markdown':
        return url, text
    body, title = knowledge.html_text(text)
    return title or url, body


def search(query, count):
    base = settings.get('searxng_url')
    address = f"{base}/search?{urllib.parse.urlencode({'q': query, 'format': 'json'})}"
    request = urllib.request.Request(address, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            results = json.load(response).get('results', [])
    except urllib.error.HTTPError as error:
        if error.code == 403:
            raise RuntimeError('SearXNG refused the JSON format: add "json" under search → formats in its settings.yml')
        raise RuntimeError(f'SearXNG answered {error.code}')
    except urllib.error.URLError as error:
        raise RuntimeError(f'SearXNG is not running at {base} ({error.reason})')
    seen, unique = set(), []
    for result in results:
        if result.get('url') and result['url'] not in seen:
            seen.add(result['url'])
            unique.append({'url': result['url'], 'title': result.get('title') or result['url'],
                           'snippet': result.get('content') or ''})
    return unique[:count]


def best_passages(question, pages, top_k):
    """Split the pages into passages and keep the top_k that best match the question."""
    passages = [(page, chunk) for page in pages for chunk in knowledge.chunk_text(page['text'])[:40]]
    if not passages:
        return []
    try:
        vectors = knowledge.embed([chunk for _, chunk in passages])
        scores = list(vectors @ knowledge.embed([question], kind='query')[0])
    except Exception:
        # No embedding model: count the question's words in each passage instead.
        words = {w for w in re.findall(r'\w{3,}', question.lower())}
        scores = [sum(chunk.lower().count(w) for w in words) for _, chunk in passages]
    ranked = sorted(range(len(passages)), key=lambda i: scores[i], reverse=True)[:top_k]
    return [passages[i] for i in sorted(ranked)]


def research(question, query, allow_private, on_status):
    """Search, read the top pages, and return the passages as numbered sources."""
    count = settings.get('web_results')
    on_status(f'Searching the web for "{query}"...')
    results = search(query, count)
    if not results:
        return []
    on_status(f'Reading {len(results)} page{"s" if len(results) != 1 else ""}...')

    def read(result):
        try:
            title, text = fetch_page(result['url'], allow_private)
            return {**result, 'title': result['title'] or title, 'text': text}
        except Exception:
            # A page that won't open still has its search snippet.
            return {**result, 'text': result['snippet']} if result['snippet'] else None

    with ThreadPoolExecutor(max_workers=min(8, len(results))) as pool:
        pages = [page for page in pool.map(read, results) if page and page['text'].strip()]
    chosen = best_passages(question, pages, max(settings.get('rag_top_k'), 3))
    return [{'n': n, 'kind': 'web', 'title': page['title'], 'url': page['url'], 'document': page['title'], 'text': chunk}
            for n, (page, chunk) in enumerate(chosen, 1)]


def links_in(text):
    return list(dict.fromkeys(match.rstrip('.,);:!?') for match in URL_IN_TEXT.findall(text)))[:5]
