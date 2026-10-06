const input = document.querySelector("#input");
const run = document.querySelector("#run");
const statusNode = document.querySelector("#status");
let csvText = "";
let lastResult = null;

input.addEventListener("change", async () => {
  const file = input.files?.[0];
  csvText = file ? await file.text() : "";
  document.querySelector("#filename").textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : "需要 date,symbol,adjusted_close,open,high,low,close";
  run.disabled = !csvText;
});

function pct(value) { return value === null || value === undefined ? "—" : `${(Number(value) * 100).toFixed(2)}%`; }
function addCell(row, value, className = "") {
  const cell = document.createElement("td"); cell.textContent = value ?? "—"; if (className) cell.className = className; row.append(cell);
}

run.addEventListener("click", async () => {
  run.disabled = true; statusNode.textContent = "正在计算特征、逐事件退出和账户账本…";
  try {
    const response = await fetch("/api/ewma/backtest", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({csv_text:csvText})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "计算失败");
    lastResult = result; render(result);
    statusNode.textContent = `完成：${result.events.length.toLocaleString("zh-CN")} 个保留状态事件，${result.approvals.length.toLocaleString("zh-CN")} 条审批状态记录。`;
  } catch (error) { statusNode.textContent = error.message; }
  finally { run.disabled = !csvText; }
});

function render(result) {
  document.querySelector("#results").hidden = false;
  document.querySelector("#title").textContent = `${result.symbol} · ${result.config.ranking_window} 日排名 · K=${result.config.holding_days}`;
  document.querySelector("#split").textContent = `切分：${result.split_method}；训练起点行 ${result.train_start_index}；验证起点行 ${result.validation_start_index}；测试起点行 ${result.test_start_index}。`;
  const warnings = document.querySelector("#warnings"); warnings.replaceChildren();
  for (const message of result.issues || []) { const p=document.createElement("p"); p.textContent=message; warnings.append(p); }
  const events = document.querySelector("#events"); events.replaceChildren();
  for (const item of result.summaries.filter(row => row.statistic_type === "overlapping_event_conditional_not_account_return")) {
    const tr=document.createElement("tr"); addCell(tr,item.state); addCell(tr,item.exit_policy); addCell(tr,item.event_count);
    addCell(tr,pct(item.mean_net_return)); addCell(tr,pct(item.median_net_return)); addCell(tr,pct(item.win_rate)); addCell(tr,pct(item.cvar05_net_return)); events.append(tr);
  }
  const horizons=document.querySelector("#horizons"); horizons.replaceChildren();
  for (const item of result.horizon_summaries || []) {
    const tr=document.createElement("tr"); addCell(tr,item.state); addCell(tr,`${item.horizon} 日`); addCell(tr,item.event_count);
    addCell(tr,pct(item.mean_log_return)); addCell(tr,pct(item.median_net_return)); addCell(tr,pct(item.win_rate_net)); addCell(tr,pct(item.cvar05_net_return)); horizons.append(tr);
  }
  const accounts = document.querySelector("#accounts"); accounts.replaceChildren();
  for (const item of result.summaries.filter(row => row.account)) {
    const tr=document.createElement("tr"); addCell(tr,item.account); addCell(tr,Number(item.final_equity).toFixed(5));
    addCell(tr,pct(item.total_return)); addCell(tr,pct(item.max_drawdown)); addCell(tr,item.completed_trades); addCell(tr,item.unfinished_positions); accounts.append(tr);
  }
  const approvals=document.querySelector("#approvals"); approvals.replaceChildren();
  for (const item of result.approvals) {
    const tr=document.createElement("tr"); addCell(tr,item.review_date); addCell(tr,item.policy); addCell(tr,item.state);
    addCell(tr,`${item.evaluation_count} / ${item.matched_count}`);
    addCell(tr,item.exploration_allowed?"允许探索":"不允许",item.exploration_allowed?"pill good":"pill muted");
    addCell(tr,item.statistically_validated?"通过":"未通过",item.statistically_validated?"pill good":"pill muted");
    addCell(tr,pct(item.cvar_lcb95)); addCell(tr,item.sensitivity_bootstrap_valid?pct(item.cvar_lcb95_sensitivity):"无法估计"); approvals.append(tr);
  }
}

document.querySelector("#download").addEventListener("click", () => {
  if (!lastResult) return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(lastResult,null,2)],{type:"application/json"}));
  const link=document.createElement("a"); link.href=url; link.download=`${lastResult.symbol}_ewma_audit.json`; link.click(); URL.revokeObjectURL(url);
});
