"""Generate short ModelScope clips using ROCm, FP16, and CPU offloading."""
import os

os.environ.setdefault("HSA_OVERRIDE_GFX_VERSION", "10.3.0")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from torch.nn.attention import SDPBackend, sdpa_kernel
from diffusers import TextToVideoSDPipeline, DPMSolverMultistepScheduler
from diffusers.utils import export_to_video

MODEL_ID = "ali-vilab/text-to-video-ms-1.7b"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "artifacts" / "modelscope"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default="A raccoon eating spaghetti in a restaurant")
    parser.add_argument("--negative-prompt", default="text, watermark")
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument("--frames", type=int, default=16)
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--guidance", type=float, default=9.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if any(value <= 0 for value in (args.width, args.height, args.frames, args.steps, args.fps)):
        parser.error("Dimensions, frames, steps, and FPS must be positive")
    if args.width % 8 or args.height % 8:
        parser.error("Width and height must be divisible by 8")
    if not torch.version.hip or not torch.cuda.is_available():
        raise RuntimeError("A working ROCm GPU is required")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"modelscope-{time.time_ns()}.mp4"
    print("Loading model; weights download on the first run...", flush=True)
    pipe = TextToVideoSDPipeline.from_pretrained(
        MODEL_ID, torch_dtype=torch.float16, variant="fp16", use_safetensors=True
    )
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config)
    pipe.enable_model_cpu_offload()
    pipe.enable_vae_slicing()
    pipe.unet.enable_forward_chunking(chunk_size=1, dim=1)

    print(f"Generating: {args.prompt}", flush=True)
    start = time.monotonic()
    with torch.inference_mode(), sdpa_kernel(SDPBackend.MATH):
        frames = pipe(
            prompt=args.prompt, negative_prompt=args.negative_prompt,
            height=args.height, width=args.width, num_frames=args.frames,
            num_inference_steps=args.steps, guidance_scale=args.guidance,
            generator=torch.Generator(device="cpu").manual_seed(args.seed),
        ).frames[0]
    if not np.isfinite(frames).all():
        raise RuntimeError("Generated frames contain invalid numbers")
    export_to_video(frames, str(output), fps=args.fps)
    metadata = {**vars(args), "model_id": MODEL_ID,
                "generation_and_export_seconds": time.monotonic() - start}
    output.with_suffix(".json").write_text(
        json.dumps(metadata, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(f"Saved: {output.resolve()}", flush=True)


if __name__ == "__main__":
    main()
