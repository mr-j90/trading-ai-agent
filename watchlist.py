# Approved 2026-09-14, see https://github.com/mr-j90/trading-ai-agent/issues/7
WATCHLIST = {
    "tech": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "AMD", "ORCL", "CRM", "PLTR", "ANET"],
    "blue_collar": ["IESC", "PWR", "EME", "FIX", "MTZ", "DY", "STRL", "URI", "CAT", "DE", "FAST", "WSO"],
}
SECTOR_OF = {t: s for s, ts in WATCHLIST.items() for t in ts}

if __name__ == "__main__":
    assert len(SECTOR_OF) == 24, "duplicate ticker across sectors"
    assert SECTOR_OF["IESC"] == "blue_collar"
    print("ok")
