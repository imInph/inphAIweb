"""Small chat site for LM Studio and Ollama: python app.py (port 5002)."""
import json
import os
import urllib.error
import urllib.request
from flask import Flask, Response, jsonify, request, stream_with_context
from werkzeug.exceptions import HTTPException

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024
# Both servers speak the OpenAI-style API under /v1.
BACKENDS = {
    'lmstudio': {'name': 'LM Studio', 'url': os.environ.get('LMSTUDIO_URL', 'http://localhost:1234').rstrip('/')},
    'ollama': {'name': 'Ollama', 'url': os.environ.get('OLLAMA_URL', 'http://localhost:11434').rstrip('/')},
}
PORT = int(os.environ.get('PORT', '5002'))


@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify(error=error.description), error.code


def upstream(backend, path, body=None, timeout=5):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BACKENDS[backend]['url'] + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    return urllib.request.urlopen(req, timeout=timeout)


def upstream_error(error):
    if isinstance(error, urllib.error.HTTPError):
        try:
            detail = json.loads(error.read().decode('utf-8', 'replace'))
            detail = detail.get('error', detail)
            if isinstance(detail, dict):
                detail = detail.get('message', json.dumps(detail))
        except ValueError:
            detail = error.reason
        return f'{error.code}: {detail}'
    if isinstance(error, urllib.error.URLError):
        return f'not reachable ({error.reason})'
    return str(error)


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object')
    allowed = {'backend', 'model', 'messages', 'temperature'}
    if set(data) - allowed:
        raise ValueError('Unknown setting: ' + ', '.join(sorted(set(data) - allowed)))
    if data.get('backend') not in BACKENDS:
        raise ValueError('backend must be ' + ' or '.join(BACKENDS))
    model = data.get('model')
    if not isinstance(model, str) or not model or len(model) > 300:
        raise ValueError('Pick a model')
    messages = data.get('messages')
    if not isinstance(messages, list) or not messages or len(messages) > 500:
        raise ValueError('messages must be a list of 1 to 500 messages')
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                or message['role'] not in ('system', 'user', 'assistant')
                or not isinstance(message['content'], str)):
            raise ValueError('Each message needs a role (system, user, assistant) and text content')
    if messages[-1]['role'] != 'user':
        raise ValueError('The last message must be from the user')
    settings = {'backend': data['backend'], 'model': model, 'messages': messages}
    if 'temperature' in data:
        value = data['temperature']
        if type(value) not in (int, float) or not 0 <= value <= 2:
            raise ValueError('temperature must be a number from 0 to 2')
        settings['temperature'] = value
    return settings


@app.get('/api/models')
def models():
    found, errors = [], {}
    for key, backend in BACKENDS.items():
        try:
            with upstream(key, '/v1/models') as response:
                listing = json.load(response)
            ids = sorted(item['id'] for item in listing.get('data', []) if 'id' in item)
            found.extend({'backend': key, 'backend_name': backend['name'], 'id': i} for i in ids)
        except Exception as error:
            errors[key] = f"{backend['name']} {upstream_error(error)}"
    return jsonify(models=found, errors=errors), 200, {'Cache-Control': 'no-store'}


@app.post('/api/chat')
def chat():
    try:
        settings = validate(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    backend = settings.pop('backend')
    body = {**settings, 'stream': True}
    try:
        # Loading a model can take minutes, so the read timeout is generous.
        response = upstream(backend, '/v1/chat/completions', body, timeout=600)
    except Exception as error:
        return jsonify(error=f"{BACKENDS[backend]['name']} {upstream_error(error)}"), 502

    def events():
        # One JSON object per line: {"text"}, {"think"}, then {"done"} or {"error"}.
        try:
            with response:
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
                        yield json.dumps({'error': error.get('message', str(error)) if isinstance(error, dict) else str(error)}) + '\n'
                        return
                    for choice in chunk.get('choices', []):
                        delta = choice.get('delta') or {}
                        # LM Studio sends reasoning_content, Ollama sends reasoning.
                        think = delta.get('reasoning_content') or delta.get('reasoning')
                        if think:
                            yield json.dumps({'think': think}) + '\n'
                        if delta.get('content'):
                            yield json.dumps({'text': delta['content']}) + '\n'
            yield json.dumps({'done': True}) + '\n'
        except Exception as error:
            yield json.dumps({'error': f"{BACKENDS[backend]['name']} {upstream_error(error)}"}) + '\n'

    return Response(stream_with_context(events()), mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@app.get('/')
def index():
    return app.send_static_file('index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True, use_reloader=False)
