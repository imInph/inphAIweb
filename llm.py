"""LM Studio and Ollama, both through their OpenAI-style /v1 API."""
import json
import time
import urllib.error
import urllib.request
import settings

BACKENDS = {'lmstudio': {'name': 'LM Studio'}, 'ollama': {'name': 'Ollama'}}


def url(backend):
    return settings.get(f'{backend}_url')


def request(backend, path, body=None, timeout=5, base=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request((base or url(backend)) + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=timeout)


def describe(backend, error, base=None):
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
        return f'{name} is not running at {base or url(backend)}'
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


def test(backend, base):
    """Check an address before it's saved: how many chat models answer there."""
    try:
        with request(backend, '/v1/models', base=base) as response:
            listing = json.load(response)
        return {'ok': True, 'models': sum('embed' not in item.get('id', '').lower() for item in listing.get('data', []))}
    except Exception as error:
        return {'ok': False, 'error': describe(backend, error, base)}


def stream_chat(backend, model, messages, options=None):
    """Yield ('think', text), ('text', text) and finally ('stats', {...}) for one reply.

    Each message is {'role', 'content', 'images': [(mime, base64), ...]}.
    """
    options = options or {}
    if backend == 'ollama':
        yield from stream_ollama(model, messages, options)
    else:
        yield from stream_lmstudio(model, messages, options)


def stream_lmstudio(model, messages, options):
    body = {'model': model, 'stream': True, 'stream_options': {'include_usage': True},
            'messages': [openai_message(m) for m in messages],
            **{name: options[name] for name in settings.LMSTUDIO_OPTIONS if name in options}}
    first = tokens = None
    started = time.monotonic()
    # Loading a model into memory can take minutes, so the read timeout is generous.
    with request('lmstudio', '/v1/chat/completions', body, timeout=600) as response:
        for raw in response:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                break
            chunk = json.loads(payload)
            if chunk.get('error'):
                error = chunk['error']
                raise RuntimeError(error.get('message', str(error)) if isinstance(error, dict) else str(error))
            if chunk.get('usage'):
                tokens = chunk['usage'].get('completion_tokens')
            for choice in chunk.get('choices', []):
                delta = choice.get('delta') or {}
                think = delta.get('reasoning_content') or delta.get('reasoning')
                if (think or delta.get('content')) and first is None:
                    first = time.monotonic()
                if think:
                    yield 'think', think
                if delta.get('content'):
                    yield 'text', delta['content']
    if tokens and first is not None:
        seconds = max(time.monotonic() - first, 1e-3)
        yield 'stats', {'tokens': tokens, 'tokens_per_second': round(tokens / seconds, 1),
                        'seconds': round(time.monotonic() - started, 1)}


def openai_message(message):
    if not message.get('images'):
        return {'role': message['role'], 'content': message['content']}
    parts = [{'type': 'text', 'text': message['content']}] if message['content'] else []
    parts += [{'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{data}'}}
              for mime, data in message['images']]
    return {'role': message['role'], 'content': parts}


def stream_ollama(model, messages, options):
    # Ollama's own API, because its /v1 endpoint ignores most options (num_ctx above all).
    body = {'model': model, 'stream': True,
            'messages': [{'role': m['role'], 'content': m['content'],
                          **({'images': [data for _, data in m['images']]} if m.get('images') else {})}
                         for m in messages]}
    model_options = {('num_predict' if name == 'max_tokens' else name): value for name, value in options.items()
                     if name not in ('keep_alive', 'think')}
    if model_options:
        body['options'] = model_options
    if 'think' in options:
        body['think'] = options['think']
    if 'keep_alive' in options:
        body['keep_alive'] = options['keep_alive']
    started = time.monotonic()
    with request('ollama', '/api/chat', body, timeout=600) as response:
        for raw in response:
            line = raw.decode('utf-8', 'replace').strip()
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get('error'):
                raise RuntimeError(chunk['error'])
            message = chunk.get('message') or {}
            if message.get('thinking'):
                yield 'think', message['thinking']
            if message.get('content'):
                yield 'text', message['content']
            if chunk.get('done'):
                tokens, duration = chunk.get('eval_count'), chunk.get('eval_duration')
                if tokens and duration:
                    yield 'stats', {'tokens': tokens, 'tokens_per_second': round(tokens / (duration / 1e9), 1),
                                    'prompt_tokens': chunk.get('prompt_eval_count'),
                                    'seconds': round(time.monotonic() - started, 1)}
                return


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
