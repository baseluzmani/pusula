#!/bin/bash
DB=/home/ahmet/FTScrapper/data/funds.db
OUT=/home/ahmet/backups/funds_$(date +%Y%m%d_%H%M).db
sqlite3 "$DB" ".backup '$OUT'"
gzip -f "$OUT"
find /home/ahmet/backups -name 'funds_*.db.gz' -mtime +21 -delete
