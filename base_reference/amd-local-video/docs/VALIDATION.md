# Validation of this package

Performed locally on 2026-10-24:

- Python AST syntax parsing passed for both generators and both GPU tests.
- `bash -n` passed for the setup and diagnostic scripts.
- The setup script rejected an unknown target with usage text and exit code 2.
- Git ignore checks passed for virtual environments, generated videos, latent
  checkpoints, model weights, `.env` files, and a local Hugging Face cache.
- All relative Markdown documentation links resolved.
- Text files passed a trailing-whitespace check.

The workspace was assembled on macOS, not on the Linux GPU server. Dependencies
were not installed here, and these packaged generators were not executed against
the AMD GPU. No model weights or generated media are bundled. These checks do
not prove memory fit, numerical correctness, image quality or end-to-end runtime
compatibility. The original server observations and the unverified decoder fix
are described separately in `SETUP-NOTES.md`.

Next server checks:

1. Run the setup script for the desired model.
2. Activate that model's environment and run its GPU test.
3. For Wan, run the small 448x256 / 17-frame / 20-step command in README.md.
4. Confirm both a latent checkpoint and a playable MP4 were saved.
5. Run `--decode` against that checkpoint to confirm decode-only recovery works.
6. Attempt the larger default configuration only after the small run succeeds.
