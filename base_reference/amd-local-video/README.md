# AMD Local Video

Personal experiment, shared as-is. Not actively maintained; expect rough edges and hardware-specific settings.

Backend made by Codex.

Run ModelScope 1.7B and Wan2.2 5B locally with an AMD GPU.
Built around an RX 6700 XT 12GB, Linux, ROCm 7.1, and roughly 40GB RAM.

## Setup

You need ROCm working already. Run commands from this folder, without `sudo`.

Install uv if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

## ModelScope

```bash
bash scripts/setup.sh modelscope
source .venvs/modelscope/bin/activate
python modelscope/gpu_test.py
python -u modelscope/generate.py --prompt "A raccoon eating spaghetti"
```

## Wan2.2

Use a fresh terminal or run `deactivate` before switching models.

```bash
bash scripts/setup.sh wan
source .venvs/wan/bin/activate
python wan/gpu_test.py
python -u wan/generate.py --prompt "2D anime. A swordswoman on a rooftop, her scarf blowing in the wind."
```

For a smaller test, add:

```text
--width 448 --height 256 --frames 17 --steps 20
```

## Files

Videos go in `artifacts/`. Models download automatically on the first run.
Wan also saves checkpoints so decoding can be retried:

```bash
python -u wan/generate.py --decode artifacts/wan/wan-YOUR-RUN.safetensors
```

The RX 6700 XT uses an unofficial compatibility workaround. Wan's CPU decoding
fix still needs testing on the original server.

[Troubleshooting](docs/TROUBLESHOOTING.md) · [Full guide](docs/REFERENCE.md) · [GitHub upload](docs/GITHUB.md)
