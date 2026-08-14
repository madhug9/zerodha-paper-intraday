# Zerodha Paper Intraday

Paper-trade **NSE equities / ETFs** with an Opening Range Breakout (ORB) strategy, Zerodha-like charges, and multi-entry risk controls. No live orders are placed.

## What it does

- Replays recent intraday bars (Yahoo Finance `.NS`)
- Builds a 15-minute opening range
- Long breakout + stop at OR low, target at 1.5R
- Optional re-entries (multiple entry/exit per day)
- Applies approx. Zerodha MIS costs + slippage
- Daily loss halt + forced square-off
- **Web UI** to type symbols and run backtests

## Setup

```powershell
cd "C:\Users\G Bhuvan\Documents\GitHub\zerodha-paper-intraday"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Web UI

```powershell
streamlit run app.py
```

Enter symbols like `CUPID` or `SBIN,VEDL`, set lookback / capital, click **Run backtest**.

## CLI

```powershell
python main.py --symbols CUPID --lookback 59 --quiet
python main.py --symbols VEDL,SBIN --lookback 30
```

Trade logs land in `logs/backtest_*.csv`.

## Notes

- This is **simulation**, not Zerodha’s official paper API.
- Yahoo 5m history is limited (~59 days).
- Charges mirror [Zerodha brokerage calculator](https://zerodha.com/brokerage-calculator/#tab-equities) (approximate).
- Not investment advice. For learning automation only.
