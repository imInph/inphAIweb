"""Image and video generation: one GPU job at a time, run as a subprocess in its own venv."""
from collections import deque
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import llm
import settings
import store

ROOT = Path(__file__).resolve().parent
OUTPUTS = store.DATA / 'outputs'
IMAGE_SCRIPT = ROOT / 'generators' / 'image' / 'generate_image.py'
VIDEO_SCRIPT = ROOT / 'generators' / 'video' / 'generate.py'
PYTHON = {
    'image': os.environ.get('IMAGE_PYTHON', str(ROOT / '.venvs' / 'image' / 'bin' / 'python')),
    'video': os.environ.get('VIDEO_PYTHON', str(ROOT / '.venvs' / 'video' / 'bin' / 'python')),
}

# generate_image.py only imports torch inside generate(), so its validation is safe to load here.
spec = importlib.util.spec_from_file_location('generate_image', IMAGE_SCRIPT)
image_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_script)
IMAGE_DEFAULTS = image_script.DEFAULTS
VIDEO_DEFAULTS = {'width': 640, 'height': 384, 'frames': 33, 'steps': 50, 'guidance': 5.0, 'seed': 42, 'fps': 24}

lock = threading.Lock()
lines = deque(maxlen=200)
status = {'state': 'idle'}
process = None
cancelled = False
STEP_BAR = re.compile(r'(\d+)/(\d+) \[')


class Busy(Exception):
    pass


def validate_image(data):
    return image_script.validate(data)


def validate_video(data):
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object')
    allowed = {'prompt', 'negative_prompt', *VIDEO_DEFAULTS}
    if set(data) - allowed:
        raise ValueError('Unknown setting: ' + ', '.join(sorted(set(data) - allowed)))
    settings = {**VIDEO_DEFAULTS, **data}
    for name in ('prompt', 'negative_prompt'):
        if name in settings and (not isinstance(settings[name], str) or len(settings[name]) > 4000
                                 or '\x00' in settings[name]):
            raise ValueError(f'{name} must be text, at most 4000 characters')
    if not settings.get('prompt', '').strip():
        raise ValueError('Enter a prompt')
    for name, low, high in [('width', 256, 1280), ('height', 256, 1280), ('frames', 5, 121),
                            ('steps', 1, 60), ('fps', 1, 60), ('seed', 0, 2**63-1)]:
        if type(settings[name]) is not int or not low <= settings[name] <= high:
            raise ValueError(f'{name} must be an integer from {low} to {high}')
    if settings['width'] % 32 or settings['height'] % 32:
        raise ValueError('width and height must be multiples of 32')
    if (settings['frames'] - 1) % 4:
        raise ValueError('frames must be 1 more than a multiple of 4 (17, 33, 49, ...)')
    value = settings['guidance']
    if type(value) not in (int, float) or not 0 <= value <= 12 or not math.isfinite(value):
        raise ValueError('guidance must be a number from 0 to 12')
    return settings


def current():
    return {**status, 'output': list(lines)[-20:]}


def start(kind, settings):
    global cancelled
    if not Path(PYTHON[kind]).is_file():
        raise FileNotFoundError(f'No {kind} environment at {PYTHON[kind]}; run bash scripts/setup.sh {kind}')
    if not lock.acquire(blocking=False):
        raise Busy(f"A{'n' if status.get('kind') == 'image' else ''} {status.get('kind')} is already generating")
    cancelled = False
    lines.clear()
    status.clear()
    status.update(state='running', kind=kind, stage='starting', prompt=settings['prompt'],
                  completed_steps=0, total_steps=settings['steps'], started_at=time.time(), error=None)
    threading.Thread(target=run, args=(kind, settings), daemon=True).start()


def cancel():
    global cancelled
    if status.get('state') == 'running' and process is not None:
        cancelled = True
        process.terminate()
        return True
    return False


def command_for(kind, settings):
    if kind == 'image':
        folder = OUTPUTS / 'images'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{settings['model']}-{time.time_ns()}.png"
        command = [PYTHON[kind], '-u', str(IMAGE_SCRIPT), f'--output={path}']
        command += [f"--{key.replace('_', '-')}={value}" for key, value in settings.items()]
        return command, path.with_suffix('.log'), lambda: path
    (OUTPUTS / 'videos').mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='run-', dir=OUTPUTS / 'videos'))
    command = [PYTHON[kind], '-u', str(VIDEO_SCRIPT), f'--output-dir={folder}']
    command += [f"--{key.replace('_', '-')}={value}" for key, value in settings.items()]
    return command, folder / 'output.log', lambda: next(folder.glob('*.mp4'), None)


def read_progress(kind, line):
    if kind == 'image':
        if line.startswith('IMAGE_PROGRESS '):
            event = json.loads(line[len('IMAGE_PROGRESS '):])
            status.update({key: event[key] for key in ('stage', 'completed_steps', 'total_steps') if key in event})
        return
    # generate.py prints plain lines; diffusers' progress bar gives the steps.
    for text, stage in (('Loading model', 'loading_model'), ('generating latents', 'generating'),
                        ('Decoding on CPU', 'decoding'), ('Saved video', 'saving')):
        if text in line:
            status['stage'] = stage
    bar = STEP_BAR.search(line)
    if bar and status['stage'] == 'generating' and int(bar.group(2)) == status['total_steps']:
        status['completed_steps'] = int(bar.group(1))


def run(kind, settings):
    global process
    try:
        freed = llm.unload_ollama() if settings.get('unload_ollama') else []
        if freed:
            lines.append('Unloaded from Ollama to free the GPU: ' + ', '.join(freed))
        command, log_path, find_output = command_for(kind, settings)
        start_time = time.monotonic()
        with log_path.open('w', buffering=1) as log:
            # Text mode splits on \r too, so each progress bar update arrives as a line.
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                                       cwd=ROOT, env={**os.environ, 'PYTHONUNBUFFERED': '1'})
            with process.stdout:
                for line in process.stdout:
                    line = line.rstrip()
                    if not line:
                        continue
                    log.write(line + '\n')
                    lines.append(line)
                    read_progress(kind, line)
            code = process.wait()
        if cancelled:
            status.update(state='cancelled', stage='cancelled')
            return
        if code:
            raise RuntimeError(f'The {kind} generator exited with code {code}; see {log_path.relative_to(ROOT)}')
        path = find_output()
        if path is None or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f'The {kind} generator finished without producing a file')
        media_id = store.add_media(kind, settings['prompt'], settings, path.relative_to(OUTPUTS).as_posix(),
                                   round(time.monotonic() - start_time, 1))
        status.update(state='done', stage='complete', media=store.get_media(media_id))
    except Exception as error:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        status.update(state='error', stage='error', error=str(error))
    finally:
        process = None
        status['finished_at'] = time.time()
        lock.release()
