(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"]);
  const state = { runId: null, events: null, report: null, debugLoadedAt: 0, scopeDraft: null };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }
  function today() { return new Date().toISOString().slice(0, 10); }
  function shiftMonths(source, months) { const value = new Date(`${source}T00:00:00Z`); const monthIndex = value.getUTCFullYear() * 12 + value.getUTCMonth() - months; const year = Math.floor(monthIndex / 12); const month = ((monthIndex % 12) + 12) % 12; const day = Math.min(value.getUTCDate(), new Date(Date.UTC(year, month + 1, 0)).getUTCDate()); return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10); }
  function presetDates() {
    const end = today(); const preset = $("period-preset").value;
    const months = { ONE_YEAR: 12, THREE_YEARS: 36, FIVE_YEARS: 60, TEN_YEARS: 120 }[preset];
    if (months) { $("publication-start").value = shiftMonths(end, months); $("publication-end").value = end; }
  }
  function derivedMode() {
    const hasDirection = Boolean($("technology-direction").value.trim());
    const hasCompetitor = parseCompanies().length > 0;
    if (hasDirection && hasCompetitor) return "COMPANY_AND_TECHNOLOGY";
    if (hasDirection) return "TECHNOLOGY_ONLY";
    if (hasCompetitor) return "COMPANY_ONLY";
    return null;
  }
  function updateMode() {
    const mode = derivedMode();
    const labels = {
      TECHNOLOGY_ONLY: "仅技术方向",
      COMPANY_ONLY: "仅公司",
      COMPANY_AND_TECHNOLOGY: "公司 + 技术方向",
    };
    $("derived-mode").textContent = mode ? `当前分析模式：${labels[mode]}（系统自动判定）` : "当前分析模式：等待输入";
  }
  function parseCompanies() {
    const values = $("competitors").value.split("\n").map((line) => line.trim()).filter(Boolean);
    if (values.length > 50) throw new Error("一次最多填写 50 个公司。");
    if (new Set(values.map(normalizeText)).size !== values.length) throw new Error("公司名称存在重复项。");
    return values;
  }
  function collectRuntimeConfig() {
    const baseUrl = $("base-url").value.trim();
    if ((baseUrl.match(/:\/\//g) || []).length !== 1) {
      throw new Error("模型 Base URL 必须是一条完整地址；请不要重复粘贴 URL。");
    }
    const apiKey = $("api-key").value.trim();
    if (!apiKey) throw new Error("请填写模型 API Key；密钥只在本次请求中使用。");
    return {
      api_key: apiKey,
      base_url: baseUrl,
      model: $("model").value.trim(),
    };
  }
  function normalizeText(value) { return value.normalize("NFKC").trim().replace(/\s+/gu, " ").toLocaleLowerCase(); }
  function stableId(prefix) { const bytes = crypto.getRandomValues(new Uint8Array(8)); return `${prefix}-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`; }
  function detectLanguage(value) { if (/[\u3400-\u4dbf\u4e00-\u9fff]/u.test(value)) return "ZH"; if (/[A-Za-z]/u.test(value)) return "EN"; return "OTHER"; }
  function draftMode(draft) { if (draft.companies.length && draft.technology_input) return "COMPANY_AND_TECHNOLOGY"; return draft.companies.length ? "COMPANY_ONLY" : "TECHNOLOGY_ONLY"; }
  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.error || `请求失败（${response.status}）`);
    return body;
  }
  async function submit(event) {
    event.preventDefault(); $("form-message").textContent = ""; $("submit-button").disabled = true;
    if (!derivedMode()) { $("form-message").textContent = "请至少填写技术方向或一个公司。"; $("submit-button").disabled = false; return; }
    try {
      const created = await jsonRequest("/api/landscape/scope-drafts", { method: "POST", body: JSON.stringify({ company_names: parseCompanies(), technology_input: $("technology-direction").value.trim() || null, publication_start: $("publication-start").value, publication_end: $("publication-end").value }) });
      rememberDraft(created);
      $("form-message").textContent = "范围草稿已保存，正在并发扩展公司名称和双语技术词…";
      await expandScopeDraft();
    }
    catch (error) { $("form-message").textContent = error.message; }
    finally { $("submit-button").disabled = false; }
  }

  function rememberDraft(draft) {
    state.scopeDraft = draft;
    const url = new URL(window.location.href); url.searchParams.set("scopeDraft", draft.draft_id); history.replaceState(null, "", url);
    renderScopeDraft();
  }
  async function expandScopeDraft() {
    const draft = state.scopeDraft;
    if (!draft || !["DRAFT", "EXPANDING"].includes(draft.status)) return;
    const expanded = await jsonRequest(`/api/landscape/scope-drafts/${encodeURIComponent(draft.draft_id)}/expand`, { method: "POST", body: JSON.stringify({ expected_revision: draft.revision, ...collectRuntimeConfig() }) });
    rememberDraft(expanded); $("form-message").textContent = "扩展完成，请逐项审查后确认。";
  }
  function decisionOptions(item, company = false) {
    const current = item.status === "PROPOSED" ? "" : item.status === "ACTIVE" ? "ACTIVE" : `EXCLUDED:${item.memory_action || "NONE"}`;
    const options = [["", "请选择"], ["ACTIVE", "纳入本次检索"], ["EXCLUDED:NONE", "仅本次排除"]];
    if (company) options.push(["EXCLUDED:REJECT", "长期拒绝，不再建议"]);
    if (company && item.source === "HISTORY") options.push(["EXCLUDED:RETIRE", "从长期档案停用"]);
    return options.map(([value, label]) => `<option value="${value}"${value === current ? " selected" : ""}>${label}</option>`).join("");
  }
  function candidateRow(item, company) {
    return `<div class="candidate-row"><div><strong>${escapeHtml(item.text)}</strong><small>${escapeHtml(item.language)} · ${escapeHtml(company ? item.relation_type : item.relation_to_original)} · ${escapeHtml(item.source)}</small>${item.rationale ? `<p>${escapeHtml(item.rationale)}</p>` : ""}</div><select class="${company ? "company" : "term"}-decision" data-id="${escapeHtml(company ? item.name_id : item.term_id)}" aria-label="审查 ${escapeHtml(item.text)}">${decisionOptions(item, company)}</select></div>`;
  }
  function renderScopeDraft() {
    const draft = state.scopeDraft; if (!draft) return;
    $("scope-review-panel").classList.remove("hidden"); $("scope-review-status").textContent = draft.status;
    $("scope-review-summary").textContent = `${draftMode(draft)} · 公开日 ${draft.publication_start} 至 ${draft.publication_end}（含起止日）· 修订 ${draft.revision}`;
    $("scope-limitations").innerHTML = (draft.limitations || []).map((item) => `<div class="limitation"><b>${escapeHtml(item.code)}</b>：${escapeHtml(item.message)}</div>`).join("");
    $("company-review-list").innerHTML = (draft.companies || []).map((company) => `<section class="review-group" data-profile-id="${escapeHtml(company.profile_id)}"><h3>${escapeHtml(company.display_name)}</h3><p class="muted">别名、法律名称、子公司和集团成员会分开标注；修改名称请新增正确项并排除原项。</p>${company.names.map((item) => candidateRow(item, true)).join("")}<div class="add-candidate"><input class="new-company-name" maxlength="300" placeholder="新增公司名称或别名"><button type="button" class="secondary add-company-name">添加并纳入</button></div></section>`).join("");
    $("technology-review").innerHTML = draft.technology_input ? `<section class="review-group"><h3>双语技术检索词</h3><p class="muted">确认时至少保留一个中文词和一个英文词。</p>${draft.technology_terms.map((item) => candidateRow(item, false)).join("")}<div class="add-candidate"><input id="new-technology-term" maxlength="300" placeholder="新增中文或英文技术词"><button type="button" id="add-technology-term" class="secondary">添加并纳入</button></div></section>` : "";
    const resumable = ["DRAFT", "EXPANDING"].includes(draft.status); $("resume-scope-expansion").classList.toggle("hidden", !resumable); $("save-scope-review").classList.toggle("hidden", draft.status !== "AWAITING_CONFIRMATION"); $("confirm-scope").classList.toggle("hidden", draft.status !== "AWAITING_CONFIRMATION");
  }
  function addCompanyName(button) {
    captureReviewDecisions(state.scopeDraft, false);
    const group = button.closest(".review-group"); const input = group.querySelector(".new-company-name"); const text = input.value.trim(); if (!text) return;
    const company = state.scopeDraft.companies.find((item) => item.profile_id === group.dataset.profileId); const normalized = normalizeText(text);
    if (company.names.some((item) => item.normalized_text === normalized)) throw new Error("该公司名称已在候选中。");
    company.names.push({ name_id: stableId("CNM"), text, normalized_text: normalized, language: detectLanguage(text), relation_type: "ALIAS", source: "USER_ADDED", status: "ACTIVE", memory_action: "NONE", rationale: "用户在范围审查中新增" }); renderScopeDraft();
  }
  function addTechnologyTerm() {
    captureReviewDecisions(state.scopeDraft, false);
    const input = $("new-technology-term"); const text = input.value.trim(); if (!text) return; const normalized = normalizeText(text);
    const language = detectLanguage(text); if (language === "OTHER") throw new Error("技术词必须包含中文或英文字母。");
    if (state.scopeDraft.technology_terms.some((item) => item.normalized_text === normalized)) throw new Error("该技术词已在候选中。");
    state.scopeDraft.technology_terms.push({ term_id: stableId("TRM"), text, normalized_text: normalized, language, relation_to_original: "RELATED", source: "USER_ADDED", status: "ACTIVE", rationale: "用户在范围审查中新增" }); renderScopeDraft();
  }
  function captureReviewDecisions(draft, requireAll) {
    const decisions = new Map(Array.from(document.querySelectorAll(".company-decision,.term-decision"), (item) => [item.dataset.id, item.value]));
    const apply = (item, id) => { const value = decisions.get(id); if (!value) { if (requireAll) throw new Error(`仍有未审查候选：${item.text}`); return; } const [status, action = "NONE"] = value.split(":"); item.status = status; if ("memory_action" in item) item.memory_action = action; };
    draft.companies.forEach((company) => company.names.forEach((item) => apply(item, item.name_id))); draft.technology_terms.forEach((item) => apply(item, item.term_id)); return draft;
  }
  function reviewedDraft() {
    const draft = captureReviewDecisions(structuredClone(state.scopeDraft), true); draft.revision += 1; draft.status = "AWAITING_CONFIRMATION"; return draft;
  }
  async function saveScopeReview() {
    try { const current = state.scopeDraft; const draft = reviewedDraft(); const saved = await jsonRequest(`/api/landscape/scope-drafts/${encodeURIComponent(current.draft_id)}`, { method: "PATCH", body: JSON.stringify({ expected_revision: current.revision, draft }) }); rememberDraft(saved); $("scope-review-message").textContent = "审查进度已保存。"; return saved; } catch (error) { $("scope-review-message").textContent = error.message; throw error; }
  }
  async function confirmScope() {
    try { const saved = await saveScopeReview(); const confirmed = await jsonRequest(`/api/landscape/scope-drafts/${encodeURIComponent(saved.draft_id)}/confirm`, { method: "POST", body: JSON.stringify({ expected_revision: saved.revision }) }); $("scope-review-status").textContent = "CONFIRMED"; $("scope-review-message").textContent = `范围已冻结：${confirmed.scope_revision_id}。正式检索只会读取这份不可变范围。`; $("save-scope-review").classList.add("hidden"); $("confirm-scope").classList.add("hidden"); } catch (error) { $("scope-review-message").textContent = error.message; }
  }
  async function restoreScopeDraft() { const id = new URL(window.location.href).searchParams.get("scopeDraft"); if (!id) return; try { rememberDraft(await jsonRequest(`/api/landscape/scope-drafts/${encodeURIComponent(id)}`)); } catch (error) { $("form-message").textContent = `无法恢复范围草稿：${error.message}`; } }
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
    const deepRead = report.deep_read || {};
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
    }).join("") || (deepRead.status === "NOT_STARTED" && Number(deepRead.selected_count || 0) > 0 ? `<p class="muted">尚未发起逐族精读；当前公司趋势已基于全量轻量指纹生成。点击下方按钮后，才会对选中的专利调用模型精读。</p>` : `<p class="muted">暂无成功精读。</p>`);
    const deepFailures = Object.entries(deepRead.last_execution?.failures || {}).map(([publication_number, message]) => ({ code: publication_number, message }));
    const limitations = [...(report.limitations || []), ...Object.entries(report.failures || {}).map(([publication_number, message]) => ({ code: publication_number, message })), ...deepFailures].map((item) => `<div class="limitation"><b>${escapeHtml(item.code || "LIMITATION")}</b>：${escapeHtml(item.message || "")}</div>`).join("") || `<p class="muted">无</p>`;
    const pendingDeepCount = Number(deepRead.pending_count || 0);
    const deepReadAction = pendingDeepCount > 0 ? `<section class="report-section"><h3>逐族精读</h3><p class="muted">待精读 ${pendingDeepCount} 件；此操作会使用当前页面的模型配置，并且 API Key 不会保存。</p><button type="button" class="secondary deep-analysis-button" data-run-id="${escapeHtml(report.run_id)}">开始精读已选 ${pendingDeepCount} 件专利</button></section>` : `<section class="report-section"><h3>逐族精读</h3><p class="muted">${deepRead.status === "EXECUTED" ? `已启动精读：成功 ${Number(deepRead.succeeded_count || 0)}，失败 ${Number(deepRead.failed_count || 0)}。` : "本次没有可供精读的选中专利。"}</p></section>`;
    $("report-view").classList.remove("hidden");
    $("report-view").innerHTML = `<div class="metric-grid"><div class="metric"><b>${summary.family_count ?? summary.candidate_count ?? 0}</b><span>唯一合格专利族</span></div><div class="metric"><b>${summary.publication_count ?? summary.candidate_count ?? 0}</b><span>合格公开文本</span></div><div class="metric"><b>${summary.analyzed_count || 0}</b><span>成功精读专利族</span></div><div class="metric"><b>${pendingDeepCount}</b><span>待发起精读</span></div><div class="metric"><b>${summary.company_profile_count || 0}</b><span>公司技术画像</span></div><div class="metric"><b>${summary.trend_count || 0}</b><span>跨公司趋势</span></div><div class="metric"><b>${deepRead.failed_count ?? summary.failed_analysis_count ?? 0}</b><span>精读失败</span></div></div><section class="report-section"><h3>公司趋势覆盖审计</h3>${coverageAuditView}</section><section class="report-section"><h3>本次检索到的友商别名</h3>${aliases}</section><section class="report-section"><h3>各公司专利族数量</h3><p class="muted">统计口径：时间与友商条件过滤后，优先按可靠 Family ID、其次按申请号聚合；身份缺失时按公开文本保守分开，不用标题相似度猜测合并。</p>${companyBars}</section><section class="report-section"><h3>公司技术画像</h3>${companyProfiles}</section><section class="report-section"><h3>跨公司整体技术趋势</h3>${companyTrends}</section><section class="report-section"><h3>公开法域布局</h3><div class="jurisdiction-list">${jurisdictions}</div></section>${legacyClusterSection}${deepReadAction}<div class="report-section"><h3>逐族精读结果</h3>${patents}</div><div class="report-section"><h3>限制与失败</h3>${limitations}</div><div class="report-links"><a href="/api/landscape/runs/${encodeURIComponent(report.run_id)}/report.md">下载 Markdown</a><a href="/api/landscape/runs/${encodeURIComponent(report.run_id)}/patents.csv">下载 CSV</a></div>`;
    const deepButton = document.querySelector(".deep-analysis-button");
    if (deepButton) deepButton.addEventListener("click", () => startDeepAnalysis(report.run_id, deepButton));
  }
  async function startDeepAnalysis(runId, button) {
    try {
      button.disabled = true;
      $("form-message").textContent = "正在逐件精读已选专利，请勿关闭页面…";
      const result = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}/deep-analyze`, { method: "POST", body: JSON.stringify(collectRuntimeConfig()) });
      state.report = result.report;
      renderReport(result.report);
      await loadDebug(runId, true);
      $("form-message").textContent = `精读完成：成功 ${Number(result.deep_analysis?.analyzed_count || 0)}，本次失败 ${Object.keys(result.deep_analysis?.failures || {}).length}。`;
    } catch (error) {
      $("form-message").textContent = `精读未完成：${error.message}`;
      button.disabled = false;
    }
  }
  async function loadHistory() { try { const body = await jsonRequest("/api/landscape/runs"); $("history-list").innerHTML = body.runs.length ? body.runs.map((run) => `<button class="history-item ${run.run_id === state.runId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}"><strong>${escapeHtml(run.scope?.technology_direction || (run.scope?.competitors || []).map((item) => item.name).join("、") || "专利态势分析")}</strong><span class="history-meta"><span>${escapeHtml(run.status)}</span><span>${escapeHtml((run.created_at ? new Date(run.created_at).toLocaleString("zh-CN") : ""))}</span></span></button>`).join("") : `<p class="muted">还没有分析任务。</p>`; document.querySelectorAll(".history-item").forEach((item) => item.addEventListener("click", () => openRun(item.dataset.runId))); } catch (error) { $("history-list").innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`; } }
  async function openRun(runId) { try { const run = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}`); showRun(run); loadDebug(runId, true); if (TERMINAL.has(run.status)) loadReport(runId); else connectEvents(runId); await loadHistory(); } catch (error) { $("form-message").textContent = error.message; } }
  async function cancelRun() { if (!state.runId) return; try { const run = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(state.runId)}/cancel`, { method: "POST", body: "{}" }); showRun(run); } catch (error) { $("form-message").textContent = error.message; } }

  $("landscape-form").addEventListener("submit", submit); $("refresh-history").addEventListener("click", loadHistory); $("cancel-run").addEventListener("click", cancelRun); $("period-preset").addEventListener("change", presetDates); $("technology-direction").addEventListener("input", updateMode); $("competitors").addEventListener("input", updateMode);
  $("resume-scope-expansion").addEventListener("click", async () => { try { await expandScopeDraft(); } catch (error) { $("scope-review-message").textContent = error.message; } });
  $("save-scope-review").addEventListener("click", () => saveScopeReview().catch(() => {})); $("confirm-scope").addEventListener("click", confirmScope);
  $("scope-review-panel").addEventListener("click", (event) => { try { const companyButton = event.target.closest(".add-company-name"); if (companyButton) addCompanyName(companyButton); if (event.target.id === "add-technology-term") addTechnologyTerm(); } catch (error) { $("scope-review-message").textContent = error.message; } });
  presetDates(); updateMode(); restoreScopeDraft(); loadHistory();
})();
