"""Wrapper around build_stock_map.py (OpenFIGI enrichment)."""
import subprocess
import sys

from core import config
from core.repo import etf as repo


def run() -> tuple[int, str]:
    from core import db
    before = db.row_count("stock_identifier_map")
    result = subprocess.run(
        [sys.executable, str(config.LEGACY_DIR / "scripts" / "build_stock_map.py")],
        cwd=str(config.LEGACY_DIR), capture_output=True, text=True, timeout=900)
    after = db.row_count("stock_identifier_map")
    repo.clear_cache()

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-400:] or "enrichment failed")
    return after - before, f"{repo.unresolved_count()} still unresolved"
