# update_instruments.py
# Updates all instrument data hardcoded from the reviewed list.
# Safe to run multiple times.
# Run: python3 update_instruments.py

import sqlite3

INSTRUMENTS = [
    # (fund_id, name, asset_type, currency, price_unit, category)
    ("YF:CC=F",                  "Cocoa Futures",                          "Commodity", "USD", "dollar", "Agriculture"),
    ("YF:ZW=F",                  "Wheat Futures",                          "Commodity", "USD", "dollar", "Agriculture"),
    ("YF:AIGA.L",                "WisdomTree Agriculture ETC",             "Commodity", "USD", "dollar", "Agriculture"),
    ("YF:COCO",                  "WisdomTree Cocoa",                       "Commodity", "GBP", "pence",  "Agriculture"),
    ("YF:WEAP.L",                "WisdomTree Wheat ETC",                   "Commodity", "GBP", "pence",  "Agriculture"),
    ("ASSET:HOUSE",              "House",                                   "Asset",     "GBP", "pound",  "Asset"),
    ("CASH:GBP",                 "Cash GBP",                               "Cash",      "GBP", "pound",  "Cash"),
    ("CASH:USD",                 "Cash USD",                               "Cash",      "USD", "dollar", "Cash"),
    ("YF:HG=F",                  "Copper Futures",                         "Commodity", "USD", "dollar", "Metals"),
    ("YF:COPB.L",                "WisdomTree Copper ETC",                  "Commodity", "GBP", "pence",  "Metals"),
    ("YF:MINE.L",                "iShares Copper Miners ETF",              "Commodity", "GBP", "pence",  "Metals"),
    ("YF:SI=F",                  "Silver Futures",                         "Commodity", "USD", "dollar", "Metals"),
    ("YF:ISLN.L",                "iShares Physical Silver ETC",            "Commodity", "USD", "dollar", "Metals"),
    ("YF:BTC-GBP",               "Bitcoin GBP",                            "Crypto",    "GBP", "pound",  "Crypto"),
    ("YF:ETH-GBP",               "Ethereum GBP",                           "Crypto",    "GBP", "pound",  "Crypto"),
    ("YF:BA.L",                  "BAE Systems",                            "Stock",     "GBP", "pence",  "Defence"),
    ("YF:NATP.L",                "Future of Defence UCITS ETF",            "ETF",       "GBP", "pence",  "Defence"),
    ("YF:QQ.L",                  "QinetiQ Group",                          "Stock",     "GBP", "pence",  "Defence"),
    ("YF:DFEU.L",                "iShares Europe Def UETF",                "ETF",       "GBP", "pence",  "Defence"),
    ("YF:HMCH.L",                "HSBC MSCI China UCITS ETF",              "ETF",       "GBP", "pence",  "Asia Pacific"),
    ("COMPOSITE:HSBC_ASIA_PAC",  "HSBC Pension Asia Pacific",              "Fund",      "GBP", "pound",  "Asia Pacific"),
    ("GB0031728438:GBP",         "SL Asia Pacific Ex Japan",               "Fund",      "GBP", "pence",  "Asia Pacific"),
    ("GB00B7Z71453:GBP",         "SL China",                               "Fund",      "GBP", "pence",  "Asia Pacific"),
    ("GB00B849FB47:GBP",         "SL Ishares Pacific",                     "Fund",      "GBP", "pence",  "Asia Pacific"),
    ("COMPOSITE:HSBC_JAPAN",     "HSBC Pension Japan",                     "Fund",      "GBP", "pound",  "Asia Pacific"),
    ("GB00B4ZFV486:GBP",         "L&G PMC Japan Equity Index",             "Fund",      "GBP", "pence",  "Asia Pacific"),
    ("YF:^N225",                 "Nikkei 225",                             "Index",     "USD", "point",  "Asia Pacific"),
    ("YF:XDJP.L",                "Xtr Nikkei 225 UCITS ETF",              "ETF",       "GBP", "pence",  "Asia Pacific"),
    ("GB00B4WT1Y33:GBP",         "L&G PMC Asia Pac ex Japan Dev Eq",      "Fund",      "GBP", "pence",  "Asia Pacific"),
    ("COMPOSITE:HSBC_EM",        "HSBC Pension Emerging Markets",          "Fund",      "GBP", "pound",  "Emerging Markets"),
    ("GB00BL0DTP33:GBP",         "JPMorgan Emerging Markets ESG Equity C", "Fund",      "GBP", "pence",  "Emerging Markets"),
    ("YF:EEM",                   "MSCI Emerging Markets",                  "ETF",       "USD", "dollar", "Emerging Markets"),
    ("LU1408526199:GBP",         "Robeco Emerging Stars Equities G GBP",  "Fund",      "GBP", "pence",  "Emerging Markets"),
    ("YF:BZ=F",                  "Brent Crude Futures",                    "Commodity", "USD", "dollar", "Energy"),
    ("YF:CL=F",                  "Crude Oil Futures",                      "Commodity", "USD", "dollar", "Energy"),
    ("YF:NG=F",                  "Natural Gas Futures",                    "Commodity", "USD", "dollar", "Energy"),
    ("YF:NRGT.L",                "WisdomTree Energy Transition ETC",      "Commodity", "GBP", "pence",  "Energy"),
    ("YF:NGSP.L",                "WisdomTree Natural Gas ETC",             "Commodity", "GBP", "pence",  "Energy"),
    ("YF:^STOXX50E",             "Euro Stoxx 50",                          "Index",     "USD", "point",  "European"),
    ("COMPOSITE:HSBC_EUROPE",    "HSBC Pension Europe",                    "Fund",      "GBP", "pound",  "European"),
    ("GB00B4YKRJ18:GBP",         "L&G PMC Europe Ex UK Equity Index",     "Fund",      "GBP", "pence",  "European"),
    ("YF:HCAN.L",                "HSBC MSCI Canada UCITS ETF",             "ETF",       "GBP", "pence",  "Canada"),
    ("YF:CSCA.L",                "iShares MSCI Canada UCITS ETF",          "ETF",       "GBP", "pence",  "Canada"),
    ("YF:GBPUSD=X",              "GBP/USD",                                "Index",     "GBP", "ratio",  "FX"),
    ("YF:CNY=X",                 "USD/CNY",                                "Index",     "GBP", "ratio",  "FX"),
    ("YF:JPM",                   "JPMorgan Chase",                         "Stock",     "USD", "dollar", "Financials"),
    ("YF:HSBA.L",                "HSBC Holdings",                          "Stock",     "GBP", "pence",  "Financials"),
    ("YF:UIFS.L",                "iShares S&P 500 Financials ETF",        "ETF",       "GBP", "pence",  "Financials"),
    ("YF:VWRL.L",                "FTSE All World",                         "ETF",       "GBP", "pence",  "Global Equity"),
    ("YF:URTH",                  "MSCI World",                             "ETF",       "USD", "dollar", "Global Equity"),
    ("CALC:XAUGBP",              "Gold / GBP (Spot)",                      "Commodity", "GBP", "pound",  "Gold"),
    ("YF:GC=F",                  "Gold Futures",                           "Commodity", "USD", "dollar", "Gold"),
    ("GB00B3VNFD68:GBP",         "SL Gold",                                "Fund",      "GBP", "pence",  "Gold"),
    ("YF:PHPP.L",                "WT Physical Precious Metals ETC",        "Commodity", "GBP", "pence",  "Gold"),
    ("YF:SPGP.L",                "iShares Gold Producers ETF",             "ETF",       "GBP", "pence",  "Gold"),
    ("YF:IGLN.L",                "iShares Physical Gold ETC",              "Commodity", "USD", "dollar", "Gold"),
    ("YF:LLY",                   "Eli Lilly",                              "Stock",     "USD", "dollar", "Healthcare"),
    ("YF:NVO",                   "Novo Nordisk",                           "Stock",     "USD", "dollar", "Healthcare"),
    ("GB00BF0TZK67:GBP",         "SL L&G Global Infrastructure",           "Fund",      "GBP", "pence",  "Infrastructure"),
    ("GB00B3K5WJ87:GBP",         "SL Macquarie Global Infrastructure Secs","Fund",      "GBP", "pence",  "Infrastructure"),
    ("GB00B61JR401:GBP",         "SL JPM Natural Resources",               "Fund",      "GBP", "pence",  "Natural Resources"),
    ("YF:ASML",                  "ASML Holding",                           "Stock",     "USD", "dollar", "Tech/AI"),
    ("YF:GOOG",                  "Alphabet (Google)",                      "Stock",     "USD", "dollar", "Tech/AI"),
    ("YF:FCBR.L",                "FT Nasdaq Cs UCITS ETF",                "ETF",       "GBP", "pence",  "Tech/AI"),
    ("YF:MU",                    "Micron Technology",                      "Stock",     "USD", "dollar", "Tech/AI"),
    ("YF:NVDA",                  "NVIDIA",                                 "Stock",     "USD", "dollar", "Tech/AI"),
    ("YF:QCOM",                  "Qualcomm",                               "Stock",     "USD", "dollar", "Tech/AI"),
    ("YF:QWTM.L",                "WisdomTree Quantum Computing ETF",       "ETF",       "GBP", "pence",  "Tech/AI"),
    ("YF:XAIX.L",                "X AI and Big Data UCITS ETF",            "ETF",       "GBP", "pence",  "Tech/AI"),
    ("YF:AINF.L",                "iShares AI Infrastructure ETF",          "ETF",       "GBP", "pence",  "Tech/AI"),
    ("YF:PLAY.L",                "iShares Digital Entertainment ETF",      "ETF",       "GBP", "pence",  "Tech/AI"),
    ("YF:QANT.L",                "iShares Quantum Computing ETF",          "ETF",       "GBP", "pence",  "Tech/AI"),
    ("YF:AMZN",                  "Amazon",                                 "Stock",     "USD", "dollar", "Tech/AI"),
    ("YF:XU030.IS",              "BIST 30",                                "Index",     "TRY", "point",  "Turkish Equity"),
    ("GB00B2PLJQ03:GBP",         "Artemis UK Special Situations Fund",     "Fund",      "GBP", "pence",  "UK Equity"),
    ("YF:BRBY.L",                "Burberry Group",                         "Stock",     "GBP", "pence",  "UK Equity"),
    ("YF:CCH.L",                 "Coca-Cola HBC",                          "Stock",     "GBP", "pence",  "UK Equity"),
    ("YF:^FTSE",                 "FTSE 100",                               "Index",     "GBP", "point",  "UK Equity"),
    ("YF:^FTMC",                 "FTSE 250",                               "Index",     "GBP", "point",  "UK Equity"),
    ("YF:GSK.L",                 "GSK plc",                                "Stock",     "GBP", "pence",  "UK Equity"),
    ("YF:DATA.L",                "GlobalData plc",                         "Stock",     "GBP", "pence",  "UK Equity"),
    ("COMPOSITE:HSBC_UK_ACTIVE", "HSBC Pension UK Active",                 "Fund",      "GBP", "pound",  "UK Equity"),
    ("YF:HFG.L",                 "Hilton Food Group",                      "Stock",     "GBP", "pence",  "UK Equity"),
    ("GB00BJH4XW03:GBP",         "L&G Future World ESG Optimised UK Index Fund","Fund", "GBP", "pence",  "UK Equity"),
    ("GB00B0ZGQD71:GBP",         "Schroder Life UK Smaller Companies",     "Fund",      "GBP", "pence",  "UK Equity"),
    ("LU2092165666:GBP",         "HSBC Islamic Global Equity",             "Fund",      "GBP", "pence",  "US Equity"),
    ("COMPOSITE:HSBC_SHARIA",    "HSBC Pension Sharia",                    "Fund",      "GBP", "pound",  "US Equity"),
    ("COMPOSITE:HSBC_NORTH_AMERICA","HSBC Pension North America",          "Fund",      "GBP", "pound",  "US Equity"),
    ("GB00B3VGBC62:GBP",         "L&G PMC North America Equity Index",     "Fund",      "GBP", "pence",  "US Equity"),
    ("YF:^IXIC",                 "NASDAQ Composite",                       "Index",     "USD", "point",  "US Equity"),
    ("YF:^GSPC",                 "S&P 500",                                "Index",     "USD", "point",  "US Equity"),
    ("YF:CSP1.L",                "iShares Core S&P 500 ETF",              "ETF",       "GBP", "pence",  "US Equity"),
]


def main():
    conn = sqlite3.connect('data/funds.db')
    updated = 0
    inserted = 0

    for fund_id, name, asset_type, currency, price_unit, category in INSTRUMENTS:
        existing = conn.execute(
            'SELECT fund_id FROM instruments WHERE fund_id=?', (fund_id,)
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE instruments
                SET name=?, asset_type=?, currency=?, price_unit=?, category=?
                WHERE fund_id=?
            """, (name, asset_type, currency, price_unit, category, fund_id))
            updated += 1
        else:
            conn.execute("""
                INSERT INTO instruments (fund_id, name, asset_type, currency, price_unit, category)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (fund_id, name, asset_type, currency, price_unit, category))
            inserted += 1

    conn.commit()
    conn.close()
    print(f"Done. Updated: {updated} | Inserted: {inserted}")


if __name__ == '__main__':
    main()