"""LM Studio and Ollama, both through their OpenAI-style /v1 API."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import urllib.error
import urllib.request
import settings

BACKENDS = {'lmstudio': {'name': 'LM Studio'}, 'ollama': {'name': 'Ollama'}}


def url(backend):
    return settings.get(f'{backend}_url')


def request(backend, path, body=None, timeout=5, base=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request((base or url(backend)) + path, data=data, method=method,
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


def get_json(backend, path, timeout=5):
    with request(backend, path, timeout=timeout) as response:
        return json.load(response)


# What a model can do ('vision', 'thinking', 'tools', 'embedding', ...). Ollama says so in /api/show;
# it doesn't change until the model is pulled again, so it's cached by name and modification time.
capability_cache = {}
cache_lock = threading.Lock()


def ollama_show(name):
    with request('ollama', '/api/show', {'model': name}, timeout=10) as response:
        return json.load(response)


def ollama_capabilities(name, modified):
    with cache_lock:
        if (name, modified) in capability_cache:
            return capability_cache[(name, modified)]
    try:
        capabilities = ollama_show(name).get('capabilities')
    except Exception:
        capabilities = None
    with cache_lock:
        capability_cache[(name, modified)] = capabilities
    return capabilities


def lmstudio_listing():
    """LM Studio's own REST API (/api/v0) says each model's type and whether it's loaded; older
    versions only have /v1/models. Returns (models, detailed)."""
    try:
        return get_json('lmstudio', '/api/v0/models').get('data', []), True
    except urllib.error.HTTPError:
        return get_json('lmstudio', '/v1/models').get('data', []), False


def list_models():
    """Every chat model of both servers, with what each can do when the server says."""
    found, errors = [], {}
    try:
        tags = get_json('ollama', '/api/tags').get('models', [])
        with ThreadPoolExecutor(max_workers=8) as pool:
            capabilities = list(pool.map(lambda m: ollama_capabilities(m['name'], m.get('modified_at')), tags))
        for model, caps in sorted(zip(tags, capabilities), key=lambda pair: pair[0]['name']):
            is_embedding = 'embedding' in (caps or []) or 'embed' in model['name'].lower()
            if not is_embedding:
                found.append({'backend': 'ollama', 'backend_name': 'Ollama', 'id': model['name'], 'capabilities': caps})
    except Exception as error:
        errors['ollama'] = describe('ollama', error)
    try:
        listing, detailed = lmstudio_listing()
        for model in sorted(listing, key=lambda m: m.get('id', '')):
            kind = model.get('type')
            if 'id' not in model or kind == 'embeddings' or 'embed' in model['id'].lower():
                continue
            # /api/v0 marks vision models as "vlm"; without it nothing is known.
            caps = (['vision'] if kind == 'vlm' else []) if detailed else None
            found.append({'backend': 'lmstudio', 'backend_name': 'LM Studio', 'id': model['id'], 'capabilities': caps})
    except Exception as error:
        errors['lmstudio'] = describe('lmstudio', error)
    return found, errors


# Ollama management (the Models page)

def ollama_overview():
    tags = get_json('ollama', '/api/tags').get('models', [])
    loaded = get_json('ollama', '/api/ps').get('models', [])
    installed = []
    for model in sorted(tags, key=lambda m: m['name']):
        details = model.get('details') or {}
        installed.append({'name': model['name'], 'size': model.get('size'), 'modified_at': model.get('modified_at'),
                          'family': details.get('family'), 'parameters': details.get('parameter_size'),
                          'quantization': details.get('quantization_level'),
                          'capabilities': ollama_capabilities(model['name'], model.get('modified_at'))})
    running = [{'name': m.get('name'), 'size': m.get('size'), 'vram': m.get('size_vram'), 'expires_at': m.get('expires_at'),
                'context': m.get('context_length')} for m in loaded]
    return {'installed': installed, 'loaded': running}


def ollama_details(name):
    shown = ollama_show(name)
    return {'capabilities': shown.get('capabilities'), 'details': shown.get('details'),
            'parameters': shown.get('parameters'), 'template': shown.get('template'),
            'license': (shown.get('license') or '')[:2000] or None}


def ollama_delete(name):
    request('ollama', '/api/delete', {'model': name}, timeout=30, method='DELETE').close()


def ollama_unload(name):
    request('ollama', '/api/generate', {'model': name, 'keep_alive': 0}, timeout=30).close()


# Pulls run on the server in their own thread, so leaving the page doesn't stop them.
pulls = {}
pulls_lock = threading.Lock()


def start_pull(name):
    with pulls_lock:
        if name in pulls and not pulls[name]['done']:
            return pulls[name]
        pulls[name] = {'name': name, 'status': 'starting', 'completed': 0, 'total': 0, 'error': None, 'done': False}
    threading.Thread(target=run_pull, args=(name,), daemon=True).start()
    return pulls[name]


def run_pull(name):
    progress = pulls[name]
    try:
        with request('ollama', '/api/pull', {'model': name, 'stream': True}, timeout=3600) as response:
            for raw in response:
                line = raw.decode('utf-8', 'replace').strip()
                if not line:
                    continue
                event = json.loads(line)
                if event.get('error'):
                    raise RuntimeError(event['error'])
                progress['status'] = event.get('status', progress['status'])
                if event.get('total'):
                    progress['total'], progress['completed'] = event['total'], event.get('completed', 0)
        progress['status'] = 'done'
        with cache_lock:
            capability_cache.clear()
    except Exception as error:
        progress['error'] = describe('ollama', error)
    finally:
        progress['done'] = True
        progress['finished_at'] = time.time()


def pull_status():
    with pulls_lock:
        # Finished pulls are shown for ten minutes.
        for name in [n for n, p in pulls.items() if p['done'] and time.time() - p['finished_at'] > 600]:
            del pulls[name]
        return list(pulls.values())


def test(backend, base):
    """Check an address before it's saved: how many chat models answer there."""
    try:
        with request(backend, '/v1/models', base=base) as response:
            listing = json.load(response)
        return {'ok': True, 'models': sum('embed' not in item.get('id', '').lower() for item in listing.get('data', []))}
    except Exception as error:
        return {'ok': False, 'error': describe(backend, error, base)}


def stream_chat(backend, model, messages, options=None, tools=None):
    """Yield ('think', text), ('text', text), ('stats', {...}) and, if the model wants tools run,
    ('tool_calls', [{'id', 'name', 'arguments'}]) for one reply.

    Each message is {'role', 'content', 'images': [(mime, base64), ...]}; an assistant message may
    carry 'tool_calls', and {'role': 'tool', 'content', 'tool_call_id', 'name'} is a tool's result.
    tools are OpenAI-style function definitions.
    """
    options = options or {}
    if backend == 'ollama':
        yield from stream_ollama(model, messages, options, tools)
    else:
        yield from stream_lmstudio(model, messages, options, tools)


def stream_lmstudio(model, messages, options, tools=None):
    body = {'model': model, 'stream': True, 'stream_options': {'include_usage': True},
            'messages': [openai_message(m) for m in messages],
            **{name: options[name] for name in settings.LMSTUDIO_OPTIONS if name in options}}
    if tools:
        body['tools'] = tools
    calls = {}
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
                # Tool calls arrive in pieces: the name once, the JSON arguments in fragments.
                for part in delta.get('tool_calls') or []:
                    call = calls.setdefault(part.get('index', 0), {'id': None, 'name': '', 'arguments': ''})
                    call['id'] = part.get('id') or call['id']
                    function = part.get('function') or {}
                    call['name'] += function.get('name') or ''
                    call['arguments'] += function.get('arguments') or ''
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
    if calls:
        yield 'tool_calls', [{'id': call['id'] or f'call_{index}', 'name': call['name'],
                              'arguments': parse_arguments(call['arguments'])} for index, call in sorted(calls.items())]


def parse_arguments(raw):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def openai_message(message):
    if message['role'] == 'tool':
        return {'role': 'tool', 'tool_call_id': message['tool_call_id'], 'content': message['content']}
    if message.get('tool_calls'):
        return {'role': 'assistant', 'content': message['content'] or None,
                'tool_calls': [{'id': call['id'], 'type': 'function',
                                'function': {'name': call['name'], 'arguments': json.dumps(call['arguments'])}}
                               for call in message['tool_calls']]}
    if not message.get('images'):
        return {'role': message['role'], 'content': message['content']}
    parts = [{'type': 'text', 'text': message['content']}] if message['content'] else []
    parts += [{'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{data}'}}
              for mime, data in message['images']]
    return {'role': message['role'], 'content': parts}


def ollama_message(message):
    if message['role'] == 'tool':
        return {'role': 'tool', 'content': message['content'], 'tool_name': message['name']}
    converted = {'role': message['role'], 'content': message['content']}
    if message.get('images'):
        converted['images'] = [data for _, data in message['images']]
    if message.get('tool_calls'):
        converted['tool_calls'] = [{'function': {'name': call['name'], 'arguments': call['arguments']}}
                                   for call in message['tool_calls']]
    return converted


def stream_ollama(model, messages, options, tools=None):
    # Ollama's own API, because its /v1 endpoint ignores most options (num_ctx above all).
    body = {'model': model, 'stream': True, 'messages': [ollama_message(m) for m in messages]}
    if tools:
        body['tools'] = tools
    calls = []
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
            for call in message.get('tool_calls') or []:
                function = call.get('function') or {}
                calls.append({'id': f'call_{len(calls)}', 'name': function.get('name', ''),
                              'arguments': parse_arguments(function.get('arguments'))})
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
                if calls:
                    yield 'tool_calls', calls
                return


def complete(backend, model, messages, options=None):
    """The whole reply at once, without its reasoning (for short jobs like titles)."""
    return ''.join(piece for kind, piece in stream_chat(backend, model, messages, options) if kind == 'text')


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
