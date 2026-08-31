"""Streamlit UI — backtest NSE symbols with GainzAlgo V2 Alpha or ORB."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.backtest import load_config, parse_symbols, run_backtest

st.set_page_config(page_title="GainzAlgo V2 Alpha", page_icon="📈", layout="wide")

cfg = load_config()
strat_cfg = cfg.get("strategy", {})

<<<<<<< HEAD
st.title("NSE ORB paper backtest")
st.caption(
    "Intraday long (buy→sell) and/or short (sell→buy). Type NSE symbols. Simulation only — not advice."
=======
st.title("GainzAlgo V2 Alpha paper backtest")
st.caption(
    "Independent recreation of the published Alpha signal rules for NSE paper trading. "
    "Simulation only — not the official TradingView product, not live trading, not advice."
>>>>>>> c222e2abe0cb05fb3b3eef0276e0329099b93f6b
)

with st.sidebar:
    st.header("Parameters")
    default_syms = "CUPID"
    strategy = st.selectbox(
        "Strategy",
        ["gainzalgo", "orb"],
        index=0,
        format_func=lambda s: "GainzAlgo V2 Alpha" if s == "gainzalgo" else "Opening Range Breakout",
    )
    lookback = st.slider("Lookback days (5m data max ~59)", 5, 59, 59)
<<<<<<< HEAD
    capital = st.number_input("Capital (Rs)", min_value=10_000, max_value=10_000_000, value=int(cfg.get("capital", 200000)), step=10_000)
    or_mins = st.slider("Opening range (minutes)", 5, 60, int(cfg.get("strategy", {}).get("opening_range_minutes", 15)))
    target_r = st.slider("Target (R)", 0.5, 3.0, float(cfg.get("strategy", {}).get("target_r", 1.5)), 0.1)
    mode_label = st.selectbox(
        "Direction",
        options=[
            "Both (buy→sell & sell→buy)",
            "Long only (buy→sell)",
            "Short only (sell→buy)",
        ],
        index=0,
    )
    trade_mode = {
        "Both (buy→sell & sell→buy)": "both",
        "Long only (buy→sell)": "long_only",
        "Short only (sell→buy)": "short_only",
    }[mode_label]
=======
    capital = st.number_input(
        "Capital (Rs)",
        min_value=10_000,
        max_value=10_000_000,
        value=int(cfg.get("capital", 200000)),
        step=10_000,
    )
    if strategy == "gainzalgo":
        rrr = st.selectbox("Risk : Reward", ["1:1", "2:3", "1:2", "1:4"], index=2)
        tp_sl_multi = st.slider("TP/SL multiplier", 1.0, 4.0, float(strat_cfg.get("tp_sl_multiplier", 1.0)), 0.1)
        allow_shorts = st.checkbox("Allow shorts", value=bool(strat_cfg.get("allow_shorts", True)))
        or_mins = int(strat_cfg.get("opening_range_minutes", 15))
        target_r = float(strat_cfg.get("target_r", 1.5))
    else:
        rrr = None
        tp_sl_multi = None
        allow_shorts = None
        or_mins = st.slider("Opening range (minutes)", 5, 60, int(strat_cfg.get("opening_range_minutes", 15)))
        target_r = st.slider("Target (R)", 0.5, 3.0, float(strat_cfg.get("target_r", 1.5)), 0.1)
>>>>>>> c222e2abe0cb05fb3b3eef0276e0329099b93f6b
    st.markdown("---")
    st.markdown("Uses Zerodha-style MIS cost model from `config.yaml`.")
    st.markdown("Signals confirm on **bar close**; entries fill at the **next bar open**.")

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
                    strategy=strategy,
                    opening_range_minutes=or_mins,
                    target_r=float(target_r),
<<<<<<< HEAD
                    trade_mode=trade_mode,
=======
                    rrr=rrr,
                    tp_sl_multiplier=tp_sl_multi,
                    allow_shorts=allow_shorts,
>>>>>>> c222e2abe0cb05fb3b3eef0276e0329099b93f6b
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
            f"**Strategy:** {result.strategy}  ·  "
            f"**Symbols:** {', '.join(result.symbols)}  ·  "
            f"**Mode:** {mode_label}  ·  "
            f"**Sessions:** {result.sessions}  ·  "
            f"**Range:** {result.date_start} → {result.date_end}  ·  "
            f"**Breakeven est.:** {result.breakeven_pct * 100:.3f}% RT"
        )
        if result.exit_reasons:
            st.write("**Exits:** " + ", ".join(f"{k}={v}" for k, v in sorted(result.exit_reasons.items())))

        left, right = st.columns(2)
        with left:
            st.subheader("Equity (trade P&L)")
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

        if result.signals:
            st.subheader("Signals (bar-close)")
            st.dataframe(pd.DataFrame(result.signals), use_container_width=True, hide_index=True)

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
