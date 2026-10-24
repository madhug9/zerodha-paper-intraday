"""Run inside the TradingAgents virtualenv.

Usage: python ta_driver.py TICKER DATE RESULT_JSON

Loads that checkout's .env in this process only. Progress goes to stdout.
The result JSON never includes API keys.
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from pathlib import Path

_GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z_\-]{8,}")
_OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_ASSIGNMENT = re.compile(
    r"(?i)\b[\w.]*(?:api[_-]?key|access[_-]?token|secret|token|password)\b\s*[:=]\s*\S+"
)

REPORT_FIELDS = (
    ("market_report", "Market"),
    ("sentiment_report", "Sentiment"),
    ("news_report", "News"),
    ("fundamentals_report", "Fundamentals"),
    ("investment_plan", "Research plan"),
    ("trader_investment_plan", "Trader plan"),
    ("final_trade_decision", "Final decision"),
)


def redact(text: str) -> str:
    cleaned = _GOOGLE_KEY.sub("[redacted]", text)
    cleaned = _OPENAI_KEY.sub("[redacted]", cleaned)
    cleaned = _ASSIGNMENT.sub("[redacted]", cleaned)
    return cleaned


def say(message: str) -> None:
    print("STATUS " + redact(message), flush=True)


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key or os.environ.get(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value


def as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def write_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def fail(path: Path, message: str) -> int:
    safe = redact(message).strip() or "TradingAgents failed."
    write_result(path, {"ok": False, "error": safe})
    say(safe.splitlines()[0])
    return 1


def main() -> int:
    if len(sys.argv) != 4:
        print("STATUS usage: ta_driver.py TICKER DATE RESULT_PATH", flush=True)
        return 2
    ticker, trade_date, result_path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    home = Path(os.environ.get("TRADINGAGENTS_HOME", "")).expanduser()
    if str(home):
        sys.path.insert(0, str(home))
        load_env_file(home / ".env")

    provider = "google"
    deep = os.environ.get("TA_DEEP_THINK_LLM") or "gemini-2.5-flash"
    quick = os.environ.get("TA_QUICK_THINK_LLM") or "gemini-2.5-flash"
    backend = (os.environ.get("TA_BACKEND_URL") or "").strip()
    say(f"starting {ticker} on {trade_date} with {provider} ({quick} / {deep})")
    try:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph
    except Exception as exc:
        return fail(result_path, f"Could not import TradingAgents ({type(exc).__name__}: {exc})")

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = provider
    config["deep_think_llm"] = deep
    config["quick_think_llm"] = quick
    if "quick_think_provider" in config:
        config["quick_think_provider"] = provider
    if "deep_think_provider" in config:
        config["deep_think_provider"] = provider
    if backend:
        config["backend_url"] = backend
    else:
        url = str(config.get("backend_url") or "")
        if "openai.com" in url.lower() or url.rstrip("/").endswith("/v1"):
            # Older checkouts default this to OpenAI. Leave it unset so the
            # Gemini client uses its own endpoint.
            config["backend_url"] = None
    results_dir = os.environ.get("TA_RESULTS_DIR")
    if results_dir and "results_dir" in config:
        config["results_dir"] = results_dir
    if os.environ.get("TA_MAX_DEBATE_ROUNDS") and "max_debate_rounds" in config:
        config["max_debate_rounds"] = int(os.environ["TA_MAX_DEBATE_ROUNDS"])
    if os.environ.get("TA_MAX_RISK_ROUNDS") and "max_risk_discuss_rounds" in config:
        config["max_risk_discuss_rounds"] = int(os.environ["TA_MAX_RISK_ROUNDS"])

    say("running the analyst team — this often takes several minutes")
    try:
        graph = TradingAgentsGraph(debug=False, config=config)
        state, decision = graph.propagate(ticker, trade_date)
    except Exception:
        return fail(result_path, traceback.format_exc(limit=12))

    reports: list[dict[str, str]] = []
    if isinstance(state, dict):
        for key, title in REPORT_FIELDS:
            body = as_text(state.get(key)).strip()
            if body:
                reports.append({"id": key, "title": title, "body": redact(body)})
        debate = state.get("investment_debate_state") or {}
        if isinstance(debate, dict):
            for key, title in (("bull_history", "Bull case"), ("bear_history", "Bear case")):
                body = as_text(debate.get(key)).strip()
                if body:
                    reports.append({"id": key, "title": title, "body": redact(body)})
    decision_text = redact(as_text(decision).strip())
    final_text = decision_text
    if isinstance(state, dict):
        final_text = redact(as_text(state.get("final_trade_decision")).strip()) or decision_text
    write_result(
        result_path,
        {
            "ok": True,
            "ticker": ticker,
            "date": trade_date,
            "provider": provider,
            "models": {"deep": deep, "quick": quick},
            "decision": decision_text or final_text,
            "final_decision": final_text or decision_text,
            "reports": reports,
        },
    )
    say("finished")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
