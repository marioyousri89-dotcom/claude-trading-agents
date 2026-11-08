"""Run the whole system in one container, for cloud hosts like Render.

Starts the 3 engine shards (750 agents), the 8-bot ring and the Whole Matrix desk as child processes,
restarts any that crash (with backoff), and serves the dashboard in this
process on $PORT with password login. Child output goes to stdout, so the
host's log viewer shows the live JSON event stream.

Environment: PORT (set by the host), DASHBOARD_PASSWORD, SESSION_SECRET,
AGENT_COUNT (750), EVENTS_DIR (/events), plus anything the engine/ring accept.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .dashboard import serve
from .webauth import WebAuth

SHARDS = 3
MAX_BACKOFF = 60


@dataclass
class Child:
    name: str
    cmd: list[str]
    env: dict
    proc: subprocess.Popen | None = None
    restarts: int = 0
    next_start: float = 0.0
    done: bool = False
    started_at: float = field(default=0.0)


class Supervisor:
    def __init__(self, children: list[Child], clock=time.monotonic, log=print):
        self.children = children
        self.clock = clock
        self.log = log
        self.stopping = False

    def start(self, child: Child) -> None:
        child.proc = subprocess.Popen(child.cmd, env=child.env)
        child.started_at = self.clock()
        self.log(f"supervisor: started {child.name} (pid {child.proc.pid})", flush=True)

    def check(self) -> None:
        """Start, reap and restart children. Call regularly."""
        now = self.clock()
        for child in self.children:
            if child.done or self.stopping:
                continue
            if child.proc is None:
                if now >= child.next_start:
                    self.start(child)
                continue
            code = child.proc.poll()
            if code is None:
                if now - child.started_at > MAX_BACKOFF:
                    child.restarts = 0  # stable again: reset the backoff
                continue
            if code == 0:
                child.done = True  # a bounded run (TICKS > 0) finished normally
                self.log(f"supervisor: {child.name} finished", flush=True)
                continue
            child.restarts += 1
            delay = min(MAX_BACKOFF, 2 ** (child.restarts - 1))
            child.proc, child.next_start = None, now + delay
            self.log(f"supervisor: {child.name} exited with {code}; restarting in {delay}s", flush=True)

    def stop(self, timeout: float = 10) -> None:
        self.stopping = True
        procs = [c.proc for c in self.children if c.proc and c.proc.poll() is None]
        for p in procs:
            p.terminate()
        deadline = time.monotonic() + timeout
        for p in procs:
            try:
                p.wait(max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()


def build_children(env: dict) -> list[Child]:
    base = {**env, "SHARD_COUNT": str(SHARDS)}
    children = [Child(f"engine-{i}", [sys.executable, "-m", "trading.engine"], {**base, "SHARD_ID": str(i)})
                for i in range(SHARDS)]
    children.append(Child("ring", [sys.executable, "-m", "trading.ring"], dict(base)))
    children.append(Child("matrix", [sys.executable, "-m", "trading.matrix"], dict(base)))
    return children


def main() -> None:
    env = dict(os.environ)
    events_dir = Path(env.setdefault("EVENTS_DIR", "/events"))
    events_dir.mkdir(parents=True, exist_ok=True)
    host = env.get("DASHBOARD_HOST", "0.0.0.0")
    port = int(env.get("PORT") or env.get("DASHBOARD_PORT") or 8080)
    auth = WebAuth.from_env(host, env)  # fails closed before anything starts
    server = serve(events_dir, host, port, auth)
    supervisor = Supervisor(build_children(env))

    def shutdown(*_):
        supervisor.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    def watch():
        while not supervisor.stopping:
            supervisor.check()
            time.sleep(1)

    threading.Thread(target=watch, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
