# Local video generation on an RX 6700 XT

ModelScope 1.7B and Wan2.2 TI2V 5B experiments on Linux with an AMD RX 6700 XT
12GB and approximately 40GB system RAM. Each model gets its own Python 3.11
environment. Generation scripts, GPU smoke tests, installation commands and
troubleshooting are included.

**Status:** reconstructed from a real setup session. Basic GPU operations and an
earlier Wan generation ran on the reported machine. The packaged Wan CPU decode
workaround still needs a full server run. See [run history](SETUP-NOTES.md).

## Contents

```text
modelscope/              FP16 generator, GPU test, requirements
wan/                     BF16 generator with saved latents, GPU test, requirements
scripts/setup.sh         Creates either isolated environment
scripts/inspect_system.sh Read-only host diagnostics
constraints.txt          Protects the working ROCm PyTorch version
docs/                    Run history, troubleshooting, validation, GitHub steps
artifacts/               Generated files and environment snapshots (ignored by Git)
.venvs/                  Python environments (ignored by Git)
```

## Before installation

This targets **Linux x86_64**, an installed AMD GPU driver/ROCm setup, access to
`/dev/kfd` and the GPU render node, and a user with the relevant device permissions.
It does not install or replace system drivers. The RX 6700 XT is outside AMD's
official supported GPU list; the architecture override is a workaround, not a
support guarantee. A virtual environment isolates Python packages, not the host
driver or kernel.

Inspect the host:

```bash
bash scripts/inspect_system.sh
```

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

Run the following commands from this repository's root. Do not use `sudo` for
environment installation, Python execution, or editing project files.

## ModelScope 1.7B

```bash
bash scripts/setup.sh modelscope
source .venvs/modelscope/bin/activate
python -u modelscope/gpu_test.py
python -u modelscope/generate.py --prompt "A raccoon eating spaghetti in a restaurant"
```

Defaults: 256x256, 16 frames, 25 steps, seed 42, 8 fps. Uses FP16 weights, model
CPU offloading, VAE slicing, forward chunking and standard math attention.
Videos and prompt/settings JSON files go to `artifacts/modelscope/`.

The old `damo-vilab/text-to-video-ms-1.7b` name now leads to
[`ali-vilab/text-to-video-ms-1.7b`](https://huggingface.co/ali-vilab/text-to-video-ms-1.7b).
FP16 weights total approximately 3.7 GB, plus small configuration/tokenizer files.
Downloads are cached by Hugging Face outside this repository by default.

## Wan2.2 TI2V 5B

Open another terminal, or run `deactivate` if the ModelScope environment is active.

```bash
bash scripts/setup.sh wan
source .venvs/wan/bin/activate
python -u wan/gpu_test.py
```

Start with a small smoke generation:

```bash
python -u wan/generate.py --width 448 --height 256 --frames 17 --steps 20
```

Then try the larger experimental quality settings:

```bash
python -u wan/generate.py
```

Defaults: 640x384, 33 frames, 50 steps, guidance 5, seed 42, 24 fps. These reduced
dimensions are an experiment below the model's native 720p target; good output
is not guaranteed. More steps do not fix every kind of visual distortion.

Custom prompt:

```bash
python -u wan/generate.py --prompt "2D anime, crisp outlines and cel shading. An adult silver-haired swordswoman on a rooftop at night. Her red scarf flutters in the wind. Medium close-up, steady camera."
```

The script:

1. Loads the official [Diffusers-format model](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B-Diffusers).
2. Generates BF16 on GPU using sequential CPU offloading and math attention.
3. Saves final denoised latents to `artifacts/wan/*.safetensors` plus JSON settings.
4. Releases the generation model and loads a fresh FP32 decoder on CPU.
5. Decodes without tiling and writes an MP4 alongside the checkpoint.

The first download is tens of gigabytes. The original official 720p command
documents at least 24GB VRAM even with its own offloading options; this workspace
uses a different, more aggressive approach for a 12GB card. 40GB system RAM helps
hold weights, but is not equivalent to 40GB VRAM and is not a universal fit guarantee.
CPU offloading and CPU decoding trade speed for reduced GPU memory use.

### Decode a saved generation again

Use the real checkpoint path printed by the script:

```bash
python -u wan/generate.py --decode artifacts/wan/wan-YOUR-RUN.safetensors
```

This skips prompt encoding and denoising. Decode-only mode does not require a
GPU. It uses the JSON sidecar's FPS, or 24 if no metadata exists. Re-decoding
overwrites that checkpoint's MP4; the checkpoint remains intact.

To generate a checkpoint without decoding immediately:

```bash
python -u wan/generate.py --latents-only
```

**A saved final latent file lets you retry decoding, not resume interrupted
denoising.** A crash before the checkpoint is written still loses that generation.

## AMD and memory notes

- Scripts set `HSA_OVERRIDE_GFX_VERSION=10.3.0` before importing PyTorch, unless
  you already set it. This targets the reported `gfx1031` RX 6700 XT setup.
- `cuda` in Python device names is expected: ROCm PyTorch reuses that API.
- BF16 smoke tests only check basic operations and finite output. They do not
  establish native hardware acceleration, numerical accuracy, or full model support.
- Do not add `.to("cuda")` to the offloaded pipeline: it defeats the memory plan.
- Avoid enabling the old Wan VAE tiling path in this pinned version. The previous
  small-tile experiment crashed during decoding.
- If GPU memory runs out, reduce Wan to `--frames 17 --width 448 --height 256`.
  Wan dimensions must be multiples of 32; frame counts must be `4n+1`.
- Math attention consumes more memory than some fused attention implementations.
  No FlashAttention, xFormers, compilation, or CUDA-specific extension is required.
- Keep other GPU workloads out of the memory budget when testing. An installed
  service is not necessarily running or occupying VRAM; inspect before changing it.

## Dependencies and reproducibility

The setup script first installs the official PyTorch 2.10.0 ROCm 7.1 wheel, then
installs model-specific dependencies with a constraint keeping that exact AMD
build. Some transitive versions remain resolver-selected. After installation,
`artifacts/modelscope-environment.txt` or `artifacts/wan-environment.txt` records
the resolved versions with `pip freeze`. This is not a complete dependency lock.

```bash
python -m pip check
python modelscope/generate.py --help
python wan/generate.py --help
```

The two model versions intentionally use separate environments. Never upgrade one
in place to accommodate the other. This workspace provides the installation
sequence used in the session; it does not claim the latest libraries are tested.

## Publishing to GitHub

See [GitHub steps](GITHUB.md). Models, videos, latent checkpoints, caches,
virtual environments, and local secrets are excluded by `.gitignore`. The setup
downloads weights to the usual Hugging Face cache rather than the repository.

No project code license has been selected. Choose one before inviting reuse;
model weights have their own licenses and terms. ModelScope's model card metadata
and body name different Creative Commons variants, so review the upstream terms
before redistribution. Wan's model card identifies Apache-2.0.

## References

- [PyTorch official ROCm 7.1 installation command](https://pytorch.org/get-started/previous-versions/)
- [PyTorch HIP/ROCm semantics](https://docs.pytorch.org/docs/stable/notes/hip.html)
- [AMD GPU system requirements](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/reference/system-requirements.html)
- [AMD webinar showing the RX 6700 XT override](https://www.amd.com/content/dam/amd/en/documents/developer/webinars/rocm/pytorch-on-radeon-and-instinct-gpus.pdf)
- [ModelScope model card](https://huggingface.co/ali-vilab/text-to-video-ms-1.7b)
- [Wan2.2 original repository](https://github.com/Wan-Video/Wan2.2)
- [Wan2.2 Diffusers model card](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B-Diffusers)
- [Diffusers 0.35.1 memory/offloading guide](https://huggingface.co/docs/diffusers/v0.35.1/optimization/memory)
