"""Image and video jobs, and the galleries."""
from flask import Blueprint, jsonify, request, send_from_directory
import jobs
import store

bp = Blueprint('media', __name__)


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


@bp.post('/api/images')
def generate_image():
    return start_job('image', jobs.validate_image)


@bp.post('/api/videos')
def generate_video():
    return start_job('video', jobs.validate_video)


@bp.get('/api/job')
def job():
    return jsonify(jobs.current()), 200, {'Cache-Control': 'no-store'}


@bp.post('/api/job/cancel')
def cancel_job():
    return jsonify(cancelled=jobs.cancel())


@bp.get('/api/media')
def media():
    kind = request.args.get('kind')
    if kind not in ('image', 'video'):
        return jsonify(error='kind must be image or video'), 400
    return jsonify(media=store.list_media(kind))


@bp.delete('/api/media/<int:media_id>')
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


@bp.get('/media/<path:filename>')
def media_file(filename):
    return send_from_directory(jobs.OUTPUTS, filename, conditional=True)
