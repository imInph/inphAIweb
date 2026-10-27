# Image Generator

A simple Flask webpage for generating images with two models:

- **Anime:** WAI-Illustrious v17
- **Realistic:** Z-Image-Turbo

## Setup

Use a Python 3.11 virtual environment with working ROCm PyTorch, then run:

```sh
bash setup.sh
```

This installs the dependencies and downloads the models (about 38 GB).

## Run

```sh
bash start.sh
```

Open http://localhost:5001, enter a prompt, pick your settings, and generate.

Model files stay in `models/`. Generated images stay in `outputs/`. Both folders are ignored by Git.
