"""Login security gate for the trading-agents CLI scripts (GitHub / Google OAuth)."""

from .config import Config
from .errors import GateError
from .gate import login, login_required, logout, require_login, verify
from .providers import Identity

__all__ = ["Config", "GateError", "Identity", "login", "login_required", "logout", "require_login", "verify"]
