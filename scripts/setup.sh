#!/usr/bin/env bash
# bash scripts/setup.sh app|image|video|voice|gpu|all [--override X.Y.Z | --no-override]
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
GPU_ENV="$ROOT/.venvs/gpu.env"
USAGE="Usage: bash scripts/setup.sh app|image|video|voice|gpu|all [--override X.Y.Z | --no-override]"
TARGET="${1:-}"
case "$TARGET" in
    app|image|video|voice|gpu|all) ;;
    *) echo "$USAGE" >&2; exit 2 ;;
esac
# Unset: detect the card. Empty: no override. Otherwise: that value.
OVERRIDE=auto
case "${2:-}" in
    '') ;;
    --no-override) OVERRIDE='' ;;
    --override)
        OVERRIDE="${3:-}"
        if [[ ! "$OVERRIDE" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "--override needs a version like 10.3.0" >&2; exit 2
        fi ;;
    *) echo "$USAGE" >&2; exit 2 ;;
esac

make_env() {
    local uv
    if command -v uv >/dev/null 2>&1; then
        uv="$(command -v uv)"
    elif [[ -x "$HOME/.local/bin/uv" ]]; then
        uv="$HOME/.local/bin/uv"
    else
        echo "Install uv using the instructions in README.md, then retry." >&2
        exit 1
    fi
    "$uv" python install 3.11
    if [[ ! -x "$ROOT/.venvs/$1/bin/python" ]]; then
        "$uv" venv --python 3.11 --seed "$ROOT/.venvs/$1"
    fi
}

# Writes .venvs/gpu.env, which start.sh loads, so the site and its generators run with it.
# RDNA2 cards other than gfx1030 (e.g. the RX 6700 XT, gfx1031) aren't supported by ROCm and
# only work when told to act as gfx1030. Supported cards must not get that override.
setup_gpu_env() {
    local rocminfo gfx=''
    if [[ "$OVERRIDE" == auto ]]; then
        OVERRIDE=''
        rocminfo="$(command -v rocminfo || echo /opt/rocm/bin/rocminfo)"
        if [[ -x "$rocminfo" ]]; then
            # Every GPU, so a machine with an integrated one is judged on all of them.
            gfx="$("$rocminfo" 2>/dev/null | grep -oE 'gfx[0-9a-f]+' | sort -u | tr '\n' ' ' || true)"
            gfx="${gfx% }"
        fi
        if [[ -z "$gfx" ]]; then
            echo "Couldn't find an AMD GPU with rocminfo; using no override." >&2
            echo "If your card needs one, rerun with --override X.Y.Z." >&2
        elif [[ " $gfx " =~ \ gfx103[1-6]\  ]]; then
            # The override applies to every GPU, so a supported card next to an RDNA2 one
            # (e.g. a gfx1100 with a gfx1036 integrated GPU) is left alone.
            if [[ -z "$(tr ' ' '\n' <<< "$gfx" | grep -vE '^gfx103[0-6]$' || true)" ]]; then
                OVERRIDE=10.3.0
            else
                echo "Mixed GPUs ($gfx); using no override. Use --override X.Y.Z to force one." >&2
            fi
        fi
    fi
    mkdir -p "$ROOT/.venvs"
    if [[ -n "$OVERRIDE" ]]; then
        echo "HSA_OVERRIDE_GFX_VERSION=$OVERRIDE" > "$GPU_ENV"
    else
        : > "$GPU_ENV"
    fi
    echo "GPU: ${gfx:-not checked}; override: ${OVERRIDE:-none} (saved in .venvs/gpu.env)"
}

load_gpu_env() {
    set -a
    . "$GPU_ENV"
    set +a
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

if [[ "$TARGET" == gpu || "$TARGET" == image || "$TARGET" == video || "$TARGET" == all ]]; then
    setup_gpu_env
    load_gpu_env
fi
if [[ "$TARGET" == app || "$TARGET" == all ]]; then
    setup_app
fi
if [[ "$TARGET" == image || "$TARGET" == all ]]; then
    setup_gpu image
    # About 38 GB: WAI-Illustrious v17 and Z-Image-Turbo.
    HF_HUB_DISABLE_XET=1 "$ROOT/.venvs/image/bin/python" -u "$ROOT/generators/image/download_models.py"
fi
if [[ "$TARGET" == voice || "$TARGET" == all ]]; then
    # CPU only: no PyTorch, no GPU. The Whisper model downloads on the first recording.
    make_env voice
    "$ROOT/.venvs/voice/bin/python" -m pip install -r "$ROOT/generators/voice/requirements.txt"
fi
if [[ "$TARGET" == video || "$TARGET" == all ]]; then
    setup_gpu video
    "$ROOT/.venvs/video/bin/python" -u "$ROOT/generators/video/gpu_test.py"
    echo "Wan2.2 (tens of GB) downloads on the first video you generate."
fi
echo
echo "Done. Start the site with: bash start.sh"
