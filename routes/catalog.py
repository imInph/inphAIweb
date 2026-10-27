"""The Models page: what Ollama has installed and loaded, pulling and deleting (admins), and LM Studio's list."""
import re
from flask import Blueprint, jsonify, request
import auth
import llm

bp = Blueprint('catalog', __name__)
MODEL_NAME = re.compile(r'^[\w.\-/:]{1,200}$')


def model_name():
    name = (request.get_json(silent=True) or {}).get('name')
    if not isinstance(name, str) or not MODEL_NAME.match(name.strip()):
        raise ValueError('Model names look like qwen3:8b or library/llama3.2')
    return name.strip()


@bp.get('/api/catalog/ollama')
def ollama():
    try:
        overview = llm.ollama_overview()
    except Exception as error:
        return jsonify(error=llm.describe('ollama', error), pulls=llm.pull_status()), 502
    return jsonify(**overview, pulls=llm.pull_status()), 200, {'Cache-Control': 'no-store'}


@bp.post('/api/catalog/ollama/show')
def ollama_show():
    try:
        return jsonify(llm.ollama_details(model_name()))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except Exception as error:
        return jsonify(error=llm.describe('ollama', error)), 502


@bp.post('/api/catalog/ollama/unload')
def ollama_unload():
    # Anyone may free the GPU; the model loads again on its next message.
    try:
        llm.ollama_unload(model_name())
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except Exception as error:
        return jsonify(error=llm.describe('ollama', error)), 502
    return jsonify(ok=True)


@bp.post('/api/catalog/ollama/pull')
@auth.admin_only
def ollama_pull():
    try:
        return jsonify(llm.start_pull(model_name())), 202
    except ValueError as error:
        return jsonify(error=str(error)), 400


@bp.post('/api/catalog/ollama/delete')
@auth.admin_only
def ollama_delete():
    try:
        llm.ollama_delete(model_name())
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except Exception as error:
        return jsonify(error=llm.describe('ollama', error)), 502
    return jsonify(ok=True)


@bp.get('/api/catalog/lmstudio')
def lmstudio():
    try:
        listing, detailed = llm.lmstudio_listing()
    except Exception as error:
        return jsonify(error=llm.describe('lmstudio', error)), 502
    models = [{'id': m.get('id'), 'type': m.get('type'), 'state': m.get('state'), 'publisher': m.get('publisher'),
               'arch': m.get('arch'), 'quantization': m.get('quantization'),
               'max_context': m.get('max_context_length')} for m in listing if m.get('id')]
    return jsonify(models=sorted(models, key=lambda m: m['id']), detailed=detailed), 200, {'Cache-Control': 'no-store'}
