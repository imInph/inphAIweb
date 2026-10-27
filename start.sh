#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ -x .venvs/app/bin/python ]]; then
    exec .venvs/app/bin/python app.py
fi
exec python3 app.py
