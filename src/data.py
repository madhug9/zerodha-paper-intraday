from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import yfinance as yf


@dataclass
class DataConfig:
    interval: str = "5m"
    lookback_days: int = 5
    exchange_suffix: str = ".NS"


def fetch_intraday(symbol: str, cfg: DataConfig) -> pd.DataFrame:
    ticker = f"{symbol}{cfg.exchange_suffix}"
    df = yf.download(
        ticker,
        period=f"{cfg.lookback_days}d",
        interval=cfg.interval,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].title() for c in df.columns]
    else:
        df.columns = [str(c).title() for c in df.columns]
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    return df


def session_days(df: pd.DataFrame) -> list[pd.Timestamp]:
    if df.empty:
        return []
    return sorted({ts.normalize() for ts in df.index})


def day_slice(df: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    start = day.normalize()
    end = start + pd.Timedelta(days=1)
    return df[(df.index >= start) & (df.index < end)].copy()
