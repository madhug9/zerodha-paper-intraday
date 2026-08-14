# Zerodha Paper Intraday

Paper-trade **NSE equities / ETFs** with an Opening Range Breakout (ORB) strategy, Zerodha-like charges, and multi-entry risk controls. No live orders are placed.

## What it does

- Replays recent intraday bars (Yahoo Finance `.NS`)
- Builds a 15-minute opening range
- Long breakout + stop at OR low, target at 1.5R
- Optional re-entries (multiple entry/exit per day)
- Applies approx. Zerodha MIS costs + slippage
- Daily loss halt + forced square-off

Default watchlist (edit `config.yaml`): `VEDL`, `TATASTEEL`, `TATAMOTORS`, `SBIN`, `ADANIENT`, `SILVERBEES`, `GOLDBEES`, `BANKBEES`

## Setup

```powershell
cd "$env:USERPROFILE\Projects\zerodha-paper-intraday"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run paper session

```powershell
python main.py --days 3
python main.py --symbols VEDL,SBIN,SILVERBEES --days 5
```

Trade logs land in `logs/paper_*.csv`.

## Notes

- This is **simulation**, not Zerodha’s official paper API.
- Yahoo data can miss some ETFs or delay — skip empty symbols.
- Charges mirror [Zerodha brokerage calculator](https://zerodha.com/brokerage-calculator/#tab-equities) (approximate).
- Not investment advice. For learning automation only.

## Next

- Hook Kite WebSocket ticks for live **paper** fills (still no real orders)
- Add short side / VWAP strategy
- Live MIS only after paper stats look acceptable
