import os

# Alpaca API Credentials - Test2 Paper Trading Account
# Replace with your actual API keys
ALPACA_API_KEY = "PKNNXOM2FRQJIYEREBUHHTJGC7"
ALPACA_API_SECRET = "EewsrDutrAV557BaFjW4R4bJYiF2DESkcDdJEC2PMQ4Y"

# Paper Trading vs Live Trading
# Set to True for paper trading (no real money)
PAPER_TRADING = True

# Alpaca API Base URLs
ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_BASE_URL = "https://api.alpaca.markets"

# Active trading URL (automatically selected based on PAPER_TRADING flag)
ALPACA_BASE_URL = ALPACA_PAPER_BASE_URL if PAPER_TRADING else ALPACA_LIVE_BASE_URL

# Trading Settings
Timezone = "US/Eastern"

# TSLA Strategy A Test Bot Configuration - Test2 Account
TSLA_BOT_CONFIG = {
    "name": "TSLA Strategy A Test Bot",
    "ticker": "TSLA",
    "risk_per_trade": 100,      # $100 risk per trade
    "capital": 5000,            # $5000 starting capital
    "rr": 3.0,                  # Risk:Reward ratio 3:1
    "paper_trading": PAPER_TRADING,
}
