"""The agent-count control: the dashboard slider writes it, every engine shard reads it.

It's one small JSON file, `control.json`, next to the event logs:
  {"per_department": 500}
Engines grow or shrink their team to match without restarting, and every agent
that stays keeps its brain.
"""

import json
import os
from pathlib import Path

from .org import DEPARTMENTS, MIN_DEPARTMENT_SIZE

MAX_DEPARTMENT_SIZE = 2500  # 10,000 agents in all; more slows a tick past the 1.5 s loop target
FILENAME = "control.json"


def validate(per_department) -> int:
    if isinstance(per_department, bool) or not isinstance(per_department, int):
        raise ValueError("per_department must be a whole number")
    if not MIN_DEPARTMENT_SIZE <= per_department <= MAX_DEPARTMENT_SIZE:
        raise ValueError(f"per_department must be between {MIN_DEPARTMENT_SIZE} and {MAX_DEPARTMENT_SIZE}")
    return per_department


def total(per_department: int) -> int:
    return per_department * len(DEPARTMENTS)


def read(directory: Path) -> int | None:
    """The requested agents per department, or None if unset or unreadable."""
    try:
        data = json.loads((directory / FILENAME).read_text(encoding="utf-8"))
        return validate(data.get("per_department"))
    except (OSError, ValueError, AttributeError):
        return None


def write(directory: Path, per_department: int) -> None:
    per_department = validate(per_department)
    tmp = directory / (FILENAME + ".tmp")
    tmp.write_text(json.dumps({"per_department": per_department}), encoding="utf-8")
    os.replace(tmp, directory / FILENAME)  # atomic, so engines never read half a file
