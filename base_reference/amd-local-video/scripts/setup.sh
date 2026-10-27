#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-}"
case "$TARGET" in
    modelscope|wan) ;;
    *) echo "Usage: bash scripts/setup.sh modelscope|wan" >&2; exit 2 ;;
esac

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    echo "This setup targets Linux x86_64 with AMD ROCm." >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
    UV="$HOME/.local/bin/uv"
else
    echo "Install uv using the instructions in README.md, then retry." >&2
    exit 1
fi

ENV_DIR="$ROOT/.venvs/$TARGET"
mkdir -p "$ROOT/.venvs" "$ROOT/artifacts"
"$UV" python install 3.11
if [[ ! -x "$ENV_DIR/bin/python" ]]; then
    "$UV" venv --python 3.11 --seed "$ENV_DIR"
fi

"$ENV_DIR/bin/python" -m pip install 'torch==2.10.0' \
    --index-url https://download.pytorch.org/whl/rocm7.1
"$ENV_DIR/bin/python" -m pip install \
    -c "$ROOT/constraints.txt" -r "$ROOT/$TARGET/requirements.txt"
"$ENV_DIR/bin/python" -m pip check
"$ENV_DIR/bin/python" -m pip freeze > "$ROOT/artifacts/$TARGET-environment.txt"

echo
echo "Installed. Activate with:"
printf 'source %q/bin/activate\n' "$ENV_DIR"
echo "Then run the GPU test documented in README.md."
