"""TradingView-compatible Wilder RSI / ATR helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder moving average (TradingView ``ta.rma``)."""
    if length <= 0:
        raise ValueError("length must be positive")
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    alpha = 1.0 / float(length)
    running: float | None = None
    window: list[float] = []
    for i, val in enumerate(values):
        if np.isnan(val):
            running = None
            window = []
            continue
        if running is None:
            window.append(val)
            if len(window) == length:
                running = float(np.mean(window))
                out[i] = running
            continue
        running = alpha * val + (1.0 - alpha) * running
        out[i] = running
    return pd.Series(out, index=series.index, name=series.name)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - prev_close).abs()
    low_close = (df["low"] - prev_close).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    tr = tr.where(prev_close.notna(), high_low)
    return tr.rename("tr")


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return rma(true_range(df), length).rename("atr")


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0.0, 0.0).mask(delta.isna())
    loss = (-delta).where(delta < 0.0, 0.0).mask(delta.isna())
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(avg_gain.notna() & avg_loss.notna())
    return out.rename("rsi")
