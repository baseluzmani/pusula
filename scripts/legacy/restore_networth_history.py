# restore_networth_history.py
# Restores networth_history table with correct first working day dates
# Run: python3 scripts/restore_networth_history.py

import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = 'data/funds.db'

conn = sqlite3.connect(DB_PATH)

conn.execute("""
    CREATE TABLE IF NOT EXISTS networth_history (
        date      TEXT PRIMARY KEY,
        total_gbp REAL NOT NULL,
        source    TEXT DEFAULT 'manual'
    )
""")

historical = [
    ('2021-05-03', 496015), ('2021-06-01', 503667), ('2021-07-01', 517889),
    ('2021-08-02', 532111), ('2021-09-01', 544862), ('2021-10-01', 557613),
    ('2021-11-01', 570364), ('2021-12-01', 583115), ('2022-01-03', 583159),
    ('2022-02-01', 589312), ('2022-03-01', 602769), ('2022-04-01', 616226),
    ('2022-05-02', 620390), ('2022-06-01', 636694), ('2022-07-01', 647205),
    ('2022-08-01', 662503), ('2022-09-01', 679245), ('2022-10-03', 682406),
    ('2022-11-01', 688176), ('2022-12-01', 699637), ('2023-01-02', 707060),
    ('2023-02-01', 725041), ('2023-03-01', 727361), ('2023-04-03', 738672),
    ('2023-05-01', 745485), ('2023-06-01', 755496), ('2023-07-03', 764880),
    ('2023-08-01', 747559), ('2023-09-01', 754685), ('2023-10-02', 758119),
    ('2023-11-01', 761554), ('2023-12-01', 764988), ('2024-01-01', 782267),
    ('2024-02-01', 799546), ('2024-03-01', 820397), ('2024-04-01', 873041),
    ('2024-05-01', 872573), ('2024-06-03', 883669), ('2024-07-01', 894765),
    ('2024-08-01', 905861), ('2024-09-02', 919396), ('2024-10-01', 954549),
    ('2024-11-01', 962183), ('2024-12-02', 1021416), ('2025-01-01', 1021659),
    ('2025-02-03', 1065654), ('2025-03-03', 1069289), ('2025-04-01', 1098164),
    ('2025-05-01', 1145853), ('2025-06-02', 1171973), ('2025-07-01', 1194239),
    ('2025-08-01', 1223250), ('2025-09-01', 1256399), ('2025-10-01', 1332348),
    ('2025-11-03', 1356334), ('2025-12-01', 1364217), ('2026-01-01', 1382718),
    ('2026-02-02', 1437909), ('2026-03-02', 1518944), ('2026-04-01', 1454362),
]

for date, total in historical:
    conn.execute(
        "INSERT OR REPLACE INTO networth_history (date, total_gbp, source) VALUES (?, ?, 'manual')",
        (date, total)
    )

conn.commit()
count = conn.execute("SELECT COUNT(*) FROM networth_history").fetchone()[0]
rows  = conn.execute("SELECT date, total_gbp FROM networth_history ORDER BY date").fetchall()
print(f"✓ Loaded {count} data points")
print(f"  From: {rows[0][0]}  £{rows[0][1]:,.0f}")
print(f"  To:   {rows[-1][0]}  £{rows[-1][1]:,.0f}")
conn.close()