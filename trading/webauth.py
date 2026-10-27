"""Password login for the dashboard when it is reachable from the internet.

- One password, from DASHBOARD_PASSWORD (Render generates it; it is never in the repo).
- Sessions are signed cookies (HMAC-SHA256) that last SESSION_DAYS (default 30), so
  the home-screen app stays signed in. The signing key mixes SESSION_SECRET with the
  password, so changing either signs everyone out.
- Failed logins are rate limited per client and globally.
- Fails closed: with auth on and no password set, the dashboard refuses to start.

Auth is off only when DASHBOARD_AUTH=off (local Docker, port bound to localhost)
or when the dashboard listens on a loopback address.
"""

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

COOKIE = "ats_session"
MIN_PASSWORD_LENGTH = 12
PER_CLIENT_FAILURES = 5
GLOBAL_FAILURES = 50
FAILURE_WINDOW = 15 * 60
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class WebAuth:
    def __init__(self, password: str | None, secret: str | None, enabled: bool = True,
                 session_days: float = 30, clock=time.time):
        if enabled and (not password or len(password) < MIN_PASSWORD_LENGTH):
            raise SystemExit(f"DASHBOARD_PASSWORD must be set (at least {MIN_PASSWORD_LENGTH} characters) "
                             "before the dashboard can be reachable from the internet.")
        self.enabled = enabled
        self.ttl = int(session_days * 86400)
        self.clock = clock
        self._password_digest = hashlib.sha256((password or "").encode()).digest()
        # Without SESSION_SECRET a random key is used, so a restart signs everyone out.
        base = (secret or secrets.token_hex(32)).encode()
        self._key = hmac.new(base, self._password_digest, hashlib.sha256).digest()
        self._failures: dict[str, deque] = defaultdict(deque)
        self._global: deque = deque()

    @classmethod
    def from_env(cls, host: str, env=os.environ) -> "WebAuth":
        enabled = env.get("DASHBOARD_AUTH", "on").lower() != "off" and host not in LOOPBACK
        return cls(env.get("DASHBOARD_PASSWORD"), env.get("SESSION_SECRET"), enabled,
                   float(env.get("SESSION_DAYS", 30)))

    @property
    def max_age(self) -> int:
        return self.ttl

    def check_password(self, attempt: str) -> bool:
        return hmac.compare_digest(hashlib.sha256(attempt.encode()).digest(), self._password_digest)

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        payload = f"{int(self.clock()) + self.ttl}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def valid(self, token: str | None) -> bool:
        if not self.enabled:
            return True
        if not token or token.count(".") != 2:
            return False
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(payload)):
            return False
        try:
            return int(payload.split(".", 1)[0]) > self.clock()
        except ValueError:
            return False

    def _prune(self, q: deque) -> None:
        cutoff = self.clock() - FAILURE_WINDOW
        while q and q[0] < cutoff:
            q.popleft()

    def allowed(self, client: str) -> bool:
        q = self._failures[client]
        self._prune(q)
        self._prune(self._global)
        return len(q) < PER_CLIENT_FAILURES and len(self._global) < GLOBAL_FAILURES

    def record_failure(self, client: str) -> None:
        now = self.clock()
        self._failures[client].append(now)
        self._global.append(now)

    def record_success(self, client: str) -> None:
        self._failures.pop(client, None)
