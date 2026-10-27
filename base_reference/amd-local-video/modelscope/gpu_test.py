"""Test the matrix multiplication and convolution paths used by ModelScope."""
import os

os.environ.setdefault("HSA_OVERRIDE_GFX_VERSION", "10.3.0")

import sys
import torch
import torch.nn.functional as F


def main():
    print("Python:", sys.version.split()[0], flush=True)
    print("PyTorch:", torch.__version__, flush=True)
    print("ROCm build:", torch.version.hip, flush=True)
    if not torch.version.hip or not torch.cuda.is_available():
        raise RuntimeError("A working ROCm PyTorch GPU is required")
    print("GPU:", torch.cuda.get_device_name(0), flush=True)
    print("Compiled architectures:", torch.cuda.get_arch_list(), flush=True)

    with torch.inference_mode():
        for dtype in (torch.float32, torch.float16):
            a = torch.randn(512, 512, device="cuda", dtype=dtype)
            result = a @ a.T
            torch.cuda.synchronize()
            if not torch.isfinite(result).all().item():
                raise RuntimeError(f"Nonfinite {dtype} matrix output")
            print(f"{dtype} matrix multiplication: PASS", flush=True)

            x = torch.randn(1, 4, 4, 32, 32, device="cuda", dtype=dtype)
            weight = torch.randn(8, 4, 3, 3, 3, device="cuda", dtype=dtype)
            result = F.conv3d(x, weight, padding=1)
            torch.cuda.synchronize()
            if not torch.isfinite(result).all().item():
                raise RuntimeError(f"Nonfinite {dtype} convolution output")
            print(f"{dtype} 3D convolution: PASS", flush=True)
    print("GPU tests passed", flush=True)


if __name__ == "__main__":
    main()
