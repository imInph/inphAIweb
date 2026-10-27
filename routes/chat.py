"""Chats, messages and their uploads; folders, search, export and import."""
import io
import json
from pathlib import Path
import re
import threading
import time
import uuid
import zipfile
from flask import Blueprint, Response, g, jsonify, request, send_file, send_from_directory, stream_with_context
import llm
import jobs
import knowledge
import settings
import store
import tools
import uploads
import web

bp = Blueprint('chat', __name__)
THINK_BLOCK = re.compile(r'<think>.*?(</think>|$)', re.S)
MAX_TOOL_ROUNDS = 5


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
    for flag in ('pinned', 'archived', 'tools'):
        if flag in data:
            if type(data[flag]) is not bool:
                raise ValueError(f'{flag} must be true or false')
            fields[flag] = int(data[flag])
    if 'knowledge_ids' in data:
        ids = data['knowledge_ids']
        if not isinstance(ids, list) or len(ids) > 20 or not all(type(i) is int for i in ids):
            raise ValueError('knowledge_ids must be a list of collection ids')
        fields['knowledge_ids'] = store.owned_knowledge(list(dict.fromkeys(ids)), g.user['id'])
    if 'persona_id' in data:
        if data['persona_id'] is not None and (type(data['persona_id']) is not int
                                               or not store.get_item('personas', data['persona_id'], g.user['id'])):
            raise ValueError('persona_id must be one of your personas, or null')
        fields['persona_id'] = data['persona_id']
    if 'folder_id' in data:
        if data['folder_id'] is not None and (type(data['folder_id']) is not int
                                              or not store.get_folder(data['folder_id'], g.user['id'])):
            raise ValueError('folder_id must be one of your folders, or null')
        fields['folder_id'] = data['folder_id']
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
    archived = request.args.get('archived') == '1'
    return jsonify(chats=store.list_chats(g.user['id'], archived), folders=store.list_folders(g.user['id']),
                   archived_count=store.count_archived(g.user['id']))


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


def system_prompt(chat):
    """The chat's instructions, plus the user's memories when they're switched on."""
    parts = [chat['system_prompt'].strip()] if chat['system_prompt'].strip() else []
    if settings.prefs(g.user)['use_memories']:
        memories = [m['content'] for m in store.list_items('memories', g.user['id'])]
        if memories:
            parts.append('Things the user has asked you to remember about them:\n' + '\n'.join(f'- {m}' for m in memories))
    return '\n\n'.join(parts)


SOURCES_PROMPT = """Use the sources below to answer. Cite each one you use by its number in brackets, like [1].
If they don't answer the question, say so, then answer from what you know.

<sources>
{sources}
</sources>

{question}"""


def history_for(chat, message_id, sources=()):
    """What the model is sent to answer message_id: the instructions, the branch up to it, and
    (for the message being answered) any knowledge passages found for it."""
    instructions = system_prompt(chat)
    history = [{'role': 'system', 'content': instructions}] if instructions else []
    for m in store.path_to(chat['id'], message_id):
        if m['role'] == 'assistant':
            # Earlier reasoning isn't sent back: it costs context and models aren't trained to see it.
            history.append({'role': 'assistant', 'content': THINK_BLOCK.sub('', m['content']).strip()})
        else:
            files = store.message_attachments(m['id'])
            content = uploads.with_files(m['content'], files)
            if m['id'] == message_id and sources:
                blocks = '\n'.join(f'<source id="{s["n"]}" title="{s["document"]}">\n{s["text"]}\n</source>' for s in sources)
                content = SOURCES_PROMPT.format(sources=blocks, question=content)
            history.append({'role': 'user', 'content': content, 'images': uploads.images(files)})
    return history


def find_sources(chat, message_id):
    """Passages from the chat's knowledge collections for the message being answered."""
    knowledge_ids = store.owned_knowledge(chat['knowledge_ids'], g.user['id'])
    message = store.get_message(chat['id'], message_id)
    if not knowledge_ids or not message or not message['content'].strip():
        return []
    passages = knowledge.retrieve(knowledge_ids, message['content'])
    return [{'n': n, 'document': p['document'], 'document_id': p['document_id'], 'knowledge': p['knowledge'],
             'knowledge_id': p['knowledge_id'], 'text': p['text']} for n, p in enumerate(passages, 1)]


def write_title(chat_id, user_id, backend, model):
    """Name a chat after its first exchange, in the background (unless it was renamed meanwhile)."""
    exchange = store.first_exchange(chat_id)
    if len(exchange) < 2:
        return
    first = exchange[0]['content'][:2000]
    reply = THINK_BLOCK.sub('', exchange[1]['content']).strip()[:2000]
    prompt = ('Write a title of 3 to 6 words for this conversation. Answer with the title only: '
              'no quotes, no full stop.\n\nUser: ' + first + '\n\nAssistant: ' + reply)
    try:
        title = llm.complete(backend, model, [{'role': 'user', 'content': prompt}], {'max_tokens': 40, 'temperature': 0.3})
    except Exception as error:
        print(f'Could not write a title for chat {chat_id}: {error}', flush=True)
        return
    title = THINK_BLOCK.sub('', title).strip()
    # The first line, without Markdown marks or quotes around it.
    title = re.sub(r'[*_`#]', '', title.splitlines()[0]).strip(' "\'.:') if title else ''
    chat = store.get_chat(chat_id, user_id)
    if title and chat and chat['title'] == ' '.join(first.split())[:60]:
        store.update_chat(chat_id, user_id, {'title': title[:80]})


def fetch_links(chat_id, message_id, user):
    """Open each #https://… link in the message and keep the page as an attachment of it. Yields statuses."""
    message = store.get_message(chat_id, message_id)
    already = {a['text'].split('\n', 1)[0] for a in store.message_attachments(message_id) if a['text']}
    for url in web.links_in(message['content'] if message else ''):
        if f'Source: {url}' in already:
            continue
        yield f'Reading {url}...'
        try:
            title, text = web.fetch_page(url, allow_private=user['role'] == 'admin')
        except Exception as error:
            yield f"Couldn't open {url}: {error}"
            continue
        text = f'Source: {url}\n\n{text.strip()}'
        truncated = len(text) > uploads.MAX_CHARS
        store.add_attachment(user['id'], uuid.uuid4().hex, 'text', (title or url)[:200], 'text/html', len(text.encode()), '',
                             text[:uploads.MAX_CHARS], truncated, message_id=message_id)


SEARCH_QUERY_PROMPT = """Write one web search query (at most 10 words) that would find what the user's last
message needs. Answer with the query only.

{conversation}"""


def search_query(chat, message_id, backend, model):
    """Let the model turn the conversation into a search query; the message itself if that fails."""
    path = store.path_to(chat['id'], message_id)
    question = path[-1]['content'] if path else ''
    recent = '\n\n'.join(f"{'User' if m['role'] == 'user' else 'Assistant'}: {THINK_BLOCK.sub('', m['content']).strip()[:600]}"
                          for m in path[-5:])
    try:
        query = llm.complete(backend, model, [{'role': 'user', 'content': SEARCH_QUERY_PROMPT.format(conversation=recent)}],
                             {'max_tokens': 40, 'temperature': 0.2})
        query = THINK_BLOCK.sub('', query).strip()
        query = re.sub(r'[*_`#]', '', query.splitlines()[0]).strip(' "\'') if query else ''
    except Exception:
        query = ''
    return (query or question)[:200], question


def reply_stream(chat, user_message_id, backend, model, use_web=False, links=False):
    """Stream one answer to user_message_id as NDJSON, saving it (even a stopped, partial one).
    Before answering: open #links (for a new message), search the web (when asked), search knowledge."""
    options = chat['options']
    user = dict(g.user)
    auto_title = settings.prefs(g.user)['auto_title']
    user_id = user['id']

    def events():
        yield json.dumps({'reply_to': user_message_id}) + '\n'
        text, think, stats, saved, sources = [], [], None, None, []
        if links:
            for status in fetch_links(chat['id'], user_message_id, user):
                yield json.dumps({'status': status}) + '\n'
        if chat['knowledge_ids']:
            yield json.dumps({'status': 'Searching your knowledge...'}) + '\n'
            try:
                sources = find_sources(chat, user_message_id)
            except Exception as error:
                yield json.dumps({'status': f"Couldn't search your knowledge: {error}"}) + '\n'
        if use_web:
            yield json.dumps({'status': 'Working out what to search for...'}) + '\n'
            query, question = search_query(chat, user_message_id, backend, model)
            statuses = []
            try:
                found = web.research(question, query, user['role'] == 'admin', statuses.append)
                for status in statuses:
                    yield json.dumps({'status': status}) + '\n'
                sources += [{**s, 'n': len(sources) + i} for i, s in enumerate(found, 1)]
                if not found:
                    yield json.dumps({'status': 'The web search found nothing'}) + '\n'
            except Exception as error:
                yield json.dumps({'status': f"Couldn't search the web: {error}"}) + '\n'
        if sources:
            yield json.dumps({'sources': sources}) + '\n'
        yield json.dumps({'status': 'Writing...'}) + '\n'
        history = history_for(chat, user_message_id, sources)
        context = {'user': user, 'chat': chat, 'sources': sources, 'message_id': None, 'jobs': []}
        steps = []
        try:
            # Each round: the model answers, or asks for tools; their results go back for another round.
            for round_number in range(MAX_TOOL_ROUNDS + 1):
                offered = tools.openai_tools() if chat['tools'] and round_number < MAX_TOOL_ROUNDS else None
                calls, round_text = [], []
                for kind, piece in llm.stream_chat(backend, model, history, options, offered):
                    if kind == 'tool_calls':
                        calls = piece
                        continue
                    if kind == 'stats':
                        stats = piece
                    else:
                        (think if kind == 'think' else text).append(piece)
                        if kind == 'text':
                            round_text.append(piece)
                    yield json.dumps({kind: piece}) + '\n'
                if not calls:
                    break
                history.append({'role': 'assistant', 'content': ''.join(round_text), 'tool_calls': calls})
                before = len(sources)
                for call in calls:
                    yield json.dumps({'tool': {'name': call['name'], 'arguments': call['arguments']}}) + '\n'
                    result, summary = tools.run(call['name'], call['arguments'], context)
                    steps.append({'name': call['name'], 'arguments': call['arguments'], 'summary': summary,
                                  'result': result[:4000]})
                    yield json.dumps({'tool_done': steps[-1]}) + '\n'
                    history.append({'role': 'tool', 'content': result, 'tool_call_id': call['id'], 'name': call['name']})
                if len(sources) > before:
                    yield json.dumps({'sources': sources}) + '\n'
                if text:
                    text.append('\n\n')
            saved = store.add_message(chat['id'], user_message_id, 'assistant', ''.join(text).strip(), ''.join(think), model,
                                      stats, sources=sources, tools=steps)
            for job_id in context['jobs']:
                # An image the model asked for is attached to this reply when it's done.
                store.set_job_message(job_id, saved)
                jobs.post_to_chat(job_id)
            yield json.dumps({'done': True, 'message_id': saved}) + '\n'
            if auto_title and store.message_count(chat['id']) == 2:
                threading.Thread(target=write_title, args=(chat['id'], user_id, backend, model), daemon=True).start()
        except Exception as error:
            yield json.dumps({'error': llm.describe(backend, error)}) + '\n'
        finally:
            # Runs on Stop too (the client disconnects), so a partial reply is kept.
            if saved is None and (text or think or steps):
                store.add_message(chat['id'], user_message_id, 'assistant', ''.join(text).strip(), ''.join(think), model,
                                  stats, sources=sources, tools=steps)

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

    use_web = data.get('web') is True
    if reply_to is not None:
        target = store.get_message(chat_id, reply_to)
        if target is None or target['role'] != 'user':
            return jsonify(error='reply_to must be one of your messages in this chat'), 400
        return reply_stream(chat, reply_to, fields['backend'], fields['model'], use_web)

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
    return reply_stream(store.get_chat(chat_id, user_id), message_id, fields['backend'], fields['model'], use_web, links=True)


COMMANDS = {'image': ('image', 'anime'), 'photo': ('image', 'realistic'), 'video': ('video', None)}


@bp.post('/api/chats/<int:chat_id>/generate')
def generate(chat_id):
    """/image, /photo or /video in the composer: the message, and a reply that the finished file is
    attached to when the GPU queue gets to it."""
    user_id = g.user['id']
    chat = store.get_chat(chat_id, user_id)
    if chat is None:
        return jsonify(error='No such chat'), 404
    data = request.get_json(silent=True) or {}
    command, prompt = data.get('command'), data.get('prompt')
    if command not in COMMANDS or not isinstance(prompt, str) or not prompt.strip():
        return jsonify(error='Write what to make after /image, /photo or /video'), 400
    kind, model = COMMANDS[command]
    server = settings.get()
    try:
        if kind == 'image':
            params = {'prompt': prompt.strip(), 'model': model}
            if model == 'anime':
                params['negative_prompt'] = server['image_negative_prompt']
            params = jobs.validate_image(params)
        else:
            params = jobs.validate_video({'prompt': prompt.strip(), 'negative_prompt': server['video_negative_prompt']})
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if chat['title'] == 'New chat' and not chat['messages']:
        store.update_chat(chat_id, user_id, {'title': ' '.join(prompt.split())[:60]})
    parent = chat['messages'][-1]['id'] if chat['messages'] else None
    asked = store.add_message(chat_id, parent, 'user', f'/{command} {prompt.strip()}')
    label = {'image': 'WAI-Illustrious', 'photo': 'Z-Image-Turbo', 'video': 'Wan2.2'}[command]
    reply = store.add_message(chat_id, asked, 'assistant', '', model=label)
    try:
        job_id = jobs.enqueue(user_id, kind, params, chat_id=chat_id, message_id=reply)
    except FileNotFoundError as error:
        store.edit_message(chat_id, reply, f"The {kind} couldn't be made: {error}")
        return jsonify(store.get_chat(chat_id, user_id)), 201
    store.update_message_tools(reply, [{'name': f'generate_{kind}', 'arguments': {'prompt': prompt.strip()},
                                        'summary': f'queued {kind} job {job_id}', 'job_id': job_id}])
    return jsonify(store.get_chat(chat_id, user_id)), 201


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
    if not found['path']:
        # A page read from a #link: there's no file, only the text that was taken from it.
        with store.connect() as db:
            text = db.execute('SELECT text FROM attachments WHERE id = ?', (attachment_id,)).fetchone()['text']
        return Response(text, mimetype='text/plain; charset=utf-8')
    folder, name = found['path'].split('/', 1)
    return send_from_directory(store.UPLOADS / folder, name, mimetype=found['mime'],
                               as_attachment=found['kind'] != 'image', download_name=found['name'])


# Folders

def folder_name(data):
    name = (data or {}).get('name')
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError('A folder name is 1 to 80 characters')
    return name.strip()


@bp.post('/api/folders')
def new_folder():
    try:
        name = folder_name(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(id=store.create_folder(g.user['id'], name)), 201


@bp.patch('/api/folders/<int:folder_id>')
def edit_folder(folder_id):
    if not store.get_folder(folder_id, g.user['id']):
        return jsonify(error='No such folder'), 404
    data = request.get_json(silent=True) or {}
    fields = {}
    try:
        if 'name' in data:
            fields['name'] = folder_name(data)
        if 'collapsed' in data:
            fields['collapsed'] = int(bool(data['collapsed']))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    store.update_folder(folder_id, g.user['id'], fields)
    return jsonify(ok=True)


@bp.delete('/api/folders/<int:folder_id>')
def remove_folder(folder_id):
    return ('', 204) if store.delete_folder(folder_id, g.user['id']) else (jsonify(error='No such folder'), 404)


# Search

@bp.get('/api/search')
def search():
    query = request.args.get('q', '')[:200]
    return jsonify(results=store.search(g.user['id'], query))


# Export and import

def markdown(chat):
    lines = [f"# {chat['title']}", '']
    if chat['system_prompt'].strip():
        lines += ['> System prompt: ' + chat['system_prompt'].strip().replace('\n', '\n> '), '']
    for m in chat['messages']:
        who = 'You' if m['role'] == 'user' else f"Assistant ({m['model']})" if m['model'] else 'Assistant'
        lines += [f'## {who}', '']
        for file in m['attachments']:
            lines.append(f"*Attached: {file['name']}*")
        if m['attachments']:
            lines.append('')
        content = THINK_BLOCK.sub('', m['content']).strip() if m['role'] == 'assistant' else m['content']
        lines += [content, '']
    return '\n'.join(lines)


def safe_filename(title):
    return (re.sub(r'[^\w\- ]+', '', title).strip() or 'chat')[:60]


@bp.get('/api/chats/<int:chat_id>/export')
def export_chat(chat_id):
    kind = request.args.get('format', 'json')
    chat = store.get_chat(chat_id, g.user['id'])
    if chat is None:
        return jsonify(error='No such chat'), 404
    name = safe_filename(chat['title'])
    if kind == 'md':
        return Response(markdown(chat), mimetype='text/markdown',
                        headers={'Content-Disposition': f'attachment; filename="{name}.md"'})
    data = store.export_chat(chat_id, g.user['id'])
    for message in data['messages']:
        for file in message['attachments']:
            file.pop('path')
    return Response(json.dumps(data, indent=2), mimetype='application/json',
                    headers={'Content-Disposition': f'attachment; filename="{name}.json"'})


@bp.get('/api/chats/export')
def export_all():
    """Every chat (archived ones too) and their files, as one .zip that /api/chats/import reads back."""
    user_id = g.user['id']
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        chats = []
        for listed in store.list_chats(user_id) + store.list_chats(user_id, archived=True):
            data = store.export_chat(listed['id'], user_id)
            for message in data['messages']:
                for file in message['attachments']:
                    path = store.UPLOADS / file.pop('path')
                    if path.is_file():
                        archive.write(path, f"files/{file['id']}/{file['name']}")
            chats.append(data)
        archive.writestr('chats.json', json.dumps({'format': 'inphai-chats', 'version': 1, 'chats': chats}, indent=2))
    buffer.seek(0)
    return send_file(buffer, mimetype='application/zip', as_attachment=True,
                     download_name=f"inphai-chats-{time.strftime('%Y-%m-%d')}.zip")


def valid_chat(data):
    return (isinstance(data, dict) and data.get('format') == 'inphai-chat' and isinstance(data.get('title'), str)
            and isinstance(data.get('messages'), list)
            and all(isinstance(m, dict) and m.get('role') in ('user', 'assistant') and isinstance(m.get('content'), str)
                    and isinstance(m.get('id'), int) for m in data['messages']))


@bp.post('/api/chats/import')
def import_chats():
    """One chat's .json, or the .zip of all chats from export."""
    upload = request.files.get('file')
    if upload is None:
        return jsonify(error='Send the file as multipart form data, field "file"'), 400
    raw = upload.read()
    user_id = g.user['id']
    imported = []
    try:
        if raw[:2] == b'PK':
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                bundle = json.loads(archive.read('chats.json'))
                if bundle.get('format') != 'inphai-chats' or not isinstance(bundle.get('chats'), list):
                    raise ValueError('This .zip is not an inphAI chat export')
                for data in bundle['chats']:
                    if not valid_chat(data):
                        raise ValueError('A chat in this export is damaged')
                    saved = {}
                    for message in data['messages']:
                        for file in message.get('attachments', []):
                            name = f"files/{file.get('id')}/{file.get('name')}"
                            if name in archive.namelist():
                                new_id = uuid.uuid4().hex
                                safe = uploads.SAFE_NAME.sub('_', Path(file['name']).name) or 'file'
                                (store.UPLOADS / new_id).mkdir(parents=True)
                                (store.UPLOADS / new_id / safe).write_bytes(archive.read(name))
                                saved[file['id']] = f'{new_id}/{safe}'
                    imported.append(store.import_chat(user_id, data, saved))
        else:
            try:
                data = json.loads(raw)
            except ValueError:
                raise ValueError('This is not an inphAI chat export (a .json of one chat, or the .zip of all)')
            if not valid_chat(data):
                raise ValueError('This is not an inphAI chat export')
            imported.append(store.import_chat(user_id, data))
    except (ValueError, KeyError, zipfile.BadZipFile) as error:
        return jsonify(error=str(error) if isinstance(error, ValueError) else 'Could not read that file'), 400
    return jsonify(imported=len(imported), chat_ids=imported), 201
