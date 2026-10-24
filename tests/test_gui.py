import json
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from src.backtest import BacktestResult
from src.redact import redact
from src.webapp import app

ROOT = Path(__file__).resolve().parents[1]
CLIENT = TestClient(app)


def test_repository_has_no_merge_conflict_markers():
    skip = {".venv", ".git", "logs", "__pycache__", ".pytest_cache"}
    allowed = {".py", ".yaml", ".yml", ".md", ".html", ".js", ".css", ".bat", ".txt", ".example"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        if any(part in skip for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert re.search(r"(?m)^<{7} |^>{7} ", text) is None, path


def test_cli_exposes_strategy_rrr_and_mode():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--strategy" in proc.stdout
    assert "--rrr" in proc.stdout
    assert "--mode" in proc.stdout


def test_redact_strips_google_key():
    raw = "failed GOOGLE_API_KEY=AIzaSyTHISISTHEKEY1234567890 please retry"
    cleaned = redact(raw)
    assert "AIza" not in cleaned
    assert "THISISTHEKEY" not in cleaned


def test_backtest_api_returns_metrics_and_chart(monkeypatch):
    def fake(symbols, cfg, **kwargs):
        assert kwargs["include_chart"] is True
        assert kwargs["strategy"] == "gainzalgo"
        assert kwargs["trade_mode"] == "long_only"
        assert kwargs["rrr"] == "1:2"
        assert symbols == ["SBIN"]
        return BacktestResult(
            symbols=symbols,
            sessions=2,
            capital=200000,
            ending_cash=200100,
            realized_pnl=100,
            total_charges=12,
            round_trips=1,
            wins=1,
            losses=0,
            win_rate=100,
            avg_win=100,
            avg_loss=None,
            best_trade=100,
            worst_trade=100,
            exit_reasons={"target": 1},
            breakeven_pct=0.001,
            fills=[
                {
                    "time": "2026-01-05 10:00",
                    "symbol": "SBIN",
                    "side": "BUY",
                    "qty": 1,
                    "price": 100,
                    "charges": 1,
                    "reason": "gainz_buy",
                }
            ],
            trade_pnls=[100.0],
            skipped=[],
            date_start="2026-01-05",
            date_end="2026-01-06",
            strategy="gainzalgo",
            trade_mode="long_only",
            chart={"prices": {"SBIN": [{"time": "2026-01-05 10:00", "close": 100.0}]}, "markers": []},
        )

    monkeypatch.setattr("src.webapp.run_backtest", fake)
    res = CLIENT.post(
        "/api/backtest",
        json={
            "symbols": "sbin.ns",
            "strategy": "gainzalgo",
            "rrr": "1:2",
            "trade_mode": "long_only",
            "capital": 200000,
            "lookback": 30,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["realized_pnl"] == 100
    assert body["summary"]["round_trips"] == 1
    assert body["summary"]["exit_reasons"] == {"target": 1}
    assert body["prices"]["SBIN"][0]["close"] == 100
    assert body["equity"][0]["equity"] == 200100
    assert body["fills"][0]["side"] == "BUY"


def test_backtest_api_rejects_non_nse_symbol():
    res = CLIENT.post(
        "/api/backtest",
        json={"symbols": "AAPL.US", "strategy": "orb", "trade_mode": "both", "capital": 200000, "lookback": 5},
    )
    assert res.status_code == 400
    assert "NSE" in res.text


def test_analysis_missing_install_is_clear(monkeypatch):
    monkeypatch.delenv("NSE_PAPER_TA_STUB", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_HOME", raising=False)
    monkeypatch.delenv("TRADINGAGENTS_PYTHON", raising=False)
    res = CLIENT.post("/api/analysis", json={"symbol": "SBIN", "date": "2026-01-05"})
    assert res.status_code == 400
    assert "TradingAgents" in res.text
    assert "AIza" not in res.text


def test_analysis_stub_completes(monkeypatch):
    monkeypatch.setenv("NSE_PAPER_TA_STUB", "1")
    res = CLIENT.post("/api/analysis", json={"symbol": "cupid", "date": "2026-01-05"})
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    body = {}
    for _ in range(50):
        body = CLIENT.get(f"/api/analysis/{job_id}").json()
        if body["status"] in {"done", "error"}:
            break
        time.sleep(0.05)
    assert body["status"] == "done"
    assert body["stub"] is True
    assert body["ticker"] == "CUPID.NS"
    assert body["result"]["decision"] == "Hold"
    assert body["result"]["reports"]
    blob = json.dumps(body)
    assert "AIza" not in blob


def _write_fake_tradingagents(home: Path) -> None:
    package = home / "tradingagents"
    graph = package / "graph"
    graph.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (graph / "__init__.py").write_text("", encoding="utf-8")
    (package / "default_config.py").write_text(
        "DEFAULT_CONFIG = {\n"
        "    'llm_provider': 'openai',\n"
        "    'deep_think_llm': 'x',\n"
        "    'quick_think_llm': 'y',\n"
        "    'backend_url': 'https://api.openai.com/v1',\n"
        "    'results_dir': 'results',\n"
        "    'max_debate_rounds': 1,\n"
        "    'max_risk_discuss_rounds': 1,\n"
        "    'quick_think_provider': None,\n"
        "    'deep_think_provider': None,\n"
        "}\n",
        encoding="utf-8",
    )
    (graph / "trading_graph.py").write_text(
        "import os\n"
        "class TradingAgentsGraph:\n"
        "    def __init__(self, debug=False, config=None):\n"
        "        self.config = config or {}\n"
        "        if self.config.get('llm_provider') != 'google':\n"
        "            raise RuntimeError('provider was not google')\n"
        "        if not os.environ.get('GOOGLE_API_KEY'):\n"
        "            raise RuntimeError('missing key')\n"
        "    def propagate(self, ticker, date):\n"
        "        print('STATUS leak GOOGLE_API_KEY=' + os.environ['GOOGLE_API_KEY'], flush=True)\n"
        "        state = {\n"
        "            'market_report': 'Range-bound session.',\n"
        "            'final_trade_decision': 'Hold. Research only.',\n"
        "            'investment_debate_state': {\n"
        "                'bull_history': 'Demand is firm.',\n"
        "                'bear_history': 'Supply is heavy.',\n"
        "            },\n"
        "        }\n"
        "        return state, 'Hold'\n",
        encoding="utf-8",
    )
    (home / ".env").write_text("GOOGLE_API_KEY=AIzaSyTHISISTHEKEY1234567890\n", encoding="utf-8")


def test_analysis_subprocess_uses_google_and_hides_the_key(tmp_path, monkeypatch):
    home = tmp_path / "trading"
    _write_fake_tradingagents(home)
    monkeypatch.setenv("TRADINGAGENTS_HOME", str(home))
    monkeypatch.setenv("TRADINGAGENTS_PYTHON", sys.executable)
    monkeypatch.delenv("NSE_PAPER_TA_STUB", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    res = CLIENT.post("/api/analysis", json={"symbol": "VEDL", "date": "2026-02-02"})
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    body = {}
    for _ in range(80):
        body = CLIENT.get(f"/api/analysis/{job_id}").json()
        if body["status"] in {"done", "error"}:
            break
        time.sleep(0.05)
    assert body["status"] == "done", body
    assert body["stub"] is False
    assert body["result"]["provider"] == "google"
    assert body["result"]["decision"] == "Hold"
    titles = [report["title"] for report in body["result"]["reports"]]
    assert "Market" in titles
    assert "Bull case" in titles
    blob = json.dumps(body)
    assert "AIza" not in blob
    assert "THISISTHEKEY" not in blob


def test_index_has_both_tabs():
    res = CLIENT.get("/")
    assert res.status_code == 200
    assert "Backtest" in res.text
    assert "AI Analysis" in res.text
    assert "No live orders" in res.text
