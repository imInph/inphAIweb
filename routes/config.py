"""Site settings and the connection test."""
from flask import Blueprint, jsonify, request
import jobs
import llm
import settings
import store

bp = Blueprint('config', __name__)


def folder_size(path):
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file()) if path.exists() else 0


@bp.get('/api/settings')
def get_settings():
    return jsonify(settings=settings.get(), defaults=settings.DEFAULTS, counts=store.counts(),
                   disk={'database': folder_size(store.DATA) - folder_size(jobs.OUTPUTS) - folder_size(store.UPLOADS),
                         'files': folder_size(store.UPLOADS),
                         'images': folder_size(jobs.OUTPUTS / 'images'),
                         'videos': folder_size(jobs.OUTPUTS / 'videos')}), 200, {'Cache-Control': 'no-store'}


@bp.patch('/api/settings')
def save_settings():
    try:
        store.save_settings(settings.validate(request.get_json(silent=True)))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(settings=settings.get())


@bp.post('/api/settings/test')
def test_connection():
    data = request.get_json(silent=True) or {}
    if data.get('backend') not in llm.BACKENDS:
        return jsonify(error='backend must be ' + ' or '.join(llm.BACKENDS)), 400
    try:
        base = settings.validate({f"{data['backend']}_url": data.get('url')})[f"{data['backend']}_url"]
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(llm.test(data['backend'], base))
