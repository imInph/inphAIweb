"""Speech-to-text through the faster-whisper worker (generators/voice/transcribe.py), kept running
so its model loads only once. One recording at a time."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import settings

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / 'generators' / 'voice' / 'transcribe.py'
PYTHON = os.environ.get('VOICE_PYTHON', str(ROOT / '.venvs' / 'voice' / 'bin' / 'python'))
MODELS = ('tiny', 'base', 'small', 'medium', 'large-v3')
LOAD_TIMEOUT = 15 * 60          # the first start downloads the model
TRANSCRIBE_TIMEOUT = 5 * 60
lock = threading.Lock()
worker = {'process': None, 'model': None, 'lines': None, 'next_id': 0}


def available():
    return Path(PYTHON).is_file()


def read_lines(process, lines):
    for line in process.stdout:
        lines.put(line)
    lines.put(None)   # the process ended


def stop():
    process = worker['process']
    if process and process.poll() is None:
        process.terminate()
    worker['process'] = None


def wait_for(lines, timeout, request_id=None):
    while True:
        try:
            line = lines.get(timeout=timeout)
        except queue.Empty:
            stop()
            raise RuntimeError('Speech-to-text took too long')
        if line is None:
            worker['process'] = None
            raise RuntimeError('The speech-to-text worker stopped; see the server log')
        try:
            message = json.loads(line)
        except ValueError:
            continue   # a library's own output on stdout
        if request_id is None and message.get('ready'):
            return message
        if request_id is not None and message.get('id') == request_id:
            return message


def ensure_worker():
    model = settings.get('whisper_model')
    process = worker['process']
    if process and process.poll() is None and worker['model'] == model:
        return
    stop()
    if not available():
        raise FileNotFoundError(f'No voice environment at {PYTHON}; run bash scripts/setup.sh voice')
    process = subprocess.Popen([PYTHON, '-u', str(SCRIPT), f'--model={model}'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=None, text=True, encoding='utf-8', cwd=ROOT)
    lines = queue.Queue()
    threading.Thread(target=read_lines, args=(process, lines), daemon=True).start()
    worker.update(process=process, model=model, lines=lines)
    wait_for(lines, LOAD_TIMEOUT)


def transcribe(path, language=None):
    with lock:
        ensure_worker()
        worker['next_id'] += 1
        request_id = worker['next_id']
        worker['process'].stdin.write(json.dumps({'id': request_id, 'path': str(path), 'language': language}) + '\n')
        worker['process'].stdin.flush()
        answer = wait_for(worker['lines'], TRANSCRIBE_TIMEOUT, request_id)
    if answer.get('error'):
        raise RuntimeError(answer['error'])
    return answer['text'], answer.get('language')
