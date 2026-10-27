# inphAIweb

My own local AI site: chat with models from **LM Studio** and **Ollama**, generate images
(WAI-Illustrious v17, Z-Image-Turbo) and generate videos (Wan2.2 5B), all from one page.
Built for my server: Linux, an AMD RX 6700 XT 12GB with ROCm 7.1, ~40GB RAM.

Chats are saved in a SQLite file, `data/inphai.db`. Images and videos go in `data/outputs/` and
show up in each tab's gallery. The `data/` folder is ignored by Git.

## Setup

Install [uv](https://docs.astral.sh/uv/) if needed:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

Then set up the parts you want:

```sh
bash scripts/setup.sh app     # the site itself (just Flask)
bash scripts/setup.sh image   # ROCm PyTorch + image models (about 38 GB download)
bash scripts/setup.sh video   # ROCm PyTorch + Wan2.2 (downloads tens of GB on the first video)
bash scripts/setup.sh all     # everything
```

Image and video each get their own environment in `.venvs/`, because they need different
`diffusers` versions. Chat works without either of them.

## LLM servers

Both are used on their default ports, through their OpenAI-style `/v1` API.

- **Ollama** (`http://localhost:11434`): `curl -fsSL https://ollama.com/install.sh | sh`, then
  pull a model, e.g. `ollama pull qwen3:8b`. It runs as a service after installing.
- **LM Studio** (`http://localhost:1234`): download a model in the app, then start the server
  from the Developer tab (or `lms server start`). Turn on just-in-time model loading so picking a
  model in the site loads it.

Any model either server has shows up in the model picker. Press ↻ after starting a server or
downloading a model. Different addresses can be set with `OLLAMA_URL` and `LMSTUDIO_URL`.

Ollama keeps a short context window by default; long chats get cut off at the start. Raise it
with `OLLAMA_CONTEXT_LENGTH` (e.g. `8192`) in Ollama's service environment.

## Run

```sh
bash start.sh
```

Open http://localhost:5002 (or the server's address from another device on the network). Set
`PORT` to use another port.

## Notes

- One image or video generates at a time; the GPU only has 12 GB. Before starting one, the site
  unloads any model Ollama has loaded. LM Studio's models are not unloaded: eject them in LM
  Studio (or give them an idle timeout) before generating, or the GPU may run out of memory.
- Videos are slow (one 448×256, 17-frame, 20-step test took about 14 minutes). The job keeps running if
  you close the page; the sidebar shows its progress.
- Each generation's full output is saved beside the file as a `.log`.
- `generators/image/` and `generators/video/` are the scripts from my image and video projects
  (in `base_reference/`), and can still be run on their own with `--help`.
