"""Streamlit UI — backtest NSE symbols with GainzAlgo V2 Alpha or ORB."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from src.backtest import load_config, parse_symbols, run_backtest

st.set_page_config(page_title="NSE paper backtest", page_icon="📈", layout="wide")

cfg = load_config()
strat_cfg = cfg.get("strategy", {})

MODE_LABELS = {
    "both": "Both (buy then sell, and sell then buy)",
    "long_only": "Long only (buy then sell)",
    "short_only": "Short only (sell then buy)",
}

st.title("NSE paper backtest")
st.caption(
    "GainzAlgo V2 Alpha or opening-range breakout, long and short. "
    "Independent recreation for NSE paper trading. "
    "Simulation only — not the official TradingView product, not live trading, not advice."
)

with st.sidebar:
    st.header("Parameters")
    default_syms = "CUPID"
    strategy = st.selectbox(
        "Strategy",
        ["gainzalgo", "orb"],
        index=0 if str(strat_cfg.get("name", "gainzalgo")) != "orb" else 1,
        format_func=lambda s: "GainzAlgo V2 Alpha" if s == "gainzalgo" else "Opening Range Breakout",
    )
    lookback = st.slider("Lookback days (5m data max ~59)", 5, 59, 30)
    capital = st.number_input(
        "Capital (Rs)",
        min_value=10_000,
        max_value=10_000_000,
        value=int(cfg.get("capital", 200000)),
        step=10_000,
    )
    trade_mode = st.selectbox(
        "Direction",
        options=list(MODE_LABELS),
        index=list(MODE_LABELS).index(strat_cfg.get("trade_mode", "both"))
        if strat_cfg.get("trade_mode", "both") in MODE_LABELS
        else 0,
        format_func=lambda key: MODE_LABELS[key],
    )
    if strategy == "gainzalgo":
        rrr_choices = ["1:1", "2:3", "1:2", "1:4"]
        current_rrr = str(strat_cfg.get("rrr", "1:2"))
        rrr = st.selectbox(
            "Risk : Reward",
            rrr_choices,
            index=rrr_choices.index(current_rrr) if current_rrr in rrr_choices else 2,
        )
        tp_sl_multi = st.slider(
            "TP/SL multiplier",
            1.0,
            4.0,
            float(strat_cfg.get("tp_sl_multiplier", 1.0)),
            0.1,
        )
        allow_shorts = st.checkbox("Allow shorts", value=bool(strat_cfg.get("allow_shorts", True)))
        or_mins = int(strat_cfg.get("opening_range_minutes", 15))
        target_r = float(strat_cfg.get("target_r", 1.5))
    else:
        rrr = None
        tp_sl_multi = None
        allow_shorts = None
        or_mins = st.slider(
            "Opening range (minutes)",
            5,
            60,
            int(strat_cfg.get("opening_range_minutes", 15)),
        )
        target_r = st.slider("Target (R)", 0.5, 3.0, float(strat_cfg.get("target_r", 1.5)), 0.1)
    st.markdown("---")
    st.markdown("Uses the Zerodha-style MIS cost model from `config.yaml`.")
    st.markdown("Signals confirm on **bar close**; entries fill at the **next bar open**.")
    st.markdown("Shorts are taken only when direction allows them and, for GainzAlgo, **Allow shorts** is on.")

mode_label = MODE_LABELS.get(trade_mode, trade_mode)

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
    try:
        symbols = parse_symbols(symbols_text)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
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
                    trade_mode=trade_mode,
                    rrr=rrr,
                    tp_sl_multiplier=tp_sl_multi,
                    allow_shorts=allow_shorts,
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
    st.info("Enter symbols above and click **Run backtest**. The local HTML desk is `start-gui.bat`.")
