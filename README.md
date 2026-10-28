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
bash scripts/setup.sh app     # the site itself (Flask, pypdf, numpy)
bash scripts/setup.sh image   # ROCm PyTorch + image models (about 38 GB download)
bash scripts/setup.sh video   # ROCm PyTorch + Wan2.2 (downloads tens of GB on the first video)
bash scripts/setup.sh voice   # Whisper for the microphone (CPU only)
bash scripts/setup.sh all     # everything
```

Image and video each get their own environment in `.venvs/`, because they need different
`diffusers` versions. Chat works without either of them.

### Other ROCm GPUs

Image and video setup checks which AMD GPU you have (with `rocminfo`) and saves what it needs in
`.venvs/gpu.env`, which `start.sh` loads. Cards ROCm supports get nothing extra. RDNA2 cards it
doesn't support, like my RX 6700 XT (gfx1031), get `HSA_OVERRIDE_GFX_VERSION=10.3.0`. To pick
it yourself, or to redo only this step:

```sh
bash scripts/setup.sh gpu                     # detect again
bash scripts/setup.sh gpu --override 10.3.0   # force an override
bash scripts/setup.sh gpu --no-override       # force none
```

The `--override`/`--no-override` flags work on `image`, `video` and `all` too. PyTorch is the
ROCm 7.1 build, which brings its own ROCm libraries but needs a recent enough AMD driver. The
memory settings are made for 12 GB: bigger cards work (just not as fast as they could), and
smaller ones may run out of memory.

**Updating an older install:** run `bash scripts/setup.sh gpu` once and restart, since the
override used to be built into the scripts.

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

## Web search

The globe in the composer searches the web for your messages: the model writes a search query,
the site asks [SearXNG](https://docs.searxng.org/) (a metasearch engine you run yourself), reads
the top pages and gives the model their best passages as numbered sources. Write `#` and a link
(`#https://example.com/article`) in a message to have that page read into it.

Run SearXNG with Docker on port 8080:

```sh
mkdir -p ~/searxng && docker run -d --name searxng --restart unless-stopped \
  -p 8080:8080 -v ~/searxng:/etc/searxng searxng/searxng
```

Then switch on its JSON output: in `~/searxng/settings.yml` find `search:` → `formats:` and add
`- json` under `- html`, and `docker restart searxng`. The address and the number of pages read
are admin settings (Settings → Web search). Only admins can make the server open pages on your own
network (so nobody can point it at your router or the LLM servers).

## Images and videos from a chat

Start a message with `/image` (anime, WAI-Illustrious), `/photo` (realistic, Z-Image-Turbo) or
`/video` (Wan2.2) and what to make: it joins the GPU queue, the reply shows its progress, and the
picture or clip appears in the chat (and in the Images or Videos gallery). "Image of this" under a
reply starts an `/image` from it, and "Ask about this" in the Images gallery starts a chat with
that image attached (for a model that sees images).

## Tools

For models that can call tools (the Models page marks them "uses tools"), the wrench in the
composer lets the model decide on its own to search the web, open a page, search your documents,
calculate, check the date and time, or draw an image (queued on the GPU and attached to the reply
when done). Up to five rounds of tools per reply; each step is listed above the answer. Only these
built-in tools exist: nothing a user writes runs on the server.

## Voice

- **Speak a message:** the microphone in the composer records you and Whisper (faster-whisper,
  on the CPU so the GPU stays free) turns it into text in the message box. Set it up with
  `bash scripts/setup.sh voice`; the model (default `small`, an admin setting) downloads from
  Hugging Face on the first recording.
- **Read aloud:** "Read aloud" under any reply, or switch on reading every reply, in Settings →
  Voice. It uses your device's own voices, so pick one per device there.

## HTTPS (needed for the microphone from other devices)

Browsers only allow the microphone, installing the site as an app and notifications on HTTPS, or at
`localhost` on the server itself. To use them from your phone, give the site a certificate: put
`cert.pem` and `key.pem` in `data/certs/` (or point `INPHAI_CERT` and `INPHAI_KEY` at them) and
`bash start.sh` serves HTTPS on the same port.

With [mkcert](https://github.com/FiloSottile/mkcert), on the server:

```sh
mkcert -install
mkdir -p data/certs
mkcert -cert-file data/certs/cert.pem -key-file data/certs/key.pem localhost 127.0.0.1 192.168.1.50 myserver.local
```

(use your server's own address and name), then install mkcert's root certificate on the phone
(the `rootCA.pem` in `mkcert -CAROOT`) so it trusts it. Or put the site behind Tailscale's HTTPS
(`tailscale serve`, see Tailscale's docs) and skip certificates entirely.

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

## Start on boot

`scripts/inphai.service` is a systemd unit. Change `YOUR_USER` (three places), then:

```sh
sudo cp scripts/inphai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inphai
journalctl -u inphai -f          # the log
sudo systemctl restart inphai    # after an update, or to finish a restore
```

Put `PORT`, `SEARXNG_URL` and the like on `Environment=` lines in the unit.

## Install as an app

Over HTTPS (or at `localhost`), the browser offers to install inphAI: "Add to Home Screen" on a
phone, the install button in the address bar on a computer. The app keeps only its own files
(page, styles, scripts, icons) so it opens fast. Chats, images and videos always come from the
server, so it still needs the server to be reachable.

When you queue an image or video, the browser asks once whether inphAI may send notifications.
Allow it and you get one when the job is done (or fails) while the page is in the background;
tapping it opens the chat or gallery it belongs to.

## Backup and restore

**Settings → Backup** (admins): **Download a backup** gives one .zip with the database (every
account, chat, setting, prompt and memory), uploaded files and knowledge documents. Tick
**Include images and videos** to add `data/outputs/` too (it can be big).

**Restore a backup** uploads such a .zip, checks it, and sets it aside. Restart inphAI to finish
(`sudo systemctl restart inphai`, or stop and start `start.sh`). What was there before is moved
to `data/before-restore-<date>/`, not deleted; delete that folder yourself once you're happy. A
backup without images and videos leaves the current ones where they are.

## Settings

Everyone has their account (password), chat defaults, appearance, prompts, personas, memories
and their data (export, import, delete). Admins also see the server's connections, generation
defaults, the users and backups. Your own settings follow you to every device.

## Notes

- One image or video generates at a time; the GPU only has 12 GB. Before starting one, the site
  unloads any model Ollama has loaded. LM Studio's models are not unloaded: eject them in LM
  Studio (or give them an idle timeout) before generating, or the GPU may run out of memory.
- Videos are slow (one 448×256, 17-frame, 20-step test took about 14 minutes). The job keeps running if
  you close the page; the sidebar shows its progress.
- Each generation's full output is saved beside the file as a `.log`.
- `generators/image/` and `generators/video/` are the scripts from my image and video projects
  (in `base_reference/`), and can still be run on their own with `--help` (load
  `.venvs/gpu.env` first, e.g. `set -a; . .venvs/gpu.env; set +a`).
