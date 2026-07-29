(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"]);
  const state = { runId: null, events: null, report: null, debugLoadedAt: 0 };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }
  function today() { return new Date().toISOString().slice(0, 10); }
  function shiftMonths(source, months) { const value = new Date(`${source}T00:00:00Z`); const monthIndex = value.getUTCFullYear() * 12 + value.getUTCMonth() - months; const year = Math.floor(monthIndex / 12); const month = ((monthIndex % 12) + 12) % 12; const day = Math.min(value.getUTCDate(), new Date(Date.UTC(year, month + 1, 0)).getUTCDate()); return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10); }
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
      COMPETITOR: "重点友商（检索公司）",
      TECHNOLOGY_COMPETITOR: "技术方向 + 重点友商（检索公司）",
    };
    $("derived-mode").textContent = mode ? `当前分析模式：${labels[mode]}（系统自动判定）` : "当前分析模式：等待输入";
  }
  function parseCompetitors(strict = false) {
    return $("competitors").value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
      const fields = line.split("|").map((value) => value.trim());
      const name = fields[0]; const rawScope = (fields[1] || "GROUP").toUpperCase();
      if (!name || fields.length > 2 || !["ENTITY", "GROUP"].includes(rawScope)) {
        if (strict) throw new Error("重点友商每行应为“公司名称 | GROUP”或“公司名称 | ENTITY”。");
        return { name: name || line, aliases: [], assignee_scope: "GROUP" };
      }
      return { name, aliases: [], assignee_scope: rawScope };
    });
  }
  function collectPayload() {
    const mode = derivedMode();
    const baseUrl = $("base-url").value.trim();
    if ((baseUrl.match(/:\/\//g) || []).length !== 1) {
      throw new Error("模型 Base URL 必须是一条完整地址；请不要重复粘贴 URL。");
    }
    return {
      api_key: $("api-key").value,
      base_url: baseUrl,
      model: $("model").value.trim(),
      scope: {
        mode,
        technology_direction: $("technology-direction").value.trim() || null,
        competitors: parseCompetitors(true),
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
    if (!derivedMode()) { $("form-message").textContent = "请至少填写具体技术方向或重点友商（检索公司）。"; $("submit-button").disabled = false; return; }
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
    state.runId = run.run_id; $("run-panel").classList.remove("hidden"); const modeLabel = { TECHNOLOGY: "技术方向", COMPETITOR: "重点友商（检索公司）", TECHNOLOGY_COMPETITOR: "技术方向 + 重点友商（检索公司）" }[run.mode] || "专利态势分析"; $("run-title").textContent = `${modeLabel} · ${run.run_id.slice(0, 8)}`;
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
      const aliases = (debug.competitor_aliases || []).map((item) => `<div class="alias-row"><b>${escapeHtml(item.primary_name)}</b><span>已用于检索：${item.searched_aliases?.length ? escapeHtml(item.searched_aliases.join("、")) : "仅主名称"}${item.unsearched_aliases?.length ? `<br><em>未纳入查询：${escapeHtml(item.unsearched_aliases.join("、"))}</em>` : ""}</span><small>${escapeHtml(item.source)} · ${item.assignee_scope === "GROUP" ? "集团名称文本口径" : "实体精确口径"}</small></div>`).join("") || `<p class="muted">本次未填写友商。</p>`;
      const expansion = debug.technical_direction_expansion; const direction = expansion ? `<div class="direction-expansion"><p><b>原始：</b>${escapeHtml(expansion.original_term)}</p><p><b>中文：</b>${escapeHtml((expansion.chinese_terms || []).join("、"))}</p><p><b>英文：</b>${escapeHtml((expansion.english_terms || []).join("、"))}</p></div>` : `<p class="muted">本次未填写技术方向。</p>`;
      const provider = Object.entries(debug.provider_statuses || {}).map(([name, status]) => `<span class="debug-chip">${escapeHtml(name)}：${escapeHtml(status)}</span>`).join("") || `<span class="muted">暂无 Provider 结果。</span>`;
      const attempts = (debug.provider_attempts || []).map((item) => `<div class="debug-row"><b>${escapeHtml(item.request_id)} · ${escapeHtml(item.provider)}</b><span>${escapeHtml(item.status)} · ${item.duration_ms || 0} ms · ${item.hit_count || 0} 条</span>${item.error_code ? `<em>${escapeHtml(item.error_code)}：${escapeHtml(item.error_message || "")}</em>` : ""}</div>`).join("") || `<p class="muted">暂无 Provider 调用。</p>`;
      const coverage = debug.coverage || {}; const excluded = Object.entries(coverage.excluded_counts || {}).map(([name, count]) => `${escapeHtml(name)} ${count}`).join("；") || "无";
      const enrichment = debug.enrichment || {};
      const selection = debug.analysis_selection || {};
      const ranking = (debug.candidate_ranking || []).slice(0, 30).map((item) => `<div class="debug-row"><b>${escapeHtml(item.publication_number)} · ${escapeHtml(item.company)}</b><span>综合分 ${Number(item.score || 0).toFixed(3)} · 同族布局 ${Number(item.family_score || 0).toFixed(2)} · 固定排名 ${Number(item.rank_quality || 0).toFixed(2)} · 同族法域 ${Number(item.family_footprint || 0)}</span><em>${escapeHtml((item.reasons || []).join("；"))}</em></div>`).join("") || `<p class="muted">暂无候选排序。</p>`;
      $("debug-content").innerHTML = `<div class="debug-grid"><section><h4>步骤</h4>${steps}</section><section><h4>中英文技术词</h4>${direction}</section><section><h4>检索式</h4><ul class="debug-queries">${queries}</ul></section><section><h4>友商别名</h4>${aliases}${debug.alias_resolution_error ? `<p class="debug-warning">别名解析失败，已回退主名称：${escapeHtml(debug.alias_resolution_error)}</p>` : ""}</section><section><h4>Provider 明细</h4><div class="debug-chips">${provider}</div>${attempts}</section><section><h4>过滤 / 身份识别</h4><p class="debug-summary">原始命中 ${coverage.raw_hit_count || 0} · 合格 ${coverage.eligible_hit_count || 0} · 唯一公开文本 ${coverage.unique_publication_count || 0} · 身份聚合组 ${coverage.unique_family_count || coverage.unique_candidate_count || 0} · 截断 ${coverage.truncated_count || 0}</p><p class="debug-summary">确认 Family ${coverage.confirmed_family_count || 0} · 申请号聚合 ${coverage.application_group_count || 0} · 公开号保守项 ${coverage.publication_fallback_count || 0} · 身份冲突 ${coverage.identity_conflict_count || 0}</p><p class="debug-summary">排除：${excluded}</p><p class="debug-summary">缺日期 ${enrichment.missing_hit_count || 0} · 检索结果缺同族身份 ${enrichment.missing_family_identity_count || 0} · 日期详情补全 ${enrichment.attempted_count || 0} · 复用 ${enrichment.reused_hit_count || 0}</p></section><section><h4>精读选样</h4><p class="debug-summary">目标 ${selection.target_count || 0} · 公司 ${selection.company_count || 0} · 公司覆盖 ${selection.company_coverage_complete === false ? "受精读上限限制" : "完整"} · 补位 ${selection.backfilled_count || 0}</p><p class="debug-summary">首选：${escapeHtml((selection.selected_publications || []).join("、") || "暂无")}</p><p class="debug-summary">实际尝试：${escapeHtml((selection.attempted_publications || []).join("、") || "暂无")}</p></section><section><h4>专利族排序（前 30）</h4>${ranking}</section></div>`;
    } catch (error) { $("debug-content").textContent = `调试信息暂不可用：${error.message}`; }
  }
  async function loadReport(runId) { try { state.report = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}/report`); renderReport(state.report); } catch (_) { /* report becomes available after BUILD_REPORT */ } }
  function renderReport(report) {
    const summary = report.summary || {};
    const companyCounts = summary.company_patent_counts || [];
    const maxCompany = Math.max(1, ...companyCounts.map((item) => Number(item.patent_count || 0)));
    const companyBars = companyCounts.map((item) => `<div class="bar-row"><span title="${escapeHtml(item.company)}">${escapeHtml(item.company)}</span><div class="bar"><i style="width:${Math.round(Number(item.patent_count || 0) / maxCompany * 100)}%"></i></div><b>${Number(item.patent_count || 0)}</b></div>`).join("") || `<p class="muted">暂无权利人数据。</p>`;
    const jurisdictions = Object.entries(summary.publication_jurisdictions || {}).map(([name, count]) => `<span class="jurisdiction-chip">${escapeHtml(name)} ${Number(count)}</span>`).join("") || `<span class="muted">暂无可用公开号法域数据。</span>`;
    const aliases = (report.searched_competitor_aliases || []).map((item) => `<div class="alias-row"><b>${escapeHtml(item.primary_name)}</b><span>${item.searched_aliases?.length ? escapeHtml(item.searched_aliases.join("、")) : "仅主名称"}</span><small>${escapeHtml(item.source)} · ${item.assignee_scope === "GROUP" ? "集团名称文本口径" : "实体精确口径"}</small></div>`).join("") || `<p class="muted">本次未填写友商。</p>`;
    const companyProfiles = (report.company_profiles || []).map((profile) => `<div class="cluster-card"><h3>${escapeHtml(profile.company_id)}</h3><p>${escapeHtml(profile.overall_summary)}</p><p class="muted">技术方向：${escapeHtml((profile.technology_directions || []).join("、") || "暂无")}</p>${(profile.technology_categories || []).map((category) => `<div class="cluster-member"><b>${escapeHtml(category.name)}</b><span>${escapeHtml(category.summary)}</span><span>${escapeHtml((category.publication_numbers || []).join("、"))}</span></div>`).join("")}</div>`).join("") || `<p class="muted">暂无公司技术画像。</p>`;
    const trendAnalysis = report.cross_company_analysis;
    const companyTrends = trendAnalysis ? `<p>${escapeHtml(trendAnalysis.overall_summary)}</p>${(trendAnalysis.trends || []).map((trend) => `<div class="cluster-member"><b>${escapeHtml(trend.trend_id)} · ${escapeHtml(trend.name)}</b><span>${escapeHtml(trend.direction)}</span><span>${escapeHtml(trend.summary)}</span></div>`).join("")}` : `<p class="muted">暂无跨公司趋势。</p>`;
    const coverageAudit = report.company_trend_coverage;
    const auditHistory = (report.company_trend_coverage_history || []).map((item, index) => `round ${Number(item.repair_round ?? index)} ${escapeHtml(item.decision || "UNKNOWN")}`).join(" → ");
    const coverageAuditView = coverageAudit ? `<p><b>${escapeHtml(coverageAudit.decision || "UNKNOWN")}</b> · 覆盖率 ${Math.round(Number(coverageAudit.coverage_ratio || 0) * 10000) / 100}% · 修复轮次 ${Number(coverageAudit.repair_round || 0)}</p>${auditHistory ? `<p class="muted">审计轨迹：${auditHistory}</p>` : ""}${(coverageAudit.limitations || []).map((message) => `<p class="muted">${escapeHtml(message)}</p>`).join("")}` : `<p class="muted">历史报告未记录公司趋势覆盖审计。</p>`;
    const legacyClusters = report.schema_version !== "landscape-report/2.0.0" && (report.clusters || []).map((cluster) => {
      const members = (cluster.members || []).map((member) => `<div class="cluster-member"><b>${escapeHtml(member.publication_number)}</b><span>${escapeHtml(member.competitor || member.current_assignee || "未知权利人")}</span><span>申请日 ${escapeHtml(member.filing_date || "未知")}</span></div>`).join("");
      return `<div class="cluster-card"><h3>${escapeHtml(cluster.name)}</h3><p>${escapeHtml(cluster.summary)}</p><div class="cluster-members">${members}</div></div>`;
    }).join("");
    const legacyClusterSection = legacyClusters ? `<div class="report-section"><h3>历史技术聚类</h3>${legacyClusters}</div>` : "";
    const patents = (report.patents || []).map((patent) => {
      const analysis = patent.analysis || {};
      const family = patent.family_status || {};
      const familyMembers = (family.members || []).map((member) => `<div class="family-member"><b>${escapeHtml(member.application_number || member.publication_number || "未知编号")}</b><span>${escapeHtml(member.jurisdiction || "未知法域")}</span><span>${escapeHtml(member.legal_status_category && member.legal_status_category !== "UNKNOWN" ? member.legal_status_category : member.legal_status || "UNKNOWN")}</span><span>申请日 ${escapeHtml(member.filing_date || "未知")}</span></div>`).join("") || `<p class="muted">当前数据源未返回同族成员明细。</p>`;
      return `<details class="patent-card"><summary>${escapeHtml(patent.publication_number)} · ${escapeHtml(patent.title)}</summary><div class="patent-facts"><span>申请号：${escapeHtml(patent.application_number || "未知")}</span><span>申请日：${escapeHtml(patent.filing_date || "未知")}</span><span>公开日：${escapeHtml(patent.publication_date || "未知")}</span><span>当前权利人：${escapeHtml(patent.current_assignee || "未知")}</span></div><div class="family-status"><h4>全族状态</h4><div class="patent-facts"><span>数据状态：${escapeHtml(family.data_status || patent.family_data_status || "UNAVAILABLE")}</span><span>总体法律状态：${escapeHtml(family.overall_legal_status || "UNKNOWN")}</span><span>法域：${escapeHtml((family.jurisdictions || []).join("、") || "未知")}</span><span>Family ID：${escapeHtml(family.family_id || patent.family_id || "未知")}</span></div>${familyMembers}</div><p><b>现有技术：</b>${escapeHtml(analysis.prior_art || "未知")}</p><p><b>现有技术问题：</b>${escapeHtml((analysis.prior_art_problems || []).join("；") || "未知")}</p><p><b>核心发明点：</b>${escapeHtml((analysis.core_invention_points || []).join("；") || "未知")}</p><p><b>解决的技术问题：</b>${escapeHtml((analysis.technical_problems_solved || []).join("；") || "未知")}</p><p><b>有益效果：</b>${escapeHtml((analysis.beneficial_effects || []).join("；") || "未知")}</p></details>`;
    }).join("") || `<p class="muted">暂无成功精读。</p>`;
    const limitations = [...(report.limitations || []), ...Object.entries(report.failures || {}).map(([publication_number, message]) => ({ code: publication_number, message }))].map((item) => `<div class="limitation"><b>${escapeHtml(item.code || "LIMITATION")}</b>：${escapeHtml(item.message || "")}</div>`).join("") || `<p class="muted">无</p>`;
    $("report-view").classList.remove("hidden");
    $("report-view").innerHTML = `<div class="metric-grid"><div class="metric"><b>${summary.family_count ?? summary.candidate_count ?? 0}</b><span>唯一合格专利族</span></div><div class="metric"><b>${summary.publication_count ?? summary.candidate_count ?? 0}</b><span>合格公开文本</span></div><div class="metric"><b>${summary.analyzed_count || 0}</b><span>成功精读专利族</span></div><div class="metric"><b>${summary.company_profile_count || 0}</b><span>公司技术画像</span></div><div class="metric"><b>${summary.trend_count || 0}</b><span>跨公司趋势</span></div><div class="metric"><b>${summary.failed_analysis_count || 0}</b><span>精读失败</span></div></div><section class="report-section"><h3>公司趋势覆盖审计</h3>${coverageAuditView}</section><section class="report-section"><h3>本次检索到的友商别名</h3>${aliases}</section><section class="report-section"><h3>各公司专利族数量</h3><p class="muted">统计口径：时间与友商条件过滤后，优先按可靠 Family ID、其次按申请号聚合；身份缺失时按公开文本保守分开，不用标题相似度猜测合并。</p>${companyBars}</section><section class="report-section"><h3>公司技术画像</h3>${companyProfiles}</section><section class="report-section"><h3>跨公司整体技术趋势</h3>${companyTrends}</section><section class="report-section"><h3>公开法域布局</h3><div class="jurisdiction-list">${jurisdictions}</div></section>${legacyClusterSection}<div class="report-section"><h3>逐族精读</h3>${patents}</div><div class="report-section"><h3>限制与失败</h3>${limitations}</div><div class="report-links"><a href="/api/landscape/runs/${encodeURIComponent(report.run_id)}/report.md">下载 Markdown</a><a href="/api/landscape/runs/${encodeURIComponent(report.run_id)}/patents.csv">下载 CSV</a></div>`;
  }
  async function loadHistory() { try { const body = await jsonRequest("/api/landscape/runs"); $("history-list").innerHTML = body.runs.length ? body.runs.map((run) => `<button class="history-item ${run.run_id === state.runId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}"><strong>${escapeHtml(run.scope?.technology_direction || (run.scope?.competitors || []).map((item) => item.name).join("、") || "专利态势分析")}</strong><span class="history-meta"><span>${escapeHtml(run.status)}</span><span>${escapeHtml((run.created_at ? new Date(run.created_at).toLocaleString("zh-CN") : ""))}</span></span></button>`).join("") : `<p class="muted">还没有分析任务。</p>`; document.querySelectorAll(".history-item").forEach((item) => item.addEventListener("click", () => openRun(item.dataset.runId))); } catch (error) { $("history-list").innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`; } }
  async function openRun(runId) { try { const run = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}`); showRun(run); loadDebug(runId, true); if (TERMINAL.has(run.status)) loadReport(runId); else connectEvents(runId); await loadHistory(); } catch (error) { $("form-message").textContent = error.message; } }
  async function cancelRun() { if (!state.runId) return; try { const run = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(state.runId)}/cancel`, { method: "POST", body: "{}" }); showRun(run); } catch (error) { $("form-message").textContent = error.message; } }

  $("landscape-form").addEventListener("submit", submit); $("refresh-history").addEventListener("click", loadHistory); $("cancel-run").addEventListener("click", cancelRun); $("period-preset").addEventListener("change", presetDates); $("technology-direction").addEventListener("input", updateMode); $("competitors").addEventListener("input", updateMode);
  presetDates(); updateMode(); loadHistory();
})();
