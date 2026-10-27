import os
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from pathlib import Path
import hashlib
import json
from huggingface_hub import HfApi, hf_hub_download, snapshot_download

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / 'models'
MODELS.mkdir(exist_ok=True)
api = HfApi()
manifest = {}
saved = json.loads((ROOT / 'models.json').read_text()) if (ROOT / 'models.json').exists() else {}

repo = 'LyliaEngine/waiIllustriousSDXL_v170'
revision = '5ef4e2da7173a160ad04aebcaa2fdcd6d20ed792'
print('Downloading WAI-Illustrious v17 checkpoint...', flush=True)
path = hf_hub_download(repo, 'waiIllustriousSDXL_v170.safetensors', revision=revision,
                       local_dir=MODELS / 'wai')
expected = 'f116b0c78ff441467b0cdc8f1936e1ed18ea31e9997c7b132b1b8db533f0bd04'
print('Checking WAI checkpoint SHA256...', flush=True)
h = hashlib.sha256()
with open(path, 'rb') as stream:
    for block in iter(lambda: stream.read(16 * 1024 * 1024), b''):
        h.update(block)
if h.hexdigest() != expected:
    raise RuntimeError('WAI checkpoint checksum mismatch')
manifest['anime'] = {'repo': repo, 'revision': revision, 'sha256': expected, 'path': str(Path(path).relative_to(ROOT))}

repo = 'stabilityai/stable-diffusion-xl-base-1.0'
revision = saved.get('sdxl_config', {}).get('revision') or api.model_info(repo).sha
print('Downloading SDXL configuration/tokenizers (no duplicate model weights)...', flush=True)
snapshot_download(repo, revision=revision, local_dir=MODELS / 'sdxl-config',
                  allow_patterns=['*.json', '**/*.json', '*.txt', '**/*.txt', '*.model', '**/*.model'], max_workers=2)
manifest['sdxl_config'] = {'repo': repo, 'revision': revision}

repo = 'Tongyi-MAI/Z-Image-Turbo'
revision = saved.get('realistic', {}).get('revision') or api.model_info(repo).sha
print('Downloading Z-Image-Turbo...', flush=True)
snapshot_download(repo, revision=revision, local_dir=MODELS / 'z-image-turbo',
                  allow_patterns=['*.json', '**/*.json', '*.txt', '**/*.txt', '*.model', '**/*.model', '*.safetensors', '**/*.safetensors'], max_workers=2)
manifest['realistic'] = {'repo': repo, 'revision': revision}
(ROOT / 'models.json').write_text(json.dumps(manifest, indent=2) + '\n')
print('Both model downloads are complete.', flush=True)
