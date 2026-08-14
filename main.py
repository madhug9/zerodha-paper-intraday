"""Zerodha NSE paper intraday — ORB multi-entry simulator."""

from __future__ import annotations

import argparse
from datetime import datetime, time
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.table import Table

from src.costs import CostConfig, round_trip_breakeven_pct
from src.data import DataConfig, day_slice, fetch_intraday, session_days
from src.paper_broker import PaperBroker
from src.strategy_orb import OrbConfig, OrbEngine, RiskConfig

ROOT = Path(__file__).resolve().parent
console = Console()


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def run_paper(cfg: dict[str, Any], symbol_filter: str | None = None, days: int | None = None) -> None:
    costs = CostConfig(**cfg.get("costs", {}))
    data_cfg = DataConfig(**cfg.get("data", {}))
    strat = cfg.get("strategy", {})
    orb = OrbConfig(
        opening_range_minutes=int(strat.get("opening_range_minutes", 15)),
        target_r=float(strat.get("target_r", 1.5)),
        stop_buffer_pct=float(strat.get("stop_buffer_pct", 0.05)),
        min_or_range_pct=float(strat.get("min_or_range_pct", 0.35)),
        allow_reentry=bool(strat.get("allow_reentry", True)),
        max_entries_per_symbol=int(strat.get("max_entries_per_symbol", 3)),
    )
    risk = RiskConfig(
        capital=float(cfg["capital"]),
        risk_per_trade_pct=float(cfg["risk_per_trade_pct"]),
        max_daily_loss_pct=float(cfg["max_daily_loss_pct"]),
        max_trades_per_day=int(cfg["max_trades_per_day"]),
        max_open_positions=int(cfg["max_open_positions"]),
        square_off_time=parse_hhmm(cfg.get("square_off_time", "15:15")),
    )

    watchlist = cfg["watchlist"]
    if symbol_filter:
        watchlist = [s.strip().upper() for s in symbol_filter.split(",") if s.strip()]

    broker = PaperBroker(capital=risk.capital, costs=costs)
    engine = OrbEngine(orb, risk, broker)

    be = round_trip_breakeven_pct(costs, 100_000)
    console.print(f"[bold]Paper capital[/bold]: Rs{risk.capital:,.0f}")
    console.print(f"[bold]Est. RT breakeven[/bold]: ~{be * 100:.3f}% (charges + slippage)")
    console.print(f"[bold]Strategy[/bold]: ORB {orb.opening_range_minutes}m | target {orb.target_r}R | reentry={orb.allow_reentry}")
    console.print(f"[bold]Watchlist[/bold]: {', '.join(watchlist)}\n")

    frames: dict[str, Any] = {}
    for sym in watchlist:
        console.print(f"Fetching {sym}...")
        df = fetch_intraday(sym, data_cfg)
        if df.empty:
            console.print(f"  [yellow]No data for {sym} — skipped[/yellow]")
            continue
        frames[sym] = df
        console.print(f"  {len(df)} bars")

    if not frames:
        console.print("[red]No market data available. Check network / symbols.[/red]")
        return

    # Union of trading days across symbols, newest last
    all_days: set = set()
    for df in frames.values():
        all_days.update(session_days(df))
    trade_days = sorted(all_days)
    if days:
        trade_days = trade_days[-days:]

    for day in trade_days:
        engine.reset_day(broker.equity({}))
        console.rule(f"{day.date()}")
        day_events = 0
        for sym, df in frames.items():
            bars = day_slice(df, day)
            if len(bars) < 5:
                continue
            for i in range(len(bars)):
                for ev in engine.on_bar(sym, bars, i):
                    day_events += 1
                    fill = ev["fill"]
                    tag = ev["event"].upper()
                    extra = ""
                    if "stop" in ev:
                        extra = f" stop={ev['stop']:.2f} tgt={ev['target']:.2f}"
                    console.print(
                        f"  {fill.time.strftime('%H:%M')} {tag:5} {fill.side:4} {fill.symbol:12} "
                        f"x{fill.qty} @ {fill.price:.2f} chg={fill.charges:.2f} ({fill.reason}){extra}"
                    )
                # Force flat leftover at end of symbol day if still open past last bar
            if sym in broker.positions:
                last = bars.iloc[-1]
                ts = bars.index[-1].to_pydatetime()
                fill = broker.flat(sym, float(last["close"]), ts, "eod_force")
                if fill:
                    console.print(
                        f"  {fill.time.strftime('%H:%M')} EXIT  {fill.side:4} {fill.symbol:12} "
                        f"x{fill.qty} @ {fill.price:.2f} chg={fill.charges:.2f} ({fill.reason})"
                    )

        if day_events == 0:
            console.print("  (no ORB trades)")

    # Summary
    table = Table(title="Paper session summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Starting capital", f"Rs{risk.capital:,.2f}")
    table.add_row("Ending cash", f"Rs{broker.cash:,.2f}")
    table.add_row("Realized P&L", f"Rs{broker.realized_pnl:,.2f}")
    table.add_row("Total charges", f"Rs{broker.total_charges:,.2f}")
    table.add_row("Fills", str(len(broker.fills)))
    table.add_row("Open positions", str(broker.open_count))
    console.print(table)

    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"paper_{stamp}.csv"
    if broker.fills:
        import csv

        with log_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "symbol", "side", "qty", "price", "charges", "reason"])
            for fill in broker.fills:
                w.writerow([fill.time.isoformat(), fill.symbol, fill.side, fill.qty, f"{fill.price:.4f}", f"{fill.charges:.2f}", fill.reason])
        console.print(f"\nWrote [cyan]{log_path}[/cyan]")


def main() -> None:
    parser = argparse.ArgumentParser(description="NSE intraday paper trading (ORB)")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--symbols", help="Comma-separated NSE symbols override")
    parser.add_argument("--days", type=int, default=3, help="How many recent sessions to replay")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    run_paper(cfg, symbol_filter=args.symbols, days=args.days)


if __name__ == "__main__":
    main()
