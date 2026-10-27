"""Site settings, saved in SQLite so every device sees the same ones."""
import os
import re
import store

# The generators' own defaults (generate_image.py DEFAULTS['anime'], generate.py NEGATIVE_PROMPT).
IMAGE_NEGATIVE_PROMPT = 'bad quality, worst quality, lowres, blurry, text, watermark'
VIDEO_NEGATIVE_PROMPT = ('blurry, low quality, distorted face, deformed anatomy, extra limbs, '
                         'duplicate subjects, text, subtitles, watermark')
DEFAULTS = {
    'lmstudio_url': os.environ.get('LMSTUDIO_URL', 'http://localhost:1234').rstrip('/'),
    'ollama_url': os.environ.get('OLLAMA_URL', 'http://localhost:11434').rstrip('/'),
    'default_system_prompt': '',
    'default_options': {},
    'unload_ollama': True,
    'image_negative_prompt': IMAGE_NEGATIVE_PROMPT,
    'video_negative_prompt': VIDEO_NEGATIVE_PROMPT,
}
URL = re.compile(r'^https?://[^\s/?#]+(/[^\s?#]*)?$')
# Model options: name -> (type, low, high). Ollama takes them all; LM Studio the ones in LMSTUDIO_OPTIONS.
OPTIONS = {
    'temperature': (float, 0, 2),
    'top_p': (float, 0, 1),
    'top_k': (int, 0, 1000),
    'min_p': (float, 0, 1),
    'repeat_penalty': (float, 0, 3),
    'seed': (int, 0, 2**31 - 1),
    'max_tokens': (int, 1, 1048576),
    'num_ctx': (int, 256, 1048576),
    'keep_alive': (str, None, None),
    'think': (bool, None, None),
}
LMSTUDIO_OPTIONS = ('temperature', 'top_p', 'top_k', 'repeat_penalty', 'seed', 'max_tokens')
KEEP_ALIVE = re.compile(r'^-?\d+(\.\d+)?[smh]?$')


def validate_options(options):
    """Only the options that are set; anything left out uses the model's own default."""
    if not isinstance(options, dict):
        raise ValueError('options must be an object')
    if set(options) - set(OPTIONS):
        raise ValueError('Unknown option: ' + ', '.join(sorted(set(options) - set(OPTIONS))))
    clean = {}
    for name, value in options.items():
        if value is None:
            continue
        kind, low, high = OPTIONS[name]
        if kind is bool:
            if type(value) is not bool:
                raise ValueError(f'{name} must be true or false')
        elif kind is str:
            if not isinstance(value, str) or not KEEP_ALIVE.match(value.strip()):
                raise ValueError('keep_alive looks like 5m, 1h, 0 (unload at once) or -1 (keep loaded)')
            value = value.strip()
        else:
            if type(value) not in ((int,) if kind is int else (int, float)) or not low <= value <= high:
                raise ValueError(f'{name} must be {"a whole number" if kind is int else "a number"} from {low} to {high}')
        clean[name] = value
    return clean


def get(key=None):
    values = store.get_settings(DEFAULTS)
    return values if key is None else values[key]


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object')
    if set(data) - set(DEFAULTS):
        raise ValueError('Unknown setting: ' + ', '.join(sorted(set(data) - set(DEFAULTS))))
    fields = {}
    for key, value in data.items():
        if key.endswith('_url'):
            if not isinstance(value, str) or len(value) > 300 or not URL.match(value.strip()):
                raise ValueError('Addresses look like http://localhost:1234')
            value = value.strip().rstrip('/')
        elif key == 'default_options':
            value = validate_options(value)
        elif key == 'unload_ollama':
            if type(value) is not bool:
                raise ValueError('unload_ollama must be true or false')
        elif not isinstance(value, str) or len(value) > (20000 if key == 'default_system_prompt' else 4000):
            raise ValueError(f'{key} is too long')
        fields[key] = value
    return fields
