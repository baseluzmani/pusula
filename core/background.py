"""
Run a script in the background from a page, and follow its log.

For jobs that take minutes - checking a few dozen symbols on Yahoo and
fetching their history - which a Dash callback cannot wait for. The page
starts the job, keeps the log path, and polls ``status`` until the log's
last line is the script's finished marker:

    == finished ok
    == finished failed

The job runs with the dashboard's own Python (the venv) from the repo root,
in its own session, so restarting pusula does not kill it. Logs go to
~/pusula/logs/<name>_<time>.log, next to daily.log.

No Dash here; pages call start / status / busy.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
FINISHED = "== finished"
STALL_SECONDS = 15 * 60      # a log untouched this long counts as stopped

_children: list[subprocess.Popen] = []


def start(args: list[str], name: str) -> str:
    """Start ``python <args>`` in the background. Returns the log path."""
    _reap()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.log"
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [sys.executable, *args], cwd=ROOT, stdout=fh,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
    _children.append(proc)
    return str(log)


def status(log_path: str | None, tail: int = 40) -> dict:
    """{'text': last lines, 'finished': bool, 'ok': bool, 'stalled': bool}"""
    _reap()
    out = {"text": "", "finished": False, "ok": False, "stalled": False}
    if not log_path:
        return out
    path = Path(log_path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        age = time.time() - path.stat().st_mtime
    except OSError:
        out["text"] = "Log not found."
        out["finished"] = True
        return out
    out["text"] = "\n".join(lines[-tail:])
    last = next((ln for ln in reversed(lines) if ln.strip()), "")
    if last.startswith(FINISHED):
        out["finished"] = True
        out["ok"] = last.strip().endswith("ok")
    elif age > STALL_SECONDS:
        out["finished"] = out["stalled"] = True
    return out


def busy(name: str) -> str | None:
    """The log of a ``name`` job still running, else None - so a second
    click does not start a second copy."""
    if not LOG_DIR.exists():
        return None
    logs = sorted(LOG_DIR.glob(f"{name}_*.log"))
    if not logs:
        return None
    s = status(str(logs[-1]))
    return None if s["finished"] else str(logs[-1])


def _reap() -> None:
    """Collect finished children so they do not linger as zombies."""
    for proc in list(_children):
        if proc.poll() is not None:
            _children.remove(proc)
