import os
from dataclasses import dataclass
from pathlib import Path

from .errors import GateError
from .providers import GitHubProvider, GoogleProvider, Provider

PROVIDERS = ("github", "google")
DEFAULT_TTL_HOURS = 12
WINDOWS = os.name == "nt"


def _default_session_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "claude-trading-agents" / "session.json"


def _inside_home(path: Path) -> bool:
    return path.expanduser().resolve().is_relative_to(Path.home().resolve())


def parse_allowlist(raw: str) -> frozenset[str]:
    entries = set()
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        prefix, _, value = item.partition(":")
        if prefix not in PROVIDERS or not value:
            raise GateError(f"Invalid allowlist entry {item!r}: use github:<username> or google:<email>")
        entries.add(item)
    return frozenset(entries)


@dataclass(frozen=True)
class Config:
    allowed: frozenset[str]
    session_path: Path
    session_ttl: int  # seconds
    github_client_id: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        allowed = parse_allowlist(os.environ.get("TRADING_GATE_ALLOWED", ""))
        if not allowed:
            # Fail closed: an empty allowlist must never mean "anyone".
            raise GateError("TRADING_GATE_ALLOWED is empty; refusing to run")
        try:
            ttl_hours = float(os.environ.get("TRADING_GATE_SESSION_HOURS", DEFAULT_TTL_HOURS))
        except ValueError:
            raise GateError("TRADING_GATE_SESSION_HOURS must be a number") from None
        if ttl_hours <= 0:
            raise GateError("TRADING_GATE_SESSION_HOURS must be positive")
        path = os.environ.get("TRADING_GATE_SESSION_FILE")
        if path and WINDOWS and not _inside_home(Path(path)):
            # POSIX permission checks don't apply on Windows; the user profile
            # is private by default, so keep the token inside it.
            raise GateError("On Windows, TRADING_GATE_SESSION_FILE must be inside your user profile folder")
        return cls(
            allowed=allowed,
            session_path=Path(path) if path else _default_session_path(),
            session_ttl=int(ttl_hours * 3600),
            github_client_id=os.environ.get("GITHUB_CLIENT_ID", ""),
            google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        )

    def provider(self, name: str) -> Provider:
        if name == "github":
            return GitHubProvider(self.github_client_id)
        if name == "google":
            return GoogleProvider(self.google_client_id, self.google_client_secret)
        raise GateError(f"Unknown provider {name!r}; choose one of {', '.join(PROVIDERS)}")
