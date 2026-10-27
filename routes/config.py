"""Server-wide settings (admin only), the connection test, and backup / restore."""
import os
import tempfile
import time
from flask import Blueprint, jsonify, request, send_file
import auth
import backup
import jobs
import llm
import settings
import store

bp = Blueprint('config', __name__)


def folder_size(path):
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file()) if path.exists() else 0


@bp.get('/api/settings')
@auth.admin_only
def get_settings():
    # The whole server's disk use; each user's own numbers come from /api/me/data.
    return jsonify(settings=settings.get(), defaults=settings.DEFAULTS, users=store.count_users(),
                   disk={'database': folder_size(store.DATA) - folder_size(jobs.OUTPUTS) - folder_size(store.UPLOADS),
                         'files': folder_size(store.UPLOADS),
                         'images': folder_size(jobs.OUTPUTS / 'images'),
                         'videos': folder_size(jobs.OUTPUTS / 'videos')}), 200, {'Cache-Control': 'no-store'}


@bp.patch('/api/settings')
@auth.admin_only
def save_settings():
    try:
        store.save_settings(settings.validate(request.get_json(silent=True)))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(settings=settings.get())


@bp.post('/api/settings/test')
@auth.admin_only
def test_connection():
    data = request.get_json(silent=True) or {}
    if data.get('backend') not in llm.BACKENDS:
        return jsonify(error='backend must be ' + ' or '.join(llm.BACKENDS)), 400
    try:
        base = settings.validate({f"{data['backend']}_url": data.get('url')})[f"{data['backend']}_url"]
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(llm.test(data['backend'], base))


@bp.get('/api/admin/backup')
@auth.admin_only
def download_backup():
    path = backup.make(request.args.get('media') == '1')
    # Open, then delete: the open file stays readable until it's sent, and nothing is left behind
    # (a streamed file's response is never closed through Flask, so call_on_close can't do it).
    archive = open(path, 'rb')
    os.unlink(path)
    return send_file(archive, mimetype='application/zip', as_attachment=True,
                     download_name=time.strftime('inphai-backup-%Y-%m-%d.zip'))


@bp.get('/api/admin/restore')
@auth.admin_only
def restore_status():
    return jsonify(pending=backup.pending())


@bp.post('/api/admin/restore')
@auth.admin_only
def restore():
    # A backup with its media can be far bigger than an ordinary upload.
    request.max_content_length = 200 * 1024 ** 3
    upload = request.files.get('file')
    if upload is None:
        return jsonify(error='Choose a backup .zip'), 400
    with tempfile.NamedTemporaryFile(dir=store.DATA, suffix='.zip', delete=False) as part:
        upload.save(part)
    try:
        found = backup.stage(part.name)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    finally:
        os.unlink(part.name)
    return jsonify(found)


@bp.delete('/api/admin/restore')
@auth.admin_only
def cancel_restore():
    backup.discard()
    return '', 204
