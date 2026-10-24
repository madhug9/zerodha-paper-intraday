"""Zerodha NSE paper intraday — GainzAlgo V2 Alpha / ORB simulator."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.backtest import load_config, run_backtest

ROOT = Path(__file__).resolve().parent
console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="NSE intraday paper trading (GainzAlgo V2 Alpha / ORB)")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--symbols", help="Comma-separated NSE symbols override")
    parser.add_argument("--strategy", choices=["gainzalgo", "orb"], default=None)
    parser.add_argument("--rrr", choices=["1:1", "2:3", "1:2", "1:4"], default=None, help="GainzAlgo risk:reward")
    parser.add_argument("--days", type=int, default=None, help="How many recent sessions to replay (default: all fetched)")
    parser.add_argument("--lookback", type=int, default=None, help="Yahoo lookback days for intraday bars (max ~59 for 5m)")
    parser.add_argument("--mode", choices=["both", "long_only", "short_only"], default=None, help="Long buy→sell, short sell→buy, or both")
    parser.add_argument("--quiet", action="store_true", help="Summary only (kept for compatibility)")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = list(cfg.get("watchlist", []))

    result = run_backtest(
        symbols,
        cfg,
        lookback_days=args.lookback,
        days=args.days,
        trade_mode=args.mode,
        strategy=args.strategy,
        rrr=args.rrr,
        write_log=True,
    )

    if result.skipped:
        console.print(f"[yellow]Skipped (no data): {', '.join(result.skipped)}[/yellow]")

    console.print(f"[bold]Strategy[/bold]: {result.strategy}")
    console.print(f"[bold]Trade mode[/bold]: {result.trade_mode}")
    console.print(f"[bold]Symbols[/bold]: {', '.join(result.symbols)}")
    console.print(f"[bold]Range[/bold]: {result.date_start} -> {result.date_end} ({result.sessions} sessions)")
    console.print(f"[bold]Est. RT breakeven[/bold]: ~{result.breakeven_pct * 100:.3f}%")

    table = Table(title="Backtest / paper summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Starting capital", f"Rs{result.capital:,.2f}")
    table.add_row("Ending cash", f"Rs{result.ending_cash:,.2f}")
    table.add_row("Realized P&L", f"Rs{result.realized_pnl:,.2f}")
    table.add_row("Total charges", f"Rs{result.total_charges:,.2f}")
    table.add_row("Round trips", str(result.round_trips))
    table.add_row("Win / Loss", f"{result.wins} / {result.losses}")
    table.add_row("Win rate", f"{result.win_rate:.1f}%")
    table.add_row("Avg win", f"Rs{result.avg_win:,.2f}" if result.avg_win is not None else "n/a")
    table.add_row("Avg loss", f"Rs{result.avg_loss:,.2f}" if result.avg_loss is not None else "n/a")
    table.add_row("Best trade", f"Rs{result.best_trade:,.2f}" if result.best_trade is not None else "n/a")
    table.add_row("Worst trade", f"Rs{result.worst_trade:,.2f}" if result.worst_trade is not None else "n/a")
    table.add_row("Exit mix", ", ".join(f"{k}={v}" for k, v in sorted(result.exit_reasons.items())) or "n/a")
    if result.signals is not None:
        table.add_row("Signals", str(len(result.signals)))
    console.print(table)
    if result.log_path:
        console.print(f"\nWrote [cyan]{result.log_path}[/cyan]")


if __name__ == "__main__":
    main()
