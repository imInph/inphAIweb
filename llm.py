"""LM Studio and Ollama, both through their OpenAI-style /v1 API."""
import json
import os
import urllib.error
import urllib.request

BACKENDS = {
    'lmstudio': {'name': 'LM Studio', 'url': os.environ.get('LMSTUDIO_URL', 'http://localhost:1234').rstrip('/')},
    'ollama': {'name': 'Ollama', 'url': os.environ.get('OLLAMA_URL', 'http://localhost:11434').rstrip('/')},
}


def request(backend, path, body=None, timeout=5):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BACKENDS[backend]['url'] + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=timeout)


def describe(backend, error):
    name = BACKENDS[backend]['name']
    if isinstance(error, urllib.error.HTTPError):
        try:
            detail = json.loads(error.read().decode('utf-8', 'replace')).get('error')
            if isinstance(detail, dict):
                detail = detail.get('message')
        except (ValueError, AttributeError):
            detail = None
        return f'{name}: {detail or error.reason} ({error.code})'
    if isinstance(error, urllib.error.URLError):
        return f'{name} is not running at {BACKENDS[backend]["url"]}'
    return f'{name}: {error}'


def list_models():
    found, errors = [], {}
    for key, backend in BACKENDS.items():
        try:
            with request(key, '/v1/models') as response:
                listing = json.load(response)
            # Embedding models can't chat, and both servers list them here.
            ids = sorted(item['id'] for item in listing.get('data', [])
                         if 'id' in item and 'embed' not in item['id'].lower())
            found.extend({'backend': key, 'backend_name': backend['name'], 'id': i} for i in ids)
        except Exception as error:
            errors[key] = describe(key, error)
    return found, errors


def stream_chat(backend, model, messages, temperature=None):
    """Yield ('think', text) and ('text', text) pieces of the reply."""
    body = {'model': model, 'messages': messages, 'stream': True}
    if temperature is not None:
        body['temperature'] = temperature
    # Loading a model into memory can take minutes, so the read timeout is generous.
    with request(backend, '/v1/chat/completions', body, timeout=600) as response:
        for raw in response:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                return
            chunk = json.loads(payload)
            if chunk.get('error'):
                error = chunk['error']
                raise RuntimeError(error.get('message', str(error)) if isinstance(error, dict) else str(error))
            for choice in chunk.get('choices', []):
                delta = choice.get('delta') or {}
                # LM Studio sends reasoning_content, Ollama sends reasoning.
                think = delta.get('reasoning_content') or delta.get('reasoning')
                if think:
                    yield 'think', think
                if delta.get('content'):
                    yield 'text', delta['content']


def unload_ollama():
    """Free the GPU for image/video work: Ollama unloads a model sent keep_alive 0."""
    try:
        with request('ollama', '/api/ps', timeout=2) as response:
            loaded = [m['name'] for m in json.load(response).get('models', [])]
        for name in loaded:
            request('ollama', '/api/generate', {'model': name, 'keep_alive': 0}, timeout=30).close()
        return loaded
    except Exception:
        return []
