#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ -x .venv/bin/python ]]; then
    image_python=.venv/bin/python
else
    image_python=python
fi
"$image_python" - <<'PY'
import os
import sys
os.environ.setdefault('HSA_OVERRIDE_GFX_VERSION', '10.3.0')
if sys.prefix == sys.base_prefix:
    raise SystemExit('Create/activate a virtual environment first; see README.md.')
try:
    import torch
except ImportError:
    raise SystemExit('Install ROCm PyTorch in your virtual environment first; see README.md.')
if not torch.version.hip:
    raise SystemExit('This PyTorch installation is not a ROCm build; see README.md.')
if not torch.cuda.is_available():
    raise SystemExit('ROCm cannot access the GPU. Check your driver/PyTorch setup first.')
print('ROCm PyTorch:', torch.__version__)
print('GPU:', torch.cuda.get_device_name(0))
PY
"$image_python" -m pip install -r requirements.txt
HF_HUB_DISABLE_XET=1 "$image_python" -u download_models.py
