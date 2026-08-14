"""Streamlit UI — backtest NSE symbols you type in."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.backtest import load_config, parse_symbols, run_backtest

st.set_page_config(page_title="NSE Paper Backtest", page_icon="📈", layout="wide")

cfg = load_config()

st.title("NSE ORB paper backtest")
st.caption("Type NSE symbols (e.g. CUPID, SBIN, SILVERBEES). Simulation only — not live trading / not advice.")

with st.sidebar:
    st.header("Parameters")
    default_syms = "CUPID"
    lookback = st.slider("Lookback days (5m data max ~59)", 5, 59, 59)
    capital = st.number_input("Capital (Rs)", min_value=10_000, max_value=10_000_000, value=int(cfg.get("capital", 200000)), step=10_000)
    or_mins = st.slider("Opening range (minutes)", 5, 60, int(cfg.get("strategy", {}).get("opening_range_minutes", 15)))
    target_r = st.slider("Target (R)", 0.5, 3.0, float(cfg.get("strategy", {}).get("target_r", 1.5)), 0.1)
    st.markdown("---")
    st.markdown("Uses Zerodha-style MIS cost model from `config.yaml`.")

symbols_text = st.text_input(
    "Symbols",
    value=default_syms,
    placeholder="CUPID, SBIN, VEDL",
    help="Comma-separated NSE cash symbols (Yahoo uses .NS under the hood)",
)

col_run, col_hint = st.columns([1, 3])
with col_run:
    run = st.button("Run backtest", type="primary", use_container_width=True)
with col_hint:
    st.write("Examples: `CUPID` · `SBIN,TATASTEEL` · `SILVERBEES,GOLDBEES`")

if run:
    symbols = parse_symbols(symbols_text)
    if not symbols:
        st.error("Enter at least one symbol.")
    else:
        with st.spinner(f"Backtesting {', '.join(symbols)}..."):
            try:
                result = run_backtest(
                    symbols,
                    cfg,
                    lookback_days=lookback,
                    capital=float(capital),
                    opening_range_minutes=or_mins,
                    target_r=float(target_r),
                    write_log=True,
                )
            except Exception as exc:  # noqa: BLE001 — show in UI
                st.error(str(exc))
                st.stop()

        if result.skipped:
            st.warning(f"No data / skipped: {', '.join(result.skipped)}")

        st.subheader("Summary")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Realized P&L", f"Rs {result.realized_pnl:,.0f}")
        m2.metric("Win rate", f"{result.win_rate:.1f}%")
        m3.metric("Round trips", f"{result.round_trips}")
        m4.metric("Charges", f"Rs {result.total_charges:,.0f}")
        m5.metric("Ending cash", f"Rs {result.ending_cash:,.0f}")

        st.write(
            f"**Symbols:** {', '.join(result.symbols)}  ·  "
            f"**Sessions:** {result.sessions}  ·  "
            f"**Range:** {result.date_start} → {result.date_end}  ·  "
            f"**Breakeven est.:** {result.breakeven_pct * 100:.3f}% RT"
        )
        if result.exit_reasons:
            st.write("**Exits:** " + ", ".join(f"{k}={v}" for k, v in sorted(result.exit_reasons.items())))

        left, right = st.columns(2)
        with left:
            st.subheader("Trade P&L")
            if result.trade_pnls:
                pnl_df = pd.DataFrame({"trade": range(1, len(result.trade_pnls) + 1), "pnl": result.trade_pnls})
                pnl_df["equity"] = float(capital) + pnl_df["pnl"].cumsum()
                st.line_chart(pnl_df.set_index("trade")["equity"], height=280)
            else:
                st.info("No round trips in this window.")
        with right:
            st.subheader("Per-trade P&L")
            if result.trade_pnls:
                bar_df = pd.DataFrame({"pnl": result.trade_pnls})
                st.bar_chart(bar_df, height=280)
            else:
                st.info("No trades to chart.")

        st.subheader("Fills")
        if result.fills:
            fills_df = pd.DataFrame(result.fills)
            st.dataframe(fills_df, use_container_width=True, hide_index=True)
            if result.log_path:
                st.caption(f"Saved: `{Path(result.log_path).name}`")
        else:
            st.info("No fills.")
else:
    st.info("Enter symbols above and click **Run backtest**.")
