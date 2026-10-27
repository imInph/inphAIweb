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
    'default_temperature': None,
    'unload_ollama': True,
    'image_negative_prompt': IMAGE_NEGATIVE_PROMPT,
    'video_negative_prompt': VIDEO_NEGATIVE_PROMPT,
}
URL = re.compile(r'^https?://[^\s/?#]+(/[^\s?#]*)?$')


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
        elif key == 'default_temperature':
            if value is not None and (type(value) not in (int, float) or not 0 <= value <= 2):
                raise ValueError('temperature must be empty or a number from 0 to 2')
        elif key == 'unload_ollama':
            if type(value) is not bool:
                raise ValueError('unload_ollama must be true or false')
        elif not isinstance(value, str) or len(value) > (20000 if key == 'default_system_prompt' else 4000):
            raise ValueError(f'{key} is too long')
        fields[key] = value
    return fields
