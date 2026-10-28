"""Basic BF16 smoke test. Passing is not a guarantee of full model compatibility."""
import torch
import torch.nn.functional as F
from torch.nn.attention import SDPBackend, sdpa_kernel
from diffusers import WanPipeline, AutoencoderKLWan


def main():
    print("Wan imports: PASS", flush=True)
    print("PyTorch:", torch.__version__, flush=True)
    if not torch.version.hip or not torch.cuda.is_available():
        raise RuntimeError("A working ROCm PyTorch GPU is required")
    print("GPU:", torch.cuda.get_device_name(0), flush=True)

    with torch.inference_mode():
        a = torch.randn(256, 256, device="cuda", dtype=torch.bfloat16)
        result = a @ a.T
        torch.cuda.synchronize()
        if not torch.isfinite(result).all().item():
            raise RuntimeError("Nonfinite BF16 matrix output")
        print("BF16 matrix multiplication: PASS", flush=True)

        q = torch.randn(1, 4, 256, 64, device="cuda", dtype=torch.bfloat16)
        with sdpa_kernel(SDPBackend.MATH):
            result = F.scaled_dot_product_attention(q, q, q)
        torch.cuda.synchronize()
        if not torch.isfinite(result).all().item():
            raise RuntimeError("Nonfinite BF16 attention output")
        print("BF16 attention: PASS", flush=True)
    print("GPU smoke test passed", flush=True)


if __name__ == "__main__":
    main()
