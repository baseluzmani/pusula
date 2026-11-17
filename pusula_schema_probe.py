#!/usr/bin/env python3
"""Pusula şema keşfi — SALT OKUNUR, veritabanına hiçbir şey yazmaz.

Bilinen birkaç enstrümanın hangi tablolarda, hangi kolonlarda durduğunu
ve o tabloların CREATE ifadelerini yazdırır. Çıktıyı sohbete yapıştır;
ekleme scripti buna göre birebir yazılacak.

Kullanım:
    python3 pusula_schema_probe.py              # varsayılan ~/data/funds.db
    python3 pusula_schema_probe.py /yol/db.db
"""
import os
import sqlite3
import sys

DB = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/data/funds.db")
PROBES = ["WisdomTree Agriculture", "Wheat Futures", "Cocoa Futures",
          "Shell plc", "Tupras", "Petrol Rafinerileri"]

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)  # read-only bağlantı
cur = con.cursor()

tables = [r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("TABLES:", ", ".join(tables))

for t in tables:
    cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{t}")')]
    hits = []
    for probe in PROBES:
        for col in cols:
            rows = cur.execute(
                f'SELECT * FROM "{t}" WHERE CAST("{col}" AS TEXT) LIKE ? LIMIT 2',
                (f"%{probe}%",)).fetchall()
            if rows:
                hits.append((probe, col, rows))
    if not hits:
        continue
    n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    print("\n" + "=" * 70)
    print(f"TABLE {t}  ({n} rows)")
    print(cur.execute("SELECT sql FROM sqlite_master WHERE name=?", (t,)).fetchone()[0])
    for probe, col, rows in hits:
        print(f"-- '{probe}' -> column {col}:")
        for r in rows:
            print("    ", dict(zip(cols, r)))

con.close()
