"""
Reference data: lookup tables that were previously buried inside importer
scripts. These are data, not logic, so they live in one place where they can
be edited without touching code.
"""

# --- Rows to skip on import ---------------------------------------------
# Currencies, FX pairs and cash placeholders that appear in holdings files.
SKIP_TICKERS = {
    "AUD", "BRL", "CAD", "CHF", "CZK", "DKK", "EUR", "GBP", "HKD", "IDR",
    "INR", "JPY", "KRW", "MXN", "MYR", "NOK", "NZD", "PLN", "SEK", "SGD",
    "THB", "TRY", "TWD", "USD", "ZAR",
    "-", "$", "CASH", "XONE",
}

SKIP_ASSET_CLASSES = {
    "cash and/or derivatives", "cash", "futures", "options",
    "cash collateral and margins",
}

# Sheets in the historical workbook that hold NAV/price data, not holdings.
SKIP_SHEETS = {
    "Sheet1", "Sheet2", "Sheet9", "AINF", "DFEU", "MINE", "SL Funds",
    "HSBC Pension", "Burcu Dashboard", "Ahmet Dashboard",
}

# --- OpenFIGI exchange code -> Yahoo Finance suffix ----------------------
EXCH_TO_YAHOO = {
    "US": "",   "UQ": "",    "UN": "",    "UP": "",    "UA": "",  "UR": "",
    "LN": ".L", "LX": ".L",
    "GY": ".DE", "GF": ".F", "GS": ".SG",
    "FP": ".PA", "IM": ".MI", "NA": ".AS", "SM": ".MC", "SS": ".ST",
    "FH": ".HE", "DC": ".CO", "BB": ".BR", "PW": ".WA",
    "KS": ".KS", "KQ": ".KQ", "JT": ".T",  "TT": ".TW", "HK": ".HK",
    "AU": ".AX", "SP": ".SI", "SJ": ".JO",
    "CN": ".TO", "CV": ".V",
    "IT": ".TA", "IN": ".NS", "IB": ".BO",
    "MK": ".KL", "TB": ".BK", "ID": ".JK",
}

# --- Display -------------------------------------------------------------
# Prefixes and suffixes stripped when showing a fund_id to a human.
DISPLAY_STRIP = ("YF:", ".L", ".IS")


def short_name(fund_id: str) -> str:
    """YF:SPGP.L -> SPGP"""
    out = str(fund_id or "")
    for token in DISPLAY_STRIP:
        out = out.replace(token, "")
    return out
