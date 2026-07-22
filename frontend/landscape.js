(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"]);
  const state = { runId: null, events: null, report: null };

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
  function selectedMode() { return document.querySelector('input[name="mode"]:checked').value; }
  function updateMode() {
    const competitor = selectedMode() === "COMPETITOR";
    $("direction-field").classList.toggle("hidden", competitor);
    $("competitor-field").classList.toggle("hidden", !competitor);
    $("technology-direction").required = !competitor;
    $("competitors").required = competitor;
  }
  function parseCompetitors() {
    return $("competitors").value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
      const values = line.split("|").map((part) => part.trim()).filter(Boolean);
      return { name: values[0], aliases: [...new Set(values.slice(1))] };
    });
  }
  function collectPayload() {
    const mode = selectedMode();
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
    try { const run = await jsonRequest("/api/landscape/runs", { method: "POST", body: JSON.stringify(collectPayload()) }); state.runId = run.run_id; showRun(run); connectEvents(run.run_id); await loadHistory(); }
    catch (error) { $("form-message").textContent = error.message; }
    finally { $("submit-button").disabled = false; }
  }
  function connectEvents(runId) {
    if (state.events) state.events.close(); state.events = new EventSource(`/api/landscape/runs/${encodeURIComponent(runId)}/events`);
    state.events.onmessage = (event) => { const payload = JSON.parse(event.data); if (payload.data) showRun(payload.data); if (payload.type === "terminal") { state.events.close(); loadReport(runId); loadHistory(); } };
    state.events.onerror = () => { if (state.events.readyState === EventSource.CLOSED) state.events = null; };
  }
  function showRun(run) {
    state.runId = run.run_id; $("run-panel").classList.remove("hidden"); $("run-title").textContent = `${run.mode === "COMPETITOR" ? "友商" : "技术方向"} · ${run.run_id.slice(0, 8)}`;
    const status = $("run-status"); status.textContent = run.status; status.className = `status ${run.status}`; $("cancel-run").classList.toggle("hidden", TERMINAL.has(run.status));
    $("step-list").innerHTML = (run.progress?.steps || []).map((step) => `<div class="step ${escapeHtml(step.status)}"><span class="step-label">${escapeHtml(step.name)}</span>${escapeHtml(step.status)}${step.attempt ? ` · ${step.attempt}` : ""}</div>`).join("");
    if (run.status === "FAILED" && run.error_message) $("form-message").textContent = run.error_message;
  }
  async function loadReport(runId) { try { state.report = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}/report`); renderReport(state.report); } catch (_) { /* report becomes available after BUILD_REPORT */ } }
  function renderReport(report) {
    const summary = report.summary || {}; const trend = summary.filing_date_trend || {}; const jurisdictions = summary.publication_jurisdictions || {};
    const maxTrend = Math.max(1, ...Object.values(trend)); const maxCountry = Math.max(1, ...Object.values(jurisdictions));
    const bars = (values, max) => Object.entries(values).map(([name, count]) => `<div class="bar-row"><span>${escapeHtml(name)}</span><div class="bar"><i style="width:${Math.round(count / max * 100)}%"></i></div><b>${count}</b></div>`).join("") || `<p class="muted">暂无数据</p>`;
    const clusters = (report.clusters || []).map((cluster) => `<div class="cluster-card"><h3>${escapeHtml(cluster.name)}</h3><p>${escapeHtml(cluster.summary)}</p><p>${escapeHtml((cluster.publication_numbers || []).join("、"))}</p></div>`).join("") || `<p class="muted">暂无聚类（可能是无成功精读文献）。</p>`;
    const patents = (report.patents || []).map((patent) => { const analysis = patent.analysis || {}; return `<details class="patent-card"><summary>${escapeHtml(patent.publication_number)} · ${escapeHtml(patent.title)}</summary><div class="patent-facts"><span>申请号：${escapeHtml(patent.application_number || "未知")}</span><span>申请日：${escapeHtml(patent.filing_date || "未知")}</span><span>公开日：${escapeHtml(patent.publication_date || "未知")}</span><span>当前权利人：${escapeHtml(patent.current_assignee || "未知")}</span><span>同族：${escapeHtml(patent.family_data_status || "UNAVAILABLE")}</span></div><p><b>现有技术：</b>${escapeHtml(analysis.prior_art || "未知")}</p><p><b>现有技术问题：</b>${escapeHtml((analysis.prior_art_problems || []).join("；") || "未知")}</p><p><b>核心发明点：</b>${escapeHtml((analysis.core_invention_points || []).join("；") || "未知")}</p><p><b>解决的技术问题：</b>${escapeHtml((analysis.technical_problems_solved || []).join("；") || "未知")}</p><p><b>有益效果：</b>${escapeHtml((analysis.beneficial_effects || []).join("；") || "未知")}</p></details>`; }).join("") || `<p class="muted">暂无成功精读。</p>`;
    const limitations = [...(report.limitations || []), ...Object.entries(report.failures || {}).map(([publication_number, message]) => ({ code: publication_number, message }))].map((item) => `<div class="limitation"><b>${escapeHtml(item.code || "LIMITATION")}</b>：${escapeHtml(item.message || "")}</div>`).join("") || `<p class="muted">无</p>`;
    $("report-view").classList.remove("hidden"); $("report-view").innerHTML = `<div class="metric-grid"><div class="metric"><b>${summary.candidate_count || 0}</b><span>唯一候选</span></div><div class="metric"><b>${summary.analyzed_count || 0}</b><span>成功精读</span></div><div class="metric"><b>${summary.cluster_count || 0}</b><span>技术聚类</span></div><div class="metric"><b>${summary.failed_analysis_count || 0}</b><span>精读失败</span></div></div><div class="report-grid"><section class="report-section"><h3>申请日趋势</h3>${bars(trend, maxTrend)}</section><section class="report-section"><h3>公开法域布局</h3>${bars(jurisdictions, maxCountry)}</section></div><div class="report-section"><h3>技术聚类</h3>${clusters}</div><div class="report-section"><h3>逐件精读</h3>${patents}</div><div class="report-section"><h3>限制与失败</h3>${limitations}</div><div class="report-links"><a href="/api/landscape/runs/${encodeURIComponent(report.run_id)}/report.md">下载 Markdown</a><a href="/api/landscape/runs/${encodeURIComponent(report.run_id)}/patents.csv">下载 CSV</a></div>`;
  }
  async function loadHistory() { try { const body = await jsonRequest("/api/landscape/runs"); $("history-list").innerHTML = body.runs.length ? body.runs.map((run) => `<button class="history-item ${run.run_id === state.runId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}"><strong>${escapeHtml(run.scope?.technology_direction || (run.scope?.competitors || []).map((item) => item.name).join("、") || "专利态势分析")}</strong><span class="history-meta"><span>${escapeHtml(run.status)}</span><span>${escapeHtml((run.created_at ? new Date(run.created_at).toLocaleString("zh-CN") : ""))}</span></span></button>`).join("") : `<p class="muted">还没有分析任务。</p>`; document.querySelectorAll(".history-item").forEach((item) => item.addEventListener("click", () => openRun(item.dataset.runId))); } catch (error) { $("history-list").innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`; } }
  async function openRun(runId) { try { const run = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}`); showRun(run); if (TERMINAL.has(run.status)) loadReport(runId); else connectEvents(runId); await loadHistory(); } catch (error) { $("form-message").textContent = error.message; } }
  async function cancelRun() { if (!state.runId) return; try { const run = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(state.runId)}/cancel`, { method: "POST", body: "{}" }); showRun(run); } catch (error) { $("form-message").textContent = error.message; } }

  $("landscape-form").addEventListener("submit", submit); $("refresh-history").addEventListener("click", loadHistory); $("cancel-run").addEventListener("click", cancelRun); $("period-preset").addEventListener("change", presetDates); document.querySelectorAll('input[name="mode"]').forEach((input) => input.addEventListener("change", updateMode));
  presetDates(); updateMode(); loadHistory();
})();
