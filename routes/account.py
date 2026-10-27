"""Signing in and out, your own account and preferences, and (for admins) managing users."""
from flask import Blueprint, current_app, g, jsonify, request
import auth
import jobs
import settings
import store

bp = Blueprint('account', __name__)


def public_user(user):
    return {key: user[key] for key in ('id', 'username', 'role')}


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('Send a JSON object')
    return data


@bp.get('/login')
def login_page():
    response = current_app.send_static_file('login.html')
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.get('/api/auth/status')
def status():
    user = auth.current_user()
    return jsonify(needs_setup=store.count_users() == 0, user=public_user(user) if user else None)


@bp.post('/api/auth/setup')
def setup():
    """The first account. Only possible while there are no users at all."""
    if store.count_users():
        return jsonify(error='This site already has an admin; sign in instead'), 409
    try:
        data = body()
        username = auth.validate_username(data.get('username'))
        password = auth.validate_password(data.get('password'))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    user_id = store.create_user(username, auth.hash_password(password), 'admin')
    store.claim_orphans(user_id)
    # Chat defaults used to be server-wide settings; the first admin keeps them.
    legacy = {key: store.get_setting_raw(key) for key in settings.LEGACY_PREFS}
    store.save_prefs(user_id, {key: value for key, value in legacy.items() if value is not None})
    return auth.start_session(jsonify(user=public_user(store.get_user(user_id))), user_id)


@bp.post('/api/auth/login')
def login():
    ip = request.remote_addr or '?'
    if auth.too_many_failures(ip):
        return jsonify(error='Too many wrong passwords. Wait ten minutes and try again.'), 429
    data = request.get_json(silent=True) or {}
    username, password = data.get('username'), data.get('password')
    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify(error='Enter your username and password'), 400
    found = store.find_login(username.strip())
    # Hash even for an unknown name, so the response time doesn't reveal which names exist.
    valid = auth.check_password(password, found['password_hash'] if found else auth.DUMMY_HASH)
    if not found or not valid or found['disabled']:
        auth.record_failure(ip)
        return jsonify(error='Wrong username or password'), 401
    auth.clear_failures(ip)
    return auth.start_session(jsonify(user=public_user(found)), found['id'])


@bp.post('/api/auth/logout')
def logout():
    return auth.end_session(jsonify(ok=True))


@bp.get('/api/me')
def me():
    server = settings.get()
    return jsonify(user=public_user(g.user), prefs=settings.prefs(g.user),
                   server={key: server[key] for key in settings.PUBLIC}), 200, {'Cache-Control': 'no-store'}


@bp.patch('/api/me/prefs')
def save_prefs():
    try:
        fields = settings.validate_prefs(body())
    except ValueError as error:
        return jsonify(error=str(error)), 400
    prefs = {**settings.prefs(g.user), **fields}
    store.save_prefs(g.user['id'], prefs)
    return jsonify(prefs=prefs)


@bp.post('/api/me/password')
def change_password():
    try:
        data = body()
        new = auth.validate_password(data.get('password'))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if not auth.check_password(data.get('current') or '', store.password_hash(g.user['id'])):
        return jsonify(error='Your current password is wrong'), 403
    store.update_user(g.user['id'], {'password_hash': auth.hash_password(new)})
    # Changing it signs out every device, this one included; start a fresh session here.
    return auth.start_session(jsonify(ok=True), g.user['id'])


def media_bytes(user_id):
    total = 0
    for kind in ('image', 'video'):
        for item in store.list_media(kind, user_id):
            path = jobs.OUTPUTS / item['file']
            total += path.stat().st_size if path.is_file() else 0
    return total


@bp.get('/api/me/data')
def my_data():
    return jsonify(counts=store.counts(g.user['id']),
                   disk={'files': store.upload_bytes(g.user['id']), 'media': media_bytes(g.user['id'])})


# Admin: users

@bp.get('/api/users')
@auth.admin_only
def users():
    return jsonify(users=[{key: u[key] for key in ('id', 'username', 'role', 'disabled', 'created_at', 'chats', 'media')}
                          for u in store.list_users()])


@bp.post('/api/users')
@auth.admin_only
def add_user():
    try:
        data = body()
        username = auth.validate_username(data.get('username'))
        password = auth.validate_password(data.get('password'))
        role = data.get('role', 'user')
        if role not in ('user', 'admin'):
            raise ValueError('role must be user or admin')
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if store.find_login(username):
        return jsonify(error=f'There is already a user called {username}'), 409
    return jsonify(user=public_user(store.get_user(store.create_user(username, auth.hash_password(password), role)))), 201


@bp.patch('/api/users/<int:user_id>')
@auth.admin_only
def edit_user(user_id):
    target = store.get_user(user_id)
    if target is None:
        return jsonify(error='No such user'), 404
    try:
        data = body()
        fields = {}
        if 'role' in data:
            if data['role'] not in ('user', 'admin'):
                raise ValueError('role must be user or admin')
            fields['role'] = data['role']
        if 'disabled' in data:
            if type(data['disabled']) is not bool:
                raise ValueError('disabled must be true or false')
            fields['disabled'] = int(data['disabled'])
        if 'password' in data:
            fields['password_hash'] = auth.hash_password(auth.validate_password(data['password']))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if user_id == g.user['id'] and (fields.get('role') == 'user' or fields.get('disabled')):
        return jsonify(error="You can't take away your own admin rights or disable yourself"), 400
    store.update_user(user_id, fields)
    return jsonify(user=public_user(store.get_user(user_id)))


@bp.delete('/api/users/<int:user_id>')
@auth.admin_only
def remove_user(user_id):
    if user_id == g.user['id']:
        return jsonify(error="You can't delete your own account"), 400
    if store.get_user(user_id) is None:
        return jsonify(error='No such user'), 404
    for item in store.user_jobs(user_id, since=0):
        if item['state'] in ('queued', 'running'):
            jobs.cancel(item['id'], g.user)
    for file in store.delete_user(user_id):
        jobs.remove_media_files(file)
    return '', 204
