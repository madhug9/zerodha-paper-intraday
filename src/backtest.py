"""Shared backtest runner used by the CLI, Streamlit, and the local web GUI."""

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
from .strategy_gainzalgo import GainzAlgoConfig, GainzAlgoEngine
from .strategy_orb import OrbConfig, OrbEngine, RiskConfig
from .symbols import parse_symbols

ROOT = Path(__file__).resolve().parent.parent

TRADE_MODES = ("both", "long_only", "short_only")


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or (ROOT / "config.yaml")
    with cfg_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def normalize_trade_mode(value: str | None) -> str:
    mode = str(value or "both").strip().lower()
    if mode not in TRADE_MODES:
        return "both"
    return mode


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
    strategy: str = "gainzalgo"
    signals: list[dict[str, Any]] | None = None
    log_path: str | None = None
    trade_mode: str = "both"
    chart: dict[str, Any] | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "trade_mode": self.trade_mode,
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
    """Pair FIFO long (BUY then SELL) and short (SELL then BUY) round trips."""
    open_stack: list[Fill] = []
    round_trips: list[float] = []
    exit_reasons: dict[str, int] = {}
    for fill in fills:
        if not open_stack:
            open_stack.append(fill)
            continue
        entry = open_stack[-1]
        is_exit = (entry.side == "BUY" and fill.side == "SELL") or (entry.side == "SELL" and fill.side == "BUY")
        if not is_exit:
            open_stack.append(fill)
            continue
        open_stack.pop()
        qty = min(entry.qty, fill.qty)
        if entry.side == "BUY":
            pnl = (fill.price - entry.price) * qty - entry.charges - fill.charges
        else:
            pnl = (entry.price - fill.price) * qty - entry.charges - fill.charges
        round_trips.append(pnl)
        exit_reasons[fill.reason] = exit_reasons.get(fill.reason, 0) + 1
    return round_trips, exit_reasons


def _gainzalgo_config(strat: dict[str, Any]) -> GainzAlgoConfig:
    mode = normalize_trade_mode(strat.get("trade_mode"))
    allow_shorts = bool(strat.get("allow_shorts", True))
    if mode == "long_only":
        allow_shorts = False
    return GainzAlgoConfig(
        rsi_length=int(strat.get("rsi_length", 14)),
        atr_length=int(strat.get("atr_length", 14)),
        candle_stability_index=float(strat.get("candle_stability_index", 0.7)),
        rsi_index=float(strat.get("rsi_index", 80)),
        candle_delta_length=int(strat.get("candle_delta_length", 10)),
        disable_repeating_signals=bool(strat.get("disable_repeating_signals", True)),
        rrr=str(strat.get("rrr", "1:2")),
        tp_sl_multiplier=float(strat.get("tp_sl_multiplier", 1.0)),
        tp_sl_precision=int(strat.get("tp_sl_precision", 2)),
        allow_shorts=allow_shorts,
        allow_reentry=bool(strat.get("allow_reentry", True)),
        max_entries_per_symbol=int(strat.get("max_entries_per_symbol", 3)),
        trade_mode=mode,  # type: ignore[arg-type]
    )


def _orb_config(strat: dict[str, Any]) -> OrbConfig:
    return OrbConfig(
        opening_range_minutes=int(strat.get("opening_range_minutes", 15)),
        target_r=float(strat.get("target_r", 1.5)),
        stop_buffer_pct=float(strat.get("stop_buffer_pct", 0.05)),
        min_or_range_pct=float(strat.get("min_or_range_pct", 0.35)),
        allow_reentry=bool(strat.get("allow_reentry", True)),
        max_entries_per_symbol=int(strat.get("max_entries_per_symbol", 3)),
        trade_mode=normalize_trade_mode(strat.get("trade_mode")),  # type: ignore[arg-type]
    )


def _signal_rows(prepared: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, df in prepared.items():
        hits = df[df["signal"].notna()]
        for ts, row in hits.iterrows():
            rows.append(
                {
                    "time": ts.isoformat(sep=" ", timespec="minutes") if hasattr(ts, "isoformat") else str(ts),
                    "symbol": symbol,
                    "signal": row["signal"],
                    "close": round(float(row["close"]), 4),
                    "tp": round(float(row["tp"]), 4),
                    "sl": round(float(row["sl"]), 4),
                    "rsi": round(float(row["rsi"]), 2) if pd.notna(row["rsi"]) else None,
                    "atr": round(float(row["atr"]), 4) if pd.notna(row["atr"]) else None,
                }
            )
    rows.sort(key=lambda r: (r["time"], r["symbol"]))
    return rows


def _chart_from_frames(frames: dict[str, pd.DataFrame], fills: list[dict[str, Any]]) -> dict[str, Any]:
    prices: dict[str, list[dict[str, Any]]] = {}
    for sym, df in frames.items():
        step = 1
        if len(df) > 4000:
            step = (len(df) + 3999) // 4000
        rows: list[dict[str, Any]] = []
        for ts, row in df.iloc[::step].iterrows():
            stamp = ts.isoformat(sep=" ", timespec="minutes") if hasattr(ts, "isoformat") else str(ts)
            rows.append({"time": stamp, "close": round(float(row["close"]), 4)})
        prices[sym] = rows
    markers = [
        {
            "time": fill["time"],
            "symbol": fill["symbol"],
            "side": fill["side"],
            "price": fill["price"],
            "reason": fill["reason"],
        }
        for fill in fills
    ]
    return {"prices": prices, "markers": markers}


def run_backtest(
    symbols: list[str],
    cfg: dict[str, Any] | None = None,
    *,
    lookback_days: int | None = None,
    days: int | None = None,
    capital: float | None = None,
    strategy: str | None = None,
    opening_range_minutes: int | None = None,
    target_r: float | None = None,
    trade_mode: str | None = None,
    rrr: str | None = None,
    tp_sl_multiplier: float | None = None,
    allow_shorts: bool | None = None,
    write_log: bool = True,
    frames: dict[str, pd.DataFrame] | None = None,
    include_chart: bool = False,
) -> BacktestResult:
    cfg = cfg or load_config()
    symbols = parse_symbols(",".join(s for s in symbols if s and str(s).strip()))
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
    if rrr is not None:
        strat["rrr"] = rrr
    if tp_sl_multiplier is not None:
        strat["tp_sl_multiplier"] = tp_sl_multiplier
    if allow_shorts is not None:
        strat["allow_shorts"] = allow_shorts
    if trade_mode is not None:
        strat["trade_mode"] = trade_mode
    mode = normalize_trade_mode(strat.get("trade_mode"))
    strat["trade_mode"] = mode

    name = (strategy or strat.get("name") or "gainzalgo").strip().lower()
    if name not in {"gainzalgo", "orb"}:
        raise ValueError("strategy must be 'gainzalgo' or 'orb'")

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
    engine: GainzAlgoEngine | OrbEngine
    if name == "orb":
        engine = OrbEngine(_orb_config(strat), risk, broker)
    else:
        engine = GainzAlgoEngine(_gainzalgo_config(strat), risk, broker)

    skipped: list[str] = []
    loaded: dict[str, pd.DataFrame] = {}
    if frames is None:
        for sym in symbols:
            df = fetch_intraday(sym, data_cfg)
            if df.empty:
                skipped.append(sym)
                continue
            loaded[sym] = df
    else:
        for sym in symbols:
            df = frames.get(sym)
            if df is None or df.empty:
                skipped.append(sym)
                continue
            loaded[sym] = df

    if not loaded:
        raise RuntimeError(f"No market data for: {', '.join(symbols)}")

    prepared: dict[str, pd.DataFrame] = {}
    if isinstance(engine, GainzAlgoEngine):
        for sym, df in loaded.items():
            prepared[sym] = engine.prepare(sym, df)

    all_days: set = set()
    for df in loaded.values():
        all_days.update(session_days(df))
    trade_days = sorted(all_days)
    if days:
        trade_days = trade_days[-int(days) :]

    date_start = trade_days[0].date().isoformat() if trade_days else None
    date_end = trade_days[-1].date().isoformat() if trade_days else None

    for day in trade_days:
        engine.reset_day(broker.equity({}))
        for sym, df in loaded.items():
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
    fill_rows = _fills_to_rows(broker.fills)

    log_path = None
    if write_log and broker.fills:
        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sym_tag = "_".join(loaded.keys())[:40]
        path = log_dir / f"backtest_{sym_tag}_{stamp}.csv"
        pd.DataFrame(fill_rows).to_csv(path, index=False)
        log_path = str(path)

    return BacktestResult(
        symbols=list(loaded.keys()),
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
        fills=fill_rows,
        trade_pnls=round_trips,
        skipped=skipped,
        date_start=date_start,
        date_end=date_end,
        strategy=name,
        signals=_signal_rows(prepared) if prepared else None,
        log_path=log_path,
        trade_mode=mode,
        chart=_chart_from_frames(loaded, fill_rows) if include_chart else None,
    )
