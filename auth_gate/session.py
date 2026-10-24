"""Session file storage. The file holds a live OAuth token, so it is owner-only."""

import json
import os
import secrets
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import GateError


@dataclass(frozen=True)
class Session:
    provider: str
    subject: str
    display: str
    access_token: str
    created_at: float
    expires_at: float

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at


def save(path: Path, session: Session) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix" and path.parent.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise GateError(f"{path.parent} is writable by other users; choose a private directory")
    # Unique name + O_EXCL + O_NOFOLLOW: a planted file or symlink can't capture the token.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(session), f)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load(path: Path) -> Session | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    if os.name == "posix":
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise GateError(f"{path} is readable by other users; delete it and log in again")
        if st.st_uid != os.getuid():
            raise GateError(f"{path} is not owned by the current user")
    try:
        return Session(**json.loads(path.read_text()))
    except (ValueError, TypeError):
        clear(path)
        return None


def clear(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
