from datetime import datetime

import pandas as pd

from src.backtest import _pair_round_trips, load_config, run_backtest
from src.costs import CostConfig
from src.indicators import rma, rsi, true_range
from src.paper_broker import Fill, PaperBroker
from src.strategy_gainzalgo import GainzAlgoConfig, GainzAlgoEngine, compute_signals, round_up
from src.strategy_orb import RiskConfig


def _free_costs() -> CostConfig:
    return CostConfig(
        brokerage_rate=0.0,
        brokerage_cap=0.0,
        stt_sell=0.0,
        exchange_txn=0.0,
        sebi=0.0,
        stamp_buy=0.0,
        gst=0.0,
        slippage_bps=0.0,
    )


def test_round_up_matches_pine_ceil():
    assert round_up(100.001, 2) == 100.01
    assert round_up(100.00, 2) == 100.00
    assert round_up(1.234, 2) == 1.24


def test_rma_first_value_is_sma_then_wilder():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], dtype=float)
    out = rma(series, 3)
    assert pd.isna(out.iloc[0])
    assert pd.isna(out.iloc[1])
    assert out.iloc[2] == 2.0  # SMA(1,2,3)
    expected = (1.0 / 3.0) * 4.0 + (2.0 / 3.0) * 2.0
    assert abs(out.iloc[3] - expected) < 1e-12


def test_rsi_climbs_on_monotone_up_close():
    close = pd.Series([float(i) for i in range(1, 40)])
    values = rsi(close, 14)
    assert pd.isna(values.iloc[13])
    assert values.iloc[-1] > 90


def test_true_range_uses_prev_close_gap():
    df = pd.DataFrame(
        {
            "open": [10.0, 14.0],
            "high": [11.0, 15.0],
            "low": [9.0, 13.5],
            "close": [10.5, 14.5],
        }
    )
    tr = true_range(df)
    assert tr.iloc[0] == 2.0
    assert tr.iloc[1] == 4.5  # high 15 vs prev close 10.5


def _downtrend_then_bullish_engulf(n_down: int = 28) -> pd.DataFrame:
    rows = []
    price = 120.0
    for _ in range(n_down):
        o = price
        c = price - 1.2
        rows.append((o, o, c, c, 1000))
        price = c
    # Bearish bar to be engulfed
    rows.append((price, price, price - 0.8, price - 0.8, 1000))
    prev_open = rows[-1][0]
    # Strong green bar: close above previous open, body == range
    o = prev_open - 0.3
    c = prev_open + 1.6
    rows.append((o, c, o, c, 1000))
    # Next bar holds, then runs to target
    rows.append((c + 0.1, c + 0.4, c - 0.2, c + 0.2, 1000))
    rows.append((c + 0.2, c + 8.0, c + 0.1, c + 7.5, 1000))
    idx = pd.date_range("2026-01-05 09:15", periods=len(rows), freq="5min")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


def test_gainzalgo_emits_buy_after_engulfing_in_downtrend():
    df = _downtrend_then_bullish_engulf()
    scored = compute_signals(df, GainzAlgoConfig())
    buys = scored[scored["signal"] == "buy"]
    assert not buys.empty
    row = buys.iloc[0]
    assert row["sl"] < row["close"] < row["tp"]
    assert (row["tp"] - row["close"]) > (row["close"] - row["sl"])


def test_disable_repeating_blocks_second_buy_until_sell():
    df = _downtrend_then_bullish_engulf()
    # Duplicate the engulfing pattern later without a sell in between
    extra = _downtrend_then_bullish_engulf()
    extra.index = extra.index + pd.Timedelta(days=1)
    both = pd.concat([df, extra])
    blocked = compute_signals(both, GainzAlgoConfig(disable_repeating_signals=True))
    free = compute_signals(both, GainzAlgoConfig(disable_repeating_signals=False))
    assert (blocked["signal"] == "buy").sum() == 1
    assert (free["signal"] == "buy").sum() >= 2


def test_broker_long_and_short_round_trips():
    costs = _free_costs()
    broker = PaperBroker(capital=100_000, costs=costs)
    ts = datetime(2026, 1, 5, 10, 0)
    assert broker.buy("AAA", 10, 100.0, ts, 95.0, 110.0, "in")
    assert broker.flat("AAA", 110.0, ts, "target")
    assert abs(broker.realized_pnl - 100.0) < 1e-9

    broker2 = PaperBroker(capital=100_000, costs=costs)
    assert broker2.sell("BBB", 10, 100.0, ts, "in", stop=105.0, target=90.0)
    assert broker2.positions["BBB"].side == "SELL"
    assert abs(broker2.equity({"BBB": 90.0}) - 100_100.0) < 1e-6
    assert broker2.flat("BBB", 90.0, ts, "target")
    assert abs(broker2.realized_pnl - 100.0) < 1e-9


def test_pair_round_trips_handles_shorts():
    ts = datetime(2026, 1, 5, 10, 0)
    fills = [
        Fill(ts, "X", "SELL", 2, 100.0, 1.0, "gainz_sell"),
        Fill(ts, "X", "BUY", 2, 90.0, 1.0, "target"),
        Fill(ts, "Y", "BUY", 1, 50.0, 0.5, "gainz_buy"),
        Fill(ts, "Y", "SELL", 1, 55.0, 0.5, "target"),
    ]
    pnls, reasons = _pair_round_trips(fills)
    assert reasons == {"target": 2}
    assert abs(pnls[0] - ((100 - 90) * 2 - 2.0)) < 1e-9
    assert abs(pnls[1] - ((55 - 50) * 1 - 1.0)) < 1e-9


def test_engine_enters_next_open_and_takes_target():
    df = _downtrend_then_bullish_engulf()
    cfg = GainzAlgoConfig()
    risk = RiskConfig(
        capital=200_000,
        risk_per_trade_pct=0.5,
        max_daily_loss_pct=5.0,
        max_trades_per_day=8,
        max_open_positions=1,
        square_off_time=datetime.strptime("15:15", "%H:%M").time(),
    )
    broker = PaperBroker(capital=risk.capital, costs=_free_costs())
    engine = GainzAlgoEngine(cfg, risk, broker)
    engine.prepare("TST", df)
    engine.reset_day(broker.equity({}))
    for i in range(len(df)):
        engine.on_bar("TST", df, i)
    assert broker.fills, "expected at least one fill"
    assert any(f.reason == "gainz_buy" for f in broker.fills)
    assert any(f.reason == "target" for f in broker.fills)


def _uptrend_then_bearish_engulf(n_up: int = 28) -> pd.DataFrame:
    rows = []
    price = 80.0
    for _ in range(n_up):
        o = price
        c = price + 1.2
        rows.append((o, c, o, c, 1000))
        price = c
    rows.append((price, price + 0.8, price, price + 0.8, 1000))
    prev_open = rows[-1][0]
    o = prev_open + 0.3
    c = prev_open - 1.6
    rows.append((o, o, c, c, 1000))
    rows.append((c - 0.1, c + 0.2, c - 0.4, c - 0.2, 1000))
    rows.append((c - 0.2, c - 0.1, c - 8.0, c - 7.5, 1000))
    idx = pd.date_range("2026-01-06 09:15", periods=len(rows), freq="5min")
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


def test_gainzalgo_emits_sell_after_engulfing_in_uptrend():
    scored = compute_signals(_uptrend_then_bearish_engulf(), GainzAlgoConfig())
    sells = scored[scored["signal"] == "sell"]
    assert not sells.empty
    row = sells.iloc[0]
    assert row["tp"] < row["close"] < row["sl"]


def test_engine_short_hits_target():
    df = _uptrend_then_bearish_engulf()
    risk = RiskConfig(
        capital=200_000,
        risk_per_trade_pct=0.5,
        max_daily_loss_pct=5.0,
        max_trades_per_day=8,
        max_open_positions=1,
        square_off_time=datetime.strptime("15:15", "%H:%M").time(),
    )
    broker = PaperBroker(capital=risk.capital, costs=_free_costs())
    engine = GainzAlgoEngine(GainzAlgoConfig(), risk, broker)
    engine.prepare("TST", df)
    engine.reset_day(broker.equity({}))
    for i in range(len(df)):
        engine.on_bar("TST", df, i)
    assert any(f.reason == "gainz_sell" for f in broker.fills)
    assert any(f.reason == "target" for f in broker.fills)


def test_orb_backtest_still_trades():
    rows = []
    # 09:15-09:30 opening range, then breakout and target
    candles = [
        (100.0, 101.0, 99.0, 100.5),
        (100.5, 102.0, 100.0, 101.5),
        (101.5, 103.0, 101.0, 102.5),  # OR ends 09:30
        (102.5, 104.5, 102.4, 104.0),  # breakout above 103
        (104.0, 110.0, 103.8, 109.0),  # run to 1.5R
    ]
    for o, h, l, c in candles:
        rows.append((o, h, l, c, 1000))
    idx = pd.date_range("2026-01-07 09:15", periods=len(rows), freq="5min")
    df = pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])
    cfg = load_config()
    result = run_backtest(["ORB"], cfg, strategy="orb", write_log=False, frames={"ORB": df})
    assert result.strategy == "orb"
    assert result.round_trips >= 1


def test_run_backtest_with_injected_frames():
    df = _downtrend_then_bullish_engulf()
    cfg = load_config()
    result = run_backtest(
        ["TST"],
        cfg,
        strategy="gainzalgo",
        write_log=False,
        frames={"TST": df},
    )
    assert result.strategy == "gainzalgo"
    assert result.sessions == 1
    assert result.signals
    assert result.round_trips >= 1
    assert result.fills
