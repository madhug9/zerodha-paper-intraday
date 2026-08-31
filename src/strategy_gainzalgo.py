"""Independent recreation of the published GainzAlgo V2 Alpha signal rules.

This is not the official TradingView indicator, not affiliated with GainzAlgo,
and is intended for paper / research simulation only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import pandas as pd

from .indicators import atr as atr_series
from .indicators import rsi as rsi_series
from .indicators import true_range
from .paper_broker import PaperBroker
from .strategy_orb import RiskConfig

SignalSide = Literal["buy", "sell"]

RRR_MULT = {
    "1:1": 1.0,
    "2:3": 1.5,
    "1:2": 2.0,
    "1:4": 4.0,
}


def round_up(number: float, decimals: int) -> float:
    """Match Pine ``math.ceil(number * 10^decimals) / 10^decimals``."""
    if not math.isfinite(number):
        return number
    factor = 10**decimals
    return math.ceil(number * factor - 1e-12) / factor


@dataclass
class GainzAlgoConfig:
    rsi_length: int = 14
    atr_length: int = 14
    candle_stability_index: float = 0.7
    rsi_index: float = 80.0
    candle_delta_length: int = 10
    disable_repeating_signals: bool = True
    rrr: str = "1:2"
    tp_sl_multiplier: float = 1.0
    tp_sl_precision: int = 2
    allow_shorts: bool = True
    allow_reentry: bool = True
    max_entries_per_symbol: int = 8

    @property
    def reward_multiple(self) -> float:
        key = str(self.rrr).strip()
        if key not in RRR_MULT:
            raise ValueError(f"Unsupported RRR '{self.rrr}'. Use one of: {', '.join(RRR_MULT)}")
        return RRR_MULT[key]


def compute_signals(df: pd.DataFrame, cfg: GainzAlgoConfig) -> pd.DataFrame:
    """Vectorized, bar-close signals aligned to ``df`` (non-repainting)."""
    if df.empty:
        out = df.copy()
        out["rsi"] = pd.Series(dtype=float)
        out["atr"] = pd.Series(dtype=float)
        out["tr"] = pd.Series(dtype=float)
        out["signal"] = pd.Series(dtype=object)
        out["tp"] = pd.Series(dtype=float)
        out["sl"] = pd.Series(dtype=float)
        return out

    need = {"open", "high", "low", "close"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"OHLC missing columns: {sorted(missing)}")

    out = df.copy()
    body = (out["close"] - out["open"]).abs()
    tr = true_range(out)
    rsi = rsi_series(out["close"], cfg.rsi_length)
    atr = atr_series(out, cfg.atr_length)
    prev_open = out["open"].shift(1)
    prev_close = out["close"].shift(1)
    close_n = out["close"].shift(cfg.candle_delta_length)

    stable = (tr > 0) & ((body / tr) > cfg.candle_stability_index)
    bullish_engulfing = (prev_close < prev_open) & (out["close"] > out["open"]) & (out["close"] > prev_open)
    bearish_engulfing = (prev_close > prev_open) & (out["close"] < out["open"]) & (out["close"] < prev_open)
    rsi_below = rsi < cfg.rsi_index
    rsi_above = rsi > (100.0 - cfg.rsi_index)
    decrease_over = out["close"] < close_n
    increase_over = out["close"] > close_n

    bull_state = bullish_engulfing & stable & rsi_below & decrease_over & rsi.notna() & atr.notna()
    bear_state = bearish_engulfing & stable & rsi_above & increase_over & rsi.notna() & atr.notna()

    signals: list[str | None] = []
    tps: list[float] = []
    sls: list[float] = []
    last_signal = ""
    rrr_mult = cfg.reward_multiple
    for i in range(len(out)):
        is_bull = bool(bull_state.iloc[i])
        is_bear = bool(bear_state.iloc[i])
        if cfg.disable_repeating_signals:
            if is_bull and last_signal == "buy":
                is_bull = False
            if is_bear and last_signal == "sell":
                is_bear = False
        # If both somehow fire, skip rather than invent a priority.
        if is_bull and is_bear:
            signals.append(None)
            tps.append(float("nan"))
            sls.append(float("nan"))
            continue
        close = float(out["close"].iloc[i])
        atr_i = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else float("nan")
        if is_bull and math.isfinite(atr_i) and atr_i > 0:
            dist = atr_i * cfg.tp_sl_multiplier
            tp = round_up(close + dist * rrr_mult, cfg.tp_sl_precision)
            sl = round_up(close - dist, cfg.tp_sl_precision)
            signals.append("buy")
            tps.append(tp)
            sls.append(sl)
            last_signal = "buy"
        elif is_bear and math.isfinite(atr_i) and atr_i > 0:
            dist = atr_i * cfg.tp_sl_multiplier
            tp = round_up(close - dist * rrr_mult, cfg.tp_sl_precision)
            sl = round_up(close + dist, cfg.tp_sl_precision)
            signals.append("sell")
            tps.append(tp)
            sls.append(sl)
            last_signal = "sell"
        else:
            signals.append(None)
            tps.append(float("nan"))
            sls.append(float("nan"))

    out["rsi"] = rsi
    out["atr"] = atr
    out["tr"] = tr
    out["signal"] = signals
    out["tp"] = tps
    out["sl"] = sls
    return out


@dataclass
class _Pending:
    side: SignalSide
    tp: float
    sl: float


class GainzAlgoEngine:
    """Intraday paper engine: enter next bar open after a confirmed Alpha signal."""

    def __init__(self, algo: GainzAlgoConfig, risk: RiskConfig, broker: PaperBroker):
        self.algo = algo
        self.risk = risk
        self.broker = broker
        self.trades_today = 0
        self.day_start_equity = broker.capital
        self.halted = False
        self._entries: dict[str, int] = {}
        self._pending: dict[str, _Pending] = {}
        self._prepared: dict[str, pd.DataFrame] = {}

    def prepare(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        scored = compute_signals(df, self.algo)
        self._prepared[symbol] = scored
        return scored

    def reset_day(self, equity: float) -> None:
        self.trades_today = 0
        self.day_start_equity = equity
        self.halted = False
        self._entries.clear()
        self._pending.clear()

    def _position_size(self, entry: float, stop: float) -> int:
        risk_cash = self.risk.capital * (self.risk.risk_per_trade_pct / 100.0)
        per_share = abs(entry - stop)
        if per_share <= 0:
            return 0
        qty = int(risk_cash // per_share)
        max_by_cash = int(self.broker.cash // (entry * 1.001))
        return max(0, min(qty, max_by_cash))

    def _daily_loss_hit(self, marks: dict[str, float]) -> bool:
        eq = self.broker.equity(marks)
        if self.day_start_equity <= 0:
            return False
        loss_pct = (self.day_start_equity - eq) / self.day_start_equity * 100.0
        return loss_pct >= self.risk.max_daily_loss_pct

    def _flatten(self, symbol: str, price: float, ts: datetime, reason: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        fill = self.broker.flat(symbol, price, ts, reason)
        if fill:
            events.append({"event": "exit", "fill": fill})
        self._pending.pop(symbol, None)
        return events

    def _manage_open(self, symbol: str, row: pd.Series, ts: datetime) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        pos = self.broker.positions.get(symbol)
        if not pos:
            return events
        high = float(row["high"])
        low = float(row["low"])
        if pos.side == "BUY":
            stop_hit = low <= pos.stop
            target_hit = high >= pos.target
            if stop_hit and target_hit:
                fill = self.broker.flat(symbol, pos.stop, ts, "stop")
            elif stop_hit:
                fill = self.broker.flat(symbol, pos.stop, ts, "stop")
            elif target_hit:
                fill = self.broker.flat(symbol, pos.target, ts, "target")
            else:
                return events
        else:
            stop_hit = high >= pos.stop
            target_hit = low <= pos.target
            if stop_hit and target_hit:
                fill = self.broker.flat(symbol, pos.stop, ts, "stop")
            elif stop_hit:
                fill = self.broker.flat(symbol, pos.stop, ts, "stop")
            elif target_hit:
                fill = self.broker.flat(symbol, pos.target, ts, "target")
            else:
                return events
        if fill:
            events.append({"event": "exit", "fill": fill})
        return events

    def _try_entry(self, symbol: str, side: SignalSide, entry: float, stop: float, target: float, ts: datetime) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if self.halted:
            return events
        if self.trades_today >= self.risk.max_trades_per_day:
            return events
        if self.broker.open_count >= self.risk.max_open_positions:
            return events
        if symbol in self.broker.positions:
            return events
        entries = self._entries.get(symbol, 0)
        if entries >= self.algo.max_entries_per_symbol:
            return events
        if entries > 0 and not self.algo.allow_reentry:
            return events
        if side == "sell" and not self.algo.allow_shorts:
            return events
        if stop <= 0 or target <= 0 or entry <= 0:
            return events
        if side == "buy" and not (stop < entry < target):
            return events
        if side == "sell" and not (target < entry < stop):
            return events
        qty = self._position_size(entry, stop)
        if qty <= 0:
            return events
        reason = "gainz_buy" if side == "buy" else "gainz_sell"
        if side == "buy":
            fill = self.broker.buy(symbol, qty, entry, ts, stop, target, reason)
        else:
            fill = self.broker.sell(symbol, qty, entry, ts, reason, stop=stop, target=target)
        if fill:
            self.trades_today += 1
            self._entries[symbol] = entries + 1
            events.append({"event": "entry", "fill": fill, "stop": stop, "target": target, "side": side})
        return events

    def on_bar(self, symbol: str, bars: pd.DataFrame, i: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        row = bars.iloc[i]
        ts: datetime = bars.index[i].to_pydatetime()
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime()
        marks = {symbol: float(row["close"])}

        if self._daily_loss_hit(marks):
            self.halted = True
            events.extend(self._flatten(symbol, float(row["close"]), ts, "max_daily_loss"))
            return events

        if ts.time() >= self.risk.square_off_time:
            events.extend(self._flatten(symbol, float(row["close"]), ts, "square_off"))
            return events

        pending = self._pending.pop(symbol, None)
        if pending:
            events.extend(
                self._try_entry(
                    symbol,
                    pending.side,
                    float(row["open"]),
                    pending.sl,
                    pending.tp,
                    ts,
                )
            )

        events.extend(self._manage_open(symbol, row, ts))

        scored = self._prepared.get(symbol)
        if scored is None or bars.index[i] not in scored.index:
            return events
        sig_row = scored.loc[bars.index[i]]
        if isinstance(sig_row, pd.DataFrame):
            sig_row = sig_row.iloc[-1]
        signal = sig_row.get("signal")
        if signal not in ("buy", "sell"):
            return events
        tp = float(sig_row["tp"])
        sl = float(sig_row["sl"])
        if not math.isfinite(tp) or not math.isfinite(sl):
            return events
        # Last bar of the session cannot be filled overnight; skip pending.
        is_last = i == len(bars) - 1
        if is_last:
            return events
        nxt = bars.index[i + 1].to_pydatetime()
        if isinstance(nxt, pd.Timestamp):
            nxt = nxt.to_pydatetime()
        if nxt.time() >= self.risk.square_off_time:
            return events
        self._pending[symbol] = _Pending(side=signal, tp=tp, sl=sl)
        return events
