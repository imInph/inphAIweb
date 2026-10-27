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

## Accounts

The first visit asks you to create the admin account; anything saved before accounts existed
becomes that admin's. After that everyone signs in. Admins add people (and reset passwords, make
admins, disable or delete accounts) in **Settings → Users**. Everyone's chats, files, images and
videos are private to them.

Server settings (connections, generation defaults) are admin only. Each person's chat defaults,
model options, Enter-key choice and theme are saved with their account.

Images and videos go into one queue for the GPU: they run in order, you see your place in line,
and you can cancel your own (admins can cancel anyone's).

## Chat

- **Files:** attach with the paperclip, drag them onto the chat, or paste. Images (PNG, JPEG,
  WebP) go to vision models; text, code and PDFs are read and put in front of your message.
  Files up to 25 MB; very long text is cut to fit. They're kept in `data/uploads/` with the chat.
- **Versions:** edit any of your messages or regenerate any reply and the old one is kept beside
  it (the ‹ 2/3 › arrows). Replies can be edited in place; any message can be deleted.
- **Compare:** the two-columns button asks up to two more models every message; their answers sit
  side by side until you pick one to continue with.
- **Model options** per chat (the sliders button) and as defaults for new chats (Settings):
  temperature, top P/K, min P, repeat penalty, seed, max tokens, context length, keep loaded,
  and thinking on/off. LM Studio uses the ones it supports; the rest are Ollama only.
- **Personas:** a model with its own instructions and options, picked like a model.
- **Prompts:** save text you send often and type `/` to use it (`{{date}}`, `{{time}}`,
  `{{clipboard}}` are filled in). **Memories:** facts every chat should know about you.
- **Organising:** chats are named by the model after the first reply, can be pinned, put in
  folders (drag them), archived, searched (every message, `Ctrl+K`), and exported as Markdown or
  JSON (or all at once as a .zip, which imports back).
- Replies render Markdown, maths (KaTeX), highlighted code, Mermaid diagrams, and a sandboxed
  preview for HTML/SVG code. Tokens per second are shown under each reply. `?` lists shortcuts.

## Knowledge

Make a collection on the Knowledge page and drop documents in (text, Markdown, code, PDF, Word
.docx, HTML). They're split into passages and indexed in the background with an embedding model:
by default Ollama's `nomic-embed-text`, so pull it first (`ollama pull nomic-embed-text`, or from
the Models page). In a chat, type `#` to attach a collection; every message then gets the best
matching passages (keyword and meaning combined), the model cites them as [1], [2], and a Sources
list under the reply shows them. Personas can carry collections too. A file attached to a message
that's too long for the model is answered from its best passages the same way.

The embedding model and the number of passages are admin settings (Settings → Knowledge).

## Models page

Shows what Ollama has installed (size, quantisation, and whether a model sees images, thinks or
uses tools) and what's loaded in GPU memory right now, with Unload. Admins can pull new models
(it keeps downloading if you leave the page) and delete them. LM Studio's downloaded models are
listed with whether they're loaded; LM Studio loads them itself when a chat uses one.

The model picker shows the same "sees images" / "thinks" notes, Thinking is greyed out for models
that can't think, and attaching an image to a model that can't see warns you.

## LLM servers

Both are used on their default ports: LM Studio through its OpenAI-style `/v1` API, Ollama
through its own `/api/chat` (its `/v1` ignores options like the context length).

- **Ollama** (`http://localhost:11434`): `curl -fsSL https://ollama.com/install.sh | sh`, then
  pull a model, e.g. `ollama pull qwen3:8b`. It runs as a service after installing.
- **LM Studio** (`http://localhost:1234`): download a model in the app, then start the server
  from the Developer tab (or `lms server start`). Turn on just-in-time model loading so picking a
  model in the site loads it.

Any model either server has shows up in the model picker. Press ↻ after starting a server or
downloading a model. Their addresses can be changed (and tested) in **Settings → Connections**.

Ollama keeps a short context window by default, so long chats (and big files) get cut off at the
start. Set **Context length** in a chat's options, or as a default in Settings (e.g. `16384`).

## Run

```sh
bash start.sh
```

Open http://localhost:5002 (or the server's address from another device on the network). Set
`PORT` to use another port.

## Settings

Everyone has their account (password), chat defaults, appearance, prompts, personas, memories
and their data (export, import, delete). Admins also see the server's connections, generation
defaults and the users. Your own settings follow you to every device.

## Notes

- One image or video generates at a time; the GPU only has 12 GB. Before starting one, the site
  unloads any model Ollama has loaded. LM Studio's models are not unloaded: eject them in LM
  Studio (or give them an idle timeout) before generating, or the GPU may run out of memory.
- Videos are slow (one 448×256, 17-frame, 20-step test took about 14 minutes). The job keeps running if
  you close the page; the sidebar shows its progress.
- Each generation's full output is saved beside the file as a `.log`.
- `generators/image/` and `generators/video/` are the scripts from my image and video projects
  (in `base_reference/`), and can still be run on their own with `--help`.
