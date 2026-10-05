const $ = (s) => document.querySelector(s);
let activeJobId = "";
let pollTimer = null;
let collectionCache = [];
let collectionsInitialized = false;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function jsonFetch(url, options={}) {
  const response = await fetch(url, options); const value = await response.json();
  if (!response.ok) throw new Error(value.error || "请求失败 (" + response.status + ")");
  return value;
}
const post = (url, body) => jsonFetch(url, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});

function renderCollections(data) {
  const priorFetch = new Set([...document.querySelectorAll("#fetch-collections input:checked")].map((item) => item.value));
  const priorInventory = $("#inventory-collection").value;
  const priorInventoryExchange = $("#inventory-exchange").value;
  const priorFetchExchange = $("#fetch-exchange").value;
  collectionCache = data.collections || [];
  $("#database-path").textContent = "SQLite: " + data.database_path;
  $("#collection-cards").innerHTML = collectionCache.length ? collectionCache.map((c) =>
    '<article class="collection-card"><header><h3>' + esc(c.name) + '</h3><strong>' + Number(c.member_count).toLocaleString("zh-CN") + '</strong></header><p>清单 ID ' + esc(c.id) + ' · 快照 ' + esc(c.imported_at || "尚无") + '<br>' + esc(c.methodology || "") + '</p></article>'
  ).join("") : "<p>尚未导入代码清单。</p>";
  $("#fetch-collections").innerHTML = collectionCache.map((c) =>
    '<label><input type="checkbox" value="' + esc(c.id) + '" ' + (!collectionsInitialized || priorFetch.has(c.id) ? "checked" : "") + '> ' + esc(c.name) + ' (' + c.member_count + ')</label>').join("");
  $("#inventory-collection").innerHTML = '<option value="">全部清单</option>' + collectionCache.map((c) =>
    '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>').join("");
  $("#inventory-collection").value = priorInventory;
  const exchangeOptions = (data.exchanges || []).map((exchange) => '<option value="' + esc(exchange) + '">' + esc(exchange) + '</option>').join("");
  $("#inventory-exchange").innerHTML = '<option value="">全部交易所</option>' + exchangeOptions;
  $("#inventory-exchange").value = priorInventoryExchange;
  $("#fetch-exchange").innerHTML = '<option value="">全部交易所</option>' + exchangeOptions;
  $("#fetch-exchange").value = priorFetchExchange;
  collectionsInitialized = true;
}

function renderInventory(data) {
  const rows = data.instruments || [];
  $("#inventory-count").textContent = rows.length.toLocaleString("zh-CN") + " 个匹配代码";
  $("#inventory-body").innerHTML = rows.slice(0,200).map((r) => {
    const statuses = data.price_status[r.symbol] || {};
    const coverage = ["daily","weekly","monthly"].map((f) => {
      const row = statuses[f];
      return row ? '<span class="coverage-count">' + Number(row.row_count).toLocaleString("zh-CN") + ' bars</span><small>' + esc(row.first_date) + ' → ' + esc(row.last_date) + '</small>' : '<span>—</span>';
    });
    const tag = String(r.validation_status || "pending");
    return '<tr><td><b>' + esc(r.symbol) + '</b></td><td>' + esc(r.name) + '</td><td>' + esc(r.exchange) + '</td><td>' + esc(r.collection_names) + '</td><td><span class="status-tag ' + esc(tag) + '">' + esc(tag) + '</span></td><td>' + coverage[0] + '</td><td>' + coverage[1] + '</td><td>' + coverage[2] + '</td></tr>';
  }).join("") || '<tr><td colspan="8">没有匹配代码。先导入清单，或调整筛选条件。</td></tr>';
}

async function refreshCatalog() {
  const collection = $("#inventory-collection").value;
  const exchange = $("#inventory-exchange").value;
  const query = $("#inventory-query").value.trim();
  const params = new URLSearchParams(); if (collection) params.set("collection", collection); if (exchange) params.set("exchange", exchange); if (query) params.set("q", query);
  const data = await jsonFetch("/api/market-data/catalog?" + params);
  renderCollections(data); renderInventory(data);
}

async function refreshJobs() {
  const result = await jsonFetch("/api/market-data/jobs"); const jobs = result.jobs;
  $("#job-list").innerHTML = jobs.length ? jobs.map((j) =>
    '<article class="job-row"><code class="job-id">' + esc(j.id) + '</code><span class="job-status">' + esc(j.status) + '</span><span class="job-progress">' + j.completed_items + '/' + j.total_items + ' 完成 · ' + j.failed_items + ' 失败/跳过</span><div class="job-buttons"><button data-job="' + esc(j.id) + '" class="job-open">详情</button><button data-job="' + esc(j.id) + '" class="job-resume" ' + (j.status === "completed" ? "disabled" : "") + '>恢复 / 重试失败项</button></div><p class="job-message">' + esc(j.message) + '</p></article>'
  ).join("") : "<p>还没有任务。</p>";
  document.querySelectorAll(".job-open").forEach((button) => button.addEventListener("click", () => showJob(button.dataset.job)));
  document.querySelectorAll(".job-resume").forEach((button) => button.addEventListener("click", async () => {
    try {
      activeJobId = button.dataset.job;
      await post("/api/market-data/jobs/" + activeJobId + "/resume", {});
      await refreshJobs();
      if (pollTimer) clearInterval(pollTimer);
      let pollCount = 0;
      pollTimer = setInterval(() => { refreshJobs().catch(() => {}); if (++pollCount % 12 === 0) refreshQuota().catch(() => {}); }, 2500);
    } catch (error) { $("#import-status").textContent = "续跑失败：" + error.message; }
  }));
  const current = jobs.find((j) => j.id === activeJobId);
  $("#pause-fetch").disabled = !current || !["queued","validating","running"].includes(current.status);
  if (current && !["queued","validating","running","pause_requested"].includes(current.status)) {
    activeJobId = "";
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
}

async function showJob(id) {
  const data = await jsonFetch("/api/market-data/jobs/" + encodeURIComponent(id));
  const items = data.items || [];
  const rows = items.length ? items.slice(0,100).map((item) => '<tr><td>' + esc(item.symbol) + '</td><td>' + esc(item.frequency) + '</td><td>' + esc(item.status) + '</td><td>' + esc(item.first_date || "—") + '</td><td>' + esc(item.last_date || "—") + '</td><td>' + Number(item.row_count || 0).toLocaleString("zh-CN") + '</td><td>' + esc(item.error) + '</td><td>' + item.attempts + '</td></tr>').join("") : '<tr><td colspan="8">任务没有条目</td></tr>';
  const remaining = Math.max(0, items.length - 100);
  $("#job-detail").innerHTML = '<h3>任务 ' + esc(id) + ' · ' + esc(data.job.status) + ' · ' + data.job.completed_items + '/' + data.job.total_items + '</h3><p>' + esc(data.job.message) + ' · 首末日期按供应商本次响应逐代码/频率记录。' + (remaining ? ' 当前先显示100条，另有 ' + remaining + ' 条。' : '') + '</p><div class="table-scroll"><table><thead><tr><th>代码</th><th>频率</th><th>状态</th><th>最早日期</th><th>最新日期</th><th>行数</th><th>错误</th><th>尝试</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

async function refreshQuota() {
  const state = $("#quota-state"); state.textContent = "查询额度中…"; state.className = "quota-chip";
  try {
    const data = await jsonFetch("/api/market-data/quota"); const usage = data.usage;
    state.textContent = "API calls " + (usage.apiRequests ?? "?") + " / " + (usage.dailyRateLimit ?? "?") + " · 已购余量 " + (usage.extraLimit ?? "?") + "（任务不会自动消耗已购余量）";
    state.className = "quota-chip ready";
  } catch (error) { state.textContent = error.message; state.className = "quota-chip error"; }
}

async function refreshAll() {
  await refreshCatalog(); await refreshJobs();
  const status = await jsonFetch("/api/eodhd/status");
  $("#token-state").textContent = status.configured ? "EODHD_API_TOKEN 已配置" : "未配置 EODHD_API_TOKEN";
  $("#token-state").className = "state-pill " + (status.configured ? "ready" : "error");
}

$("#import-universes").addEventListener("click", async () => {
  const label = $("#import-status"), button = $("#import-universes"); button.disabled = true;
  label.textContent = "正在读取用户 HTML 指定的数据源并保存新快照…";
  try {
    const result = await post("/api/market-data/import", {});
    label.textContent = result.collections.map((c) => c.name + " " + c.member_count + " 只").join(" · ");
    await refreshAll();
  } catch (error) { label.textContent = "导入失败：" + error.message; }
  finally { button.disabled = false; }
});

$("#start-fetch").addEventListener("click", async () => {
  const collections = [...document.querySelectorAll("#fetch-collections input:checked")].map((x) => x.value);
  const frequencies = [...document.querySelectorAll(".frequency-list input:checked")].map((x) => x.value);
  if (!collections.length || !frequencies.length) { $("#import-status").textContent = "至少选择一个清单和一个频率。"; return; }
  try {
    const data = await post("/api/market-data/jobs", {collections,frequencies,exchange:$("#fetch-exchange").value,batch_size:Number($("#batch-size").value || 30)});
    activeJobId = data.job_id; $("#import-status").textContent = "任务 " + data.job_id + " 已启动。";
    await refreshJobs();
    if (pollTimer) clearInterval(pollTimer);
    let pollCount = 0;
    pollTimer = setInterval(() => { refreshJobs().catch(() => {}); if (++pollCount % 12 === 0) refreshQuota().catch(() => {}); }, 2500);
  } catch (error) { $("#import-status").textContent = "无法启动任务：" + error.message; }
});
$("#pause-fetch").addEventListener("click", async () => {
  if (!activeJobId) return;
  try { await post("/api/market-data/jobs/" + activeJobId + "/pause", {}); await refreshJobs(); }
  catch (error) { $("#import-status").textContent = error.message; }
});
$("#check-quota").addEventListener("click", () => refreshQuota());
$("#refresh-all").addEventListener("click", () => refreshAll().catch((e) => { $("#import-status").textContent = e.message; }));
$("#refresh-jobs").addEventListener("click", () => refreshJobs().catch((e) => { $("#import-status").textContent = e.message; }));
$("#inventory-collection").addEventListener("change", () => refreshCatalog().catch(() => {}));
$("#inventory-exchange").addEventListener("change", () => refreshCatalog().catch(() => {}));
$("#search-inventory").addEventListener("click", () => refreshCatalog().catch((e) => { $("#inventory-count").textContent = e.message; }));
$("#export-inventory").addEventListener("click", () => {
  const params = new URLSearchParams();
  const collection = $("#inventory-collection").value, exchange = $("#inventory-exchange").value, query = $("#inventory-query").value.trim();
  if (collection) params.set("collection", collection);
  if (exchange) params.set("exchange", exchange);
  if (query) params.set("q", query);
  const link = document.createElement("a"); link.href = "/api/market-data/export.csv?" + params; link.download = "market-instruments.csv"; link.click();
});
$("#inventory-query").addEventListener("keydown", (e) => { if (e.key === "Enter") refreshCatalog().catch(() => {}); });
refreshAll().then(refreshQuota).catch((error) => { $("#import-status").textContent = error.message; });
