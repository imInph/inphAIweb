"""One-image ROCm generator. Run with .venv/bin/python generate_image.py --help."""
import argparse
import json
import math
import os
from pathlib import Path
import time

os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
os.environ.setdefault('MIOPEN_FIND_MODE', 'FAST')
ROOT = Path(__file__).resolve().parent
MODELS = ROOT / 'models'
DEFAULTS = {
    'anime': {'steps': 28, 'guidance': 6.0, 'negative_prompt': 'bad quality, worst quality, lowres, blurry, text, watermark'},
    'realistic': {'steps': 9, 'guidance': 0.0, 'negative_prompt': ''},
}


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object')
    allowed = {'model', 'prompt', 'negative_prompt', 'width', 'height', 'steps', 'guidance', 'seed'}
    if set(data) - allowed:
        raise ValueError('Unknown setting: ' + ', '.join(sorted(set(data) - allowed)))
    model = data.get('model', 'anime')
    if not isinstance(model, str) or model not in DEFAULTS:
        raise ValueError('model must be anime or realistic')
    settings = {'model': model, 'prompt': '', 'width': 1024, 'height': 1024,
                'seed': 42, **DEFAULTS[model], **data}
    for name in ('prompt', 'negative_prompt'):
        if not isinstance(settings[name], str) or len(settings[name]) > 4000 or '\x00' in settings[name]:
            raise ValueError(f'{name} must be text, at most 4000 characters')
    if not settings['prompt'].strip():
        raise ValueError('Enter a prompt')
    for name, low, high in [('width', 512, 1536), ('height', 512, 1536), ('steps', 1, 60), ('seed', 0, 2**63-1)]:
        if type(settings[name]) is not int or not low <= settings[name] <= high:
            raise ValueError(f'{name} must be an integer from {low} to {high}')
    if settings['width'] % 64 or settings['height'] % 64:
        raise ValueError('width and height must be multiples of 64')
    if settings['width'] * settings['height'] > 1310720:
        raise ValueError('Keep image area at or below 1,310,720 pixels, e.g. 1024x1280')
    value = settings['guidance']
    if type(value) not in (int, float) or not 0 <= value <= 12 or not math.isfinite(value):
        raise ValueError('guidance must be a number from 0 to 12')
    if model == 'realistic' and (settings['guidance'] != 0 or settings['negative_prompt']):
        raise ValueError('Z-Image-Turbo uses guidance 0 and no negative prompt')
    return settings


def event(stage, **details):
    print('\nIMAGE_PROGRESS ' + json.dumps({'stage': stage, **details}), flush=True)


def generate(settings, output_path):
    import numpy as np
    import torch
    from torch.nn.attention import SDPBackend, sdpa_kernel
    from diffusers import StableDiffusionXLPipeline, EulerAncestralDiscreteScheduler, ZImagePipeline
    if not torch.version.hip or not torch.cuda.is_available():
        raise RuntimeError('A working ROCm GPU is required')
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    start = time.monotonic()
    event('loading_model', model=settings['model'])
    if settings['model'] == 'anime':
        pipe = StableDiffusionXLPipeline.from_single_file(
            str(MODELS / 'wai/waiIllustriousSDXL_v170.safetensors'),
            config=str(MODELS / 'sdxl-config'), torch_dtype=torch.float16,
            local_files_only=True, add_watermarker=False,
        )
        pipe.scheduler = EulerAncestralDiscreteScheduler.from_config(pipe.scheduler.config)
        pipe.enable_model_cpu_offload()
    else:
        pipe = ZImagePipeline.from_pretrained(
            str(MODELS / 'z-image-turbo'), torch_dtype=torch.bfloat16,
            local_files_only=True, low_cpu_mem_usage=True,
        )
        # Whole-model offload still leaves a nearly 12 GB transformer on the GPU.
        # Stream individual modules instead, to leave room for attention work.
        pipe.enable_sequential_cpu_offload()
    pipe.vae.tile_sample_min_size = 512
    pipe.vae.tile_latent_min_size = 512 // pipe.vae_scale_factor
    pipe.vae.enable_tiling()
    pipe.vae.enable_slicing()
    original_decode = pipe.vae.decode

    def decode_with_status(*args, **kwargs):
        event('decoding')
        return original_decode(*args, **kwargs)

    pipe.vae.decode = decode_with_status
    load_seconds = time.monotonic() - start

    def on_step(pipeline, step, timestep, callback_kwargs):
        torch.cuda.synchronize()
        event('generating', completed_steps=step + 1, total_steps=pipeline.num_timesteps)
        return callback_kwargs

    kwargs = dict(prompt=settings['prompt'], width=settings['width'], height=settings['height'],
                  num_inference_steps=settings['steps'], guidance_scale=settings['guidance'],
                  generator=torch.Generator(device='cpu').manual_seed(settings['seed']),
                  callback_on_step_end=on_step, callback_on_step_end_tensor_inputs=[],
                  output_type='np')
    if settings['model'] == 'anime':
        kwargs['negative_prompt'] = settings['negative_prompt']
    event('generating', completed_steps=0, total_steps=settings['steps'])
    torch.cuda.reset_peak_memory_stats()
    # The known-working backend on this RDNA2/ROCm installation.
    with torch.inference_mode(), sdpa_kernel(SDPBackend.MATH):
        pixels = pipe(**kwargs).images[0]
    if not np.isfinite(pixels).all() or float(pixels.std()) < 0.001:
        raise RuntimeError('Image contains invalid or blank pixels')
    from PIL import Image
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    event('saving')
    Image.fromarray((pixels.clip(0, 1) * 255).round().astype('uint8')).save(output_path)
    metrics = {'settings': settings, 'load_seconds': round(load_seconds, 2),
               'total_seconds': round(time.monotonic() - start, 2),
               'peak_vram_gib': round(torch.cuda.max_memory_allocated() / 1024**3, 2),
               'gpu': torch.cuda.get_device_name(0), 'torch': torch.__version__}
    output_path.with_suffix('.json').write_text(json.dumps(metrics, indent=2) + '\n')
    event('complete', file_path=str(output_path), **{k:v for k,v in metrics.items() if k != 'settings'})
    print(f'Saved image: {output_path}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=DEFAULTS, default='anime')
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--negative-prompt')
    parser.add_argument('--width', type=int, default=1024)
    parser.add_argument('--height', type=int, default=1024)
    parser.add_argument('--steps', type=int)
    parser.add_argument('--guidance', type=float)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--output', type=Path, default=ROOT.parents[1] / 'data' / 'outputs' / 'images' / f'image-{time.time_ns()}.png')
    args = vars(parser.parse_args())
    output = args.pop('output')
    try:
        settings = validate({key:value for key,value in args.items() if value is not None})
    except ValueError as error:
        parser.error(str(error))
    generate(settings, output)
