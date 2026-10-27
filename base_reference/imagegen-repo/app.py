"""Small image API: python app.py (port 5001)."""
from collections import deque
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException
from generate_image import validate, DEFAULTS

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024
ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / 'outputs'
lock = threading.Lock()
lines = deque(maxlen=100)
status = {'state': 'idle', 'stage': 'idle', 'image_url': None, 'error': None}


@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify(error=error.description), error.code


@app.get('/api')
def home():
    return jsonify(message='Image generation is ready. POST JSON to /generate.',
                   models={'anime': 'WAI-Illustrious v17', 'realistic': 'Z-Image-Turbo'},
                   defaults=DEFAULTS, status_url='/status', images='/outputs/<filename>')


@app.post('/generate')
def generate():
    try:
        settings = validate(request.get_json())
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if not lock.acquire(blocking=False):
        return jsonify(error='An image is already generating'), 409
    process = None
    try:
        lines.clear()
        status.clear()
        status.update(state='running', stage='starting', completed_steps=0,
                      total_steps=settings['steps'], image_url=None, error=None)
        OUTPUTS.mkdir(exist_ok=True)
        path = OUTPUTS / f"{settings['model']}-{time.time_ns()}.png"
        command = [sys.executable, '-u', str(ROOT / 'generate_image.py'), f'--output={path}']
        command.extend(f"--{key.replace('_', '-')}={value}" for key,value in settings.items())
        with (path.with_suffix('.log')).open('w', buffering=1) as log:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace')
            with process.stdout:
                for line in process.stdout:
                    log.write(line)
                    lines.append(line.rstrip())
                    if line.startswith('IMAGE_PROGRESS '):
                        event = json.loads(line[len('IMAGE_PROGRESS '):])
                        status.update({key:event[key] for key in
                                       ('stage', 'completed_steps', 'total_steps') if key in event})
            code = process.wait()
        if code:
            raise RuntimeError(f'Generator exited with code {code}; see output or {path.name[:-4]}.log')
        if not path.is_file():
            raise RuntimeError('No image was produced')
        status.update(state='done', stage='complete', image_url=f'/outputs/{path.name}',
                      file_path=str(path), metadata_url=f'/outputs/{path.stem}.json')
        return jsonify(**status, output=list(lines))
    except Exception as error:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        status.update(state='error', stage='error', error=str(error))
        return jsonify(**status, output=list(lines)), 500
    finally:
        lock.release()


@app.get('/status')
def get_status():
    return jsonify(**status, output=list(lines)), 200, {'Cache-Control': 'no-store'}


@app.get('/outputs/<path:filename>')
def image(filename):
    return send_from_directory(OUTPUTS, filename, conditional=True)


@app.get('/')
def index():
    return app.send_static_file('index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True, use_reloader=False)
