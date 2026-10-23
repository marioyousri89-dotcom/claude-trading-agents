"""Structured JSON-lines event log: printed live to stdout and appended to a file."""

import json
import sys
import time
from pathlib import Path


class EventLog:
    def __init__(self, shard: int, path: Path | None = None, stream=None):
        self.shard = shard
        self.stream = stream if stream is not None else sys.stdout
        self.file = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.file = open(path, "a", buffering=1, encoding="utf-8")

    def emit(self, event_type: str, **fields) -> dict:
        event = {"ts": round(time.time(), 3), "shard": self.shard, "type": event_type, **fields}
        line = json.dumps(event, separators=(",", ":"))
        if self.stream:
            print(line, file=self.stream, flush=True)
        if self.file:
            self.file.write(line + "\n")
        return event

    def close(self) -> None:
        if self.file:
            self.file.close()
