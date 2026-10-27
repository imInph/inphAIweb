"""Image and video generation: a first-in, first-out queue of GPU jobs (saved in SQLite), run one at
a time by a worker thread, each as a subprocess in its generator's own venv."""
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
import shutil
import uuid
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

lines = deque(maxlen=200)        # output of the running job
live = {}                        # progress of the running job, mirrored into its row
process = None
cancelled = False
wake = threading.Event()
STEP_BAR = re.compile(r'(\d+)/(\d+) \[')


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


def check_environment(kind):
    if not Path(PYTHON[kind]).is_file():
        raise FileNotFoundError(f'No {kind} environment at {PYTHON[kind]}; run bash scripts/setup.sh {kind}')


def enqueue(user_id, kind, params, chat_id=None, message_id=None):
    """Queue a job. With chat_id and message_id, the finished file is attached to that chat message."""
    check_environment(kind)
    job_id = store.add_job(user_id, kind, params, chat_id, message_id)
    wake.set()
    return job_id


def post_to_chat(job_id):
    """Attach a finished job's file to the chat message it was made for (once both exist)."""
    job = store.get_job(job_id)
    if not job or job['state'] != 'done' or not job['message_id'] or not job['media_id']:
        return
    media = store.get_media(job['media_id'], job['user_id'])
    message = store.get_message(job['chat_id'], job['message_id']) if job['chat_id'] else None
    source = OUTPUTS / media['file'] if media else None
    if not message or not source or not source.is_file():
        return
    attachment_id = uuid.uuid4().hex
    folder = store.UPLOADS / attachment_id
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, folder / source.name)
    mime = 'image/png' if source.suffix == '.png' else 'video/mp4'
    store.add_attachment(job['user_id'], attachment_id, 'image' if job['kind'] == 'image' else 'video', source.name, mime,
                         source.stat().st_size, f'{attachment_id}/{source.name}', message_id=job['message_id'])
    store.set_job_message(job_id, None)   # attached: don't do it twice


def describe(job):
    """A job as the page sees it: its place in line, and the output while it runs."""
    job = dict(job)
    if job['state'] == 'queued':
        job['ahead'] = store.queue_ahead(job['id'])
    if job['state'] == 'running':
        job.update(live)
        job['output'] = list(lines)[-20:]
    if job['media_id']:
        job['media'] = store.get_media(job['media_id'], job['user_id'])
    return job


def jobs_for(user_id, since):
    running = store.running_job()
    return {'jobs': [describe(job) for job in store.user_jobs(user_id, since)],
            # Someone else's job holds the GPU: say what kind, not whose or what.
            'busy': running['kind'] if running and running['user_id'] != user_id else None}


def cancel(job_id, user):
    global cancelled
    job = store.get_job(job_id)
    if job is None or (job['user_id'] != user['id'] and user['role'] != 'admin'):
        return False
    if job['state'] == 'queued':
        return store.cancel_queued_job(job_id)
    if job['state'] == 'running' and process is not None and live.get('id') == job_id:
        cancelled = True
        process.terminate()
        return True
    return False


def remove_media_files(file):
    """Delete a finished image/video and the sidecar files its generator wrote beside it."""
    path = OUTPUTS / file
    for extra in (path, path.with_suffix('.json'), path.with_suffix('.log'), path.with_suffix('.safetensors')):
        extra.unlink(missing_ok=True)
    if path.parent != OUTPUTS / 'images':
        (path.parent / 'output.log').unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass


def command_for(kind, params):
    if kind == 'image':
        folder = OUTPUTS / 'images'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{params['model']}-{time.time_ns()}.png"
        command = [PYTHON[kind], '-u', str(IMAGE_SCRIPT), f'--output={path}']
        command += [f"--{key.replace('_', '-')}={value}" for key, value in params.items()]
        return command, path.with_suffix('.log'), lambda: path
    (OUTPUTS / 'videos').mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='run-', dir=OUTPUTS / 'videos'))
    command = [PYTHON[kind], '-u', str(VIDEO_SCRIPT), f'--output-dir={folder}']
    command += [f"--{key.replace('_', '-')}={value}" for key, value in params.items()]
    return command, folder / 'output.log', lambda: next(folder.glob('*.mp4'), None)


def read_progress(kind, line):
    if kind == 'image':
        if line.startswith('IMAGE_PROGRESS '):
            event = json.loads(line[len('IMAGE_PROGRESS '):])
            live.update({key: event[key] for key in ('stage', 'completed_steps', 'total_steps') if key in event})
        return
    # generate.py prints plain lines; diffusers' progress bar gives the steps.
    for text, stage in (('Loading model', 'loading_model'), ('generating latents', 'generating'),
                        ('Decoding on CPU', 'decoding'), ('Saved video', 'saving')):
        if text in line:
            live['stage'] = stage
    bar = STEP_BAR.search(line)
    if bar and live['stage'] == 'generating' and int(bar.group(2)) == live['total_steps']:
        live['completed_steps'] = int(bar.group(1))


def run(job):
    """Run one job to the end. Only the worker thread calls this, so one runs at a time."""
    global process, cancelled
    kind, params = job['kind'], job['settings']
    cancelled = False
    lines.clear()
    live.clear()
    live.update(id=job['id'], stage='starting', completed_steps=0, total_steps=params['steps'])
    store.update_job(job['id'], {'state': 'running', 'stage': 'starting', 'started_at': time.time()})
    result = {}
    try:
        freed = llm.unload_ollama() if settings.get('unload_ollama') else []
        if freed:
            lines.append('Unloaded from Ollama to free the GPU: ' + ', '.join(freed))
        check_environment(kind)
        command, log_path, find_output = command_for(kind, params)
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
            result = {'state': 'cancelled', 'stage': 'cancelled'}
            return
        if code:
            raise RuntimeError(f'The {kind} generator exited with code {code}; see {log_path.relative_to(ROOT)}')
        path = find_output()
        if path is None or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f'The {kind} generator finished without producing a file')
        media_id = store.add_media(job['user_id'], kind, params['prompt'], params,
                                   path.relative_to(OUTPUTS).as_posix(), round(time.monotonic() - start_time, 1))
        result = {'state': 'done', 'stage': 'complete', 'media_id': media_id}
    except Exception as error:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        result = {'state': 'error', 'stage': 'error', 'error': str(error)}
    finally:
        process = None
        store.update_job(job['id'], {**result, 'completed_steps': live.get('completed_steps', 0),
                                     'total_steps': live.get('total_steps', 0), 'finished_at': time.time()})
        live.clear()
        if result.get('state') == 'done':
            post_to_chat(job['id'])


def worker():
    while True:
        job = store.next_queued_job()
        if job is None:
            wake.wait(timeout=30)
            wake.clear()
            continue
        try:
            run(job)
        except Exception as error:
            # Never let one bad job stop the queue.
            print(f'Job {job["id"]} failed outside the generator: {error}', flush=True)
            store.update_job(job['id'], {'state': 'error', 'error': str(error), 'finished_at': time.time()})


def start_worker():
    threading.Thread(target=worker, name='gpu-queue', daemon=True).start()
