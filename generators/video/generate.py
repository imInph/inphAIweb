"""Generate Wan2.2 latents on AMD GPU, save them, then decode on CPU."""
import os

os.environ.setdefault("HSA_OVERRIDE_GFX_VERSION", "10.3.0")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import argparse
import gc
import json
import time
from pathlib import Path

import torch
from safetensors.torch import load_file, save_file
from torch.nn.attention import SDPBackend, sdpa_kernel
from diffusers import AutoencoderKLWan, WanPipeline
from diffusers.utils import export_to_video

MODEL_ID = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "data" / "outputs" / "videos"
DEFAULT_PROMPT = (
    "2D anime, crisp outlines and cel shading. An adult silver-haired "
    "swordswoman on a city rooftop at night. Her red scarf flutters in "
    "the wind as she slowly turns toward the camera. "
    "Medium close-up, steady camera."
)
NEGATIVE_PROMPT = (
    "blurry, low quality, distorted face, deformed anatomy, extra limbs, "
    "duplicate subjects, text, subtitles, watermark"
)


def decode_video(latent_path, fps_override=None):
    """Decode only saved latents; no transformer, GPU, or denoising required."""
    latent_path = latent_path.expanduser().resolve()
    metadata_path = latent_path.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    saved_model = metadata.get("model_id", MODEL_ID)
    if saved_model != MODEL_ID:
        raise ValueError(f"Expected {MODEL_ID} latents, found {saved_model}")
    fps = fps_override if fps_override is not None else metadata.get("fps", 24)
    if not isinstance(fps, (int, float)) or fps <= 0:
        raise ValueError("FPS must be positive")
    print(f"Loading saved latents: {latent_path}", flush=True)
    latents = load_file(str(latent_path), device="cpu")["latents"].float()
    if latents.ndim != 5 or not torch.isfinite(latents).all().item():
        raise ValueError("Expected finite B,C,T,H,W latents")

    print("Loading FP32 decoder on CPU...", flush=True)
    decoder = AutoencoderKLWan.from_pretrained(
        MODEL_ID, subfolder="vae", torch_dtype=torch.float32,
        use_safetensors=True, low_cpu_mem_usage=True,
    )
    decoder.to("cpu")
    decoder.eval()
    decoder.disable_tiling()
    if latents.shape[1] != decoder.config.z_dim:
        raise ValueError("Latent channels do not match this decoder")

    mean = torch.tensor(decoder.config.latents_mean, dtype=torch.float32).view(
        1, decoder.config.z_dim, 1, 1, 1
    )
    std = torch.tensor(decoder.config.latents_std, dtype=torch.float32).view(
        1, decoder.config.z_dim, 1, 1, 1
    )
    print("Decoding on CPU without tiling; this may take a while...", flush=True)
    start = time.monotonic()
    with torch.inference_mode():
        # Undo the normalization exactly as WanPipeline does before VAE decode.
        video = decoder.decode(latents * std + mean, return_dict=False)[0]
    if not torch.isfinite(video).all().item():
        raise RuntimeError("Invalid decoded values; the saved latents are preserved")
    frames = (
        video[0].float().add(1).div(2).clamp(0, 1)
        .permute(1, 2, 3, 0).contiguous().numpy()
    )
    output = latent_path.with_suffix(".mp4")
    export_to_video(frames, str(output), fps=fps)
    print(f"Saved video: {output}", flush=True)
    print(f"CPU decoding and export: {(time.monotonic() - start) / 60:.1f} minutes")
    return output


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--negative-prompt", default=NEGATIVE_PROMPT)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--frames", type=int, default=33)
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--guidance", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fps", type=int, default=None,
                        help="Generation defaults to 24; decode uses saved FPS")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--decode", type=Path,
                        help="Decode an existing .safetensors checkpoint only")
    parser.add_argument("--latents-only", action="store_true",
                        help="Save denoised latents and skip CPU decoding")
    args = parser.parse_args()
    if args.fps is not None and args.fps <= 0:
        parser.error("FPS must be positive")
    if not args.decode:
        if args.width <= 0 or args.height <= 0 or args.width % 32 or args.height % 32:
            parser.error("Width and height must be positive multiples of 32")
        if args.frames < 1 or (args.frames - 1) % 4:
            parser.error("Frames must be 1, 5, 9, 13, 17, ... (4n+1)")
        if args.steps <= 0:
            parser.error("Steps must be positive")
    return args


def main():
    args = parse_args()
    if args.decode:
        decode_video(args.decode, args.fps)
        return
    if not torch.version.hip or not torch.cuda.is_available():
        raise RuntimeError("A working ROCm GPU is required for generation")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    latent_path = args.output_dir / f"wan-{time.time_ns()}.safetensors"
    fps = args.fps if args.fps is not None else 24
    metadata = {**vars(args), "fps": fps, "model_id": MODEL_ID,
                "torch_version": torch.__version__, "rocm_build": torch.version.hip,
                "gpu": torch.cuda.get_device_name(0)}
    metadata_path = latent_path.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, indent=2, default=str) + "\n")

    print(f"GPU: {metadata['gpu']}", flush=True)
    print(f"Settings: {args.width}x{args.height}, {args.frames} frames, {args.steps} steps")
    print(f"Prompt: {args.prompt}", flush=True)
    print("Loading model; weights download only if not cached...", flush=True)
    vae = AutoencoderKLWan.from_pretrained(
        MODEL_ID, subfolder="vae", torch_dtype=torch.float32,
        use_safetensors=True, low_cpu_mem_usage=True,
    )
    pipe = WanPipeline.from_pretrained(
        MODEL_ID, vae=vae, torch_dtype=torch.bfloat16,
        use_safetensors=True, low_cpu_mem_usage=True,
    )
    pipe.vae.disable_tiling()
    pipe.enable_sequential_cpu_offload()

    print("Encoding prompt and generating latents on GPU...", flush=True)
    start = time.monotonic()
    with torch.inference_mode(), sdpa_kernel(SDPBackend.MATH):
        latents = pipe(
            prompt=args.prompt, negative_prompt=args.negative_prompt,
            height=args.height, width=args.width, num_frames=args.frames,
            num_inference_steps=args.steps, guidance_scale=args.guidance,
            max_sequence_length=256,
            generator=torch.Generator(device="cpu").manual_seed(args.seed),
            output_type="latent",
        ).frames
    latents = latents.detach().cpu().float().contiguous()
    if not torch.isfinite(latents).all().item():
        raise RuntimeError("Generation produced invalid latents")
    save_file({"latents": latents}, str(latent_path))
    metadata["denoising_and_prompt_encoding_seconds"] = time.monotonic() - start
    metadata_path.write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    print(f"Saved latents: {latent_path.resolve()}", flush=True)
    print("The generation checkpoint is saved; decoding can be retried separately.", flush=True)

    del pipe, vae, latents
    gc.collect()
    torch.cuda.empty_cache()
    if not args.latents_only:
        decode_video(latent_path, fps)


if __name__ == "__main__":
    main()
