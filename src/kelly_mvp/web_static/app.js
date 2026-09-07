const state = { csvText: "", workbookBase64: null, priceSeries: null, strategyId: "KELLY_SIX_MODEL", strategySource: "", strategyFilename: "", result: null, tradePage: 0, tradePageSize: 25 };
let kappaTimer = null;
const $ = (selector) => document.querySelector(selector);
const runButton = $("#run-button");
const status = $("#status");
const strategySelect = $("#strategy-select");
const strategyFile = $("#strategy-file");
const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const pct = (value) => value == null ? "—" : `${(Number(value) * 100).toFixed(2)}%`;
const num = (value, digits=4) => value == null ? "—" : Number(value).toFixed(digits);

function setSource(text, name, series=null, workbookBase64=null) {
  state.csvText = text || ""; state.priceSeries = series; state.workbookBase64 = workbookBase64;
  $("#file-label").textContent = name; updateRunAvailability();
  status.className = "status"; status.textContent = "行情已加载，尚未计算。";
}

function hasPriceSource() { return Boolean(state.csvText || state.workbookBase64 || state.priceSeries); }
function updateRunAvailability() {
  runButton.disabled = !hasPriceSource() || (state.strategyId === "uploaded" && !state.strategySource);
}
function applyStrategyMode() {
  const uploaded = state.strategyId === "uploaded";
  $("#strategy-upload").hidden = !uploaded;
  $("#kelly-controls").hidden = uploaded;
  $("#position-rule").querySelector("span").textContent = uploaded ? "上传策略仓位" : "Kelly 内部仓位";
  $("#position-rule").querySelector("strong").textContent = uploaded ? "TARGET" : "RAW · BOUNDED · SAFE";
  $("#strategy-description").textContent = uploaded
    ? "按模板上传可信 Python 策略；框架统一完成无前视的下一期验证。"
    : "完整运行六个 Kelly 模型及 RAW、BOUNDED、SAFE 三类独立仓位。";
  $("#logic-title").textContent = uploaded ? "一个目标仓位，同一套验证链路" : "六个目标函数，三种独立求解";
  $("#logic-caption").textContent = uploaded
    ? "策略只读取当前窗口 · 框架限制仓位 · 下一期结果独立评价"
    : "RAW 看模型原始倾向 · BOUNDED 限制敞口 · SAFE 再限制 Taylor 收敛域";
  $("#empty-copy").textContent = uploaded
    ? "加载策略和行情后，查看 TARGET 仓位的真实逐期计算。"
    : "加载行情后，选择模型、仓位类型和频率查看真实逐期计算。";
  status.textContent = hasPriceSource()
    ? uploaded && !state.strategySource ? "行情已加载；请再上传策略文件。" : "行情已加载，尚未计算。"
    : uploaded ? "先上传策略并加载行情，再运行验证。" : "先加载行情，再运行六模型验证。";
  runButton.querySelector("span").textContent = uploaded ? "运行上传策略" : "按当前 κ 计算";
  updateRunAvailability();
}
strategySelect.addEventListener("change", () => {
  state.strategyId = strategySelect.value;
  state.result = null;
  $("#results").hidden = true;
  $("#empty-state").hidden = false;
  applyStrategyMode();
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
applyStrategyMode();

document.querySelectorAll(".source-tabs button").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll(".source-tabs button").forEach((item) => item.classList.toggle("active", item === button));
  $("#csv-source").hidden = button.dataset.source !== "csv";
  $("#eodhd-source").hidden = button.dataset.source !== "eodhd";
}));

async function readFile(file) {
  if (!file) return;
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".xlsx")) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i=0; i<bytes.length; i+=0x8000) binary += String.fromCharCode(...bytes.subarray(i,i+0x8000));
    setSource("", file.name, null, btoa(binary)); return;
  }
  if (!lower.endsWith(".csv")) {
    status.className = "status error"; status.textContent = "请选择 CSV 或 XLSX。"; return;
  }
  setSource(await file.text(), file.name);
}
$("#file-input").addEventListener("change", (event) => readFile(event.target.files[0]));
["dragenter","dragover"].forEach((name) => $("#drop-zone").addEventListener(name, (event) => { event.preventDefault(); event.currentTarget.classList.add("dragging"); }));
["dragleave","drop"].forEach((name) => $("#drop-zone").addEventListener(name, (event) => { event.preventDefault(); event.currentTarget.classList.remove("dragging"); }));
$("#drop-zone").addEventListener("drop", (event) => readFile(event.dataTransfer.files[0]));
$("#demo-button").addEventListener("click", async () => {
  const response = await fetch("/demo.csv"); setSource(await response.text(), "SYNTHETIC_DEMO.csv");
});

async function checkEodhd() {
  const note = $("#eodhd-config-status");
  try {
    const data = await (await fetch("/api/eodhd/status")).json();
    note.className = data.configured ? "api-note ready" : "api-note error";
    note.textContent = data.configured ? "Token 已配置；密钥不会发送到浏览器。" : "服务端未配置 EODHD_API_TOKEN。";
    $("#eodhd-fetch").disabled = !data.configured;
  } catch (_) { note.className = "api-note error"; note.textContent = "无法检查 EODHD 配置。"; }
}
checkEodhd();

$("#eodhd-fetch").addEventListener("click", async (event) => {
  const button = event.currentTarget; button.disabled = true; status.textContent = "正在分别拉取日、周、月行情…";
  try {
    const response = await fetch("/api/eodhd/prices", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({symbol:$("#eodhd-symbol").value,start_date:$("#eodhd-from").value,end_date:$("#eodhd-to").value})});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || "EODHD 取数失败");
    setSource("", `${data.symbol} · EODHD 三频`, data.price_series);
    status.textContent = `已加载日 ${data.rows.daily}、周 ${data.rows.weekly}、月 ${data.rows.monthly} 条。`;
  } catch (error) { status.className = "status error"; status.textContent = error.message; }
  finally { button.disabled = false; }
});

function setKappa(value) {
  $("#kappa").value = value; $("#kappa-value").textContent = Number(value).toFixed(2);
  document.querySelectorAll("[data-kappa]").forEach((b) => b.classList.toggle("active", Number(b.dataset.kappa) === Number(value)));
  if (state.result && state.strategyId === "KELLY_SIX_MODEL") {
    status.textContent = "κ 已改变，正在准备自动重算…";
    clearTimeout(kappaTimer);
    kappaTimer = setTimeout(() => runButton.click(), 450);
  }
}
$("#kappa").addEventListener("input", (event) => setKappa(event.target.value));
document.querySelectorAll("[data-kappa]").forEach((button) => button.addEventListener("click", () => setKappa(button.dataset.kappa)));

runButton.addEventListener("click", async () => {
  const uploaded = state.strategyId === "uploaded";
  runButton.disabled = true; status.className = "status"; status.textContent = uploaded ? "正在运行上传策略…" : "正在运行六模型与三类仓位…";
  try {
    const payload = {strategy_id:state.strategyId, convergence_kappa:Number($("#kappa").value)};
    if (uploaded) {
      payload.strategy_source = state.strategySource;
      payload.strategy_filename = state.strategyFilename;
    }
    if (state.workbookBase64) payload.workbook_b64 = state.workbookBase64;
    else if (state.priceSeries) payload.price_series = state.priceSeries;
    else payload.csv_text = state.csvText;
    const response = await fetch("/api/backtest", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data = await response.json(); if (!response.ok) throw new Error(data.error || "计算失败");
    state.result = data;
    fillSelect("#symbol-select", [...new Set(data.summaries.map((r) => r.symbol))]);
    fillSelect("#model-select", [...new Set(data.summaries.map((r) => r.model_id))]);
    fillSelect("#position-select", [...new Set(data.summaries.map((r) => r.position_type))]);
    if (!uploaded) { $("#model-select").value = "M4_SIMPLE"; $("#position-select").value = "SAFE"; }
    $("#download-statistics").hidden = uploaded;
    $("#empty-state").hidden = true; $("#results").hidden = false; render();
    status.textContent = uploaded
      ? `完成 ${data.periods.length.toLocaleString("zh-CN")} 行逐期评价；策略 ${data.strategy.name}。`
      : `完成 ${data.periods.length.toLocaleString("zh-CN")} 行逐期评价；κ=${data.config.convergence_kappa}。`;
  } catch (error) { status.className = "status error"; status.textContent = error.message; }
  finally { updateRunAvailability(); runButton.querySelector("span").textContent = uploaded ? "重新运行上传策略" : "按当前 κ 重新计算"; }
});

function fillSelect(selector, values) { $(selector).innerHTML = values.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join(""); }
["#symbol-select","#model-select","#position-select","#frequency-select"].forEach((selector) => $(selector).addEventListener("change", () => { state.tradePage = 0; render(); }));
$("#trade-prev").addEventListener("click", () => { state.tradePage -= 1; renderTradeTable(); });
$("#trade-next").addEventListener("click", () => { state.tradePage += 1; renderTradeTable(); });

function selected() { return {symbol:$("#symbol-select").value,model:$("#model-select").value,position:$("#position-select").value,frequency:$("#frequency-select").value}; }
function filtered(collection) { const f=selected(); return collection.filter((r) => r.symbol===f.symbol && r.model_id===f.model && r.position_type===f.position && r.frequency===f.frequency); }

function render() {
  const rows = filtered(state.result.periods); const signals = filtered(state.result.signals);
  const summary = filtered(state.result.summaries)[0];
  $("#metric-cards").innerHTML = summary ? [
    ["方向准确率",pct(summary.direction_accuracy),`${summary.direction_observations} 次有方向判断`],
    ["平均截断对数增长",num(summary.average_truncated_log_growth,6),summary.formal_sample_eligible?"达到正式样本门槛":"仅描述 未达门槛"],
    ["累计收益",pct(summary.total_return),`买入持有 ${pct(summary.buy_hold_return)}`],
    ["最大回撤",pct(summary.max_drawdown),`覆盖率 ${pct(summary.coverage)}`],
  ].map((x) => `<article class="metric-card"><header><b>${x[0]}</b></header><div class="primary-metric"><strong>${x[1]}</strong><small>${x[2]}</small></div></article>`).join("") : "<p>该组合数据不足。</p>";
  renderFrequencyOverview(); renderCharts(signals, rows); renderDiagnostics(summary, signals); renderTradeTable(); renderTable(rows);
  const latest = signals[signals.length-1]; $("#latest-position").textContent = latest ? `${latest.evaluation_status === "pending" ? "待验证" : "最新"} ${pct(latest.position_value)}` : "";
  const warnings = state.result.issues; $("#warnings").hidden = !warnings.length; $("#warnings").innerHTML = warnings.map((x) => `<div>${escapeHtml(x)}</div>`).join("");
}

function dateLabels(rows,width,left,right,y) {
  if (!rows.length) return ""; const indexes=[...new Set([0,Math.floor((rows.length-1)/2),rows.length-1])];
  return indexes.map((i)=>{const x=left+i/Math.max(1,rows.length-1)*(width-left-right);const anchor=i===0?"start":i===rows.length-1?"end":"middle";return `<text x="${x}" y="${y}" text-anchor="${anchor}" class="axis-label">${escapeHtml(rows[i].signal_date)}</text>`;}).join("");
}
function positionChart(rows) {
  const valid=rows.filter((r)=>r.position_value!=null); if(!valid.length)return '<div class="chart-empty">该仓位当前全部缺失</div>';
  const w=760,h=210,l=48,r=18,t=16,b=30,x=(i)=>l+i/Math.max(1,valid.length-1)*(w-l-r),y=(v)=>t+(1-Number(v))/2*(h-t-b);
  const path=valid.map((row,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(row.position_value).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}"><rect x="${l}" y="${t}" width="${w-l-r}" height="${(h-t-b)/2}" class="long-zone"/><rect x="${l}" y="${y(0)}" width="${w-l-r}" height="${(h-t-b)/2}" class="short-zone"/><line x1="${l}" y1="${y(0)}" x2="${w-r}" y2="${y(0)}" class="zero-line"/><text x="5" y="${y(1)+4}" class="axis-label">+100%</text><text x="22" y="${y(0)+4}" class="axis-label">0%</text><text x="5" y="${y(-1)+4}" class="axis-label">−100%</text><path d="${path}" class="position-line"/>${dateLabels(valid,w,l,r,h-7)}</svg>`;
}
function changeChart(rows) {
  const valid=rows.filter((r)=>r.position_change!=null); if(!valid.length)return '<div class="chart-empty">没有可比较的仓位变化</div>';
  const w=760,h=170,l=48,r=18,t=15,b=30,m=(h-b+t)/2,cap=Math.max(.01,...valid.map((r)=>Math.abs(r.position_change))),scale=(h-t-b)/2/cap,x=(i)=>l+i/Math.max(1,valid.length-1)*(w-l-r);let up=[],down=[];
  valid.forEach((row,i)=>{const command=`M${x(i).toFixed(1)},${m}V${(m-Number(row.position_change)*scale).toFixed(1)}`;(row.position_change>=0?up:down).push(command);});
  return `<svg viewBox="0 0 ${w} ${h}"><line x1="${l}" y1="${m}" x2="${w-r}" y2="${m}" class="zero-line"/><path d="${up.join(" ")}" class="change-positive"/><path d="${down.join(" ")}" class="change-negative"/>${dateLabels(valid,w,l,r,h-7)}</svg>`;
}
function wealthChart(rows) {
  if(!rows.length)return '<div class="chart-empty">没有净值数据</div>';const w=760,h=210,l=48,r=18,t=16,b=30;
  const all=rows.flatMap((row)=>[row.cumulative_wealth,row.buy_hold_wealth]).filter(Number.isFinite),lo=Math.min(...all),hi=Math.max(...all),span=hi-lo||1,x=(i)=>l+i/Math.max(1,rows.length-1)*(w-l-r),y=(v)=>t+(hi-v)/span*(h-t-b);
  const path=(field)=>rows.map((row,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(row[field]).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}"><path d="${path("cumulative_wealth")}" class="line-strategy"/><path d="${path("buy_hold_wealth")}" class="line-benchmark"/>${dateLabels(rows,w,l,r,h-7)}</svg>`;
}
function renderCharts(signals, rows) {
  $("#position-chart").innerHTML=positionChart(signals);$("#change-chart").innerHTML=changeChart(rows);$("#wealth-chart").innerHTML=wealthChart(rows);
  $("#position-caption").textContent=`${signals.length} 个信号`;$("#change-caption").textContent=`${rows.filter((r)=>r.position_change!=null&&Math.abs(r.position_change)>1e-12).length} 笔变化`;
  const last=rows[rows.length-1];$("#wealth-caption").textContent=last?`策略 ${num(last.cumulative_wealth,3)} · 持有 ${num(last.buy_hold_wealth,3)}`:"";
}
function renderDiagnostics(summary, signals) {
  const uploaded=state.result?.strategy?.kind==="uploaded";
  $("#diagnostic-title").textContent=uploaded?"上传策略诊断":"当前模型 / 经验精确 Kelly";
  $("#diagnostic-caption").textContent=uploaded?"来自策略返回的 diagnostics，不参与仓位修正":"只作近似误差诊断，不改变正式判定";
  const latest=signals[signals.length-1];
  const items=uploaded
    ? Object.entries(latest?.diagnostics||{}).map(([key,value])=>[key,escapeHtml(value)])
    : summary?[["平均绝对仓位",pct(summary.average_abs_position)],["相对经验精确平均仓位差",pct(summary.mean_abs_exact_position_gap)],["经验精确目标平均损失",num(summary.mean_exact_empirical_objective_loss,7)],["与经验精确方向一致率",pct(summary.exact_direction_agreement)],["触及 ±100% 边界",pct(summary.boundary_rate)],["财富可行率",pct(summary.wealth_feasibility_rate)]]:[];
  $("#strategy-diagnostics").innerHTML=items.map(([label,value])=>`<div><span>${label}</span><strong>${value}</strong></div>`).join("");
}
function renderFrequencyOverview(){const f=selected(),names={daily:"日频",weekly:"周频",monthly:"月频"},rows=state.result.summaries.filter((r)=>r.symbol===f.symbol&&r.model_id===f.model&&r.position_type===f.position);$("#frequency-overview-grid").innerHTML=["daily","weekly","monthly"].map((frequency)=>{const row=rows.find((r)=>r.frequency===frequency);return `<article><b>${names[frequency]}</b>${row?`<span>准确 ${pct(row.direction_accuracy)}</span><span>收益 ${pct(row.total_return)}</span><span>回撤 ${pct(row.max_drawdown)}</span>`:"<span>数据不足</span>"}</article>`;}).join("");}
function tradeActionName(value){return ({open_long:"开多",open_short:"开空",close_long:"平多",close_short:"平空",add_long:"加多",reduce_long:"减多",add_short:"加空",cover_short:"减空",reverse_to_long:"反手做多",reverse_to_short:"反手做空"})[value]||value;}
function renderTradeTable(){
  if(!state.result)return;const rows=filtered(state.result.trades).slice().reverse(),pages=Math.max(1,Math.ceil(rows.length/state.tradePageSize));state.tradePage=Math.max(0,Math.min(state.tradePage,pages-1));const page=rows.slice(state.tradePage*state.tradePageSize,(state.tradePage+1)*state.tradePageSize);
  $("#trade-count").textContent=`${rows.length.toLocaleString("zh-CN")} 笔`;$("#trade-page").textContent=rows.length?`第 ${state.tradePage+1} / ${pages} 页`:"无调仓";$("#trade-prev").disabled=state.tradePage===0;$("#trade-next").disabled=state.tradePage>=pages-1;
  $("#trade-table-body").innerHTML=page.map((r)=>{const cls=r.position_change>0?"increase":"decrease",pending=r.evaluation_status==="pending";return `<tr><td>${escapeHtml(r.signal_date)}</td><td><span class="action-tag ${cls}">${tradeActionName(r.action)}</span></td><td>${pct(r.previous_position)}</td><td>${pct(r.target_position)}</td><td class="${cls}">${pct(r.position_change)}</td><td><span class="evaluation-tag ${pending?"pending":"evaluated"}">${pending?"待验证":"已评价"}</span></td><td>${r.return_date||"—"}</td><td>${num(r.wealth_multiplier,6)}</td><td>${num(r.cumulative_wealth,4)}</td></tr>`;}).join("")||'<tr><td colspan="9" class="table-empty">当前筛选条件下没有非零仓位变化</td></tr>';
}
function renderTable(rows) {
  const page=rows.slice(-100).reverse();
  $("#period-table-body").innerHTML = page.map((r)=>`<tr><td>${escapeHtml(r.signal_date)}</td><td>${r.bankrupt?"破产":r.wealth_feasible===true?"可行":"缺失"}</td><td>${pct(r.position_value)}</td><td>${pct(r.position_change)}</td><td>${pct(r.next_return)}</td><td>${num(r.truncated_log_growth,6)}</td><td>${r.direction_success==null?"—":r.direction_success?"成功":"未成功"}</td></tr>`).join("") || '<tr><td colspan="7" class="table-empty">没有逐期结果</td></tr>';
}

function downloadCsv(rows, filename) {
  if (!rows || !rows.length) return; const keys=Object.keys(rows[0]); const quote=(v)=>`"${String(v??"").replaceAll('"','""')}"`;
  const text="\ufeff"+[keys.join(","),...rows.map((r)=>keys.map((k)=>quote(r[k])).join(","))].join("\n");
  const link=document.createElement("a"); link.href=URL.createObjectURL(new Blob([text],{type:"text/csv"})); link.download=filename; link.click(); URL.revokeObjectURL(link.href);
}
$("#download-summary").addEventListener("click",()=>downloadCsv(state.result?.summaries,"kelly_summary.csv"));
$("#download-statistics").addEventListener("click",()=>downloadCsv(state.result?.statistics,"kelly_statistics.csv"));
$("#download-signals").addEventListener("click",()=>downloadCsv(state.result?.signals,"kelly_signals.csv"));
$("#download-periods").addEventListener("click",()=>downloadCsv(state.result?.periods,"kelly_periods.csv"));
$("#download-trades").addEventListener("click",()=>downloadCsv(state.result?.trades,"kelly_trades.csv"));
