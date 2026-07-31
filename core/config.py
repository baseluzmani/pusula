"""
Central configuration.

Locations and startup constants only. Anything you might retune — thresholds,
default dates, tax-year limits — lives in app_settings and is edited from
Data → Config; anything list-like lives in its own table. This file holds the
things that must be known before the database can be opened, which is exactly
why they cannot come from it.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = the folder containing this file's parent (i.e. ~/pusula)
ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

# --- Database ------------------------------------------------------------
# Outside any app directory: this outlived FTScrapper and should not be
# nested inside whatever replaces Pusula either.
DB_PATH = Path.home() / "data" / "funds.db"

# --- Folders -------------------------------------------------------------
INBOX_DIR = ROOT / "inbox"          # drop provider files here for importers
SCRIPTS_DIR = ROOT / "scripts"

# Absolute, off ROOT. These were relative strings that only resolved because
# the scripts were run with cwd set to the FTScrapper tree.
IMPORT_DIR  = ROOT / "data" / "etf_holdings_import" / "input"
ARCHIVE_DIR = ROOT / "data" / "etf_holdings_import" / "archive"
EXCEL_PATH  = ROOT / "data" / "Funds Database.xlsx"

# --- App -----------------------------------------------------------------
APP_NAME = "Pusula"
PORT = 8060
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
    if not IMPORT_DIR.exists():
        problems.append(f"ETF import folder not found: {IMPORT_DIR}")
    if not OPENFIGI_API_KEY:
        problems.append("OPENFIGI_API_KEY not set in .env")
    return problems