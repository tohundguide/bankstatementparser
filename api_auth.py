"""
API Authentication & Rate Limiting
====================================
Lightweight API key auth + sliding-window rate limiter for the
Bank Statement Parser public API.

Keys are loaded from the API_KEYS environment variable:
    API_KEYS=key1:MyApp,key2:ClientCA

Usage:
    from api_auth import require_api_key

    @app.route('/api/v1/parse', methods=['POST'])
    @require_api_key
    def api_parse():
        # request._api_key_label is available here
        ...
"""

import os
import time
import functools
from flask import request, jsonify


# ── Load API keys from env ──
def _load_api_keys():
    """Parse API_KEYS env var into a dict: {key: label}."""
    raw = os.environ.get('API_KEYS', '').strip()
    if not raw:
        return {}
    keys = {}
    for entry in raw.split(','):
        entry = entry.strip()
        if not entry:
            continue
        if ':' in entry:
            key, label = entry.split(':', 1)
            keys[key.strip()] = label.strip()
        else:
            keys[entry] = 'unnamed'
    return keys


_api_keys = _load_api_keys()


def reload_keys():
    """Re-read keys from env (useful after hot-reload)."""
    global _api_keys
    _api_keys = _load_api_keys()


def is_valid_key(key: str) -> bool:
    """Check if a key is valid."""
    return key in _api_keys


def get_key_label(key: str) -> str:
    """Return the label for a key."""
    return _api_keys.get(key, 'unknown')


# ── Sliding-window rate limiter (in-memory) ──
_rate_windows: dict[str, list[float]] = {}
_RATE_LIMIT = int(os.environ.get('API_RATE_LIMIT', '30'))  # requests per hour
_WINDOW_SECONDS = 3600  # 1 hour


def _check_rate_limit(key: str) -> tuple[bool, int, int]:
    """
    Check if key is within rate limit.
    Returns: (allowed, remaining, reset_seconds)
    """
    now = time.time()
    cutoff = now - _WINDOW_SECONDS

    # Clean old entries
    if key in _rate_windows:
        _rate_windows[key] = [t for t in _rate_windows[key] if t > cutoff]
    else:
        _rate_windows[key] = []

    current_count = len(_rate_windows[key])

    if current_count >= _RATE_LIMIT:
        # Find when the oldest request in window will expire
        oldest = _rate_windows[key][0]
        reset_in = int(oldest + _WINDOW_SECONDS - now) + 1
        return False, 0, reset_in

    # Allow and record
    _rate_windows[key].append(now)
    remaining = _RATE_LIMIT - current_count - 1
    return True, remaining, _WINDOW_SECONDS


# ── Decorator ──
def require_api_key(f):
    """
    Flask route decorator that enforces API key auth + rate limiting.

    Looks for the key in:
      1. X-API-Key header
      2. api_key query parameter

    On success, sets:
      - request._api_key = the key string
      - request._api_key_label = the label
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # Extract key
        api_key = request.headers.get('X-API-Key', '').strip()
        if not api_key:
            api_key = request.args.get('api_key', '').strip()

        if not api_key:
            return jsonify({
                'error': 'Missing API key. Include X-API-Key header or api_key query parameter.',
                'code': 'MISSING_KEY',
                'docs': '/api/v1/docs',
            }), 401

        if not is_valid_key(api_key):
            return jsonify({
                'error': 'Invalid API key.',
                'code': 'INVALID_KEY',
                'docs': '/api/v1/docs',
            }), 403

        # Rate limit check
        allowed, remaining, reset_in = _check_rate_limit(api_key)
        if not allowed:
            resp = jsonify({
                'error': f'Rate limit exceeded. Try again in {reset_in} seconds.',
                'code': 'RATE_LIMITED',
                'retry_after': reset_in,
            })
            resp.headers['Retry-After'] = str(reset_in)
            resp.headers['X-RateLimit-Limit'] = str(_RATE_LIMIT)
            resp.headers['X-RateLimit-Remaining'] = '0'
            return resp, 429

        # Attach metadata to request
        request._api_key = api_key
        request._api_key_label = get_key_label(api_key)

        # Call the actual route
        response = f(*args, **kwargs)

        # Attach rate limit headers to successful responses
        if hasattr(response, 'headers'):
            response.headers['X-RateLimit-Limit'] = str(_RATE_LIMIT)
            response.headers['X-RateLimit-Remaining'] = str(remaining)
        elif isinstance(response, tuple) and len(response) >= 1:
            resp_obj = response[0]
            if hasattr(resp_obj, 'headers'):
                resp_obj.headers['X-RateLimit-Limit'] = str(_RATE_LIMIT)
                resp_obj.headers['X-RateLimit-Remaining'] = str(remaining)

        return response

    return decorated
