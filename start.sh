#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# The GPU settings scripts/setup.sh picked for this machine (e.g. HSA_OVERRIDE_GFX_VERSION).
if [[ -f .venvs/gpu.env ]]; then
    set -a
    . .venvs/gpu.env
    set +a
fi
if [[ -x .venvs/app/bin/python ]]; then
    exec .venvs/app/bin/python app.py
fi
exec python3 app.py
