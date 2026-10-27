"""Accounts: password hashing, session cookies, the login guard and its rate limit."""
from collections import defaultdict, deque
from functools import wraps
import hashlib
import hmac
import re
import secrets
import threading
import time
from flask import g, jsonify, redirect, request
import store

COOKIE = 'inphai_session'
SESSION_DAYS = 30
USERNAME = re.compile(r'^[\w.\-]{2,32}$')
# scrypt with these costs takes ~50 ms and 16 MB: slow to guess, fast enough to log in.
SCRYPT = {'n': 2 ** 14, 'r': 8, 'p': 1}
# Pages and endpoints that work without signing in. Everything else needs a session.
OPEN_PATHS = {'/login', '/api/auth/status', '/api/auth/login', '/api/auth/setup'}
# A cross-site form can't set a custom header, so requiring one stops CSRF.
CSRF_HEADER = 'X-Requested-With'
FAILED_LIMIT, FAILED_WINDOW = 8, 10 * 60
failed = defaultdict(deque)
failed_lock = threading.Lock()


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def check_password(password, stored):
    try:
        _, n, r, p, salt, digest = stored.split('$')
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32,
                                   n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


# Checked against when the username doesn't exist, so a wrong name takes as long as a wrong password.
DUMMY_HASH = hash_password(secrets.token_hex(8))


def validate_username(username):
    if not isinstance(username, str) or not USERNAME.match(username.strip()):
        raise ValueError('Usernames are 2 to 32 letters, digits, dots, dashes or underscores')
    return username.strip()


def validate_password(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 200:
        raise ValueError('Passwords are 8 to 200 characters')
    return password


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(response, user_id):
    token = secrets.token_urlsafe(32)
    expires = time.time() + SESSION_DAYS * 86400
    store.add_session(token_hash(token), user_id, expires)
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite='Lax',
                        secure=request.is_secure, path='/')
    return response


def end_session(response):
    token = request.cookies.get(COOKIE)
    if token:
        store.delete_session(token_hash(token))
    response.delete_cookie(COOKIE, path='/')
    return response


def current_user():
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    user, expires = store.session_user(token_hash(token))
    # Sliding expiry, written at most once a day.
    if user and expires - time.time() < (SESSION_DAYS - 1) * 86400:
        store.extend_session(token_hash(token), time.time() + SESSION_DAYS * 86400)
    return user


def too_many_failures(ip):
    with failed_lock:
        attempts = failed[ip]
        while attempts and attempts[0] < time.time() - FAILED_WINDOW:
            attempts.popleft()
        return len(attempts) >= FAILED_LIMIT


def record_failure(ip):
    with failed_lock:
        failed[ip].append(time.time())


def clear_failures(ip):
    with failed_lock:
        failed.pop(ip, None)


def guard():
    """before_request: sign-in for everything but the login page, and the CSRF header on writes."""
    path = request.path
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get(CSRF_HEADER) != 'inphai':
        return jsonify(error='Missing the X-Requested-With: inphai header'), 403
    if path.startswith('/static/') or path in OPEN_PATHS:
        return None
    g.user = current_user()
    if g.user is None:
        if path.startswith('/api/') or path.startswith('/media/'):
            return jsonify(error='Sign in first'), 401
        return redirect('/login')
    return None


def admin_only(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user['role'] != 'admin':
            return jsonify(error='Only an admin can do that'), 403
        return view(*args, **kwargs)
    return wrapper
