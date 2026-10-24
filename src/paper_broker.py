from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .costs import CostConfig, apply_slippage, estimate_charges

Side = Literal["BUY", "SELL"]  # BUY = long, SELL = short


@dataclass
class Fill:
    time: datetime
    symbol: str
    side: Side  # order side executed
    qty: int
    price: float
    charges: float
    reason: str


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float
    side: Side  # BUY = long, SELL = short
    stop: float
    target: float
    entries: int = 1


@dataclass
class PaperBroker:
    """Cash-account paper broker for long and short intraday positions.

    ``buy`` / ``sell`` open, add, or close. ``open_long`` / ``open_short`` are
    the single-entry helpers the opening-range engine uses. ``flat`` closes
    whichever side is open. No live orders are sent.
    """

    capital: float
    costs: CostConfig
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    realized_pnl: float = 0.0
    total_charges: float = 0.0

    def __post_init__(self) -> None:
        self.cash = float(self.capital)

    @property
    def open_count(self) -> int:
        return len(self.positions)

    def equity(self, marks: dict[str, float]) -> float:
        value = self.cash
        for sym, pos in self.positions.items():
            px = marks.get(sym, pos.avg_price)
            if pos.side == "BUY":
                value += px * pos.qty
            else:
                # Short: sale proceeds are already in cash; subtract the cover liability.
                value -= px * pos.qty
        return value

    def buy(
        self,
        symbol: str,
        qty: int,
        price: float,
        time: datetime,
        stop: float,
        target: float,
        reason: str,
    ) -> Fill | None:
        if qty <= 0:
            return None
        pos = self.positions.get(symbol)
        if pos and pos.side == "SELL":
            return self._cover_short(pos, qty, price, time, reason)
        return self._open_or_add_long(symbol, qty, price, time, stop, target, reason)

    def sell(
        self,
        symbol: str,
        qty: int,
        price: float,
        time: datetime,
        reason: str,
        stop: float | None = None,
        target: float | None = None,
    ) -> Fill | None:
        if qty <= 0:
            return None
        pos = self.positions.get(symbol)
        if pos and pos.side == "BUY":
            return self._close_long(pos, qty, price, time, reason)
        if stop is None or target is None:
            return None
        return self._open_or_add_short(symbol, qty, price, time, stop, target, reason)

    def open_long(
        self,
        symbol: str,
        qty: int,
        price: float,
        time: datetime,
        stop: float,
        target: float,
        reason: str,
    ) -> Fill | None:
        if qty <= 0 or symbol in self.positions:
            return None
        return self._open_or_add_long(symbol, qty, price, time, stop, target, reason)

    def open_short(
        self,
        symbol: str,
        qty: int,
        price: float,
        time: datetime,
        stop: float,
        target: float,
        reason: str,
    ) -> Fill | None:
        if qty <= 0 or symbol in self.positions:
            return None
        return self._open_or_add_short(symbol, qty, price, time, stop, target, reason)

    def flat(self, symbol: str, price: float, time: datetime, reason: str) -> Fill | None:
        pos = self.positions.get(symbol)
        if not pos or pos.qty <= 0:
            return None
        if pos.side == "BUY":
            return self._close_long(pos, pos.qty, price, time, reason)
        return self._cover_short(pos, pos.qty, price, time, reason)

    def _open_or_add_long(
        self,
        symbol: str,
        qty: int,
        price: float,
        time: datetime,
        stop: float,
        target: float,
        reason: str,
    ) -> Fill | None:
        fill_px = apply_slippage("BUY", price, self.costs)
        charges = estimate_charges("BUY", fill_px, qty, self.costs).total
        cost = fill_px * qty + charges
        if cost > self.cash:
            return None
        self.cash -= cost
        self.total_charges += charges
        existing = self.positions.get(symbol)
        if existing:
            new_qty = existing.qty + qty
            existing.avg_price = (existing.avg_price * existing.qty + fill_px * qty) / new_qty
            existing.qty = new_qty
            existing.entries += 1
            existing.stop = stop
            existing.target = target
        else:
            self.positions[symbol] = Position(symbol, qty, fill_px, "BUY", stop, target, 1)
        fill = Fill(time, symbol, "BUY", qty, fill_px, charges, reason)
        self.fills.append(fill)
        return fill

    def _close_long(self, pos: Position, qty: int, price: float, time: datetime, reason: str) -> Fill | None:
        qty = min(qty, pos.qty)
        fill_px = apply_slippage("SELL", price, self.costs)
        charges = estimate_charges("SELL", fill_px, qty, self.costs).total
        proceeds = fill_px * qty - charges
        self.realized_pnl += (fill_px - pos.avg_price) * qty - charges
        self.cash += proceeds
        self.total_charges += charges
        pos.qty -= qty
        if pos.qty == 0:
            del self.positions[pos.symbol]
        fill = Fill(time, pos.symbol, "SELL", qty, fill_px, charges, reason)
        self.fills.append(fill)
        return fill

    def _open_or_add_short(
        self,
        symbol: str,
        qty: int,
        price: float,
        time: datetime,
        stop: float,
        target: float,
        reason: str,
    ) -> Fill | None:
        fill_px = apply_slippage("SELL", price, self.costs)
        charges = estimate_charges("SELL", fill_px, qty, self.costs).total
        # Conservative MIS proxy: require full notional in cash (no extra leverage).
        if fill_px * qty + charges > self.cash:
            return None
        self.cash += fill_px * qty - charges
        self.total_charges += charges
        existing = self.positions.get(symbol)
        if existing:
            new_qty = existing.qty + qty
            existing.avg_price = (existing.avg_price * existing.qty + fill_px * qty) / new_qty
            existing.qty = new_qty
            existing.entries += 1
            existing.stop = stop
            existing.target = target
        else:
            self.positions[symbol] = Position(symbol, qty, fill_px, "SELL", stop, target, 1)
        fill = Fill(time, symbol, "SELL", qty, fill_px, charges, reason)
        self.fills.append(fill)
        return fill

    def _cover_short(self, pos: Position, qty: int, price: float, time: datetime, reason: str) -> Fill | None:
        qty = min(qty, pos.qty)
        fill_px = apply_slippage("BUY", price, self.costs)
        charges = estimate_charges("BUY", fill_px, qty, self.costs).total
        self.realized_pnl += (pos.avg_price - fill_px) * qty - charges
        self.cash -= fill_px * qty + charges
        self.total_charges += charges
        pos.qty -= qty
        if pos.qty == 0:
            del self.positions[pos.symbol]
        fill = Fill(time, pos.symbol, "BUY", qty, fill_px, charges, reason)
        self.fills.append(fill)
        return fill
