"""
Wrapper around the existing ETF holdings importer script.

The parsing logic stays where it is for now; this only gives the Data page a
uniform entry point and makes every run appear in job_runs.
"""
import subprocess
import sys

from core import config
from core.repo import etf as repo


def run() -> tuple[int, str]:
    before = _row_count()
    result = subprocess.run(
        [sys.executable, str(config.SCRIPTS_DIR / "import_etf_holdings.py")],
        cwd=str(config.ROOT), capture_output=True, text=True, timeout=900)
    after = _row_count()
    repo.clear_cache()

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-400:] or "importer failed")
    tail = [l for l in result.stdout.strip().splitlines() if l.strip()][-1:]
    return after - before, tail[0] if tail else "done"


def _row_count() -> int:
    from core import db
    return db.row_count("etf_holdings")
