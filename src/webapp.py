"""Local paper-trading desk: backtests plus a TradingAgents analysis tab.

Binds to localhost. Does not place live orders and does not read API keys.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .backtest import load_config, run_backtest
from .symbols import parse_symbols, to_yahoo_nse
from .ta_runner import TaConfigError, get_job, start_analysis

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
load_dotenv(ROOT / ".env")

app = FastAPI(title="NSE Paper Desk", docs_url=None, redoc_url=None)


class BacktestIn(BaseModel):
    symbols: str = Field(min_length=1, max_length=200)
    strategy: Literal["gainzalgo", "orb"] = "gainzalgo"
    rrr: Literal["1:1", "2:3", "1:2", "1:4"] | None = None
    trade_mode: Literal["both", "long_only", "short_only"] = "both"
    capital: float = Field(default=200_000, gt=0, le=10_000_000)
    lookback: int = Field(default=30, ge=1, le=59)
    opening_range_minutes: int | None = Field(default=None, ge=5, le=60)
    target_r: float | None = Field(default=None, ge=0.1, le=5)


class AnalysisIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    date: str

    @field_validator("date")
    @classmethod
    def _date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Date must be YYYY-MM-DD.") from exc
        return value


def _num(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _ta_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    block = dict(cfg.get("tradingagents") or {})
    home = os.environ.get("TRADINGAGENTS_HOME") or str(block.get("home") or "")
    python = os.environ.get("TRADINGAGENTS_PYTHON") or str(block.get("python") or "")
    stub = os.environ.get("NSE_PAPER_TA_STUB", "").strip().lower() in {"1", "true", "yes"}
    return {
        "home": home,
        "python": python.strip(),
        "deep_think_llm": str(block.get("deep_think_llm") or "gemini-2.5-flash"),
        "quick_think_llm": str(block.get("quick_think_llm") or "gemini-2.5-flash"),
        "max_debate_rounds": int(block.get("max_debate_rounds") or 1),
        "max_risk_discuss_rounds": int(block.get("max_risk_discuss_rounds") or 1),
        "backend_url": str(block.get("backend_url") or ""),
        "stub": stub,
    }


def _payload(result: Any, capital: float) -> dict[str, Any]:
    equity: list[dict[str, float | int]] = []
    running = float(capital)
    for index, pnl in enumerate(result.trade_pnls, start=1):
        running += float(pnl)
        equity.append({"trade": index, "pnl": round(float(pnl), 2), "equity": round(running, 2)})
    chart = result.chart or {}
    summary = result.to_summary_dict()
    summary["realized_pnl"] = _num(summary["realized_pnl"])
    summary["total_charges"] = _num(summary["total_charges"])
    summary["ending_cash"] = _num(summary["ending_cash"])
    summary["capital"] = _num(summary["capital"])
    summary["win_rate_pct"] = _num(summary["win_rate_pct"], 1)
    summary["avg_win"] = _num(summary["avg_win"])
    summary["avg_loss"] = _num(summary["avg_loss"])
    summary["best_trade"] = _num(summary["best_trade"])
    summary["worst_trade"] = _num(summary["worst_trade"])
    summary["breakeven_pct"] = round(float(result.breakeven_pct) * 100.0, 3)
    return {
        "summary": summary,
        "fills": result.fills,
        "signals": result.signals or [],
        "equity": equity,
        "prices": chart.get("prices") or {},
        "markers": chart.get("markers") or [],
        "skipped": result.skipped,
        "log_name": Path(result.log_path).name if result.log_path else None,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/defaults")
def defaults() -> dict[str, Any]:
    cfg = load_config()
    strat = cfg.get("strategy") or {}
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    ta = _ta_settings(cfg)
    return {
        "symbols": "CUPID",
        "strategy": strat.get("name") or "gainzalgo",
        "rrr": strat.get("rrr") or "1:2",
        "trade_mode": strat.get("trade_mode") or "both",
        "capital": cfg.get("capital") or 200000,
        "lookback": 30,
        "opening_range_minutes": strat.get("opening_range_minutes") or 15,
        "target_r": strat.get("target_r") or 1.5,
        "analysis_date": today,
        "tradingagents_configured": ta["stub"] or bool(ta["home"]),
        "tradingagents_stub": ta["stub"],
        "models": {"deep": ta["deep_think_llm"], "quick": ta["quick_think_llm"]},
    }


@app.post("/api/backtest")
def post_backtest(body: BacktestIn) -> dict[str, Any]:
    try:
        symbols = parse_symbols(body.symbols)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not symbols:
        raise HTTPException(status_code=400, detail="Enter at least one NSE symbol.")
    cfg = load_config()
    try:
        result = run_backtest(
            symbols,
            cfg,
            lookback_days=body.lookback,
            capital=float(body.capital),
            strategy=body.strategy,
            trade_mode=body.trade_mode,
            rrr=body.rrr,
            opening_range_minutes=body.opening_range_minutes,
            target_r=body.target_r,
            write_log=True,
            include_chart=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface a short message, not a traceback
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _payload(result, float(body.capital))


@app.post("/api/analysis")
def post_analysis(body: AnalysisIn) -> dict[str, str]:
    try:
        ticker = to_yahoo_nse(body.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg = load_config()
    try:
        job = start_analysis(ticker, body.date, _ta_settings(cfg))
    except TaConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_id": job.id}


@app.get("/api/analysis/{job_id}")
def get_analysis(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown analysis job.")
    return job.snapshot()


app.mount("/static", StaticFiles(directory=WEB), name="static")
