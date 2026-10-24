"""Minimal stdlib HTTP helpers (no third-party dependencies)."""

import json
import urllib.error
import urllib.parse
import urllib.request

from .errors import GateError

USER_AGENT = "claude-trading-agents-auth-gate"
TIMEOUT = 15


def _send(request: urllib.request.Request) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            status, body = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        # OAuth servers report polling states (authorization_pending etc.)
        # as 4xx responses with a JSON body, so read it instead of failing.
        status, body = err.code, err.read()
    except OSError as err:  # DNS failure, refused connection, timeout
        host = urllib.parse.urlsplit(request.full_url).netloc
        raise GateError(f"Network error contacting {host}: {err}") from None
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        data = {}
    return status, data if isinstance(data, dict) else {}


def post_form(url: str, fields: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    return _send(request)


def get_json(url: str, token: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        },
    )
    return _send(request)
