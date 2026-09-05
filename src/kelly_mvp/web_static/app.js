const state = { csvText: "", filename: "", result: null, symbol: "", segment: "all" };

const $ = (selector) => document.querySelector(selector);
const fileInput = $("#file-input");
const dropZone = $("#drop-zone");
const runButton = $("#run-button");
const status = $("#status");

document.querySelectorAll(".ticks").forEach((el) => {
  el.title = "60 个同频率收益观测";
});

function setFile(text, name) {
  state.csvText = text;
  state.filename = name;
  $("#file-label").textContent = name;
  runButton.disabled = false;
  status.className = "status";
  const lines = Math.max(0, text.trim().split(/\r?\n/).length - 1);
  status.textContent = `已读取 ${lines.toLocaleString("zh-CN")} 行，尚未计算。`;
}

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
        kelly_fraction: Number($("#fraction").value),
        transaction_cost_bps: Number($("#cost").value),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "计算失败");
    state.result = data;
    const symbols = [...new Set(data.summaries.map((row) => row.symbol))];
    state.symbol = symbols[0];
    $("#symbol-select").innerHTML = symbols.map((symbol) => `<option>${escapeHtml(symbol)}</option>`).join("");
    $("#empty-state").hidden = true;
    $("#results").hidden = false;
    render();
    status.textContent = `完成 ${data.periods.length.toLocaleString("zh-CN")} 次样本外评价。`;
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

$("#symbol-select").addEventListener("change", (event) => { state.symbol = event.target.value; render(); });
document.querySelectorAll(".segment-tabs button").forEach((button) => button.addEventListener("click", () => {
  state.segment = button.dataset.segment;
  document.querySelectorAll(".segment-tabs button").forEach((item) => item.classList.toggle("active", item === button));
  render();
}));

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}
function pct(value) { return value == null ? "—" : `${(value * 100).toFixed(2)}%`; }
function number(value, digits = 2) { return value == null ? "—" : Number(value).toFixed(digits); }
function frequencyName(value) { return ({ daily: "日频", weekly: "周频", monthly: "月频" })[value]; }

function render() {
  if (!state.result) return;
  const frequencies = ["daily", "weekly", "monthly"];
  const summaries = frequencies.map((frequency) => state.result.summaries.find((row) => row.symbol === state.symbol && row.frequency === frequency && row.segment === state.segment));
  $("#metric-cards").innerHTML = summaries.map((row, index) => {
    const frequency = frequencies[index];
    if (!row) return `<article class="metric-card ${frequency}"><header><b>${frequencyName(frequency)}</b></header><p>数据不足</p></article>`;
    return `<article class="metric-card ${frequency}">
      <header><b>${frequencyName(frequency)}</b><span>60 ${frequency === "daily" ? "days" : frequency === "weekly" ? "weeks" : "months"}</span></header>
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
  const warnings = state.result.issues.filter((issue) => issue.startsWith(`${state.symbol}/`));
  $("#warnings").hidden = warnings.length === 0;
  $("#warnings").innerHTML = warnings.map((warning) => `<div>${escapeHtml(warning)}</div>`).join("");
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
$("#download-summary").addEventListener("click", () => downloadCsv(state.result.summaries, "kelly_summary.csv"));
$("#download-periods").addEventListener("click", () => downloadCsv(state.result.periods, "kelly_periods.csv"));
