"""Run generate.py and pass its output back to your page."""
from collections import deque
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)
BASE = Path(__file__).resolve().parent
SCRIPT = BASE / "generate.py"
VIDEOS = BASE / "static" / "output"
run_lock = threading.Lock()
output = deque(maxlen=200)
status = {"state": "idle", "file_path": None, "video_url": None, "error": None}
OPTIONS = ("prompt", "negative_prompt", "width", "height", "frames", "steps",
           "guidance", "seed", "fps")


@app.post("/generate")
def generate():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify(error="Send a JSON object"), 400
    if not run_lock.acquire(blocking=False):
        return jsonify(error="The script is already running"), 409

    try:
        output.clear()
        status.update(state="running", file_path=None, video_url=None, error=None)
        VIDEOS.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="run-", dir=VIDEOS))
        command = [sys.executable, "-u", str(SCRIPT), f"--output-dir={folder}"]
        # Only pass supported options. generate.py handles their types/defaults.
        for name in OPTIONS:
            if name in data:
                command.append(f"--{name.replace('_', '-')}={data[name]}")

        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, text=True,
                              encoding="utf-8", errors="replace") as process:
            with process.stdout:
                for line in process.stdout:
                    output.append(line.rstrip())
            code = process.wait()
        if code:
            raise RuntimeError(f"generate.py exited with code {code}; see output")
        video = next(folder.glob("*.mp4"), None)
        if video is None or video.stat().st_size == 0:
            raise RuntimeError("The script finished without producing a video")
        status.update(state="done", file_path=str(video),
                      video_url=f"/video/{folder.name}/{video.name}")
        return jsonify(**status, output=list(output))
    except Exception as error:
        status.update(state="error", error=str(error))
        return jsonify(**status, output=list(output)), 500
    finally:
        run_lock.release()


@app.get("/status")
def get_status():
    return jsonify(**status, output=list(output)), 200, {"Cache-Control": "no-store"}


@app.get("/video/<path:filename>")
def video(filename):
    return send_from_directory(VIDEOS, filename, mimetype="video/mp4", conditional=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)
