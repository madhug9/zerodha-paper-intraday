from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    brokerage_rate: float = 0.0003
    brokerage_cap: float = 20.0
    stt_sell: float = 0.00025
    exchange_txn: float = 0.0000297
    sebi: float = 0.0000001
    stamp_buy: float = 0.00003
    gst: float = 0.18
    slippage_bps: float = 2.0


@dataclass(frozen=True)
class TradeCharges:
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp: float
    gst: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst


def _brokerage(turnover: float, cfg: CostConfig) -> float:
    return min(turnover * cfg.brokerage_rate, cfg.brokerage_cap)


def estimate_charges(side: str, price: float, qty: int, cfg: CostConfig) -> TradeCharges:
    """Approximate Zerodha equity intraday charges for one executed order."""
    turnover = abs(price * qty)
    brokerage = _brokerage(turnover, cfg)
    exchange = turnover * cfg.exchange_txn
    sebi = turnover * cfg.sebi
    stamp = turnover * cfg.stamp_buy if side.upper() == "BUY" else 0.0
    stt = turnover * cfg.stt_sell if side.upper() == "SELL" else 0.0
    gst = cfg.gst * (brokerage + exchange + sebi)
    return TradeCharges(brokerage, stt, exchange, sebi, stamp, gst)


def apply_slippage(side: str, price: float, cfg: CostConfig) -> float:
    slip = cfg.slippage_bps / 10_000.0
    if side.upper() == "BUY":
        return price * (1.0 + slip)
    return price * (1.0 - slip)


def round_trip_breakeven_pct(cfg: CostConfig, order_value: float = 100_000.0) -> float:
    """Rough % move needed to cover buy+sell charges + slippage (same qty)."""
    qty = max(1, int(order_value / 100.0))
    buy = estimate_charges("BUY", 100.0, qty, cfg)
    sell = estimate_charges("SELL", 100.0, qty, cfg)
    notional = 100.0 * qty
    slip = 2 * (cfg.slippage_bps / 10_000.0)
    return (buy.total + sell.total) / notional + slip
