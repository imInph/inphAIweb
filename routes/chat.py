"""Chats, messages and their uploads."""
import json
import re
from flask import Blueprint, Response, jsonify, request, send_from_directory, stream_with_context
import llm
import settings
import store
import uploads

bp = Blueprint('chat', __name__)
THINK_BLOCK = re.compile(r'<think>.*?(</think>|$)', re.S)


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
    if 'options' in data:
        fields['options'] = settings.validate_options(data['options'])
    if 'backend' in data or 'model' in data or not partial:
        if data.get('backend') not in llm.BACKENDS:
            raise ValueError('backend must be ' + ' or '.join(llm.BACKENDS))
        if not isinstance(data.get('model'), str) or not data['model'] or len(data['model']) > 300:
            raise ValueError('Pick a model')
        fields.update(backend=data['backend'], model=data['model'])
    return fields


@bp.get('/api/models')
def models():
    found, errors = llm.list_models()
    return jsonify(models=found, errors=errors), 200, {'Cache-Control': 'no-store'}


@bp.get('/api/chats')
def chats():
    return jsonify(chats=store.list_chats())


@bp.post('/api/chats')
def new_chat():
    try:
        fields = chat_fields(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(store.get_chat(store.create_chat(fields))), 201


@bp.delete('/api/chats')
def remove_all_chats():
    return jsonify(deleted=store.delete_all_chats())


@bp.get('/api/chats/<int:chat_id>')
def chat(chat_id):
    found = store.get_chat(chat_id)
    return jsonify(found) if found else (jsonify(error='No such chat'), 404)


@bp.patch('/api/chats/<int:chat_id>')
def edit_chat(chat_id):
    if not store.get_chat(chat_id):
        return jsonify(error='No such chat'), 404
    try:
        store.update_chat(chat_id, chat_fields(request.get_json(silent=True)))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(store.get_chat(chat_id))


@bp.delete('/api/chats/<int:chat_id>')
def remove_chat(chat_id):
    return ('', 204) if store.delete_chat(chat_id) else (jsonify(error='No such chat'), 404)


@bp.post('/api/chats/<int:chat_id>/messages')
def send(chat_id):
    """Save the user's message (or regenerate the last reply) and stream the answer as NDJSON."""
    data = request.get_json(silent=True)
    chat = store.get_chat(chat_id)
    if chat is None:
        return jsonify(error='No such chat'), 404
    try:
        fields = chat_fields(data, partial=False)
        regenerate = data.get('regenerate') is True
        content = data.get('content') or ''
        attachments = data.get('attachments') or []
        if not isinstance(attachments, list) or len(attachments) > 20 or not all(isinstance(a, str) for a in attachments):
            raise ValueError('attachments must be a list of at most 20 upload ids')
        attachments = store.unattached(attachments)
        if not regenerate and (not isinstance(content, str) or len(content) > 200000
                               or not (content.strip() or attachments)):
            raise ValueError('Write a message or attach a file (at most 200000 characters)')
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if regenerate:
        store.drop_trailing_replies(chat_id)
    else:
        if chat['title'] == 'New chat' and not chat['messages']:
            first_file = store.get_attachment(attachments[0])['name'] if attachments else ''
            fields['title'] = ' '.join(content.split())[:60] or first_file[:60]
        store.add_message(chat_id, 'user', content, attachments=attachments)
    store.update_chat(chat_id, fields, touch=True)
    chat = store.get_chat(chat_id)
    if not chat['messages'] or chat['messages'][-1]['role'] != 'user':
        return jsonify(error='There is no message to answer'), 400

    history = [{'role': 'system', 'content': chat['system_prompt']}] if chat['system_prompt'].strip() else []
    for m in chat['messages']:
        if m['role'] == 'assistant':
            # Earlier reasoning isn't sent back: it costs context and models aren't trained to see it.
            history.append({'role': 'assistant', 'content': THINK_BLOCK.sub('', m['content']).strip()})
        else:
            files = store.message_attachments(m['id']) if m['attachments'] else []
            history.append({'role': 'user', 'content': uploads.with_files(m['content'], files),
                            'images': uploads.images(files)})
    backend, model = chat['backend'], chat['model']

    def events():
        text, think, stats = [], [], None
        try:
            for kind, piece in llm.stream_chat(backend, model, history, chat['options']):
                if kind == 'stats':
                    stats = piece
                else:
                    (think if kind == 'think' else text).append(piece)
                yield json.dumps({kind: piece}) + '\n'
            yield json.dumps({'done': True}) + '\n'
        except Exception as error:
            yield json.dumps({'error': llm.describe(backend, error)}) + '\n'
        finally:
            # Runs on Stop too (the client disconnects), so a partial reply is kept.
            if text or think:
                store.add_message(chat_id, 'assistant', ''.join(text), ''.join(think), model, stats)

    return Response(stream_with_context(events()), mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@bp.post('/api/uploads')
def upload():
    file = request.files.get('file')
    if file is None:
        return jsonify(error='Send the file as multipart form data, field "file"'), 400
    try:
        return jsonify(uploads.save(file)), 201
    except ValueError as error:
        return jsonify(error=str(error)), 400


@bp.delete('/api/uploads/<attachment_id>')
def remove_upload(attachment_id):
    # Only an upload that hasn't been sent yet; sent ones belong to their chat.
    return ('', 204) if store.delete_unattached(attachment_id) else (jsonify(error='No such upload'), 404)


@bp.get('/api/uploads/<attachment_id>')
def upload_file(attachment_id):
    found = store.get_attachment(attachment_id)
    if found is None:
        return jsonify(error='No such file'), 404
    folder, name = found['path'].split('/', 1)
    return send_from_directory(store.UPLOADS / folder, name, mimetype=found['mime'],
                               as_attachment=found['kind'] != 'image', download_name=found['name'])
