"""inphAI: chat (LM Studio, Ollama), images and videos on one page. python app.py (port 5002)."""
import os
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException
import store
import uploads
from routes import chat, config, media

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = uploads.MAX_BYTES + 1024 * 1024
PORT = int(os.environ.get('PORT', '5002'))
store.init()
for blueprint in (chat.bp, config.bp, media.bp):
    app.register_blueprint(blueprint)


@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify(error=error.description), error.code


@app.get('/')
def index():
    return app.send_static_file('index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True, use_reloader=False)
