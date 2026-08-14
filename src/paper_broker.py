from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .costs import CostConfig, apply_slippage, estimate_charges

Side = Literal["BUY", "SELL"]


@dataclass
class Fill:
    time: datetime
    symbol: str
    side: Side
    qty: int
    price: float
    charges: float
    reason: str


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float
    side: Side  # long = BUY
    stop: float
    target: float
    entries: int = 1


@dataclass
class PaperBroker:
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
        # Cash already excludes capital tied in positions; add mark-to-market value.
        value = self.cash
        for sym, pos in self.positions.items():
            px = marks.get(sym, pos.avg_price)
            value += px * pos.qty
        return value

    def buy(self, symbol: str, qty: int, price: float, time: datetime, stop: float, target: float, reason: str) -> Fill | None:
        if qty <= 0:
            return None
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

    def sell(self, symbol: str, qty: int, price: float, time: datetime, reason: str) -> Fill | None:
        pos = self.positions.get(symbol)
        if not pos or qty <= 0:
            return None
        qty = min(qty, pos.qty)
        fill_px = apply_slippage("SELL", price, self.costs)
        charges = estimate_charges("SELL", fill_px, qty, self.costs).total
        proceeds = fill_px * qty - charges
        pnl = (fill_px - pos.avg_price) * qty - charges
        # buy charges already deducted from cash; only sell charges here for realized
        # Approximate realized vs entry avg (entry charges already in cash)
        self.realized_pnl += (fill_px - pos.avg_price) * qty - charges
        self.cash += proceeds
        self.total_charges += charges
        pos.qty -= qty
        if pos.qty == 0:
            del self.positions[symbol]
        fill = Fill(time, symbol, "SELL", qty, fill_px, charges, reason)
        self.fills.append(fill)
        return fill

    def flat(self, symbol: str, price: float, time: datetime, reason: str) -> Fill | None:
        pos = self.positions.get(symbol)
        if not pos:
            return None
        return self.sell(symbol, pos.qty, price, time, reason)
