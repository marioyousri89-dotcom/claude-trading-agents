"""Structured JSON-lines event log: printed live to stdout and appended to a file.

The file rotates at EVENTS_MAX_MB (default 50): the current file is renamed to
<name>.1 (replacing any older one) and a fresh file is started, so a 24/7 run
uses at most about twice that per shard.
"""

import json
import os
import sys
import time
from pathlib import Path


class EventLog:
    def __init__(self, shard: int | str, path: Path | None = None, stream=None, max_bytes: int | None = None):
        self.shard = shard
        self.stream = stream if stream is not None else sys.stdout
        self.path = path
        self.max_bytes = max_bytes if max_bytes is not None else int(float(os.environ.get("EVENTS_MAX_MB", 50)) * 1e6)
        self.file = None
        self.size = 0
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.file = open(path, "a", buffering=1, encoding="utf-8")
            self.size = path.stat().st_size

    def _rotate(self) -> None:
        self.file.close()
        os.replace(self.path, self.path.with_name(self.path.name + ".1"))
        self.file = open(self.path, "a", buffering=1, encoding="utf-8")
        self.size = 0

    def emit(self, event_type: str, **fields) -> dict:
        event = {"ts": round(time.time(), 3), "shard": self.shard, "type": event_type, **fields}
        line = json.dumps(event, separators=(",", ":"))
        if self.stream:
            print(line, file=self.stream, flush=True)
        if self.file:
            if self.max_bytes and self.size + len(line) + 1 > self.max_bytes and self.size:
                self._rotate()
            self.file.write(line + "\n")
            self.size += len(line.encode()) + 1
        return event

    def close(self) -> None:
        if self.file:
            self.file.close()
