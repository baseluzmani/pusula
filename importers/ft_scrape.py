"""
Scraping FT Markets fund pages.

Given a fund identifier, fetch and parse. Knows nothing about the database -
that separation was in the original and is worth keeping, because it means the
parsing can be tested against saved HTML without a database anywhere near it.

The session cookie now comes from the environment rather than a literal in
config.py. It is a credential and it expires, so it belongs in .env where it
can be rotated without editing code or leaving it in git history.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup
from requests.exceptions import RequestException

BASE = "https://markets.ft.com/data/funds/tearsheet"

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/122.0.0.0 Safari/537.36")


def headers() -> dict:
    """Request headers, with the FT session cookie from the environment.

    Read per-request rather than at import, so rotating the cookie in .env
    takes effect on the next run instead of needing a restart.
    """
    cookie = os.environ.get("FT_COOKIE", "")
    if not cookie:
        print("  WARNING: FT_COOKIE not set - FT will likely refuse the request")
    return {"User-Agent": USER_AGENT,
            "Accept-Language": "en-GB,en;q=0.9",
            "Cookie": cookie}


def _price_url(fund_id, start, end) -> str:
    return (f"{BASE}/historical?s={fund_id}"
            f"&startDate={start:%d/%m/%Y}&endDate={end:%d/%m/%Y}")


def _holdings_url(fund_id) -> str:
    # A holdings_id can be a full URL when the fund's holdings live elsewhere.
    if str(fund_id).startswith("http"):
        return fund_id
    return f"{BASE}/holdings?s={fund_id}"


def fetch(url, tries=3) -> str:
    """GET with backoff. FT drops connections under load fairly often, and a
    single failure should not lose the whole run."""
    last = None
    for attempt in range(tries):
        try:
            response = requests.get(url, headers=headers(), timeout=(8, 30))
            response.raise_for_status()
            return response.text
        except RequestException as exc:
            last = exc
            print(f"  attempt {attempt + 1}/{tries} failed: {type(exc).__name__}")
            time.sleep(2 ** attempt)
    raise last


def fund_name(html, fallback) -> str:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else ""
    return name or fallback


def parse_prices(html) -> list:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table",
                      {"class": "mod-tearsheet-historical-prices__results"})
    if not table:
        print("  WARNING: price table not found on page")
        return []

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) != 6:
            continue
        try:
            date_text = cells[0].find(
                "span", {"class": "mod-ui-hide-small-below"}).get_text(strip=True)
            date = datetime.strptime(date_text, "%A, %B %d, %Y")
        except (AttributeError, ValueError):
            continue

        def number(cell, cast=float):
            text = cell.get_text(strip=True).replace(",", "")
            try:
                return cast(text or 0)
            except ValueError:
                return cast(0)

        volume_cell = cells[5].find("span",
                                    {"class": "mod-ui-hide-small-below"})
        rows.append({
            "date": date.strftime("%Y-%m-%d"),
            "open": number(cells[1]), "high": number(cells[2]),
            "low": number(cells[3]), "close": number(cells[4]),
            "volume": number(volume_cell, int) if volume_cell else 0,
        })
    return rows


def parse_holdings(html) -> tuple:
    """
    Top holdings, and the underlying fund id if this is a feeder.

    A feeder fund holds one position at ~100%, so its holdings page shows the
    wrapper rather than anything useful. Detecting that lets the caller follow
    through to the fund that actually holds securities.
    """
    soup = BeautifulSoup(html, "lxml")

    table = None
    for candidate in soup.find_all("table"):
        head = [th.get_text(strip=True) for th in candidate.find_all("th")]
        if "Company" in head and "Portfolio weight" in head:
            table = candidate
            break
    if not table:
        print("  WARNING: holdings table not found on page")
        return [], None

    body = table.find("tbody")
    rows = body.find_all("tr") if body else table.find_all("tr")

    holdings = []
    for i, tr in enumerate(rows):
        if tr.find("th"):
            continue
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue

        name_cell = cells[0]
        link = name_cell.find("a")
        name = (link.get_text(strip=True) if link
                else name_cell.get_text(separator=" ", strip=True
                                        ).split("\n")[0].strip())
        if not name:
            continue

        ticker = ""
        for span in name_cell.find_all("span"):
            text = span.get_text(strip=True)
            if text and text != name:
                ticker = text
                break

        underlying = None
        if link and link.get("href"):
            href = link["href"]
            if "tearsheet" in href and "?s=" in href:
                underlying = href.split("?s=")[-1]

        # Weight is normally the third column, but the layout varies, so fall
        # back to the first cell holding a plausible percentage.
        weight = None
        for cell in ([cells[2]] if len(cells) >= 3 else []) + list(cells[1:]):
            text = cell.get_text(strip=True).replace("%", "").replace(",", "")
            try:
                value = float(text)
            except ValueError:
                continue
            if 0 < value <= 100:
                weight = value
                break

        holdings.append({"rank": i + 1, "name": name, "ticker": ticker,
                         "weight_pct": weight, "underlying_id": underlying})

    if (len(holdings) == 1 and holdings[0]["weight_pct"]
            and holdings[0]["weight_pct"] >= 95):
        print(f"  feeder fund - underlying: {holdings[0]['name']}")
        return holdings, holdings[0].get("underlying_id")

    return holdings, None


def scrape_prices(fund_id, fallback_name, latest_date=None) -> tuple:
    """Price rows since latest_date, and the fund's name as FT reports it."""
    today = datetime.today()

    if latest_date:
        start = datetime.strptime(latest_date, "%Y-%m-%d") + timedelta(days=1)
        if start.date() > today.date():
            print("  already up to date")
            return [], fallback_name
        print(f"  incremental from {start.date()}")
    else:
        start = today - timedelta(days=30)
        print(f"  first run - fetching {start.date()} to {today.date()}")

    html = fetch(_price_url(fund_id, start, today))
    name = fund_name(html, fallback_name)

    seen, unique = set(), []
    for row in parse_prices(html):
        if row["date"] not in seen:
            seen.add(row["date"])
            unique.append(row)
    print(f"  {len(unique)} unique rows")
    return unique, name


def scrape_holdings(holdings_id, fund_name_, max_follow=1) -> list:
    """Top holdings, following through a feeder fund once if needed."""
    today = datetime.today().strftime("%Y-%m-%d")
    target = holdings_id

    for depth in range(max_follow + 1):
        try:
            html = fetch(_holdings_url(target))
        except Exception as exc:                               # noqa: BLE001
            print(f"  ERROR fetching holdings for {fund_name_}: {exc}")
            return []

        holdings, underlying = parse_holdings(html)
        if underlying and depth < max_follow:
            print(f"  following through to {underlying}")
            target = underlying
            time.sleep(1)
            continue

        for h in holdings:
            h["scraped_date"] = today
            h.pop("underlying_id", None)
        print(f"  {len(holdings)} holdings for {fund_name_}")
        return holdings
    return []
