const $ = (id) => document.getElementById(id);

const state = { backtest: null };

function show(el, on) {
  el.hidden = !on;
}

function text(el, value) {
  el.textContent = value == null ? "" : String(value);
}

function money(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  const formatted = Math.abs(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  return (n < 0 ? "−" : "") + "Rs " + formatted;
}

function setTab(name) {
  for (const id of ["backtest", "analysis"]) {
    const on = id === name;
    $(`panel-${id}`).hidden = !on;
    const btn = $(`tab-btn-${id}`);
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  }
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => setTab(btn.dataset.tab));
});

function syncStrategyFields() {
  const orb = $("strategy").value === "orb";
  show($("rrr-field"), !orb);
  show($("or-field"), orb);
  show($("target-field"), orb);
}

$("strategy").addEventListener("change", syncStrategyFields);

async function loadDefaults() {
  const res = await fetch("/api/defaults");
  if (!res.ok) return;
  const data = await res.json();
  $("symbols").value = data.symbols || "CUPID";
  $("strategy").value = data.strategy === "orb" ? "orb" : "gainzalgo";
  if (data.rrr) $("rrr").value = data.rrr;
  if (data.trade_mode) $("trade-mode").value = data.trade_mode;
  if (data.capital) $("capital").value = data.capital;
  if (data.lookback) $("lookback").value = data.lookback;
  if (data.opening_range_minutes) $("or-minutes").value = data.opening_range_minutes;
  if (data.target_r) $("target-r").value = data.target_r;
  if (data.analysis_date) $("ai-date").value = data.analysis_date;
  $("ai-symbol").value = "SBIN";
  if (data.tradingagents_stub) {
    text(
      $("ai-hint"),
      "Demo mode is on in this environment: the button runs a local stub instead of Gemini. On your PC it calls TradingAgents."
    );
  } else if (data.models) {
    text(
      $("ai-hint"),
      `Runs TradingAgents with Google Gemini (${data.models.quick} / ${data.models.deep}). A run can take several minutes. .NS is added for you.`
    );
  }
  syncStrategyFields();
}

$("backtest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("run-backtest");
  show($("backtest-error"), false);
  show($("backtest-status"), true);
  show($("backtest-out"), false);
  button.disabled = true;
  const orb = $("strategy").value === "orb";
  const body = {
    symbols: $("symbols").value,
    strategy: $("strategy").value,
    trade_mode: $("trade-mode").value,
    capital: Number($("capital").value),
    lookback: Number($("lookback").value),
  };
  if (!orb) body.rrr = $("rrr").value;
  if (orb) {
    body.opening_range_minutes = Number($("or-minutes").value);
    body.target_r = Number($("target-r").value);
  }
  try {
    const res = await fetch("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Backtest failed.");
    state.backtest = data;
    renderBacktest(data);
  } catch (err) {
    text($("backtest-error"), err.message || String(err));
    show($("backtest-error"), true);
  } finally {
    show($("backtest-status"), false);
    button.disabled = false;
  }
});

function renderBacktest(data) {
  const s = data.summary;
  const mode = { both: "both", long_only: "long only", short_only: "short only" }[s.trade_mode] || s.trade_mode;
  text(
    $("backtest-meta"),
    `${s.strategy} · ${mode} · ${s.symbols} · ${s.sessions} sessions · ${s.date_start || "—"} to ${s.date_end || "—"} · breakeven ~${s.breakeven_pct}%`
  );
  const metrics = [
    ["Realized P&L", money(s.realized_pnl), s.realized_pnl],
    ["Charges", money(s.total_charges), null],
    ["Win rate", s.win_rate_pct == null ? "—" : `${s.win_rate_pct}%`, null],
    ["Round trips", String(s.round_trips), null],
    ["Ending cash", money(s.ending_cash), null],
  ];
  const box = $("metrics");
  box.replaceChildren();
  for (const [label, value, signed] of metrics) {
    const card = document.createElement("div");
    card.className = "metric";
    const span = document.createElement("span");
    span.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    if (typeof signed === "number") strong.className = signed > 0 ? "pos" : signed < 0 ? "neg" : "";
    card.append(span, strong);
    box.append(card);
  }
  const exits = s.exit_reasons || {};
  const mix = Object.keys(exits).sort().map((key) => `${key}=${exits[key]}`).join(", ");
  text($("exit-mix"), mix ? `Exit mix: ${mix}` : "Exit mix: none");
  if (data.skipped && data.skipped.length) {
    $("exit-mix").textContent += ` · Skipped (no data): ${data.skipped.join(", ")}`;
  }

  const symbols = Object.keys(data.prices || {});
  const select = $("chart-symbol");
  select.replaceChildren();
  for (const symbol of symbols) {
    const option = document.createElement("option");
    option.value = symbol;
    option.textContent = symbol;
    select.append(option);
  }
  select.onchange = () => drawPrice(data, select.value);
  renderTrades(data.fills || []);
  show($("backtest-out"), true);
  requestAnimationFrame(() => {
    drawPrice(data, select.value || symbols[0]);
    drawEquity(data.equity || []);
  });
}

function renderTrades(fills) {
  const body = $("trades").querySelector("tbody");
  body.replaceChildren();
  for (const fill of fills) {
    const tr = document.createElement("tr");
    for (const key of ["time", "symbol", "side", "qty", "price", "charges", "reason"]) {
      const td = document.createElement("td");
      td.textContent = fill[key] == null ? "" : String(fill[key]);
      if (key === "side") td.className = String(fill.side).toLowerCase();
      tr.append(td);
    }
    body.append(tr);
  }
  if (!fills.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.textContent = "No fills.";
    tr.append(td);
    body.append(tr);
  }
}

$("download-csv").addEventListener("click", () => {
  const fills = (state.backtest && state.backtest.fills) || [];
  const headers = ["time", "symbol", "side", "qty", "price", "charges", "reason"];
  const lines = [headers.join(",")];
  for (const fill of fills) {
    lines.push(headers.map((key) => csvCell(fill[key])).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const symbols = (state.backtest && state.backtest.summary.symbols) || "trades";
  link.href = url;
  link.download = `backtest_${symbols.replace(/[^A-Z0-9]+/gi, "_")}.csv`;
  link.click();
  URL.revokeObjectURL(url);
});

function csvCell(value) {
  const raw = value == null ? "" : String(value);
  if (/[",\n]/.test(raw)) return `"${raw.replaceAll('"', '""')}"`;
  return raw;
}

function drawPrice(data, symbol) {
  const bars = (data.prices && data.prices[symbol]) || [];
  const markers = (data.markers || []).filter((mark) => mark.symbol === symbol);
  const canvas = $("price-chart");
  show($("price-empty"), bars.length === 0);
  canvas.style.visibility = bars.length ? "visible" : "hidden";
  if (!bars.length) return;
  const points = bars.map((bar) => ({ t: Date.parse(bar.time.replace(" ", "T")), y: Number(bar.close), time: bar.time }));
  const markPoints = markers.map((mark) => ({
    t: Date.parse(mark.time.replace(" ", "T")),
    y: Number(mark.price),
    side: mark.side,
    time: mark.time,
    reason: mark.reason,
  }));
  const plotted = plot(canvas, points, { markers: markPoints, hover: $("chart-hover") });
  canvas.onmousemove = (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const hit = nearest(plotted, x);
    text($("chart-hover"), hit ? `${hit.time}  ${hit.y}` : "");
  };
  canvas.onmouseleave = () => text($("chart-hover"), "");
}

function drawEquity(equity) {
  const canvas = $("equity-chart");
  show($("equity-empty"), equity.length === 0);
  canvas.style.visibility = equity.length ? "visible" : "hidden";
  if (!equity.length) return;
  const points = equity.map((row) => ({ t: row.trade, y: Number(row.equity), time: `Trade ${row.trade}` }));
  plot(canvas, points, { markers: [], hover: null });
}

function plot(canvas, points, opts) {
  const ctx = canvas.getContext("2d");
  const width = canvas.clientWidth || 800;
  const height = canvas.clientHeight || 280;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const pad = { l: 58, r: 16, t: 16, b: 28 };
  const xs = points.map((p) => p.t);
  const ys = points.map((p) => p.y).concat((opts.markers || []).map((m) => m.y));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const xOf = (t) => pad.l + ((t - minX) / spanX) * (width - pad.l - pad.r);
  const yOf = (y) => pad.t + (1 - (y - minY) / spanY) * (height - pad.t - pad.b);

  ctx.strokeStyle = "#d9d0c0";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.l, pad.t);
  ctx.lineTo(pad.l, height - pad.b);
  ctx.lineTo(width - pad.r, height - pad.b);
  ctx.stroke();
  ctx.fillStyle = "#5e584e";
  ctx.font = "11px Segoe UI, sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(formatAxis(maxY), pad.l - 6, pad.t + 8);
  ctx.fillText(formatAxis(minY), pad.l - 6, height - pad.b);
  ctx.textAlign = "left";
  ctx.fillText(points[0].time, pad.l, height - 8);
  ctx.textAlign = "right";
  ctx.fillText(points[points.length - 1].time, width - pad.r, height - 8);

  ctx.strokeStyle = "#1c1915";
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = xOf(point.t);
    const y = yOf(point.y);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();

  for (const mark of opts.markers || []) {
    const x = xOf(mark.t);
    const y = yOf(mark.y);
    ctx.fillStyle = mark.side === "BUY" ? "#1f6b4a" : "#9c2f2f";
    ctx.beginPath();
    if (mark.side === "BUY") {
      ctx.moveTo(x, y - 7);
      ctx.lineTo(x + 5, y + 4);
      ctx.lineTo(x - 5, y + 4);
    } else {
      ctx.moveTo(x, y + 7);
      ctx.lineTo(x + 5, y - 4);
      ctx.lineTo(x - 5, y - 4);
    }
    ctx.closePath();
    ctx.fill();
  }
  return points.map((point) => ({ ...point, x: xOf(point.t) }));
}

function formatAxis(value) {
  if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString("en-IN");
  return Number(value).toFixed(2);
}

function nearest(points, x) {
  let best = null;
  let bestDist = Infinity;
  for (const point of points) {
    const dist = Math.abs(point.x - x);
    if (dist < bestDist) {
      best = point;
      bestDist = dist;
    }
  }
  return best;
}

window.addEventListener("resize", () => {
  if (!state.backtest) return;
  const symbol = $("chart-symbol").value;
  drawPrice(state.backtest, symbol);
  drawEquity(state.backtest.equity || []);
});

let pollTimer = null;

$("analysis-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pollTimer) clearInterval(pollTimer);
  const button = $("run-analysis");
  show($("analysis-error"), false);
  show($("analysis-out"), false);
  show($("analysis-progress"), true);
  text($("analysis-log"), "");
  text($("analysis-pill"), "queued");
  $("analysis-pill").className = "pill";
  button.disabled = true;
  const symbol = $("ai-symbol").value.trim();
  const date = $("ai-date").value;
  text($("analysis-title"), `${symbol.toUpperCase()} · ${date}`);
  try {
    const res = await fetch("/api/analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, date }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(detailMessage(data.detail) || "Could not start analysis.");
    pollTimer = setInterval(() => pollAnalysis(data.job_id, button), 700);
    pollAnalysis(data.job_id, button);
  } catch (err) {
    text($("analysis-error"), err.message || String(err));
    show($("analysis-error"), true);
    button.disabled = false;
  }
});

function detailMessage(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg || JSON.stringify(item)).join(" ");
  return String(detail);
}

async function pollAnalysis(jobId, button) {
  const res = await fetch(`/api/analysis/${jobId}`);
  const data = await res.json();
  if (!res.ok) {
    clearInterval(pollTimer);
    text($("analysis-error"), detailMessage(data.detail) || "Lost the analysis job.");
    show($("analysis-error"), true);
    button.disabled = false;
    return;
  }
  text($("analysis-pill"), data.status);
  $("analysis-pill").className = `pill ${data.status}`;
  text($("analysis-log"), (data.logs || []).join("\n"));
  const log = $("analysis-log");
  log.scrollTop = log.scrollHeight;
  if (data.status === "done") {
    clearInterval(pollTimer);
    button.disabled = false;
    renderAnalysis(data);
  } else if (data.status === "error") {
    clearInterval(pollTimer);
    button.disabled = false;
    text($("analysis-error"), data.error || "TradingAgents failed.");
    show($("analysis-error"), true);
  }
}

function renderAnalysis(data) {
  const result = data.result || {};
  const stub = Boolean(data.stub || result.stub);
  show($("stub-banner"), stub);
  text(
    $("stub-banner"),
    "Demo result. TradingAgents was not called because this environment has no Google API key. On your PC the same button uses the TradingAgents venv and Gemini. This is not a trade and not advice."
  );
  const decision = result.decision || result.final_decision || "No decision";
  text($("decision-text"), decision.split("\n")[0]);
  text($("decision-note"), result.final_decision && result.final_decision !== decision ? result.final_decision : `${result.ticker || ""} · ${result.date || ""} · ${result.provider || "google"}`);
  const host = $("reports");
  host.replaceChildren();
  for (const report of result.reports || []) {
    const article = document.createElement("article");
    article.className = "report";
    const title = document.createElement("h3");
    title.textContent = report.title || "Report";
    const body = document.createElement("p");
    body.textContent = report.body || "";
    article.append(title, body);
    host.append(article);
  }
  show($("analysis-out"), true);
}

loadDefaults().catch(() => syncStrategyFields());
