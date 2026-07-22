(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"]);
  const state = { runId: null, events: null, report: null, debugLoadedAt: 0 };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }
  function today() { return new Date().toISOString().slice(0, 10); }
  function shiftMonths(source, months) { const value = new Date(`${source}T00:00:00`); value.setMonth(value.getMonth() - months); return value.toISOString().slice(0, 10); }
  function presetDates() {
    const end = today(); const preset = $("period-preset").value;
    const months = { ONE_MONTH: 1, QUARTER: 3, SIX_MONTHS: 6, TWELVE_MONTHS: 12 }[preset];
    if (months) { $("publication-start").value = shiftMonths(end, months); $("publication-end").value = end; }
  }
  function derivedMode() {
    const hasDirection = Boolean($("technology-direction").value.trim());
    const hasCompetitor = parseCompetitors().length > 0;
    if (hasDirection && hasCompetitor) return "TECHNOLOGY_COMPETITOR";
    if (hasDirection) return "TECHNOLOGY";
    if (hasCompetitor) return "COMPETITOR";
    return null;
  }
  function updateMode() {
    const mode = derivedMode();
    const labels = {
      TECHNOLOGY: "技术方向",
      COMPETITOR: "重点友商",
      TECHNOLOGY_COMPETITOR: "技术方向 + 重点友商",
    };
    $("derived-mode").textContent = mode ? `当前分析模式：${labels[mode]}（系统自动判定）` : "当前分析模式：等待输入";
  }
  function parseCompetitors() {
    return $("competitors").value.split("\n").map((line) => line.trim()).filter(Boolean).map((name) => ({ name, aliases: [] }));
  }
  function collectPayload() {
    const mode = derivedMode();
    return {
      api_key: $("api-key").value,
      base_url: $("base-url").value.trim(),
      model: $("model").value.trim(),
      scope: {
        mode,
        technology_direction: $("technology-direction").value.trim() || null,
        competitors: parseCompetitors(),
        period_preset: $("period-preset").value,
        publication_start: $("publication-start").value,
        publication_end: $("publication-end").value,
        budget: { candidate_limit: Number($("candidate-limit").value), analysis_limit: Number($("analysis-limit").value), per_query_limit: 50 },
      },
    };
  }
  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.error || `请求失败（${response.status}）`);
    return body;
  }
  async function submit(event) {
    event.preventDefault(); $("form-message").textContent = ""; $("submit-button").disabled = true;
    if (!derivedMode()) { $("form-message").textContent = "请至少填写具体技术方向或重点友商。"; $("submit-button").disabled = false; return; }
    try { const run = await jsonRequest("/api/landscape/runs", { method: "POST", body: JSON.stringify(collectPayload()) }); state.runId = run.run_id; showRun(run); connectEvents(run.run_id); await loadHistory(); }
    catch (error) { $("form-message").textContent = error.message; }
    finally { $("submit-button").disabled = false; }
  }
  function connectEvents(runId) {
    if (state.events) state.events.close(); state.events = new EventSource(`/api/landscape/runs/${encodeURIComponent(runId)}/events`);
    state.events.onmessage = (event) => { const payload = JSON.parse(event.data); if (payload.data) showRun(payload.data); if (payload.type === "terminal") { state.events.close(); loadReport(runId); loadDebug(runId, true); loadHistory(); } };
    state.events.onerror = () => { if (state.events.readyState === EventSource.CLOSED) state.events = null; };
  }
  function showRun(run) {
    state.runId = run.run_id; $("run-panel").classList.remove("hidden"); const modeLabel = { TECHNOLOGY: "技术方向", COMPETITOR: "重点友商", TECHNOLOGY_COMPETITOR: "技术方向 + 重点友商" }[run.mode] || "专利态势分析"; $("run-title").textContent = `${modeLabel} · ${run.run_id.slice(0, 8)}`;
    const status = $("run-status"); status.textContent = run.status; status.className = `status ${run.status}`; $("cancel-run").classList.toggle("hidden", TERMINAL.has(run.status));
    $("step-list").innerHTML = (run.progress?.steps || []).map((step) => `<div class="step ${escapeHtml(step.status)}"><span class="step-label">${escapeHtml(step.name)}</span>${escapeHtml(step.status)}${step.attempt ? ` · ${step.attempt}` : ""}</div>`).join("");
    if (run.status === "FAILED" && run.error_message) $("form-message").textContent = run.error_message;
    loadDebug(run.run_id);
  }
  async function loadDebug(runId, force = false) {
    if (!force && state.runId === runId && Date.now() - state.debugLoadedAt < 1500) return;
    state.debugLoadedAt = Date.now();
    try {
      const debug = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}/debug`);
      const steps = (debug.steps || []).map((step) => `<div class="debug-row"><b>${escapeHtml(step.step_name)}</b><span>${escapeHtml(step.status)} · 尝试 ${step.attempt} · ${step.duration_ms == null ? "进行中" : `${step.duration_ms} ms`}</span>${step.error_message ? `<em>${escapeHtml(step.error_code || "ERROR")}：${escapeHtml(step.error_message)}</em>` : ""}</div>`).join("") || `<p class="muted">暂无步骤记录。</p>`;
      const queries = (debug.queries || []).map((query) => `<li><code>${escapeHtml(query.query_text)}</code><span>${escapeHtml(query.rationale)}</span></li>`).join("") || `<li class="muted">暂无检索式。</li>`;
      const aliases = (debug.competitor_aliases || []).map((item) => `<div class="alias-row"><b>${escapeHtml(item.primary_name)}</b><span>${item.aliases?.length ? escapeHtml(item.aliases.join("、")) : "仅主名称"}</span><small>${escapeHtml(item.source)}</small></div>`).join("") || `<p class="muted">本次未填写友商。</p>`;
      const provider = Object.entries(debug.provider_statuses || {}).map(([name, status]) => `<span class="debug-chip">${escapeHtml(name)}：${escapeHtml(status)}</span>`).join("") || `<span class="muted">暂无 Provider 结果。</span>`;
      const coverage = debug.coverage || {}; const excluded = Object.entries(coverage.excluded_counts || {}).map(([name, count]) => `${escapeHtml(name)} ${count}`).join("；") || "无";
      $("debug-content").innerHTML = `<div class="debug-grid"><section><h4>步骤</h4>${steps}</section><section><h4>检索式</h4><ul class="debug-queries">${queries}</ul></section><section><h4>友商别名</h4>${aliases}${debug.alias_resolution_error ? `<p class="debug-warning">别名解析失败，已回退主名称：${escapeHtml(debug.alias_resolution_error)}</p>` : ""}</section><section><h4>Provider / 覆盖</h4><div class="debug-chips">${provider}</div><p class="debug-summary">原始命中 ${coverage.raw_hit_count || 0} · 合格 ${coverage.eligible_hit_count || 0} · 唯一候选 ${coverage.unique_candidate_count || 0} · 入选 ${coverage.selected_count || 0} · 截断 ${coverage.truncated_count || 0}</p><p class="debug-summary">排除：${excluded}</p></section></div>`;
    } catch (error) { $("debug-content").textContent = `调试信息暂不可用：${error.message}`; }
  }
  async function loadReport(runId) { try { state.report = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}/report`); renderReport(state.report); } catch (_) { /* report becomes available after BUILD_REPORT */ } }
  function renderReport(report) {
    const summary = report.summary || {}; const trend = summary.filing_date_trend || {}; const jurisdictions = summary.publication_jurisdictions || {};
    const maxTrend = Math.max(1, ...Object.values(trend)); const maxCountry = Math.max(1, ...Object.values(jurisdictions));
    const bars = (values, max) => Object.entries(values).map(([name, count]) => `<div class="bar-row"><span>${escapeHtml(name)}</span><div class="bar"><i style="width:${Math.round(count / max * 100)}%"></i></div><b>${count}</b></div>`).join("") || `<p class="muted">暂无数据</p>`;
    const aliases = (report.searched_competitor_aliases || []).map((item) => `<div class="alias-row"><b>${escapeHtml(item.primary_name)}</b><span>${item.aliases?.length ? escapeHtml(item.aliases.join("、")) : "仅主名称"}</span><small>${escapeHtml(item.source)}</small></div>`).join("") || `<p class="muted">本次未填写友商。</p>`;
    const clusters = (report.clusters || []).map((cluster) => `<div class="cluster-card"><h3>${escapeHtml(cluster.name)}</h3><p>${escapeHtml(cluster.summary)}</p><p>${escapeHtml((cluster.publication_numbers || []).join("、"))}</p></div>`).join("") || `<p class="muted">暂无聚类（可能是无成功精读文献）。</p>`;
    const patents = (report.patents || []).map((patent) => { const analysis = patent.analysis || {}; return `<details class="patent-card"><summary>${escapeHtml(patent.publication_number)} · ${escapeHtml(patent.title)}</summary><div class="patent-facts"><span>申请号：${escapeHtml(patent.application_number || "未知")}</span><span>申请日：${escapeHtml(patent.filing_date || "未知")}</span><span>公开日：${escapeHtml(patent.publication_date || "未知")}</span><span>当前权利人：${escapeHtml(patent.current_assignee || "未知")}</span><span>同族：${escapeHtml(patent.family_data_status || "UNAVAILABLE")}</span></div><p><b>现有技术：</b>${escapeHtml(analysis.prior_art || "未知")}</p><p><b>现有技术问题：</b>${escapeHtml((analysis.prior_art_problems || []).join("；") || "未知")}</p><p><b>核心发明点：</b>${escapeHtml((analysis.core_invention_points || []).join("；") || "未知")}</p><p><b>解决的技术问题：</b>${escapeHtml((analysis.technical_problems_solved || []).join("；") || "未知")}</p><p><b>有益效果：</b>${escapeHtml((analysis.beneficial_effects || []).join("；") || "未知")}</p></details>`; }).join("") || `<p class="muted">暂无成功精读。</p>`;
    const limitations = [...(report.limitations || []), ...Object.entries(report.failures || {}).map(([publication_number, message]) => ({ code: publication_number, message }))].map((item) => `<div class="limitation"><b>${escapeHtml(item.code || "LIMITATION")}</b>：${escapeHtml(item.message || "")}</div>`).join("") || `<p class="muted">无</p>`;
    $("report-view").classList.remove("hidden"); $("report-view").innerHTML = `<div class="metric-grid"><div class="metric"><b>${summary.candidate_count || 0}</b><span>唯一候选</span></div><div class="metric"><b>${summary.analyzed_count || 0}</b><span>成功精读</span></div><div class="metric"><b>${summary.cluster_count || 0}</b><span>技术聚类</span></div><div class="metric"><b>${summary.failed_analysis_count || 0}</b><span>精读失败</span></div></div><section class="report-section"><h3>本次检索到的友商别名</h3>${aliases}</section><div class="report-grid"><section class="report-section"><h3>申请日趋势</h3>${bars(trend, maxTrend)}</section><section class="report-section"><h3>公开法域布局</h3>${bars(jurisdictions, maxCountry)}</section></div><div class="report-section"><h3>技术聚类</h3>${clusters}</div><div class="report-section"><h3>逐件精读</h3>${patents}</div><div class="report-section"><h3>限制与失败</h3>${limitations}</div><div class="report-links"><a href="/api/landscape/runs/${encodeURIComponent(report.run_id)}/report.md">下载 Markdown</a><a href="/api/landscape/runs/${encodeURIComponent(report.run_id)}/patents.csv">下载 CSV</a></div>`;
  }
  async function loadHistory() { try { const body = await jsonRequest("/api/landscape/runs"); $("history-list").innerHTML = body.runs.length ? body.runs.map((run) => `<button class="history-item ${run.run_id === state.runId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}"><strong>${escapeHtml(run.scope?.technology_direction || (run.scope?.competitors || []).map((item) => item.name).join("、") || "专利态势分析")}</strong><span class="history-meta"><span>${escapeHtml(run.status)}</span><span>${escapeHtml((run.created_at ? new Date(run.created_at).toLocaleString("zh-CN") : ""))}</span></span></button>`).join("") : `<p class="muted">还没有分析任务。</p>`; document.querySelectorAll(".history-item").forEach((item) => item.addEventListener("click", () => openRun(item.dataset.runId))); } catch (error) { $("history-list").innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`; } }
  async function openRun(runId) { try { const run = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}`); showRun(run); loadDebug(runId, true); if (TERMINAL.has(run.status)) loadReport(runId); else connectEvents(runId); await loadHistory(); } catch (error) { $("form-message").textContent = error.message; } }
  async function cancelRun() { if (!state.runId) return; try { const run = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(state.runId)}/cancel`, { method: "POST", body: "{}" }); showRun(run); } catch (error) { $("form-message").textContent = error.message; } }

  $("landscape-form").addEventListener("submit", submit); $("refresh-history").addEventListener("click", loadHistory); $("cancel-run").addEventListener("click", cancelRun); $("period-preset").addEventListener("change", presetDates); $("technology-direction").addEventListener("input", updateMode); $("competitors").addEventListener("input", updateMode);
  presetDates(); updateMode(); loadHistory();
})();
