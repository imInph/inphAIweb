# Troubleshooting

## `SyntaxError` at `HSA_OVERRIDE_GFX_VERSION=... python ...`

That line belongs in the terminal, not a Python file. Python files contain only
the Python code. This workspace sets the override inside its Python entry points.

## Missing `/opt/amdgpu/share/libdrm/amdgpu.ids`

The reported system printed this warning but still passed GPU calculations.
The warning alone does not establish a compute failure. Test actual operations
before changing the host driver installation.

## `No module named numpy`

Run the environment's setup script and activate the corresponding environment.
Both requirements files install NumPy. Never use system `sudo pip`.

## PyTorch cannot see the GPU

Run `scripts/inspect_system.sh`; verify `/dev/kfd`, the render node, permissions,
the active Python environment, `torch.version.hip`, and the GPU test output.
Architecture overrides cannot add missing kernels to a binary or fix every
unsupported GPU combination.

## Download progress looks stuck

In another terminal, inspect the default cache:

```bash
watch -n 5 'du -sh ~/.cache/huggingface/hub ~/.cache/huggingface/xet 2>/dev/null'
```

Growing sizes show activity. Unchanged sizes do not prove a hang: the downloader
may be waiting or assembling data. If `HF_HOME` or related cache paths are set,
inspect those paths instead. Terminal escape sequences can make progress bars
look corrupted without affecting downloads.

## Tokenizer fork warning

The generators default `TOKENIZERS_PARALLELISM=false` before imports. This
suppresses the warning observed when the video exporter launches FFmpeg.

## Wan temporal shape mismatch inside `tiled_decode`

The small-tile path failed in the session with tensor sizes 2 versus 4. The
packaged generator never enables it. It saves latents first and uses a fresh CPU
decoder without tiling. This proposed workaround still needs confirmation on
the server. Existing runs from the earlier script cannot be recovered if they
exited before saving latents.

## CPU decode fails or the system runs out of RAM

Keep the `.safetensors` and JSON sidecar. You can retry `--decode` after resolving
the issue without repeating GPU generation. Use available RAM reported by
`free -h`, not just installed RAM. A system with 40GB installed does not always
have 40GB free. For future runs, lower dimensions or frames.

## Output is distorted, dark, or low quality

A finite-value check catches NaNs/Infs, not every numerical or quality issue.
The earlier distorted video was not inspected; no cause was established. Keep
the prompt and seed constant when comparing one setting at a time. Higher steps
and resolution can help but do not guarantee improvement. The reduced-resolution
Wan settings are below the model's native target and remain experimental.

## Different results with the same seed

Seed 42 helps comparisons, but matching seeds do not guarantee identical output
across different GPU architectures, package versions, attention implementations,
or numerical precision choices. Preserve the run's metadata and environment
snapshot when comparing results.
