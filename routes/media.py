"""Image and video jobs (the GPU queue), and each user's galleries."""
import shutil
import time
import uuid
from flask import Blueprint, g, jsonify, request, send_from_directory
import jobs
import store

bp = Blueprint('media', __name__)


def queue(kind, validate):
    try:
        params = validate(request.get_json(silent=True))
        job_id = jobs.enqueue(g.user['id'], kind, params)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except FileNotFoundError as error:
        return jsonify(error=str(error)), 503
    return jsonify(jobs.describe(store.get_job(job_id))), 202


@bp.post('/api/images')
def generate_image():
    return queue('image', jobs.validate_image)


@bp.post('/api/videos')
def generate_video():
    return queue('video', jobs.validate_video)


@bp.get('/api/jobs')
def my_jobs():
    # Finished jobs stay listed for a minute, so the page sees how they ended.
    since = request.args.get('since', type=float) or time.time() - 60
    return jsonify(jobs.jobs_for(g.user['id'], since)), 200, {'Cache-Control': 'no-store'}


@bp.post('/api/jobs/<int:job_id>/cancel')
def cancel_job(job_id):
    if not jobs.cancel(job_id, g.user):
        return jsonify(error='That job is not waiting or running'), 404
    return jsonify(cancelled=True)


@bp.get('/api/media')
def media():
    kind = request.args.get('kind')
    if kind not in ('image', 'video'):
        return jsonify(error='kind must be image or video'), 400
    return jsonify(media=store.list_media(kind, g.user['id']))


@bp.delete('/api/media/<int:media_id>')
def remove_media(media_id):
    item = store.get_media(media_id, g.user['id'])
    if item is None:
        return jsonify(error='No such file'), 404
    jobs.remove_media_files(item['file'])
    store.delete_media(media_id, g.user['id'])
    return '', 204


@bp.post('/api/media/<int:media_id>/attach')
def attach_media(media_id):
    """Make a gallery image into an attachment for a new message ("Ask about this")."""
    item = store.get_media(media_id, g.user['id'])
    if item is None or item['kind'] != 'image':
        return jsonify(error='No such image'), 404
    source = jobs.OUTPUTS / item['file']
    attachment_id = uuid.uuid4().hex
    (store.UPLOADS / attachment_id).mkdir(parents=True)
    shutil.copyfile(source, store.UPLOADS / attachment_id / source.name)
    return jsonify(store.add_attachment(g.user['id'], attachment_id, 'image', source.name, 'image/png',
                                        source.stat().st_size, f'{attachment_id}/{source.name}')), 201


@bp.get('/media/<path:filename>')
def media_file(filename):
    # Only the file's owner; a guessed path from someone else is just "not found".
    if store.media_owner(filename) != g.user['id']:
        return jsonify(error='No such file'), 404
    return send_from_directory(jobs.OUTPUTS, filename, conditional=True)
