"""Chats, messages and their uploads."""
import json
import re
from flask import Blueprint, Response, g, jsonify, request, send_from_directory, stream_with_context
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
    return jsonify(chats=store.list_chats(g.user['id']))


@bp.post('/api/chats')
def new_chat():
    try:
        fields = chat_fields(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(store.get_chat(store.create_chat(g.user['id'], fields), g.user['id'])), 201


@bp.delete('/api/chats')
def remove_all_chats():
    return jsonify(deleted=store.delete_all_chats(g.user['id']))


@bp.get('/api/chats/<int:chat_id>')
def chat(chat_id):
    found = store.get_chat(chat_id, g.user['id'])
    return jsonify(found) if found else (jsonify(error='No such chat'), 404)


@bp.patch('/api/chats/<int:chat_id>')
def edit_chat(chat_id):
    if not store.get_chat(chat_id, g.user['id']):
        return jsonify(error='No such chat'), 404
    try:
        store.update_chat(chat_id, g.user['id'], chat_fields(request.get_json(silent=True)))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(store.get_chat(chat_id, g.user['id']))


@bp.delete('/api/chats/<int:chat_id>')
def remove_chat(chat_id):
    return ('', 204) if store.delete_chat(chat_id, g.user['id']) else (jsonify(error='No such chat'), 404)


def history_for(chat, message_id):
    """What the model is sent to answer message_id: the system prompt and the branch up to it."""
    history = [{'role': 'system', 'content': chat['system_prompt']}] if chat['system_prompt'].strip() else []
    for m in store.path_to(chat['id'], message_id):
        if m['role'] == 'assistant':
            # Earlier reasoning isn't sent back: it costs context and models aren't trained to see it.
            history.append({'role': 'assistant', 'content': THINK_BLOCK.sub('', m['content']).strip()})
        else:
            files = store.message_attachments(m['id'])
            history.append({'role': 'user', 'content': uploads.with_files(m['content'], files),
                            'images': uploads.images(files)})
    return history


def reply_stream(chat, user_message_id, backend, model):
    """Stream one answer to user_message_id as NDJSON, saving it (even a stopped, partial one)."""
    history = history_for(chat, user_message_id)
    options = chat['options']

    def events():
        yield json.dumps({'reply_to': user_message_id}) + '\n'
        text, think, stats, saved = [], [], None, None
        try:
            for kind, piece in llm.stream_chat(backend, model, history, options):
                if kind == 'stats':
                    stats = piece
                else:
                    (think if kind == 'think' else text).append(piece)
                yield json.dumps({kind: piece}) + '\n'
            saved = store.add_message(chat['id'], user_message_id, 'assistant', ''.join(text), ''.join(think), model, stats)
            yield json.dumps({'done': True, 'message_id': saved}) + '\n'
        except Exception as error:
            yield json.dumps({'error': llm.describe(backend, error)}) + '\n'
        finally:
            # Runs on Stop too (the client disconnects), so a partial reply is kept.
            if saved is None and (text or think):
                store.add_message(chat['id'], user_message_id, 'assistant', ''.join(text), ''.join(think), model, stats)

    return Response(stream_with_context(events()), mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


@bp.post('/api/chats/<int:chat_id>/messages')
def send(chat_id):
    """Answer something and stream the reply. The body says what:
    - content (+ attachments): a new message at the end of the shown branch;
    - reply_to: another answer to that user message (regenerate, or a second model to compare);
    - edit + content: a new version of that user message (beside the old one), then its answer.
    """
    data = request.get_json(silent=True)
    user_id = g.user['id']
    chat = store.get_chat(chat_id, user_id)
    if chat is None:
        return jsonify(error='No such chat'), 404
    try:
        fields = chat_fields(data, partial=False)
        content = data.get('content') or ''
        attachments = data.get('attachments') or []
        if not isinstance(attachments, list) or len(attachments) > 20 or not all(isinstance(a, str) for a in attachments):
            raise ValueError('attachments must be a list of at most 20 upload ids')
        attachments = store.unattached(attachments, user_id)
        reply_to, edit = data.get('reply_to'), data.get('edit')
        for name, value in (('reply_to', reply_to), ('edit', edit)):
            if value is not None and type(value) is not int:
                raise ValueError(f'{name} must be a message id')
        if reply_to is None and (not isinstance(content, str) or len(content) > 200000
                                 or not (content.strip() or attachments or edit)):
            raise ValueError('Write a message or attach a file (at most 200000 characters)')
    except ValueError as error:
        return jsonify(error=str(error)), 400
    store.update_chat(chat_id, user_id, fields, touch=True)
    chat = store.get_chat(chat_id, user_id)

    if reply_to is not None:
        target = store.get_message(chat_id, reply_to)
        if target is None or target['role'] != 'user':
            return jsonify(error='reply_to must be one of your messages in this chat'), 400
        return reply_stream(chat, reply_to, fields['backend'], fields['model'])

    if edit is not None:
        original = store.get_message(chat_id, edit)
        if original is None or original['role'] != 'user':
            return jsonify(error='Only your own messages can be edited this way'), 400
        message_id = store.add_message(chat_id, original['parent_id'], 'user', content)
        store.copy_attachments(edit, message_id)
    else:
        if chat['title'] == 'New chat' and not chat['messages']:
            first_file = store.get_attachment(attachments[0], user_id)['name'] if attachments else ''
            store.update_chat(chat_id, user_id, {'title': ' '.join(content.split())[:60] or first_file[:60]})
        parent = chat['messages'][-1]['id'] if chat['messages'] else None
        message_id = store.add_message(chat_id, parent, 'user', content, attachments=attachments)
    return reply_stream(store.get_chat(chat_id, user_id), message_id, fields['backend'], fields['model'])


@bp.post('/api/chats/<int:chat_id>/branch')
def switch_branch(chat_id):
    """Show the branch through message_id (following its newest replies to the end)."""
    if not store.get_chat(chat_id, g.user['id']):
        return jsonify(error='No such chat'), 404
    message_id = (request.get_json(silent=True) or {}).get('message_id')
    if type(message_id) is not int or store.get_message(chat_id, message_id) is None:
        return jsonify(error='message_id must be a message in this chat'), 400
    store.set_leaf(chat_id, store.newest_leaf(chat_id, message_id))
    return jsonify(store.get_chat(chat_id, g.user['id']))


@bp.patch('/api/chats/<int:chat_id>/messages/<int:message_id>')
def edit_message(chat_id, message_id):
    """Change a reply's text in place (your own messages get a new version through send instead)."""
    if not store.get_chat(chat_id, g.user['id']):
        return jsonify(error='No such chat'), 404
    content = (request.get_json(silent=True) or {}).get('content')
    message = store.get_message(chat_id, message_id)
    if message is None:
        return jsonify(error='No such message'), 404
    if not isinstance(content, str) or len(content) > 200000:
        return jsonify(error='content must be text, at most 200000 characters'), 400
    store.edit_message(chat_id, message_id, content)
    return jsonify(store.get_chat(chat_id, g.user['id']))


@bp.delete('/api/chats/<int:chat_id>/messages/<int:message_id>')
def delete_message(chat_id, message_id):
    if not store.get_chat(chat_id, g.user['id']):
        return jsonify(error='No such chat'), 404
    if not store.delete_message(chat_id, message_id):
        return jsonify(error='No such message'), 404
    return jsonify(store.get_chat(chat_id, g.user['id']))


@bp.post('/api/uploads')
def upload():
    file = request.files.get('file')
    if file is None:
        return jsonify(error='Send the file as multipart form data, field "file"'), 400
    try:
        return jsonify(uploads.save(g.user['id'], file)), 201
    except ValueError as error:
        return jsonify(error=str(error)), 400


@bp.delete('/api/uploads/<attachment_id>')
def remove_upload(attachment_id):
    # Only an upload that hasn't been sent yet; sent ones belong to their chat.
    return ('', 204) if store.delete_unattached(attachment_id, g.user['id']) else (jsonify(error='No such upload'), 404)


@bp.get('/api/uploads/<attachment_id>')
def upload_file(attachment_id):
    found = store.get_attachment(attachment_id, g.user['id'])
    if found is None:
        return jsonify(error='No such file'), 404
    folder, name = found['path'].split('/', 1)
    return send_from_directory(store.UPLOADS / folder, name, mimetype=found['mime'],
                               as_attachment=found['kind'] != 'image', download_name=found['name'])
