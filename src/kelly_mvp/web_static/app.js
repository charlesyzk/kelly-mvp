const state = {
  csvText: "", filename: "", result: null, symbol: "", segment: "all",
  tradeFrequency: "daily", tradePage: 0, tradePageSize: 20,
  strategySource: "", strategyFilename: "", strategyId: "M4_SIMPLE",
};

const $ = (selector) => document.querySelector(selector);
const fileInput = $("#file-input");
const dropZone = $("#drop-zone");
const runButton = $("#run-button");
const status = $("#status");
const eodhdFetchButton = $("#eodhd-fetch");
const strategySelect = $("#strategy-select");
const strategyFile = $("#strategy-file");

const strategyDescriptions = {
  M2_LOG: "对数收益二阶 Taylor 基准；使用均值与二阶风险。",
  M3_LOG: "在二阶基准上加入对数收益三阶矩。",
  M4_LOG_ZERO: "围绕零点展开的四阶对数收益 Kelly。",
  M4_SIMPLE: "简单收益四阶 Taylor Kelly；保持当前项目既有口径。",
  M4_LOG_MEAN: "围绕样本均值展开的四阶对数收益 Kelly。",
  EMPIRICAL_EXACT: "直接最大化窗口经验样本的平均精确对数增长。",
  uploaded: "按模板上传本地 Python 策略；回测、成本和指标仍由框架统一计算。",
};

function updateRunAvailability() {
  runButton.disabled = !state.csvText || (state.strategyId === "uploaded" && !state.strategySource);
}

document.querySelectorAll(".ticks").forEach((el) => {
  el.title = "60 个同频率收益观测";
});

function setFile(text, name) {
  state.csvText = text;
  state.filename = name;
  $("#file-label").textContent = name;
  updateRunAvailability();
  status.className = "status";
  const lines = Math.max(0, text.trim().split(/\r?\n/).length - 1);
  status.textContent = `已读取 ${lines.toLocaleString("zh-CN")} 行，尚未计算。`;
}

strategySelect.addEventListener("change", () => {
  state.strategyId = strategySelect.value;
  const uploaded = state.strategyId === "uploaded";
  $("#strategy-upload").hidden = !uploaded;
  $("#kelly-fraction-field").hidden = uploaded;
  $("#strategy-description").textContent = strategyDescriptions[state.strategyId] || "";
  updateRunAvailability();
});

strategyFile.addEventListener("change", async () => {
  const file = strategyFile.files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".py")) {
    state.strategySource = "";
    status.className = "status error";
    status.textContent = "请选择 .py 策略文件。";
    updateRunAvailability();
    return;
  }
  state.strategySource = await file.text();
  state.strategyFilename = file.name;
  $("#strategy-file-label").textContent = file.name;
  status.className = "status";
  status.textContent = `已加载策略 ${file.name}；选择或拉取行情后即可计算。`;
  updateRunAvailability();
});

document.querySelectorAll(".source-tabs button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".source-tabs button").forEach((item) => item.classList.toggle("active", item === button));
  $("#csv-source").hidden = button.dataset.source !== "csv";
  $("#eodhd-source").hidden = button.dataset.source !== "eodhd";
}));

async function checkEodhdStatus() {
  const note = $("#eodhd-config-status");
  try {
    const response = await fetch("/api/eodhd/status");
    const data = await response.json();
    note.className = data.configured ? "api-note ready" : "api-note error";
    note.textContent = data.configured
      ? "服务端 Token 已配置；密钥不会发送到浏览器。"
      : "服务端未配置 EODHD_API_TOKEN。";
    eodhdFetchButton.disabled = !data.configured;
  } catch (_error) {
    note.className = "api-note error";
    note.textContent = "无法检查 EODHD 配置状态。";
    eodhdFetchButton.disabled = true;
  }
}

checkEodhdStatus();

async function readFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".csv")) {
    status.className = "status error";
    status.textContent = "请选择 .csv 文件。";
    return;
  }
  setFile(await file.text(), file.name);
}

fileInput.addEventListener("change", () => readFile(fileInput.files[0]));
["dragenter", "dragover"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
  event.preventDefault(); dropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => {
  event.preventDefault(); dropZone.classList.remove("dragging");
}));
dropZone.addEventListener("drop", (event) => readFile(event.dataTransfer.files[0]));

$("#demo-button").addEventListener("click", async () => {
  status.className = "status";
  status.textContent = "正在载入合成演示数据…";
  try {
    const response = await fetch("/demo.csv");
    if (!response.ok) throw new Error((await response.json()).error);
    setFile(await response.text(), "SYNTHETIC_DEMO.csv");
  } catch (error) {
    status.className = "status error";
    status.textContent = error.message;
  }
});

eodhdFetchButton.addEventListener("click", async () => {
  eodhdFetchButton.disabled = true;
  const originalText = eodhdFetchButton.textContent;
  eodhdFetchButton.textContent = "正在连接 EODHD…";
  status.className = "status";
  status.textContent = "正在从 EODHD 拉取真实日线；策略计算仍在本机完成。";
  try {
    const response = await fetch("/api/eodhd/prices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: $("#eodhd-symbol").value,
        start_date: $("#eodhd-from").value,
        end_date: $("#eodhd-to").value,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "EODHD取数失败");
    setFile(data.csv_text, `${data.symbol}_${data.first_date}_${data.last_date}_EODHD.csv`);
    status.textContent = `EODHD 已返回 ${data.rows.toLocaleString("zh-CN")} 条真实日线（${data.first_date} 至 ${data.last_date}），可以开始计算。`;
  } catch (error) {
    status.className = "status error";
    status.textContent = error.message;
  } finally {
    eodhdFetchButton.disabled = false;
    eodhdFetchButton.textContent = originalText;
  }
});

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  runButton.classList.add("loading");
  runButton.querySelector("span").textContent = "正在滚动计算";
  status.className = "status";
  status.textContent = "正在生成日、周、月仓位并验证下一期…";
  try {
    const response = await fetch("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        csv_text: state.csvText,
        strategy_id: state.strategyId,
        strategy_source: state.strategyId === "uploaded" ? state.strategySource : undefined,
        strategy_filename: state.strategyId === "uploaded" ? state.strategyFilename : undefined,
        kelly_fraction: Number($("#fraction").value),
        transaction_cost_bps: Number($("#cost").value),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "计算失败");
    state.result = data;
    const symbols = [...new Set(data.summaries.map((row) => row.symbol))];
    state.symbol = symbols[0];
    state.tradeFrequency = "daily";
    state.tradePage = 0;
    $("#symbol-select").innerHTML = symbols.map((symbol) => `<option>${escapeHtml(symbol)}</option>`).join("");
    $("#empty-state").hidden = true;
    $("#results").hidden = false;
    $("#active-strategy").textContent = `${data.strategy.name} · v${data.strategy.version}`;
    render();
    status.textContent = `${data.strategy.name} 完成 ${data.periods.length.toLocaleString("zh-CN")} 次样本外评价。`;
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    status.className = "status error";
    status.textContent = error.message;
  } finally {
    runButton.disabled = false;
    runButton.classList.remove("loading");
    runButton.querySelector("span").textContent = "重新计算";
  }
});

$("#symbol-select").addEventListener("change", (event) => {
  state.symbol = event.target.value; state.tradePage = 0; render();
});
document.querySelectorAll(".segment-tabs button").forEach((button) => button.addEventListener("click", () => {
  state.segment = button.dataset.segment;
  state.tradePage = 0;
  document.querySelectorAll(".segment-tabs button").forEach((item) => item.classList.toggle("active", item === button));
  render();
}));
document.querySelectorAll("#trade-frequency-tabs button").forEach((button) => button.addEventListener("click", () => {
  state.tradeFrequency = button.dataset.frequency;
  state.tradePage = 0;
  document.querySelectorAll("#trade-frequency-tabs button").forEach((item) => item.classList.toggle("active", item === button));
  renderPositionWorkbench();
}));
$("#trade-prev").addEventListener("click", () => { state.tradePage -= 1; renderTradeTable(); });
$("#trade-next").addEventListener("click", () => { state.tradePage += 1; renderTradeTable(); });

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}
function pct(value) { return value == null ? "—" : `${(value * 100).toFixed(2)}%`; }
function number(value, digits = 2) { return value == null ? "—" : Number(value).toFixed(digits); }
function bps(value) { return value == null ? "—" : `${(Number(value) * 10000).toFixed(2)} bps`; }
function frequencyName(value) { return ({ daily: "日频", weekly: "周频", monthly: "月频" })[value]; }
function signedPct(value) {
  if (value == null) return "—";
  const numeric = Number(value);
  return `${numeric > 0 ? "+" : ""}${(numeric * 100).toFixed(2)}%`;
}
function tradeActionName(value) {
  return ({
    open_long: "开多", open_short: "开空", close_long: "平多", close_short: "平空",
    add_long: "加多", reduce_long: "减多", add_short: "加空", cover_short: "减空",
    reverse_to_long: "反手做多", reverse_to_short: "反手做空",
  })[value] || value;
}

function render() {
  if (!state.result) return;
  const frequencies = ["daily", "weekly", "monthly"];
  const summaries = frequencies.map((frequency) => state.result.summaries.find((row) => row.symbol === state.symbol && row.frequency === frequency && row.segment === state.segment));
  $("#metric-cards").innerHTML = summaries.map((row, index) => {
    const frequency = frequencies[index];
    if (!row) return `<article class="metric-card ${frequency}"><header><b>${frequencyName(frequency)}</b></header><p>数据不足</p></article>`;
    return `<article class="metric-card ${frequency}">
      <header><b>${frequencyName(frequency)}</b><span>${row.window} ${frequency === "daily" ? "days" : frequency === "weekly" ? "weeks" : "months"}</span></header>
      <div class="primary-metric"><strong>${pct(row.direction_accuracy)}</strong><small>方向准确率<br>${row.direction_observations} 次判断</small></div>
      <div class="metric-list">
        <div><span>策略累计收益</span><b>${pct(row.total_return)}</b></div>
        <div><span>买入持有</span><b>${pct(row.buy_hold_return)}</b></div>
        <div><span>年化收益</span><b>${pct(row.annualized_return)}</b></div>
        <div><span>最大回撤</span><b>${pct(row.max_drawdown)}</b></div>
        <div><span>覆盖率</span><b>${pct(row.coverage)}</b></div>
        <div><span>平均换手</span><b>${number(row.average_turnover, 3)}</b></div>
      </div></article>`;
  }).join("");
  $("#charts").innerHTML = frequencies.map((frequency) => chartCard(frequency)).join("");
  renderPositionWorkbench();
  const warnings = state.result.issues.filter((issue) => issue.startsWith(`${state.symbol}/`));
  $("#warnings").hidden = warnings.length === 0;
  $("#warnings").innerHTML = warnings.map((warning) => `<div>${escapeHtml(warning)}</div>`).join("");
}

function visiblePeriods(frequency = state.tradeFrequency) {
  let rows = state.result.signals.filter((row) => row.symbol === state.symbol && row.frequency === frequency);
  if (state.segment !== "all") rows = rows.filter((row) => row.segment === state.segment);
  return rows;
}

function dateLabels(rows, width, left, right, y) {
  if (!rows.length) return "";
  const indices = [...new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1])];
  const span = width - left - right;
  return indices.map((index) => {
    const x = left + (index / Math.max(1, rows.length - 1)) * span;
    const anchor = index === 0 ? "start" : index === rows.length - 1 ? "end" : "middle";
    return `<text x="${x.toFixed(1)}" y="${y}" text-anchor="${anchor}" class="axis-label">${escapeHtml(rows[index].signal_date)}</text>`;
  }).join("");
}

function renderPositionChart(rows) {
  if (!rows.length) return '<div class="chart-empty">当前区间没有仓位记录</div>';
  const width = 760, height = 228, left = 48, right = 18, top = 18, bottom = 34;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = (index) => left + index / Math.max(1, rows.length - 1) * plotWidth;
  const y = (value) => top + (1 - (Number(value) + 1) / 2) * plotHeight;
  const path = rows.map((row, index) => `${index ? "L" : "M"}${x(index).toFixed(2)},${y(row.position).toFixed(2)}`).join(" ");
  const last = rows[rows.length - 1];
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="目标仓位随时间变化，零线上方为做多，下方为做空">
    <defs><linearGradient id="position-gradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#2858cc"/><stop offset="49.5%" stop-color="#2858cc"/><stop offset="50.5%" stop-color="#d26a31"/><stop offset="100%" stop-color="#d26a31"/></linearGradient></defs>
    <rect x="${left}" y="${top}" width="${plotWidth}" height="${plotHeight / 2}" class="long-zone"/>
    <rect x="${left}" y="${top + plotHeight / 2}" width="${plotWidth}" height="${plotHeight / 2}" class="short-zone"/>
    <line x1="${left}" y1="${y(1)}" x2="${width - right}" y2="${y(1)}" class="tape-grid"/>
    <line x1="${left}" y1="${y(0)}" x2="${width - right}" y2="${y(0)}" class="zero-line"/>
    <line x1="${left}" y1="${y(-1)}" x2="${width - right}" y2="${y(-1)}" class="tape-grid"/>
    <text x="8" y="${y(1) + 4}" class="axis-label">+100%</text><text x="21" y="${y(0) + 4}" class="axis-label">0%</text><text x="8" y="${y(-1) + 4}" class="axis-label">−100%</text>
    <path d="${path}" class="position-line"/>
    <circle cx="${x(rows.length - 1)}" cy="${y(last.position)}" r="3.5" class="position-end"/>
    ${dateLabels(rows, width, left, right, height - 8)}
  </svg>`;
}

function renderChangeChart(rows) {
  if (!rows.length) return '<div class="chart-empty">当前区间没有调仓变化</div>';
  const width = 760, height = 170, left = 48, right = 18, top = 15, bottom = 34;
  const plotWidth = width - left - right, middle = top + (height - top - bottom) / 2;
  const maxChange = Math.max(0.01, ...rows.map((row) => Math.abs(Number(row.position_change))));
  const scale = (height - top - bottom) / 2 / maxChange;
  const x = (index) => left + index / Math.max(1, rows.length - 1) * plotWidth;
  const positive = [], negative = [];
  rows.forEach((row, index) => {
    const target = middle - Number(row.position_change) * scale;
    const command = `M${x(index).toFixed(2)},${middle.toFixed(2)}V${target.toFixed(2)}`;
    (row.position_change >= 0 ? positive : negative).push(command);
  });
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="每次目标仓位变化，向上表示仓位数值增加，向下表示仓位数值减少">
    <line x1="${left}" y1="${top}" x2="${width - right}" y2="${top}" class="tape-grid"/>
    <line x1="${left}" y1="${middle}" x2="${width - right}" y2="${middle}" class="zero-line"/>
    <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" class="tape-grid"/>
    <text x="4" y="${top + 4}" class="axis-label">${signedPct(maxChange)}</text><text x="21" y="${middle + 4}" class="axis-label">0%</text><text x="4" y="${height - bottom + 4}" class="axis-label">${signedPct(-maxChange)}</text>
    <path d="${positive.join(" ")}" class="change-positive"/><path d="${negative.join(" ")}" class="change-negative"/>
    ${dateLabels(rows, width, left, right, height - 8)}
  </svg>`;
}

function visibleTrades() {
  let rows = state.result.trades.filter((row) => row.symbol === state.symbol && row.frequency === state.tradeFrequency);
  if (state.segment !== "all") rows = rows.filter((row) => row.segment === state.segment);
  return rows.slice().reverse();
}

function renderTradeTable() {
  const rows = visibleTrades();
  const pages = Math.max(1, Math.ceil(rows.length / state.tradePageSize));
  state.tradePage = Math.max(0, Math.min(state.tradePage, pages - 1));
  const start = state.tradePage * state.tradePageSize;
  const pageRows = rows.slice(start, start + state.tradePageSize);
  $("#trade-count").textContent = `${rows.length.toLocaleString("zh-CN")} 笔`;
  $("#trade-page").textContent = rows.length ? `第 ${state.tradePage + 1} / ${pages} 页` : "无调仓记录";
  $("#trade-prev").disabled = state.tradePage === 0;
  $("#trade-next").disabled = state.tradePage >= pages - 1;
  $("#trade-table-body").innerHTML = pageRows.length ? pageRows.map((row) => {
    const directionClass = row.position_change > 0 ? "increase" : "decrease";
    const pending = row.evaluation_status === "pending";
    return `<tr><td>${escapeHtml(row.signal_date)}</td><td><span class="action-tag ${directionClass}">${escapeHtml(tradeActionName(row.action))}</span></td>
      <td>${pct(row.previous_position)}</td><td>${pct(row.target_position)}</td><td class="${directionClass}">${signedPct(row.position_change)}</td>
      <td>${pct(row.cost_rate)}</td><td><span class="evaluation-tag ${pending ? "pending" : "evaluated"}">${pending ? "待验证" : "已评价"}</span></td>
      <td>${pending ? "—" : escapeHtml(row.return_date)}</td><td class="${pending ? "" : row.net_return >= 0 ? "increase" : "decrease"}">${pending ? "—" : signedPct(row.net_return)}</td></tr>`;
  }).join("") : '<tr><td colspan="9" class="table-empty">当前区间没有非零仓位变化</td></tr>';
}

function renderDiagnostics() {
  const summary = state.result.summaries.find((row) => row.symbol === state.symbol && row.frequency === state.tradeFrequency && row.segment === state.segment);
  const items = summary ? [
    ["单期平均对数增长", bps(summary.average_log_growth)],
    ["对数增长年化", pct(summary.annualized_log_growth)],
    ["触及仓位边界比例", pct(summary.boundary_rate)],
  ] : [];
  if (summary && summary.mean_abs_exact_kelly_gap != null) {
    items.push(["与经验精确仓位平均差", pct(summary.mean_abs_exact_kelly_gap)]);
    items.push(["与经验精确方向一致率", pct(summary.exact_direction_agreement)]);
  } else if (summary) {
    const latest = visiblePeriods().at(-1);
    Object.entries(latest?.diagnostics || {}).slice(0, 2).forEach(([key, value]) => {
      items.push([key, typeof value === "number" ? number(value, 4) : String(value)]);
    });
  }
  $("#diagnostic-title").textContent = `${state.result.strategy.name} · 策略诊断`;
  $("#diagnostic-note").textContent = state.result.strategy.kind === "uploaded"
    ? "上传策略的自定义诊断与统一绩效"
    : "经验精确解用于比较同一窗口的目标差异";
  $("#strategy-diagnostics").innerHTML = items.length
    ? items.map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("")
    : '<p class="diagnostic-empty">当前频率与区间没有可评价诊断数据。</p>';
}

function renderPositionWorkbench() {
  if (!state.result) return;
  const rows = visiblePeriods();
  const trades = visibleTrades();
  $("#position-chart").innerHTML = renderPositionChart(rows);
  $("#change-chart").innerHTML = renderChangeChart(rows);
  const latest = rows[rows.length - 1];
  $("#position-caption").textContent = latest
    ? `最新 ${pct(latest.position)} · ${latest.signal_date}${latest.evaluation_status === "pending" ? " · 待验证" : ""}`
    : "无记录";
  const maxChange = rows.length ? Math.max(...rows.map((row) => Math.abs(row.position_change))) : 0;
  $("#change-caption").textContent = `${trades.length.toLocaleString("zh-CN")} 笔 · 最大变动 ${pct(maxChange)}`;
  renderDiagnostics();
  renderTradeTable();
}

function chartCard(frequency) {
  let rows = state.result.periods.filter((row) => row.symbol === state.symbol && row.frequency === frequency);
  if (state.segment !== "all") rows = rows.filter((row) => row.segment === state.segment);
  if (!rows.length) return `<article class="chart-card"><header><b>${frequencyName(frequency)}</b><span>无数据</span></header></article>`;
  let strategy = 1, benchmark = 1;
  const strategyValues = [1], benchmarkValues = [1];
  rows.forEach((row) => {
    strategy *= 1 + row.net_return; benchmark *= 1 + row.next_return;
    strategyValues.push(strategy); benchmarkValues.push(benchmark);
  });
  const all = strategyValues.concat(benchmarkValues);
  const low = Math.min(...all), high = Math.max(...all), span = high - low || 1;
  const points = (values) => values.map((value, index) => `${(index / (values.length - 1) * 300).toFixed(1)},${(140 - (value - low) / span * 140).toFixed(1)}`).join(" ");
  return `<article class="chart-card"><header><b>${frequencyName(frequency)}净值</b><span>${rows.length} 期</span></header>
    <svg viewBox="0 0 300 140" role="img" aria-label="${frequencyName(frequency)}策略与买入持有净值">
      <line x1="0" y1="35" x2="300" y2="35" class="chart-grid"/><line x1="0" y1="70" x2="300" y2="70" class="chart-grid"/><line x1="0" y1="105" x2="300" y2="105" class="chart-grid"/>
      <polyline points="${points(benchmarkValues)}" class="line-benchmark"/><polyline points="${points(strategyValues)}" class="line-strategy"/>
    </svg><div class="chart-legend"><span class="strategy-key"><i></i>策略 ${pct(strategy - 1)}</span><span class="benchmark-key"><i></i>买入持有 ${pct(benchmark - 1)}</span></div></article>`;
}

function csvValue(value) {
  const text = value == null ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}
function downloadCsv(rows, filename) {
  if (!rows.length) return;
  const columns = Object.keys(rows[0]);
  const csv = [columns.join(","), ...rows.map((row) => columns.map((column) => csvValue(row[column])).join(","))].join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
  link.download = filename; link.click(); URL.revokeObjectURL(link.href);
}
function outputName(suffix) { return `${state.result.strategy.id}_${suffix}.csv`; }
$("#download-summary").addEventListener("click", () => downloadCsv(state.result.summaries, outputName("summary")));
$("#download-signals").addEventListener("click", () => downloadCsv(state.result.signals, outputName("signals")));
$("#download-periods").addEventListener("click", () => downloadCsv(state.result.periods, outputName("periods")));
$("#download-trades").addEventListener("click", () => downloadCsv(state.result.trades, outputName("trades")));
