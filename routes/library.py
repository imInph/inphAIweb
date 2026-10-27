"""Each user's saved prompts, personas and memories."""
import re
import sqlite3
from flask import Blueprint, g, jsonify, request
import llm
import settings
import store

bp = Blueprint('library', __name__)
COMMAND = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
COLORS = ('green', 'blue', 'purple', 'pink', 'orange', 'teal', 'graphite')


def text(data, key, limit, required=True):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{key} must be {"1" if required else "0"} to {limit} characters')
    return value.strip() if key != 'content' and key != 'system_prompt' else value


def validate_prompt(data, partial):
    fields = {}
    if 'command' in data or not partial:
        command = data.get('command')
        command = command.strip().lstrip('/').lower() if isinstance(command, str) else ''
        if not COMMAND.match(command):
            raise ValueError('A command is 1 to 32 lowercase letters, digits, dashes or underscores, like "summary"')
        fields['command'] = command
    if 'title' in data or not partial:
        fields['title'] = text(data, 'title', 100)
    if 'content' in data or not partial:
        fields['content'] = text(data, 'content', 20000)
    return fields


def validate_persona(data, partial):
    fields = {}
    if 'name' in data or not partial:
        fields['name'] = text(data, 'name', 60)
    if 'color' in data:
        if data['color'] not in COLORS:
            raise ValueError('color must be one of ' + ', '.join(COLORS))
        fields['color'] = data['color']
    if 'backend' in data or 'model' in data or not partial:
        if data.get('backend') not in llm.BACKENDS:
            raise ValueError('Pick the model this persona uses')
        fields['backend'] = data['backend']
        fields['model'] = text(data, 'model', 300)
    if 'system_prompt' in data:
        fields['system_prompt'] = text(data, 'system_prompt', 20000, required=False)
    if 'options' in data:
        fields['options'] = settings.validate_options(data['options'])
    if 'knowledge_ids' in data:
        ids = data['knowledge_ids']
        if not isinstance(ids, list) or not all(type(i) is int for i in ids):
            raise ValueError('knowledge_ids must be a list of collection ids')
        fields['knowledge_ids'] = store.owned_knowledge(ids, g.user['id'])
    return fields


def validate_memory(data, partial):
    return {'content': text(data, 'content', 1000)}


KINDS = {'prompts': validate_prompt, 'personas': validate_persona, 'memories': validate_memory}


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object')
    return data


@bp.get('/api/<any(prompts, personas, memories):kind>')
def list_items(kind):
    return jsonify(items=store.list_items(kind, g.user['id']))


@bp.post('/api/<any(prompts, personas, memories):kind>')
def add_item(kind):
    try:
        fields = KINDS[kind](body(), partial=False)
        item_id = store.add_item(kind, g.user['id'], fields)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except sqlite3.IntegrityError:
        return jsonify(error='You already have a prompt with that command'), 409
    return jsonify(store.get_item(kind, item_id, g.user['id'])), 201


@bp.patch('/api/<any(prompts, personas, memories):kind>/<int:item_id>')
def edit_item(kind, item_id):
    if not store.get_item(kind, item_id, g.user['id']):
        return jsonify(error='Not found'), 404
    try:
        store.update_item(kind, item_id, g.user['id'], KINDS[kind](body(), partial=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except sqlite3.IntegrityError:
        return jsonify(error='You already have a prompt with that command'), 409
    return jsonify(store.get_item(kind, item_id, g.user['id']))


@bp.delete('/api/<any(prompts, personas, memories):kind>/<int:item_id>')
def delete_item(kind, item_id):
    return ('', 204) if store.delete_item(kind, item_id, g.user['id']) else (jsonify(error='Not found'), 404)
