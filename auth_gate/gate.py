import functools
import sys
import time

from . import session as store
from .config import Config
from .errors import GateError
from .providers import Identity, Provider
from .session import Session

MAX_INTERVAL = 60


def run_device_flow(provider: Provider, out=print, sleep=None, clock=None) -> dict:
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    code = provider.start()
    out(f"\nTo sign in with {provider.name.title()}, open {code.verification_uri}")
    out(f"and enter the code: {code.user_code}\n")
    interval = max(code.interval, 1)
    deadline = clock() + code.expires_in
    while clock() < deadline:
        sleep(interval)
        result = provider.poll(code.device_code)
        error = result.get("error")
        if not error:
            if not result.get("access_token"):
                raise GateError("Provider returned no access token")
            return result
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = min(int(result.get("interval") or interval + 5), MAX_INTERVAL)
            continue
        if error == "access_denied":
            raise GateError("Sign-in was denied")
        if error == "expired_token":
            break
        raise GateError(f"Sign-in failed: {result.get('error_description') or error}")
    raise GateError("Sign-in code expired; run login again")


def _check_allowed(config: Config, identity: Identity) -> None:
    if identity.subject not in config.allowed:
        raise GateError(f"{identity.subject} is not on the allowlist")


def login(config: Config, provider_name: str, out=print) -> Identity:
    provider = config.provider(provider_name)
    token = run_device_flow(provider, out=out)
    access_token = token["access_token"]
    identity = provider.identify(access_token)
    if identity is None:
        raise GateError("Provider rejected the new token")
    try:
        _check_allowed(config, identity)
    except GateError:
        provider.revoke(access_token)
        raise
    now = time.time()
    ttl = config.session_ttl
    if token.get("expires_in"):
        ttl = min(ttl, int(token["expires_in"]))
    store.save(
        config.session_path,
        Session(provider.name, identity.subject, identity.display, access_token, now, now + ttl),
    )
    return identity


def logout(config: Config) -> bool:
    current = store.load(config.session_path)
    if current is None:
        return False
    try:
        config.provider(current.provider).revoke(current.access_token)
    except Exception:
        pass  # local logout must succeed even if revocation fails
    store.clear(config.session_path)
    return True


def verify(config: Config) -> Identity | None:
    """Return the current identity, or None if a fresh login is required."""
    current = store.load(config.session_path)
    if current is None:
        return None
    if current.expired():
        store.clear(config.session_path)
        return None
    # Re-check the token with the provider every run, so revoking app
    # access on GitHub/Google locks the scripts out immediately.
    identity = config.provider(current.provider).identify(current.access_token)
    if identity is None or identity.subject != current.subject:
        store.clear(config.session_path)
        return None
    _check_allowed(config, identity)
    return identity


def require_login(config: Config | None = None, provider: str | None = None, interactive: bool | None = None) -> Identity:
    """Block until an allowlisted user is signed in, or raise GateError."""
    config = config or Config.from_env()
    identity = verify(config)
    if identity is not None:
        return identity
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        raise GateError("Not signed in. Run: python -m auth_gate login")
    if provider is None:
        choice = input("Sign in with [1] GitHub or [2] Google? ").strip().lower()
        provider = {"1": "github", "2": "google"}.get(choice, choice)
    return login(config, provider)


def login_required(fn):
    """Decorator: run fn only after require_login() succeeds."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            identity = require_login()
        except GateError as err:
            sys.exit(f"Access denied: {err}")
        print(f"Signed in as {identity.display} ({identity.provider})")
        return fn(*args, **kwargs)

    return wrapper
