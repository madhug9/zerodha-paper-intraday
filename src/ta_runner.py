"""Background TradingAgents jobs.

The child process is the TradingAgents virtualenv. This process never reads
that checkout's .env, so API keys are not loaded or logged here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from .redact import redact

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ROOT / "scripts" / "ta_driver.py"
RUN_TIMEOUT_SEC = 45 * 60


class TaConfigError(RuntimeError):
    """The TradingAgents checkout is not configured on this machine."""


@dataclass
class AnalysisJob:
    id: str
    ticker: str
    trade_date: str
    status: str = "queued"
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    stub: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, line: str) -> None:
        text = redact(line).rstrip()
        if not text:
            return
        with self._lock:
            self.logs.append(text)
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.id,
                "ticker": self.ticker,
                "date": self.trade_date,
                "status": self.status,
                "logs": list(self.logs),
                "error": self.error,
                "stub": self.stub,
                "result": self.result,
            }


_jobs: dict[str, AnalysisJob] = {}
_jobs_lock = threading.Lock()


def get_job(job_id: str) -> AnalysisJob | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _interpreter(home: Path, configured: str) -> Path:
    if configured:
        path = Path(configured)
        if not path.is_file():
            raise TaConfigError(f"TradingAgents Python was not found at {path}.")
        return path
    if sys.platform == "win32":
        candidate = home / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = home / ".venv" / "bin" / "python"
    if not candidate.is_file():
        raise TaConfigError(
            f"TradingAgents was not found at {home}. Set tradingagents.home in config.yaml "
            "or TRADINGAGENTS_HOME to the checkout that contains .venv and a .env. "
            "Put GOOGLE_API_KEY in that .env. This server does not read or store the key."
        )
    return candidate


def _require_install(settings: dict[str, Any]) -> tuple[Path, Path]:
    home = Path(str(settings.get("home") or "")).expanduser()
    if not home.is_dir():
        raise TaConfigError(
            f"TradingAgents folder does not exist: {home or '(empty)'}. "
            "Set tradingagents.home in config.yaml or the TRADINGAGENTS_HOME environment variable. "
            "The default on this project is C:\\Users\\G Bhuvan\\Documents\\GitHub\\trading."
        )
    python = _interpreter(home, str(settings.get("python") or "").strip())
    if not DRIVER.is_file():
        raise TaConfigError(f"Missing driver script at {DRIVER}.")
    return home, python


def start_analysis(ticker: str, trade_date: str, settings: dict[str, Any]) -> AnalysisJob:
    stub = bool(settings.get("stub"))
    if not stub:
        _require_install(settings)
    job = AnalysisJob(id=uuid.uuid4().hex, ticker=ticker, trade_date=trade_date, stub=stub)
    with _jobs_lock:
        _jobs[job.id] = job
    target = _run_stub if stub else _run_subprocess
    threading.Thread(target=target, args=(job, settings), name=f"ta-{job.id[:8]}", daemon=True).start()
    return job


def _finish_error(job: AnalysisJob, message: str) -> None:
    job.error = redact(message)
    job.status = "error"
    job.append(job.error)


def _run_stub(job: AnalysisJob, settings: dict[str, Any]) -> None:
    job.status = "running"
    notes = [
        "Demo mode: TradingAgents was not started.",
        "No Google API key is configured in this environment, so Gemini was not called.",
        f"A real run would analyze {job.ticker} on {job.trade_date} with provider google.",
        "Local stub finished.",
    ]
    for note in notes:
        job.append(note)
        time.sleep(0.05)
    body = (
        "Local stub report for the paper desk. TradingAgents did not run, "
        "and this text is not a model decision."
    )
    job.result = {
        "ok": True,
        "ticker": job.ticker,
        "date": job.trade_date,
        "provider": "google",
        "models": {
            "deep": settings.get("deep_think_llm") or "gemini-2.5-flash",
            "quick": settings.get("quick_think_llm") or "gemini-2.5-flash",
        },
        "decision": "Hold",
        "final_decision": "Hold. Stub result only — not a Gemini decision and not a trade.",
        "reports": [
            {"id": "market_report", "title": "Market", "body": body},
            {"id": "news_report", "title": "News", "body": body},
            {"id": "fundamentals_report", "title": "Fundamentals", "body": body},
            {
                "id": "final_trade_decision",
                "title": "Final decision",
                "body": "Hold. Research stub for the UI. No order is placed.",
            },
        ],
        "stub": True,
    }
    job.status = "done"


def _run_subprocess(job: AnalysisJob, settings: dict[str, Any]) -> None:
    job.status = "running"
    try:
        home, python = _require_install(settings)
    except TaConfigError as exc:
        _finish_error(job, str(exc))
        return

    work = Path(mkdtemp(prefix="nse-ta-"))
    result_path = work / "result.json"
    env = os.environ.copy()
    # The child loads GOOGLE_API_KEY from the TradingAgents .env itself.
    for secret_name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        env.pop(secret_name, None)
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "TRADINGAGENTS_HOME": str(home),
            "TRADINGAGENTS_NO_UPDATE_CHECK": "1",
            "TA_LLM_PROVIDER": "google",
            "TA_DEEP_THINK_LLM": str(settings.get("deep_think_llm") or "gemini-2.5-flash"),
            "TA_QUICK_THINK_LLM": str(settings.get("quick_think_llm") or "gemini-2.5-flash"),
            "TA_MAX_DEBATE_ROUNDS": str(int(settings.get("max_debate_rounds") or 1)),
            "TA_MAX_RISK_ROUNDS": str(int(settings.get("max_risk_discuss_rounds") or 1)),
            "TA_BACKEND_URL": str(settings.get("backend_url") or ""),
            "TA_RESULTS_DIR": str(work / "reports"),
        }
    )
    cmd = [str(python), str(DRIVER), job.ticker, job.trade_date, str(result_path)]
    job.append(f"Starting TradingAgents for {job.ticker} on {job.trade_date} (google).")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(home),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        _finish_error(job, f"Could not start TradingAgents: {exc}")
        return

    def _read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            job.append(line)

    reader = threading.Thread(target=_read, name=f"ta-log-{job.id[:8]}", daemon=True)
    reader.start()
    try:
        code = proc.wait(timeout=RUN_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        reader.join(timeout=2)
        _finish_error(job, "TradingAgents timed out after 45 minutes.")
        return
    reader.join(timeout=5)
    if not result_path.is_file():
        _finish_error(job, f"TradingAgents exited with code {code} and did not write a result.")
        return
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _finish_error(job, "TradingAgents wrote a result file that was not valid JSON.")
        return
    if not isinstance(payload, dict) or not payload.get("ok"):
        message = payload.get("error") if isinstance(payload, dict) else "TradingAgents failed."
        _finish_error(job, str(message or "TradingAgents failed."))
        return
    payload["stub"] = False
    payload["decision"] = redact(str(payload.get("decision") or ""))
    payload["final_decision"] = redact(str(payload.get("final_decision") or payload["decision"]))
    reports = []
    for report in payload.get("reports") or []:
        if not isinstance(report, dict):
            continue
        reports.append(
            {
                "id": str(report.get("id") or "report"),
                "title": redact(str(report.get("title") or "Report")),
                "body": redact(str(report.get("body") or "")),
            }
        )
    payload["reports"] = reports
    job.result = payload
    job.status = "done"
    job.append("TradingAgents finished.")
