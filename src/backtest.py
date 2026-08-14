"""Shared ORB backtest runner used by CLI and web UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .costs import CostConfig, round_trip_breakeven_pct
from .data import DataConfig, day_slice, fetch_intraday, session_days
from .paper_broker import Fill, PaperBroker
from .strategy_orb import OrbConfig, OrbEngine, RiskConfig

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or (ROOT / "config.yaml")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def parse_symbols(text: str) -> list[str]:
    parts = [p.strip().upper() for p in text.replace(";", ",").replace(" ", ",").split(",")]
    return [p for p in parts if p]


@dataclass
class BacktestResult:
    symbols: list[str]
    sessions: int
    capital: float
    ending_cash: float
    realized_pnl: float
    total_charges: float
    round_trips: int
    wins: int
    losses: int
    win_rate: float
    avg_win: float | None
    avg_loss: float | None
    best_trade: float | None
    worst_trade: float | None
    exit_reasons: dict[str, int]
    breakeven_pct: float
    fills: list[dict[str, Any]]
    trade_pnls: list[float]
    skipped: list[str]
    date_start: str | None
    date_end: str | None
    log_path: str | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "symbols": ", ".join(self.symbols),
            "sessions": self.sessions,
            "capital": self.capital,
            "ending_cash": self.ending_cash,
            "realized_pnl": self.realized_pnl,
            "total_charges": self.total_charges,
            "round_trips": self.round_trips,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate_pct": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "best_trade": self.best_trade,
            "worst_trade": self.worst_trade,
            "exit_reasons": self.exit_reasons,
            "date_start": self.date_start,
            "date_end": self.date_end,
        }


def _fills_to_rows(fills: list[Fill]) -> list[dict[str, Any]]:
    rows = []
    for fill in fills:
        rows.append(
            {
                "time": fill.time.isoformat(sep=" ", timespec="minutes"),
                "symbol": fill.symbol,
                "side": fill.side,
                "qty": fill.qty,
                "price": round(fill.price, 4),
                "charges": round(fill.charges, 2),
                "reason": fill.reason,
            }
        )
    return rows


def _pair_round_trips(fills: list[Fill]) -> tuple[list[float], dict[str, int]]:
    open_stack: list[Fill] = []
    round_trips: list[float] = []
    exit_reasons: dict[str, int] = {}
    for fill in fills:
        if fill.side == "BUY":
            open_stack.append(fill)
        elif fill.side == "SELL" and open_stack:
            entry = open_stack.pop()
            qty = min(entry.qty, fill.qty)
            pnl = (fill.price - entry.price) * qty - entry.charges - fill.charges
            round_trips.append(pnl)
            exit_reasons[fill.reason] = exit_reasons.get(fill.reason, 0) + 1
    return round_trips, exit_reasons


def run_backtest(
    symbols: list[str],
    cfg: dict[str, Any] | None = None,
    *,
    lookback_days: int | None = None,
    days: int | None = None,
    capital: float | None = None,
    opening_range_minutes: int | None = None,
    target_r: float | None = None,
    write_log: bool = True,
) -> BacktestResult:
    cfg = cfg or load_config()
    symbols = [s.strip().upper() for s in symbols if s and s.strip()]
    if not symbols:
        raise ValueError("Provide at least one NSE symbol (e.g. CUPID, SBIN)")

    costs = CostConfig(**cfg.get("costs", {}))
    data_cfg = DataConfig(**cfg.get("data", {}))
    if lookback_days:
        data_cfg.lookback_days = int(lookback_days)

    strat = dict(cfg.get("strategy", {}))
    if opening_range_minutes is not None:
        strat["opening_range_minutes"] = opening_range_minutes
    if target_r is not None:
        strat["target_r"] = target_r

    orb = OrbConfig(
        opening_range_minutes=int(strat.get("opening_range_minutes", 15)),
        target_r=float(strat.get("target_r", 1.5)),
        stop_buffer_pct=float(strat.get("stop_buffer_pct", 0.05)),
        min_or_range_pct=float(strat.get("min_or_range_pct", 0.35)),
        allow_reentry=bool(strat.get("allow_reentry", True)),
        max_entries_per_symbol=int(strat.get("max_entries_per_symbol", 3)),
    )

    start_capital = float(capital if capital is not None else cfg["capital"])
    risk = RiskConfig(
        capital=start_capital,
        risk_per_trade_pct=float(cfg["risk_per_trade_pct"]),
        max_daily_loss_pct=float(cfg["max_daily_loss_pct"]),
        max_trades_per_day=int(cfg["max_trades_per_day"]),
        max_open_positions=int(cfg["max_open_positions"]),
        square_off_time=parse_hhmm(cfg.get("square_off_time", "15:15")),
    )

    broker = PaperBroker(capital=risk.capital, costs=costs)
    engine = OrbEngine(orb, risk, broker)

    frames: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    for sym in symbols:
        df = fetch_intraday(sym, data_cfg)
        if df.empty:
            skipped.append(sym)
            continue
        frames[sym] = df

    if not frames:
        raise RuntimeError(f"No market data for: {', '.join(symbols)}")

    all_days: set = set()
    for df in frames.values():
        all_days.update(session_days(df))
    trade_days = sorted(all_days)
    if days:
        trade_days = trade_days[-int(days) :]

    date_start = trade_days[0].date().isoformat() if trade_days else None
    date_end = trade_days[-1].date().isoformat() if trade_days else None

    for day in trade_days:
        engine.reset_day(broker.equity({}))
        for sym, df in frames.items():
            bars = day_slice(df, day)
            if len(bars) < 5:
                continue
            for i in range(len(bars)):
                engine.on_bar(sym, bars, i)
            if sym in broker.positions:
                last = bars.iloc[-1]
                ts = bars.index[-1].to_pydatetime()
                broker.flat(sym, float(last["close"]), ts, "eod_force")

    round_trips, exit_reasons = _pair_round_trips(broker.fills)
    wins = [p for p in round_trips if p > 0]
    losses = [p for p in round_trips if p <= 0]
    wr = (len(wins) / len(round_trips) * 100.0) if round_trips else 0.0

    log_path = None
    if write_log and broker.fills:
        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sym_tag = "_".join(frames.keys())[:40]
        path = log_dir / f"backtest_{sym_tag}_{stamp}.csv"
        pd.DataFrame(_fills_to_rows(broker.fills)).to_csv(path, index=False)
        log_path = str(path)

    return BacktestResult(
        symbols=list(frames.keys()),
        sessions=len(trade_days),
        capital=start_capital,
        ending_cash=broker.cash,
        realized_pnl=broker.realized_pnl,
        total_charges=broker.total_charges,
        round_trips=len(round_trips),
        wins=len(wins),
        losses=len(losses),
        win_rate=wr,
        avg_win=(sum(wins) / len(wins)) if wins else None,
        avg_loss=(sum(losses) / len(losses)) if losses else None,
        best_trade=max(round_trips) if round_trips else None,
        worst_trade=min(round_trips) if round_trips else None,
        exit_reasons=exit_reasons,
        breakeven_pct=round_trip_breakeven_pct(costs, 100_000),
        fills=_fills_to_rows(broker.fills),
        trade_pnls=round_trips,
        skipped=skipped,
        date_start=date_start,
        date_end=date_end,
        log_path=log_path,
    )
