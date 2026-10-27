"""inphAI: chat (LM Studio, Ollama), images and videos on one page. python app.py (port 5002)."""
import json
import os
import re
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from werkzeug.exceptions import HTTPException
import jobs
import llm
import settings
import store

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024
PORT = int(os.environ.get('PORT', '5002'))
THINK_BLOCK = re.compile(r'<think>.*?(</think>|$)', re.S)
store.init()


@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify(error=error.description), error.code


def chat_fields(data, partial=True):
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object')
    fields = {}
    if 'title' in data:
        if not isinstance(data['title'], str) or not data['title'].strip() or len(data['title']) > 200:
            raise ValueError('title must be 1 to 200 characters')
        fields['title'] = data['title'].strip()
    if 'system_prompt' in data:
        if not isinstance(data['system_prompt'], str) or len(data['system_prompt']) > 20000:
            raise ValueError('system_prompt must be text, at most 20000 characters')
        fields['system_prompt'] = data['system_prompt']
    if 'temperature' in data:
        value = data['temperature']
        if value is not None and (type(value) not in (int, float) or not 0 <= value <= 2):
            raise ValueError('temperature must be empty or a number from 0 to 2')
        fields['temperature'] = value
    if 'backend' in data or 'model' in data or not partial:
        if data.get('backend') not in llm.BACKENDS:
            raise ValueError('backend must be ' + ' or '.join(llm.BACKENDS))
        if not isinstance(data.get('model'), str) or not data['model'] or len(data['model']) > 300:
            raise ValueError('Pick a model')
        fields.update(backend=data['backend'], model=data['model'])
    return fields


# Chat

@app.get('/api/models')
def models():
    found, errors = llm.list_models()
    return jsonify(models=found, errors=errors), 200, {'Cache-Control': 'no-store'}


@app.get('/api/chats')
def chats():
    return jsonify(chats=store.list_chats())


@app.post('/api/chats')
def new_chat():
    try:
        fields = chat_fields(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(store.get_chat(store.create_chat(fields))), 201


@app.delete('/api/chats')
def remove_all_chats():
    return jsonify(deleted=store.delete_all_chats())


@app.get('/api/chats/<int:chat_id>')
def chat(chat_id):
    found = store.get_chat(chat_id)
    return jsonify(found) if found else (jsonify(error='No such chat'), 404)


@app.patch('/api/chats/<int:chat_id>')
def edit_chat(chat_id):
    if not store.get_chat(chat_id):
        return jsonify(error='No such chat'), 404
    try:
        store.update_chat(chat_id, chat_fields(request.get_json(silent=True)))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(store.get_chat(chat_id))


@app.delete('/api/chats/<int:chat_id>')
def remove_chat(chat_id):
    return ('', 204) if store.delete_chat(chat_id) else (jsonify(error='No such chat'), 404)


@app.post('/api/chats/<int:chat_id>/messages')
def send(chat_id):
    """Save the user's message (or regenerate the last reply) and stream the answer as NDJSON."""
    data = request.get_json(silent=True)
    chat = store.get_chat(chat_id)
    if chat is None:
        return jsonify(error='No such chat'), 404
    try:
        fields = chat_fields(data, partial=False)
        regenerate = data.get('regenerate') is True
        content = data.get('content')
        if not regenerate and (not isinstance(content, str) or not content.strip() or len(content) > 200000):
            raise ValueError('Write a message (at most 200000 characters)')
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if regenerate:
        store.drop_trailing_replies(chat_id)
    else:
        if chat['title'] == 'New chat' and not chat['messages']:
            fields['title'] = ' '.join(content.split())[:60]
        store.add_message(chat_id, 'user', content)
    store.update_chat(chat_id, fields, touch=True)
    chat = store.get_chat(chat_id)
    if not chat['messages'] or chat['messages'][-1]['role'] != 'user':
        return jsonify(error='There is no message to answer'), 400

    history = [{'role': 'system', 'content': chat['system_prompt']}] if chat['system_prompt'].strip() else []
    # Earlier reasoning isn't sent back: it costs context and models aren't trained to see it.
    history += [{'role': m['role'], 'content': THINK_BLOCK.sub('', m['content']).strip()
                 if m['role'] == 'assistant' else m['content']} for m in chat['messages']]
    backend, model = chat['backend'], chat['model']

    def events():
        text, think = [], []
        try:
            for kind, piece in llm.stream_chat(backend, model, history, chat['temperature']):
                (think if kind == 'think' else text).append(piece)
                yield json.dumps({kind: piece}) + '\n'
            yield json.dumps({'done': True}) + '\n'
        except Exception as error:
            yield json.dumps({'error': llm.describe(backend, error)}) + '\n'
        finally:
            # Runs on Stop too (the client disconnects), so a partial reply is kept.
            if text or think:
                store.add_message(chat_id, 'assistant', ''.join(text), ''.join(think), model)

    return Response(stream_with_context(events()), mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


# Settings

def folder_size(path):
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file()) if path.exists() else 0


@app.get('/api/settings')
def get_settings():
    return jsonify(settings=settings.get(), defaults=settings.DEFAULTS, counts=store.counts(),
                   disk={'database': folder_size(store.DATA) - folder_size(jobs.OUTPUTS),
                         'images': folder_size(jobs.OUTPUTS / 'images'),
                         'videos': folder_size(jobs.OUTPUTS / 'videos')}), 200, {'Cache-Control': 'no-store'}


@app.patch('/api/settings')
def save_settings():
    try:
        store.save_settings(settings.validate(request.get_json(silent=True)))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(settings=settings.get())


@app.post('/api/settings/test')
def test_connection():
    data = request.get_json(silent=True) or {}
    if data.get('backend') not in llm.BACKENDS:
        return jsonify(error='backend must be ' + ' or '.join(llm.BACKENDS)), 400
    try:
        base = settings.validate({f"{data['backend']}_url": data.get('url')})[f"{data['backend']}_url"]
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(llm.test(data['backend'], base))


# Images and videos

def start_job(kind, validate):
    try:
        settings = validate(request.get_json(silent=True))
        jobs.start(kind, settings)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except FileNotFoundError as error:
        return jsonify(error=str(error)), 503
    except jobs.Busy as error:
        return jsonify(error=str(error)), 409
    return jsonify(jobs.current()), 202


@app.post('/api/images')
def generate_image():
    return start_job('image', jobs.validate_image)


@app.post('/api/videos')
def generate_video():
    return start_job('video', jobs.validate_video)


@app.get('/api/job')
def job():
    return jsonify(jobs.current()), 200, {'Cache-Control': 'no-store'}


@app.post('/api/job/cancel')
def cancel_job():
    return jsonify(cancelled=jobs.cancel())


@app.get('/api/media')
def media():
    kind = request.args.get('kind')
    if kind not in ('image', 'video'):
        return jsonify(error='kind must be image or video'), 400
    return jsonify(media=store.list_media(kind))


@app.delete('/api/media/<int:media_id>')
def remove_media(media_id):
    item = store.get_media(media_id)
    if item is None:
        return jsonify(error='No such file'), 404
    path = jobs.OUTPUTS / item['file']
    # Also remove the sidecar files the generators write beside it.
    for extra in (path, path.with_suffix('.json'), path.with_suffix('.log'), path.with_suffix('.safetensors')):
        extra.unlink(missing_ok=True)
    if path.parent != jobs.OUTPUTS / 'images':
        (path.parent / 'output.log').unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
    store.delete_media(media_id)
    return '', 204


@app.get('/media/<path:filename>')
def media_file(filename):
    return send_from_directory(jobs.OUTPUTS, filename, conditional=True)


@app.get('/')
def index():
    return app.send_static_file('index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True, use_reloader=False)
