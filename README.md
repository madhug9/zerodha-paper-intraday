# GainzAlgo V2 Alpha paper backtest

Paper-trade **NSE equities / ETFs** with a **GainzAlgo V2 Alpha** signal engine (independent recreation of the published rules), optional Opening Range Breakout (ORB), Zerodha-like charges, and multi-entry risk controls. No live orders are placed.

This is **not** the official TradingView indicator and is **not affiliated** with GainzAlgo.

## What it does

- Replays recent intraday bars (Yahoo Finance `.NS`)
- **GainzAlgo V2 Alpha** (default): bar-close buy/sell signals from
  - simplified bullish/bearish engulfing
  - candle body vs true range (stability)
  - RSI(14) filter
  - 10-bar close delta
  - ATR(14) take-profit / stop-loss at a chosen RRR
- Optional **ORB**: 15-minute opening range, long breakout, 1.5R target
- Entries fill at the **next bar open** after a confirmed close
- Long and short (GainzAlgo); ORB remains long-only
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

Enter symbols like `CUPID` or `SBIN,VEDL`, pick GainzAlgo V2 Alpha or ORB, set lookback / capital, click **Run backtest**.

## CLI

```powershell
python main.py --symbols CUPID --lookback 59 --quiet
python main.py --symbols VEDL,SBIN --lookback 30 --strategy gainzalgo --rrr 1:2
python main.py --symbols SBIN --lookback 30 --strategy orb
```

Trade logs land in `logs/backtest_*.csv`.

## Tests

```powershell
python -m pytest -q
```

## Notes

- This is **simulation**, not Zerodha’s official paper API.
- Yahoo 5m history is limited (~59 days).
- Charges mirror [Zerodha brokerage calculator](https://zerodha.com/brokerage-calculator/#tab-equities) (approximate).
- Not investment advice. For learning automation only.
