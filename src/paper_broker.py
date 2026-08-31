from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .costs import CostConfig, apply_slippage, estimate_charges

Side = Literal["BUY", "SELL"]  # position direction: BUY=long, SELL=short


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
    side: Side  # BUY=long, SELL=short
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
        value = self.cash
        for sym, pos in self.positions.items():
            px = marks.get(sym, pos.avg_price)
            if pos.side == "BUY":
                value += px * pos.qty
            else:
                # Short: cash already includes sale proceeds; subtract repurchase liability
                value -= px * pos.qty
        return value

    def open_long(
        self, symbol: str, qty: int, price: float, time: datetime, stop: float, target: float, reason: str
    ) -> Fill | None:
        if qty <= 0 or symbol in self.positions:
            return None
        fill_px = apply_slippage("BUY", price, self.costs)
        charges = estimate_charges("BUY", fill_px, qty, self.costs).total
        cost = fill_px * qty + charges
        if cost > self.cash:
            return None
        self.cash -= cost
        self.total_charges += charges
        self.positions[symbol] = Position(symbol, qty, fill_px, "BUY", stop, target, 1)
        fill = Fill(time, symbol, "BUY", qty, fill_px, charges, reason)
        self.fills.append(fill)
        return fill

    def open_short(
        self, symbol: str, qty: int, price: float, time: datetime, stop: float, target: float, reason: str
    ) -> Fill | None:
        if qty <= 0 or symbol in self.positions:
            return None
        fill_px = apply_slippage("SELL", price, self.costs)
        charges = estimate_charges("SELL", fill_px, qty, self.costs).total
        # Require margin-like cash reserve ≈ notional (simple MIS proxy)
        if fill_px * qty + charges > self.cash:
            return None
        self.cash += fill_px * qty - charges
        self.total_charges += charges
        self.positions[symbol] = Position(symbol, qty, fill_px, "SELL", stop, target, 1)
        fill = Fill(time, symbol, "SELL", qty, fill_px, charges, reason)
        self.fills.append(fill)
        return fill

    def flat(self, symbol: str, price: float, time: datetime, reason: str) -> Fill | None:
        pos = self.positions.get(symbol)
        if not pos or pos.qty <= 0:
            return None
        qty = pos.qty
        if pos.side == "BUY":
            fill_px = apply_slippage("SELL", price, self.costs)
            charges = estimate_charges("SELL", fill_px, qty, self.costs).total
            proceeds = fill_px * qty - charges
            self.realized_pnl += (fill_px - pos.avg_price) * qty - charges
            self.cash += proceeds
            order_side: Side = "SELL"
        else:
            fill_px = apply_slippage("BUY", price, self.costs)
            charges = estimate_charges("BUY", fill_px, qty, self.costs).total
            cost = fill_px * qty + charges
            if cost > self.cash + fill_px * qty:  # allow using short proceeds already in cash
                pass
            self.realized_pnl += (pos.avg_price - fill_px) * qty - charges
            self.cash -= cost
            order_side = "BUY"
        self.total_charges += charges
        del self.positions[symbol]
        fill = Fill(time, symbol, order_side, qty, fill_px, charges, reason)
        self.fills.append(fill)
        return fill

    # Back-compat aliases used by older call sites
    def buy(self, symbol: str, qty: int, price: float, time: datetime, stop: float, target: float, reason: str) -> Fill | None:
        return self.open_long(symbol, qty, price, time, stop, target, reason)

    def sell(self, symbol: str, qty: int, price: float, time: datetime, reason: str) -> Fill | None:
        pos = self.positions.get(symbol)
        if not pos or pos.side != "BUY":
            return None
        return self.flat(symbol, price, time, reason)
