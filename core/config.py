"""
Central configuration. Every path, port and secret comes from here.
Nothing else in the codebase should hardcode a file location.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = the folder containing this file's parent (i.e. ~/pusula)
ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

# --- Database ------------------------------------------------------------
# Single shared file, also used by the legacy dashboards.
# ADDITIVE SCHEMA CHANGES ONLY while both are running.
DB_PATH = Path.home() / "FTScrapper" / "data" / "funds.db"

# --- Folders -------------------------------------------------------------
INBOX_DIR = ROOT / "inbox"          # drop provider files here for importers
SCRIPTS_DIR = ROOT / "scripts"

# Legacy FTScrapper tree. Importer scripts still live there and are invoked
# in place until they are migrated. Remove once importers/ owns the logic.
LEGACY_DIR = Path.home() / "FTScrapper"

# --- App -----------------------------------------------------------------
APP_NAME = "Pusula"
PORT = 8060                          # old dashboards keep 8050-8053
DEBUG = True

# --- Secrets (never committed; live in .env) -----------------------------
OPENFIGI_API_KEY = os.environ.get("OPENFIGI_API_KEY", "")

# --- OpenFIGI ------------------------------------------------------------
FIGI_URL = "https://api.openfigi.com/v3/mapping"
FIGI_BATCH_SIZE = 100          # OpenFIGI maximum per request
FIGI_RATE_SLEEP = 1.0          # seconds between batches

# --- ETF holdings --------------------------------------------------------
AUTO_APPROVE_THRESHOLD = 0.90  # name-match confidence for auto-approval
TREND_TOP_N = 20               # holdings shown in weight-trend charts
MAP_ROW_LIMIT = 500            # rows returned to the Ticker map tab

# --- Sanity check --------------------------------------------------------
def check() -> list[str]:
    """Return a list of problems, empty if everything looks right."""
    problems = []
    if not DB_PATH.exists():
        problems.append(f"Database not found: {DB_PATH}")
    if not INBOX_DIR.exists():
        problems.append(f"Inbox folder not found: {INBOX_DIR}")
    if not OPENFIGI_API_KEY:
        problems.append("OPENFIGI_API_KEY not set in .env")
    return problems

# --- Markets --------------------------------------------------------
# Earliest date shown on price charts by default (the "From" floor).
MARKETS_CHART_START = "2020-01-01"
# Default baseline for the "Since" return column and rebased Compare chart.
MARKETS_SINCE_DEFAULT = "2026-03-01"