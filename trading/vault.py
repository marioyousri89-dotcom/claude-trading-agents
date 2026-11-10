"""Vault: holds API keys and wallet secrets so nothing else in the system sees them.

Secrets come only from environment variables (never the repo or the config
file). They are never logged or printed: repr() and status() show names only.
Paper mode never releases a secret.
"""

import os

SECRET_NAMES = ("POLYMARKET_API_KEY", "POLYMARKET_API_SECRET", "SOLANA_PRIVATE_KEY", "BROKER_API_KEY",
                "ETORO_API_KEY", "ETORO_USER_KEY", "MT5_PASSWORD", "MOOMOO_TRADE_PASSWORD")


class VaultLocked(PermissionError):
    pass


class Vault:
    def __init__(self, mode: str = "paper", env=os.environ):
        self.mode = mode
        self._secrets = {name: env[name] for name in SECRET_NAMES if env.get(name)}

    def get(self, name: str) -> str:
        if self.mode != "live":
            raise VaultLocked(f"{name} is locked: secrets are only released in live mode")
        return self._secrets[name]

    def status(self) -> dict:
        return {"mode": self.mode, "locked": self.mode != "live", "loaded": sorted(self._secrets)}

    def __repr__(self) -> str:
        return f"Vault(mode={self.mode!r}, loaded={sorted(self._secrets)}, values=<redacted>)"
