# Hardware and run history

These notes record the setup reported in the original interactive session. They
are not an official compatibility matrix or a benchmark of the packaged scripts.
The files in this workspace were reconstructed from that session; the Linux
server's actual files and environment were not copied remotely.

## Reported machine

- Ubuntu 26.04 LTS, Linux `7.0.0-38-generic`, x86_64.
- Ryzen 5 5600; RX 6700 XT 12GB, detected as `gfx1031`.
- `free -h` reported 38 GiB total RAM, 35 GiB available, and 8 GiB swap at setup.
- Ubuntu ROCm 7.1 packages; no `/opt/rocm/.info/version*` result.
- User belonged to `video` and `render`, with `/dev/kfd` and a render node present.
- System Python 3.14.4; isolated Python 3.11.17 used for these experiments.
- PyTorch `2.10.0+rocm7.1`, HIP build `7.1.25424`.
- `HSA_OVERRIDE_GFX_VERSION=10.3.0` used for the gfx1031 card.

## What was observed

- FP32 and FP16 GPU matrix multiplication and 3D convolution tests passed.
- ModelScope dependency checks and pipeline imports passed.
- Wan 0.35.1 dependency installation and the BF16 smoke-test stage were reported
  complete; exact BF16 test output was not pasted into the session.
- The original Wan script produced an MP4 at 448x256, 17 frames and 20 steps,
  using BF16 generation, FP32 GPU VAE decode, and sequential CPU offloading.
- That run reported 14.3 minutes for prompt encoding, generation, decoding and
  export. The denoising bar reported 3:02 for 20 steps. Model loading was excluded
  from the 14.3-minute timer. This is one observation, not an expected runtime.
- The user described the resulting visuals as distorted; the clip was not
  inspected here, so the cause was not established.

## Failure and workaround

A later 640x384 / 33-frame configuration enabled 256-pixel VAE tiles. Decoding
failed inside `tiled_decode` with a temporal shape mismatch (`2` versus `4`).
The v0.35.1 source's tiled decoder omits the `first_chunk=True` treatment used
by its non-tiled path, and also differs in handling patched output. The small
tiles exposed this path; the first successful low-resolution run had not.

The packaged Wan script avoids tiled decoding, saves the denoised latents in
Safetensors format, releases the generation pipeline, and loads a fresh FP32 VAE
on CPU. This workaround was provided after the error but has **not yet been
confirmed on the user's server**. It is a proposed fix, not a measured successful
run. CPU decode can still require substantial RAM and time.

Sources:

- [Wan VAE implementation, v0.35.1](https://github.com/huggingface/diffusers/blob/v0.35.1/src/diffusers/models/autoencoders/autoencoder_kl_wan.py)
- [Wan pipeline normalization and latent output, v0.35.1](https://github.com/huggingface/diffusers/blob/v0.35.1/src/diffusers/pipelines/wan/pipeline_wan.py)

## Packaging validation

See `VALIDATION.md` for local checks. Local syntax and command-line checks do not
establish AMD runtime compatibility or video quality.
