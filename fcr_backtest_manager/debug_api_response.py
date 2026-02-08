import requests
import json

url = "http://127.0.0.1:5001/api/backtest"
payload = {
    "ticker": "SPY",
    "start_date": "2024-01-08",
    "end_date": "2024-01-12",
    "strategy": "A",
    "risk": 100,
    "initial_capital": 1000
}

try:
    response = requests.post(url, json=payload)
    if response.status_code == 200:
        data = response.json()
        print("Keys:", data.keys())
        if "equity_curve" in data:
            ec = data["equity_curve"]
            print(f"Equity Curve Length: {len(ec)}")
            print("First 2 points:", ec[:2])
            print("Last 2 points:", ec[-2:])
        else:
            print("ERROR: 'equity_curve' key missing in response")
    else:
        print(f"Error: Status {response.status_code}")
        print(response.text)
except Exception as e:
    print(f"Exception: {e}")
