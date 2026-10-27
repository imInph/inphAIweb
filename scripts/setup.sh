#!/usr/bin/env bash
# bash scripts/setup.sh app|image|video|all
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-}"
case "$TARGET" in
    app|image|video|all) ;;
    *) echo "Usage: bash scripts/setup.sh app|image|video|all" >&2; exit 2 ;;
esac

if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
    UV="$HOME/.local/bin/uv"
else
    echo "Install uv using the instructions in README.md, then retry." >&2
    exit 1
fi

make_env() {
    "$UV" python install 3.11
    if [[ ! -x "$ROOT/.venvs/$1/bin/python" ]]; then
        "$UV" venv --python 3.11 --seed "$ROOT/.venvs/$1"
    fi
}

setup_app() {
    make_env app
    "$ROOT/.venvs/app/bin/python" -m pip install -r "$ROOT/requirements.txt"
}

# Each generator keeps its own environment: their diffusers versions differ.
setup_gpu() {
    local name="$1" python="$ROOT/.venvs/$1/bin/python"
    if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
        echo "Image and video generation target Linux x86_64 with AMD ROCm." >&2
        exit 1
    fi
    make_env "$name"
    "$python" -m pip install 'torch==2.10.0' --index-url https://download.pytorch.org/whl/rocm7.1
    "$python" - <<'PY'
import os
os.environ.setdefault('HSA_OVERRIDE_GFX_VERSION', '10.3.0')
import torch
if not torch.version.hip:
    raise SystemExit('This PyTorch installation is not a ROCm build.')
if not torch.cuda.is_available():
    raise SystemExit('ROCm cannot access the GPU. Check your driver/ROCm setup first.')
print('ROCm PyTorch:', torch.__version__)
print('GPU:', torch.cuda.get_device_name(0))
PY
    "$python" -m pip install -c "$ROOT/constraints.txt" -r "$ROOT/generators/$name/requirements.txt"
    "$python" -m pip check
}

if [[ "$TARGET" == app || "$TARGET" == all ]]; then
    setup_app
fi
if [[ "$TARGET" == image || "$TARGET" == all ]]; then
    setup_gpu image
    # About 38 GB: WAI-Illustrious v17 and Z-Image-Turbo.
    HF_HUB_DISABLE_XET=1 "$ROOT/.venvs/image/bin/python" -u "$ROOT/generators/image/download_models.py"
fi
if [[ "$TARGET" == video || "$TARGET" == all ]]; then
    setup_gpu video
    "$ROOT/.venvs/video/bin/python" -u "$ROOT/generators/video/gpu_test.py"
    echo "Wan2.2 (tens of GB) downloads on the first video you generate."
fi
echo
echo "Done. Start the site with: bash start.sh"
