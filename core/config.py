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
# Shared with the legacy dashboards on 8050-8053.
# ADDITIVE SCHEMA CHANGES ONLY while both are running.
DB_MODE = os.environ.get("PUSULA_DB", "live")      # "live" or "sandbox"

_DB = {
    "live":    Path.home() / "FTScrapper" / "data" / "funds.db",
    "sandbox": ROOT / "data" / "funds_sandbox.db",
}
DB_PATH = _DB[DB_MODE]

# --- Folders -------------------------------------------------------------
INBOX_DIR = ROOT / "inbox"          # drop provider files here for importers
SCRIPTS_DIR = ROOT / "scripts"

# --- App -----------------------------------------------------------------
APP_NAME = "Pusula"
PORT = int(os.environ.get("PUSULA_PORT", 8060))
DEBUG = os.environ.get("PUSULA_DEBUG", "0") == "1"


# --- Secrets (never committed; live in .env) -----------------------------
OPENFIGI_API_KEY = os.environ.get("OPENFIGI_API_KEY", "")

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
