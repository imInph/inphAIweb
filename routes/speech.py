"""Speech-to-text for the microphone button."""
import tempfile
from flask import Blueprint, jsonify, request
import store
import voice

bp = Blueprint('speech', __name__)


@bp.get('/api/transcribe')
def status():
    return jsonify(available=voice.available())


@bp.post('/api/transcribe')
def transcribe():
    audio = request.files.get('audio')
    if audio is None:
        return jsonify(error='Send the recording as multipart form data, field "audio"'), 400
    language = request.form.get('language') or None
    if language and (len(language) > 8 or not language.isalpha()):
        return jsonify(error='language is a code like en or tr'), 400
    folder = store.DATA / 'tmp'
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, suffix='.audio') as recording:
        audio.save(recording)
        recording.flush()
        try:
            text, detected = voice.transcribe(recording.name, language)
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 503
        except RuntimeError as error:
            return jsonify(error=str(error)), 502
    return jsonify(text=text, language=detected)
