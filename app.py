"""inphAI: chat (LM Studio, Ollama), images and videos on one page. python app.py (port 5002)."""
import os
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException
import auth
import jobs
import knowledge
import store
import uploads
from routes import account, catalog, chat, collections, config, library, media, speech

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = uploads.MAX_BYTES + 1024 * 1024
PORT = int(os.environ.get('PORT', '5002'))
store.init()
jobs.start_worker()
knowledge.start_worker()
app.before_request(auth.guard)
for blueprint in (account.bp, catalog.bp, chat.bp, collections.bp, config.bp, library.bp, media.bp, speech.bp):
    app.register_blueprint(blueprint)


@app.errorhandler(HTTPException)
def http_error(error):
    return jsonify(error=error.description), error.code


@app.get('/')
def index():
    return app.send_static_file('index.html')


def certificate():
    """(cert, key) when HTTPS is set up: INPHAI_CERT/INPHAI_KEY, or data/certs/cert.pem and key.pem."""
    cert = os.environ.get('INPHAI_CERT') or str(store.DATA / 'certs' / 'cert.pem')
    key = os.environ.get('INPHAI_KEY') or str(store.DATA / 'certs' / 'key.pem')
    return (cert, key) if os.path.isfile(cert) and os.path.isfile(key) else None


if __name__ == '__main__':
    tls = certificate()
    print(f"inphAI on {'https' if tls else 'http'}://0.0.0.0:{PORT}", flush=True)
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True, use_reloader=False, ssl_context=tls)
