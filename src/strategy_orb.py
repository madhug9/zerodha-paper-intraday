from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Literal

import pandas as pd

from .paper_broker import PaperBroker

TradeMode = Literal["both", "long_only", "short_only"]


@dataclass
class OrbConfig:
    opening_range_minutes: int = 15
    target_r: float = 1.5
    stop_buffer_pct: float = 0.05
    min_or_range_pct: float = 0.35
    allow_reentry: bool = True
    max_entries_per_symbol: int = 3
    trade_mode: TradeMode = "both"  # long buy→sell, short sell→buy, or both


@dataclass
class RiskConfig:
    capital: float
    risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_trades_per_day: int
    max_open_positions: int
    square_off_time: time


class OrbEngine:
    """Opening-range breakout with long and/or short entries (paper)."""

    def __init__(self, orb: OrbConfig, risk: RiskConfig, broker: PaperBroker):
        self.orb = orb
        self.risk = risk
        self.broker = broker
        self.trades_today = 0
        self.day_start_equity = broker.capital
        self.halted = False
        self._or: dict[str, tuple[float, float]] = {}
        self._entries: dict[str, int] = {}
        self._armed: dict[str, bool] = {}

    def reset_day(self, equity: float) -> None:
        self.trades_today = 0
        self.day_start_equity = equity
        self.halted = False
        self._or.clear()
        self._entries.clear()
        self._armed.clear()

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
        loss_pct = (self.day_start_equity - eq) / self.day_start_equity * 100.0
        return loss_pct >= self.risk.max_daily_loss_pct

    def _manage_open(self, symbol: str, row: Any, ts: datetime, or_high: float, or_low: float) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        pos = self.broker.positions.get(symbol)
        if not pos:
            return events

        high = float(row["high"])
        low = float(row["low"])

        if pos.side == "BUY":
            # Long: stop below, target above
            if low <= pos.stop:
                fill = self.broker.flat(symbol, pos.stop, ts, "stop")
                if fill:
                    events.append({"event": "exit", "fill": fill})
                    self._armed[symbol] = False
                return events
            if high >= pos.target:
                fill = self.broker.flat(symbol, pos.target, ts, "target")
                if fill:
                    events.append({"event": "exit", "fill": fill})
                    self._armed[symbol] = False
                return events
        else:
            # Short: stop above, target below
            if high >= pos.stop:
                fill = self.broker.flat(symbol, pos.stop, ts, "stop")
                if fill:
                    events.append({"event": "exit", "fill": fill})
                    self._armed[symbol] = False
                return events
            if low <= pos.target:
                fill = self.broker.flat(symbol, pos.target, ts, "target")
                if fill:
                    events.append({"event": "exit", "fill": fill})
                    self._armed[symbol] = False
                return events
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
            if symbol in self.broker.positions:
                fill = self.broker.flat(symbol, float(row["close"]), ts, "max_daily_loss")
                if fill:
                    events.append({"event": "exit", "fill": fill})
            return events

        if ts.time() >= self.risk.square_off_time:
            if symbol in self.broker.positions:
                fill = self.broker.flat(symbol, float(row["close"]), ts, "square_off")
                if fill:
                    events.append({"event": "exit", "fill": fill})
            return events

        session_open = bars.index[0]
        or_end = session_open + pd.Timedelta(minutes=self.orb.opening_range_minutes)
        if symbol not in self._or:
            if bars.index[i] < or_end:
                return events
            or_bars = bars[bars.index <= or_end]
            if or_bars.empty:
                return events
            or_high = float(or_bars["high"].max())
            or_low = float(or_bars["low"].min())
            mid = (or_high + or_low) / 2.0
            rng_pct = (or_high - or_low) / mid * 100.0 if mid else 0.0
            if rng_pct < self.orb.min_or_range_pct:
                self._or[symbol] = (or_high, or_low)
                return events
            self._or[symbol] = (or_high, or_low)
            self._armed[symbol] = True
            return events

        or_high, or_low = self._or[symbol]
        if or_high <= or_low:
            return events

        if symbol in self.broker.positions:
            return self._manage_open(symbol, row, ts, or_high, or_low)

        # Re-arm after exit: price must return inside the opening range
        if not self._armed.get(symbol, True):
            close = float(row["close"])
            if or_low < close < or_high:
                self._armed[symbol] = True
            else:
                return events

        if self.halted:
            return events
        if self.trades_today >= self.risk.max_trades_per_day:
            return events
        if self.broker.open_count >= self.risk.max_open_positions:
            return events

        entries = self._entries.get(symbol, 0)
        if entries >= self.orb.max_entries_per_symbol:
            return events
        if entries > 0 and not self.orb.allow_reentry:
            return events

        mode = self.orb.trade_mode
        allow_long = mode in ("both", "long_only")
        allow_short = mode in ("both", "short_only")

        high = float(row["high"])
        low = float(row["low"])

        # Prefer the side that breaks first in this bar; if both, use close bias
        long_signal = allow_long and high > or_high
        short_signal = allow_short and low < or_low
        if long_signal and short_signal:
            close = float(row["close"])
            if close >= (or_high + or_low) / 2.0:
                short_signal = False
            else:
                long_signal = False

        if long_signal:
            entry = or_high
            stop = or_low * (1.0 - self.orb.stop_buffer_pct / 100.0)
            risk = entry - stop
            if risk > 0:
                target = entry + risk * self.orb.target_r
                qty = self._position_size(entry, stop)
                if qty > 0:
                    fill = self.broker.open_long(symbol, qty, entry, ts, stop, target, "orb_long")
                    if fill:
                        self.trades_today += 1
                        self._entries[symbol] = entries + 1
                        events.append({"event": "entry", "fill": fill, "stop": stop, "target": target})
            return events

        if short_signal:
            entry = or_low
            stop = or_high * (1.0 + self.orb.stop_buffer_pct / 100.0)
            risk = stop - entry
            if risk > 0:
                target = entry - risk * self.orb.target_r
                qty = self._position_size(entry, stop)
                if qty > 0:
                    fill = self.broker.open_short(symbol, qty, entry, ts, stop, target, "orb_short")
                    if fill:
                        self.trades_today += 1
                        self._entries[symbol] = entries + 1
                        events.append({"event": "entry", "fill": fill, "stop": stop, "target": target})
        return events
