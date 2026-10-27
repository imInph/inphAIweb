"""Knowledge collections: their documents (uploaded, indexed in the background) and a test search."""
from flask import Blueprint, g, jsonify, request, send_from_directory
import knowledge
import store

bp = Blueprint('collections', __name__)


def name_fields(data, partial):
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object')
    fields = {}
    if 'name' in data or not partial:
        name = data.get('name')
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError('A collection name is 1 to 80 characters')
        fields['name'] = name.strip()
    if 'description' in data:
        if not isinstance(data['description'], str) or len(data['description']) > 1000:
            raise ValueError('The description is at most 1000 characters')
        fields['description'] = data['description'].strip()
    return fields


@bp.get('/api/knowledge')
def collections():
    return jsonify(items=store.list_knowledge(g.user['id'])), 200, {'Cache-Control': 'no-store'}


@bp.post('/api/knowledge')
def new_collection():
    try:
        fields = name_fields(request.get_json(silent=True), partial=False)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    knowledge_id = store.create_knowledge(g.user['id'], fields['name'], fields.get('description', ''))
    return jsonify(store.get_knowledge(knowledge_id, g.user['id'])), 201


@bp.get('/api/knowledge/<int:knowledge_id>')
def collection(knowledge_id):
    found = store.get_knowledge(knowledge_id, g.user['id'])
    return (jsonify(found), 200, {'Cache-Control': 'no-store'}) if found else (jsonify(error='No such collection'), 404)


@bp.patch('/api/knowledge/<int:knowledge_id>')
def edit_collection(knowledge_id):
    if not store.get_knowledge(knowledge_id, g.user['id']):
        return jsonify(error='No such collection'), 404
    try:
        store.update_knowledge(knowledge_id, g.user['id'], name_fields(request.get_json(silent=True), partial=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(store.get_knowledge(knowledge_id, g.user['id']))


@bp.delete('/api/knowledge/<int:knowledge_id>')
def remove_collection(knowledge_id):
    return ('', 204) if store.delete_knowledge(knowledge_id, g.user['id']) else (jsonify(error='No such collection'), 404)


@bp.post('/api/knowledge/<int:knowledge_id>/documents')
def add_documents(knowledge_id):
    if not store.get_knowledge(knowledge_id, g.user['id']):
        return jsonify(error='No such collection'), 404
    files = request.files.getlist('file')
    if not files:
        return jsonify(error='Send the files as multipart form data, field "file"'), 400
    results = []
    for file in files[:50]:
        try:
            results.append({'name': file.filename, 'id': knowledge.add_document(knowledge_id, file)})
        except ValueError as error:
            results.append({'name': file.filename, 'error': str(error)})
    return jsonify(results=results, collection=store.get_knowledge(knowledge_id, g.user['id'])), 201


def owned_document(knowledge_id, document_id):
    document = store.get_document(document_id)
    if document is None or document['user_id'] != g.user['id'] or document['knowledge_id'] != knowledge_id:
        return None
    return document


@bp.delete('/api/knowledge/<int:knowledge_id>/documents/<int:document_id>')
def remove_document(knowledge_id, document_id):
    if not owned_document(knowledge_id, document_id):
        return jsonify(error='No such document'), 404
    store.delete_document(document_id, g.user['id'])
    knowledge.vector_cache.pop(knowledge_id, None)
    return '', 204


@bp.post('/api/knowledge/<int:knowledge_id>/documents/<int:document_id>/reindex')
def reindex_document(knowledge_id, document_id):
    if not owned_document(knowledge_id, document_id):
        return jsonify(error='No such document'), 404
    store.set_document_status(document_id, 'queued')
    knowledge.wake.set()
    return jsonify(ok=True)


@bp.get('/api/knowledge/<int:knowledge_id>/documents/<int:document_id>/file')
def document_file(knowledge_id, document_id):
    document = owned_document(knowledge_id, document_id)
    if not document:
        return jsonify(error='No such document'), 404
    folder, name = document['path'].split('/', 1)
    return send_from_directory(store.KNOWLEDGE / folder, name, mimetype=document['mime'], download_name=document['name'])


@bp.post('/api/knowledge/search')
def search():
    """Try a question against collections and see which passages it would get."""
    data = request.get_json(silent=True) or {}
    ids, query = data.get('knowledge_ids'), data.get('query')
    if not isinstance(ids, list) or not all(type(i) is int for i in ids) or not isinstance(query, str) or not query.strip():
        return jsonify(error='Send knowledge_ids and a query'), 400
    try:
        passages = knowledge.retrieve(store.owned_knowledge(ids, g.user['id']), query)
    except Exception as error:
        return jsonify(error=str(error)), 502
    return jsonify(results=passages)
