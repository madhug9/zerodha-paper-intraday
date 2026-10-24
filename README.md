# NSE intraday paper desk

Paper-trade **NSE equities and ETFs** with a **GainzAlgo V2 Alpha** signal engine (an independent recreation of the published rules), optional Opening Range Breakout (ORB), Zerodha-like MIS charges, and a long/short trade mode. No live orders are placed.

This is **not** the official TradingView indicator and is **not affiliated** with GainzAlgo. It is **not investment advice**.

## What it does

- Replays recent intraday bars (Yahoo Finance `.NS` only)
- **GainzAlgo V2 Alpha** (default): bar-close buy/sell signals from
  - simplified bullish/bearish engulfing
  - candle body vs true range (stability)
  - RSI filter
  - close delta over N bars
  - ATR take-profit / stop-loss at a chosen RRR (`1:1`, `2:3`, `1:2`, `1:4`)
- **ORB**: opening range, breakout, target in R
- `trade_mode`: `both`, `long_only` (buy then sell), or `short_only` (sell then buy)
- Entries fill at the **next bar open** after a confirmed close (GainzAlgo)
- Approx. Zerodha MIS costs + slippage, daily loss halt, forced square-off
- Streamlit UI, CLI, and a local HTML desk (backtest + TradingAgents)

## Setup (Windows)

```powershell
cd "C:\Users\G Bhuvan\Documents\GitHub\zerodha-paper-intraday"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

PowerShell 5 and Python 3.12 are the expected host. If activation is blocked, `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or call `.\.venv\Scripts\python.exe` directly.

## Local HTML desk

`start-gui.bat` activates `.venv`, sets `PYTHONUTF8=1`, starts the server on `127.0.0.1:8765`, and opens the browser.

```powershell
.\start-gui.bat
```

Two tabs:

- **Backtest** — NSE symbols (`CUPID` or `SBIN,VEDL`), GainzAlgo or ORB, RRR, trade mode, capital, lookback. Shows P&L, charges, win rate, round trips, exit mix, a price chart with trade markers, an equity curve, the trade table, and a CSV download.
- **AI Analysis** — one NSE ticker (`.NS` is added for you) and a date. The server starts [TradingAgents](https://github.com/TauricResearch/TradingAgents) in the background with the Google Gemini provider and the page polls until the decision and reports are ready.

TradingAgents is a separate checkout. The default path is `C:\Users\G Bhuvan\Documents\GitHub\trading` (`tradingagents.home` in `config.yaml`, or `TRADINGAGENTS_HOME`). That folder should contain its own `.venv` and a `.env` with `GOOGLE_API_KEY`. This project never stores or logs that key. The child process is that venv's Python (`tradingagents.python` overrides it). Gemini model ids default to `gemini-2.5-flash`; change `deep_think_llm` and `quick_think_llm` in `config.yaml` if your checkout expects different ids.

From PowerShell without the bat file:

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONUTF8 = "1"
python -m uvicorn src.webapp:app --host 127.0.0.1 --port 8765
```

## Streamlit

The original Streamlit app is unchanged in role: same backtest, with strategy, RRR, and trade mode in the sidebar.

```powershell
streamlit run app.py
```

## CLI

```powershell
python main.py --symbols CUPID --lookback 30
python main.py --symbols VEDL,SBIN --lookback 30 --strategy gainzalgo --rrr 1:2 --mode both
python main.py --symbols SBIN --lookback 30 --strategy orb --mode long_only
```

`--mode` is `both`, `long_only`, or `short_only`. Trade logs land in `logs/backtest_*.csv`.

## Tests

```powershell
python -m pytest -q
```

## Notes

- This is **simulation**, not Zerodha’s official paper API, and it does not place orders.
- Yahoo 5-minute history is limited (about 59 days).
- Symbols are NSE cash only. `SBIN.NS` is accepted; other exchange suffixes are rejected.
- Charges mirror the [Zerodha brokerage calculator](https://zerodha.com/brokerage-calculator/#tab-equities) (approximate).
- TradingAgents output is research text from a language model. It is not a signal to trade.
