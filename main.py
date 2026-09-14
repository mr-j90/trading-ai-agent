import os

from alpaca.trading.client import TradingClient
from dotenv import load_dotenv

load_dotenv()

# ponytail: paper=True hardcoded; flip to env var when going live
client = TradingClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"], paper=True)

if __name__ == "__main__":
    acct = client.get_account()
    print(acct.status, acct.buying_power)
