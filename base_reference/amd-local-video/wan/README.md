# Simple connection to generate.py

Put `app.py` and `generate.py` together. In the Python environment where your original ROCm script works:

```sh
python -m pip install -r requirements-backend.txt
python app.py
```

`generate.py` is now **unchanged from your upload**. No extra progress system, job IDs, or custom settings limits.

## What your page calls

**`POST /generate`** — send your textbox/options as JSON:

```json
{"prompt":"A cat in a sunny garden","steps":50,"seed":42}
```

The request waits while the script runs, then returns:

```json
{
  "state": "done",
  "file_path": "/absolute/path/to/videos/run-abc/wan-123.mp4",
  "video_url": "/video/run-abc/wan-123.mp4",
  "error": null,
  "output": ["...actual script output...", "Saved video: ..."]
}
```

Use `video_url` for your video tag's `src` or link's `href`. `file_path` is the location on disk; a browser needs the HTTP URL to load it.

Accepted options: `prompt`, `negative_prompt`, `width`, `height`, `frames`, `steps`, `guidance`, `seed`, `fps`. Omitted options use the script's defaults. The script handles its existing validation; unknown keys are ignored.

**`GET /status`** — optional: read the current `state` and last 200 lines of actual script output while it runs. States: `idle`, `running`, `done`, `error`. Progress is whatever the script prints; no estimated percentage is added.

**`GET /video/<folder>/<filename>`** — serves the video using the returned `video_url`.

Only one generation can run at a time; a second request returns `409`. Script failures return `500` with the error and captured output. Keep the generation request open until it finishes.

Put your own HTML at `static/index.html` beside `app.py`, then open `http://127.0.0.1:5000/static/index.html`. That lets your page call `/generate` and `/status` directly. No HTML or JavaScript is included.

Run this as one local server process. Status resets on restart; videos stay in `videos/`. Use your existing ROCm/PyTorch/Diffusers setup; the requirements file only adds Flask.
