#!/usr/bin/env python3
"""Pusula: eksik enstrümanları `instruments` tablosuna ekler, Tüpraş'ı Commodity dilimine taşır.

Varsayılan KURU ÇALIŞMA: hiçbir şey yazmaz, ne yapacağını gösterir.

    python3 pusula_add_instruments.py              # kuru çalışma + Yahoo kontrolü
    python3 pusula_add_instruments.py --apply      # yedek alır, sonra yazar

Seçenekler:
    --db PATH     veritabanı (varsayılan ~/data/funds.db)
    --no-check    Yahoo fiyat kontrolünü atla (yfinance yoksa gerekli)
    --force       Yahoo'da veri dönmeyen sembolleri de ekle

Tekrar çalıştırmak güvenli: var olan fund_id'ler atlanır.
"""
import argparse
import os
import sqlite3
from datetime import datetime

# (Yahoo sembolü, ad, asset_type, para birimi, price_unit, category)
# Not: Yahoo ZS/ZL/ZC/KC/SB/CT/KE fiyatlarını ABD senti cinsinden verir. Mevcut
# 'Wheat Futures' (ZW=F, o da sent) 'dollar' olarak kayıtlı; tutarlılık için aynısı kullanıldı.
NEW = [
    ("ZS=F",   "Soybean Futures",                      "Commodity", "USD", "dollar", "Agriculture"),
    ("ZL=F",   "Soybean Oil Futures",                  "Commodity", "USD", "dollar", "Agriculture"),
    ("ZM=F",   "Soybean Meal Futures",                 "Commodity", "USD", "dollar", "Agriculture"),
    ("ZC=F",   "Corn Futures",                         "Commodity", "USD", "dollar", "Agriculture"),
    ("KC=F",   "Coffee Futures",                       "Commodity", "USD", "dollar", "Agriculture"),
    ("SB=F",   "Sugar #11 Futures",                    "Commodity", "USD", "dollar", "Agriculture"),
    ("CT=F",   "Cotton Futures",                       "Commodity", "USD", "dollar", "Agriculture"),
    ("KE=F",   "KC HRW Wheat Futures",                 "Commodity", "USD", "dollar", "Agriculture"),
    ("BCOM.L", "L&G All Commodities UCITS ETF (USD)",  "Commodity", "USD", "dollar", "Broad Commodities"),
    # Gümüş: Gold dilimine konuyor. Commodity listesinde görmek istersen "Gold" -> "Commodity".
    ("SI=F",   "Silver Futures",                       "Gold",      "USD", "dollar", "Silver"),
    ("PHAG.L", "WisdomTree Physical Silver (USD)",     "Gold",      "USD", "dollar", "Silver"),
]

# (fund_id, kolon, yeni değer)
UPDATES = [
    ("YF:TUPRS.IS", "asset_type", "Commodity"),  # Tüpraş: ayrı dilim yerine Commodity altına
]

COLS = ("fund_id", "name", "asset_type", "currency", "price_unit", "category",
        "active", "source", "source_id", "provider", "holdings_id", "latest_only")


def yahoo_last(sym):
    """(son fiyat, durum) döner. Ağ veya yfinance yoksa fiyat None olur."""
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance yok (--no-check kullan)"
    try:
        hist = yf.Ticker(sym).history(period="5d")
        closes = hist["Close"].dropna() if not hist.empty else []
        if len(closes) == 0:
            return None, "veri yok"
        return float(closes.iloc[-1]), "ok"
    except Exception as exc:  # ağ hatası, rate limit vb.
        return None, f"hata: {exc}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="~/data/funds.db")
    ap.add_argument("--apply", action="store_true", help="yedek al ve yaz")
    ap.add_argument("--no-check", action="store_true", help="Yahoo kontrolünü atla")
    ap.add_argument("--force", action="store_true", help="kontrolden geçmeyenleri de ekle")
    args = ap.parse_args()

    db = os.path.expanduser(args.db)
    con = sqlite3.connect(db)
    cur = con.cursor()

    existing_ids = {r[0] for r in cur.execute("SELECT fund_id FROM instruments")}
    existing_names = {r[0].lower() for r in cur.execute("SELECT name FROM instruments")}
    asset_types = {r[0] for r in cur.execute("SELECT DISTINCT asset_type FROM instruments") if r[0]}
    units = sorted(r[0] for r in cur.execute("SELECT DISTINCT price_unit FROM instruments") if r[0])
    print(f"DB: {db}  ({len(existing_ids)} enstrüman)")
    print("Mevcut asset_type:", ", ".join(sorted(asset_types)))
    print("Mevcut price_unit:", ", ".join(units))

    print("\nEKLENECEKLER")
    to_insert = []
    for sym, name, atype, ccy, unit, cat in NEW:
        fid = f"YF:{sym}"
        if fid in existing_ids:
            print(f"  ATLA  {fid:<14} zaten var")
            continue
        notes = []
        if name.lower() in existing_names:
            notes.append("aynı adla başka kayıt var")
        if atype not in asset_types:
            notes.append(f"yeni asset_type '{atype}'")
        if args.no_check:
            status, ok = "kontrol edilmedi", True
        else:
            px, status = yahoo_last(sym)
            if px is not None:
                status = f"ok, son {px:,.2f}"
            ok = px is not None or args.force
        mark = "EKLE" if ok else "ATLA"
        extra = f"  [{'; '.join(notes)}]" if notes else ""
        print(f"  {mark}  {fid:<14} {name:<38} {atype}/{cat:<17} Yahoo: {status}{extra}")
        if ok:
            to_insert.append((fid, name, atype, ccy, unit, cat, 1, "yahoo", sym, None, None, 0))

    print("\nGÜNCELLENECEKLER")
    to_update = []
    for fid, col, new in UPDATES:
        row = cur.execute(f'SELECT "{col}" FROM instruments WHERE fund_id = ?', (fid,)).fetchone()
        if row is None:
            print(f"  YOK   {fid}")
        elif row[0] == new:
            print(f"  ATLA  {fid}: {col} zaten '{new}'")
        else:
            print(f"  GÜNC  {fid}: {col} '{row[0]}' -> '{new}'")
            to_update.append((fid, col, new))

    if not args.apply:
        print("\nKuru çalışma: hiçbir şey yazılmadı. Yazmak için --apply ekle.")
        con.close()
        return
    if not to_insert and not to_update:
        print("\nYazılacak bir şey yok.")
        con.close()
        return

    backup = f"{db}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    bcon = sqlite3.connect(backup)
    con.backup(bcon)
    bcon.close()
    print(f"\nYedek: {backup}")

    placeholders = ", ".join("?" * len(COLS))
    with con:  # tek transaction: hata olursa hiçbiri yazılmaz
        con.executemany(f"INSERT INTO instruments ({', '.join(COLS)}) VALUES ({placeholders})",
                        to_insert)
        for fid, col, new in to_update:
            con.execute(f'UPDATE instruments SET "{col}" = ? WHERE fund_id = ?', (new, fid))
    con.close()
    print(f"Yazıldı: {len(to_insert)} ekleme, {len(to_update)} güncelleme.")


if __name__ == "__main__":
    main()
