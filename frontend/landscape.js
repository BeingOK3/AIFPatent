(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"]);
  const state = { runId: null, events: null, report: null, scopeDraft: null };
  const MODE_LABELS = {
    TECHNOLOGY_ONLY: "仅技术方向",
    COMPANY_ONLY: "仅公司",
    COMPANY_AND_TECHNOLOGY: "公司 + 技术方向",
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
  }
  function today() { return new Date().toISOString().slice(0, 10); }
  function shiftMonths(source, months) {
    const value = new Date(`${source}T00:00:00Z`);
    const monthIndex = value.getUTCFullYear() * 12 + value.getUTCMonth() - months;
    const year = Math.floor(monthIndex / 12);
    const month = ((monthIndex % 12) + 12) % 12;
    const day = Math.min(value.getUTCDate(), new Date(Date.UTC(year, month + 1, 0)).getUTCDate());
    return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
  }
  function presetDates() {
    const end = today();
    const months = { ONE_YEAR: 12, THREE_YEARS: 36, FIVE_YEARS: 60, TEN_YEARS: 120 }[$("period-preset").value];
    if (months) { $("publication-start").value = shiftMonths(end, months); $("publication-end").value = end; }
  }
  function parseCompanies() {
    const values = $("competitors").value.split("\n").map((value) => value.trim()).filter(Boolean);
    if (values.length > 50) throw new Error("一次最多填写 50 个公司。");
    if (new Set(values.map(normalizeText)).size !== values.length) throw new Error("公司名称存在重复项。");
    return values;
  }
  function derivedMode() {
    const technology = Boolean($("technology-direction").value.trim());
    const companies = parseCompanies().length > 0;
    return technology && companies ? "COMPANY_AND_TECHNOLOGY" : technology ? "TECHNOLOGY_ONLY" : companies ? "COMPANY_ONLY" : null;
  }
  function updateMode() {
    const mode = derivedMode();
    $("derived-mode").textContent = mode ? `当前分析模式：${MODE_LABELS[mode]}（系统自动判定）` : "当前分析模式：等待输入";
  }
  function collectRuntimeConfig() {
    const baseUrl = $("base-url").value.trim();
    if ((baseUrl.match(/:\/\//g) || []).length !== 1) throw new Error("模型 Base URL 必须是一条完整地址。");
    const apiKey = $("api-key").value.trim();
    if (!apiKey) throw new Error("请填写模型 API Key；密钥只在内存中用于本次运行。");
    return { api_key: apiKey, base_url: baseUrl, model: $("model").value.trim() };
  }
  function normalizeText(value) { return value.normalize("NFKC").trim().replace(/\s+/gu, " ").toLocaleLowerCase(); }
  function stableId(prefix) { const bytes = crypto.getRandomValues(new Uint8Array(8)); return `${prefix}-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`; }
  function detectLanguage(value) { return /[\u3400-\u4dbf\u4e00-\u9fff]/u.test(value) ? "ZH" : /[A-Za-z]/u.test(value) ? "EN" : "OTHER"; }
  function draftMode(draft) { return draft.companies.length && draft.technology_input ? "COMPANY_AND_TECHNOLOGY" : draft.companies.length ? "COMPANY_ONLY" : "TECHNOLOGY_ONLY"; }
  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || body.error || `请求失败（${response.status}）`);
    return body;
  }

  async function submit(event) {
    event.preventDefault(); $("form-message").textContent = ""; $("submit-button").disabled = true;
    try {
      if (!derivedMode()) throw new Error("请至少填写技术方向或一个公司。");
      const created = await jsonRequest("/api/landscape/scope-drafts", { method: "POST", body: JSON.stringify({ company_names: parseCompanies(), technology_input: $("technology-direction").value.trim() || null, publication_start: $("publication-start").value, publication_end: $("publication-end").value }) });
      rememberDraft(created); $("form-message").textContent = "范围草稿已保存，正在并发扩展公司名称和双语技术词…";
      await expandScopeDraft();
    } catch (error) { $("form-message").textContent = error.message; }
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
  function candidateStatusLabel(item) {
    if (item.status !== "EXCLUDED") return "";
    if (item.memory_action === "REJECT") return "长期拒绝";
    if (item.memory_action === "RETIRE") return "已停用";
    return "仅本次排除";
  }
  function sourceBadge(item) {
    const labels = { HISTORY: "历史", MODEL_SUGGESTED: "模型", USER_INPUT: "用户", USER_ADDED: "手动" };
    return labels[item.source] || item.source;
  }
  function sourceGroupLabel(item) {
    if (item.status === "EXCLUDED") return "已排除 · 可恢复";
    if (item.source === "HISTORY") return "历史档案";
    if (item.source === "MODEL_SUGGESTED") return "模型建议";
    return "其他候选";
  }
  function scopeCounts(items, company) {
    const active = items.filter((item) => item.status === "ACTIVE").length;
    const model = items.filter((item) => item.status !== "ACTIVE" && item.source === "MODEL_SUGGESTED").length;
    const history = items.filter((item) => item.status !== "ACTIVE" && item.source === "HISTORY").length;
    const excluded = items.filter((item) => item.status === "EXCLUDED").length;
    const parts = [`已纳入 ${active}`];
    if (model) parts.push(`模型建议 ${model}`);
    if (history) parts.push(`历史档案 ${history}`);
    if (excluded) parts.push(`已排除 ${excluded}`);
    return parts.join(" · ") || `已纳入 0${company ? " · 可从历史或模型建议中选择" : " · 可从模型建议中选择"}`;
  }
  function candidateLabel(item, company) {
    const relation = company ? item.relation_type : item.relation_to_original;
    const status = candidateStatusLabel(item);
    return `${item.text} · ${item.language} · ${relation} · ${item.source}${status ? ` · ${status}` : ""}`;
  }
  function chipActions(item, company) {
    const id = escapeHtml(item.name_id || item.term_id);
    const buttons = [`<button type="button" class="chip-action chip-remove" data-id="${id}" title="仅本次排除" aria-label="仅本次排除">×</button>`];
    if (company) {
      buttons.push(`<button type="button" class="chip-action chip-reject" data-id="${id}" title="长期拒绝，不再建议" aria-label="长期拒绝">拒</button>`);
      if (item.source === "HISTORY") buttons.push(`<button type="button" class="chip-action chip-retire" data-id="${id}" title="从长期档案停用" aria-label="从长期档案停用">停</button>`);
    }
    return buttons.join("");
  }
  function chipHTML(item, company) {
    return `<span class="chip"><small class="chip-source">${escapeHtml(sourceBadge(item))}</small><span class="chip-text" title="${escapeHtml(candidateLabel(item, company))}">${escapeHtml(item.text)}</span>${chipActions(item, company)}</span>`;
  }
  function chipList(items, company) {
    return items.length ? `<div class="chip-list">${items.map((item) => chipHTML(item, company)).join("")}</div>` : `<p class="muted">尚未纳入任何${company ? "名称" : "技术词"}。</p>`;
  }
  function pickerHTML(candidates, company) {
    const order = ["模型建议", "历史档案", "已排除 · 可恢复", "其他候选"];
    const groups = new Map();
    for (const item of candidates) {
      const label = sourceGroupLabel(item);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(item);
    }
    const options = order.filter((label) => groups.has(label)).map((label) => {
      const items = groups.get(label);
      return `<optgroup label="${label}">${items.map((item) => {
        const id = item.name_id || item.term_id;
        const rationale = item.rationale ? ` · ${item.rationale}` : "";
        return `<option value="${escapeHtml(id)}" title="${escapeHtml(candidateLabel(item, company) + rationale)}">${escapeHtml(candidateLabel(item, company))}</option>`;
      }).join("")}</optgroup>`;
    }).join("");
    return `<div class="add-picker">
      <button type="button" class="secondary toggle-add">＋ 添加${company ? "公司名称/别名" : "技术词"}</button>
      <div class="picker hidden">
        ${options ? `<div class="picker-row"><select class="picker-select" aria-label="${company ? "候选公司名称" : "候选技术词"}">${options}</select><button type="button" class="secondary pick-add">添加选中项</button></div>` : `<p class="muted">没有更多候选，可直接在下方新增。</p>`}
        <div class="picker-row"><input class="picker-input" maxlength="300" placeholder="新增${company ? "名称或别名" : "中文或英文技术词"}"><button type="button" class="secondary pick-custom">新增并纳入</button></div>
      </div>
    </div>`;
  }
  function renderScopeDraft() {
    const draft = state.scopeDraft; if (!draft) return;
    $("scope-review-panel").classList.remove("hidden"); $("scope-review-status").textContent = draft.status;
    $("scope-review-summary").textContent = `${MODE_LABELS[draftMode(draft)]} · 公开日 ${draft.publication_start} 至 ${draft.publication_end}（含起止日）· 修订 ${draft.revision}`;
    $("scope-limitations").innerHTML = (draft.limitations || []).map((item) => `<div class="limitation"><b>${escapeHtml(item.code)}</b>：${escapeHtml(item.message)}</div>`).join("");
    $("company-review-list").innerHTML = (draft.companies || []).map((company) => {
      const active = company.names.filter((item) => item.status === "ACTIVE");
      const pool = company.names.filter((item) => item.status !== "ACTIVE");
      return `<section class="review-group" data-profile-id="${escapeHtml(company.profile_id)}"><h3>${escapeHtml(company.display_name)}</h3><p class="muted">${escapeHtml(scopeCounts(company.names, true))} · 下拉从“模型建议 / 历史档案”选择纳入检索；未选中的候选默认仅本次排除。</p>${chipList(active, true)}${pickerHTML(pool, true)}</section>`;
    }).join("");
    $("technology-review").innerHTML = draft.technology_input ? `<section class="review-group"><h3>双语技术检索词</h3><p class="muted">${escapeHtml(scopeCounts(draft.technology_terms, false))} · 确认时至少保留一个中文词和一个英文词。</p>${chipList(draft.technology_terms.filter((item) => item.status === "ACTIVE"), false)}${pickerHTML(draft.technology_terms.filter((item) => item.status !== "ACTIVE"), false)}</section>` : "";
    const editable = draft.status === "AWAITING_CONFIRMATION";
    $("resume-scope-expansion").classList.toggle("hidden", !["DRAFT", "EXPANDING"].includes(draft.status));
    $("save-scope-review").classList.toggle("hidden", !editable); $("confirm-scope").classList.toggle("hidden", !editable);
    $("start-confirmed-run").classList.toggle("hidden", draft.status !== "CONFIRMED");
  }
  function findCandidate(id) {
    for (const company of state.scopeDraft.companies) {
      const item = company.names.find((candidate) => candidate.name_id === id);
      if (item) return item;
    }
    return state.scopeDraft.technology_terms.find((item) => item.term_id === id);
  }
  function setCandidateStatus(id, status, memoryAction) {
    const item = findCandidate(id);
    if (!item) throw new Error("候选不存在，请刷新页面后重试。");
    item.status = status;
    if ("memory_action" in item) item.memory_action = memoryAction || "NONE";
    renderScopeDraft();
  }
  function pickCandidate(group) {
    const select = group.querySelector(".picker-select");
    if (!select || !select.value) return;
    setCandidateStatus(select.value, "ACTIVE", "NONE");
  }
  function addCustomCandidate(group) {
    const input = group.querySelector(".picker-input");
    const text = input.value.trim(); if (!text) return;
    const normalized = normalizeText(text);
    const language = detectLanguage(text);
    const company = group.dataset.profileId ? state.scopeDraft.companies.find((item) => item.profile_id === group.dataset.profileId) : null;
    if (company) {
      if (company.names.some((item) => item.normalized_text === normalized)) throw new Error("该公司名称已在候选中。");
      company.names.push({ name_id: stableId("CNM"), text, normalized_text: normalized, language, relation_type: "ALIAS", source: "USER_ADDED", status: "ACTIVE", memory_action: "NONE", rationale: "用户在范围审查中新增" });
    } else {
      if (language === "OTHER") throw new Error("技术词必须包含中文或英文字母。");
      if (state.scopeDraft.technology_terms.some((item) => item.normalized_text === normalized)) throw new Error("该技术词已在候选中。");
      state.scopeDraft.technology_terms.push({ term_id: stableId("TRM"), text, normalized_text: normalized, language, relation_to_original: "RELATED", source: "USER_ADDED", status: "ACTIVE", rationale: "用户在范围审查中新增" });
    }
    input.value = "";
    renderScopeDraft();
  }
  function reviewedDraft() {
    const draft = structuredClone(state.scopeDraft);
    draft.companies.forEach((company) => company.names.forEach((item) => { if (item.status === "PROPOSED") { item.status = "EXCLUDED"; item.memory_action = "NONE"; } }));
    draft.technology_terms.forEach((item) => { if (item.status === "PROPOSED") item.status = "EXCLUDED"; });
    const emptyCompany = draft.companies.find((company) => !company.names.some((item) => item.status === "ACTIVE"));
    if (emptyCompany) throw new Error(`公司「${emptyCompany.display_name}」至少要纳入一个名称。`);
    if (draft.technology_input) {
      const languages = new Set(draft.technology_terms.filter((item) => item.status === "ACTIVE").map((item) => item.language));
      if (!languages.has("ZH") || !languages.has("EN")) throw new Error("技术方向至少要保留一个中文词和一个英文词。");
    }
    draft.revision += 1; draft.status = "AWAITING_CONFIRMATION";
    return draft;
  }
  async function saveScopeReview() {
    const current = state.scopeDraft; const draft = reviewedDraft();
    const saved = await jsonRequest(`/api/landscape/scope-drafts/${encodeURIComponent(current.draft_id)}`, { method: "PATCH", body: JSON.stringify({ expected_revision: current.revision, draft }) });
    rememberDraft(saved); $("scope-review-message").textContent = "审查进度已保存。"; return saved;
  }
  async function startRun(scopeRevisionId) {
    const run = await jsonRequest("/api/landscape/runs", { method: "POST", body: JSON.stringify({ scope_revision_id: scopeRevisionId }) });
    await jsonRequest(`/api/landscape/runs/${encodeURIComponent(run.run_id)}/credentials`, { method: "POST", body: JSON.stringify(collectRuntimeConfig()) });
    showRun(run); connectEvents(run.run_id); await loadHistory();
  }
  async function confirmScope() {
    try {
      const saved = await saveScopeReview();
      const confirmed = await jsonRequest(`/api/landscape/scope-drafts/${encodeURIComponent(saved.draft_id)}/confirm`, { method: "POST", body: JSON.stringify({ expected_revision: saved.revision }) });
      $("scope-review-status").textContent = "CONFIRMED"; $("save-scope-review").classList.add("hidden"); $("confirm-scope").classList.add("hidden");
      $("scope-review-message").textContent = `范围已冻结：${confirmed.scope_revision_id}，正在建立正式运行…`;
      await startRun(confirmed.scope_revision_id);
    } catch (error) { $("scope-review-message").textContent = error.message; }
  }
  async function startConfirmedScope() {
    try {
      const draft = state.scopeDraft;
      const confirmed = await jsonRequest(`/api/landscape/scope-drafts/${encodeURIComponent(draft.draft_id)}/confirm`, { method: "POST", body: JSON.stringify({ expected_revision: draft.revision }) });
      $("scope-review-message").textContent = `正在从不可变范围 ${confirmed.scope_revision_id} 建立新运行…`;
      await startRun(confirmed.scope_revision_id);
    } catch (error) { $("scope-review-message").textContent = error.message; }
  }
  async function restoreScopeDraft() { const id = new URL(window.location.href).searchParams.get("scopeDraft"); if (!id) return; try { rememberDraft(await jsonRequest(`/api/landscape/scope-drafts/${encodeURIComponent(id)}`)); } catch (error) { $("form-message").textContent = `无法恢复范围草稿：${error.message}`; } }

  function connectEvents(runId) {
    if (state.events) state.events.close(); state.events = new EventSource(`/api/landscape/runs/${encodeURIComponent(runId)}/events`);
    state.events.onmessage = (event) => { const payload = JSON.parse(event.data); if (payload.data) showRun(payload.data); if (payload.type === "terminal") { state.events.close(); loadReport(runId); loadDebug(runId); loadHistory(); } };
  }
  function showRun(run) {
    state.runId = run.run_id; $("run-panel").classList.remove("hidden");
    $("run-title").textContent = `${MODE_LABELS[run.mode] || "专利态势分析"} · ${run.run_id}`;
    $("run-status").textContent = run.status; $("run-status").className = `status ${run.status}`;
    $("cancel-run").classList.toggle("hidden", TERMINAL.has(run.status));
    $("step-list").innerHTML = (run.progress?.steps || []).map((step) => `<div class="step ${escapeHtml(step.status)}"><span class="step-label">${escapeHtml(step.stage_name)}</span>${escapeHtml(step.status)} · ${Number(step.completed_count || 0)}/${step.total_count == null ? "?" : Number(step.total_count)}</div>`).join("");
    const awaitingScale = run.status === "AWAITING_SCALE_CONFIRMATION";
    $("scale-gate-actions").classList.toggle("hidden", !awaitingScale);
    const estimate = run.scale_gate?.estimate; if (estimate) $("scale-gate-message").textContent = `预计 ${estimate.estimated_total_results} 篇、${estimate.estimated_total_pages} 页、${estimate.estimated_shards} 个分片。系统不会静默截断。`;
    $("credential-actions").classList.toggle("hidden", run.status !== "WAITING_FOR_CREDENTIALS");
    if (run.status === "FAILED") $("form-message").textContent = `${run.error_code || "FAILED"}：${run.error_message || "运行失败"}`;
    loadDebug(run.run_id);
  }
  async function loadDebug(runId) {
    try {
      const debug = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}/debug`);
      const queries = (debug.query_plan?.queries || []).map((query) => `<li><code>${escapeHtml(query.query_text)}</code><span>${escapeHtml(query.publication_start)} 至 ${escapeHtml(query.publication_end)}</span></li>`).join("") || `<li class="muted">尚未生成查询。</li>`;
      const stages = (debug.stages || []).map((step) => `<div class="debug-row"><b>${escapeHtml(step.stage_name)}</b><span>${escapeHtml(step.status)} · 尝试 ${step.attempt} · ${Number(step.completed_count || 0)}/${step.total_count == null ? "?" : Number(step.total_count)}</span></div>`).join("");
      const limitations = (debug.limitations || []).map((item) => `<div class="limitation"><b>${escapeHtml(item.code)}</b>：${escapeHtml(item.message)}</div>`).join("") || `<p class="muted">暂无限制。</p>`;
      $("debug-content").innerHTML = `<div class="debug-grid"><section><h4>13 阶段检查点</h4>${stages}</section><section><h4>规模闸门</h4><p class="debug-summary">${debug.scale_gate ? `估算 ${Number(debug.scale_gate.estimate?.estimated_total_results || 0)} · ${escapeHtml(debug.scale_gate.decision || "等待决策")}` : "估算中"}</p>${limitations}</section><section><h4>确认后的可回放查询</h4><ul class="debug-queries">${queries}</ul></section><section><h4>固定版本</h4><p class="audit-code">Scope ${escapeHtml(debug.scope_revision_id)}<br>Taxonomy ${escapeHtml(debug.taxonomy_version)}</p></section></div>`;
    } catch (error) { $("debug-content").textContent = `运行审计暂不可用：${error.message}`; }
  }
  function directionName(report, directionId) {
    if (directionId === "UNRESOLVED") return "Unresolved";
    const representative = report.representatives.find((item) => item.direction_id === directionId);
    return representative?.classification_path?.join(" / ") || directionId;
  }
  function renderReport(report) {
    if (!["landscape-report/4.0.0", "landscape-report/4.1.0"].includes(report.schema_version)) throw new Error("不支持的报告版本");
    $("report-view").classList.remove("hidden"); const counts = report.counts;
    const metrics = [[counts.raw_hit_count, "原始命中"], [counts.frozen_publication_count, "公开文本"], [counts.analysis_unit_count, "分析单元"], [counts.classified_count, "已分类"], [counts.others_count, "Others"], [counts.unresolved_count, "Unresolved"], [counts.detail_fetch_failed_count, "摘要失败"]].map(([value, label]) => `<div class="metric"><b>${Number(value)}</b><span>${label}</span></div>`).join("");
    const bucketLabels = new Map(report.metric_cube.buckets.map((item) => [item.bucket_id, item.label]));
    const trends = report.trends.map((trend) => `<tr><td>${escapeHtml(directionName(report, trend.direction_id))}</td><td>${escapeHtml(trend.change_type)}</td><td>${trend.bucket_metrics.map((item) => `${escapeHtml(bucketLabels.get(item.bucket_id) || item.bucket_id)}: ${Number(item.analysis_unit_count)}`).join(" · ")}</td><td>${escapeHtml((trend.limitation_codes || []).join("、") || "无")}</td></tr>`).join("") || `<tr><td colspan="4">数据不足，未形成趋势候选。</td></tr>`;
    const representatives = report.representatives.map((item) => `<article><a href="${escapeHtml(item.patent_url)}" target="_blank" rel="noopener noreferrer"><strong>${escapeHtml(item.title)}</strong></a><p>${escapeHtml(item.publication_number)} · ${escapeHtml(item.publication_date || "日期未知")} · ${escapeHtml(item.classification_path.join(" / "))}</p><small>${escapeHtml(item.selection_reasons.join("；"))}</small></article>`).join("") || `<p class="muted">暂无代表专利。</p>`;
    const patents = report.patents.map((item) => `<tr><td>${item.patent_url ? `<a href="${escapeHtml(item.patent_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a>` : escapeHtml(item.title)}</td><td>${escapeHtml(item.publication_number)}</td><td>${escapeHtml(item.publication_date || "未知")}</td><td>${escapeHtml(item.applicants.join("、") || "未知")}</td><td>${escapeHtml(item.classification_terminal)}${item.unresolved_reason ? `<br><small>${escapeHtml(item.unresolved_reason)}</small>` : ""}</td></tr>`).join("");
    const limitations = report.limitations.map((item) => `<div class="limitation"><b>${escapeHtml(item.code)}</b>：${escapeHtml(item.message)}（${Number(item.affected_count)}）</div>`).join("") || `<p class="muted">无运行限制。</p>`;
    const audits = report.query_audit.map((item) => `<div class="debug-row"><b>${escapeHtml(item.query_text)}</b><span>${Number(item.page_count)} 页 · ${Number(item.raw_hit_count)} 条 · ${escapeHtml(item.final_stop_reason)}</span></div>`).join("");
        const macro = report.macro_summary || {};
    const macroPulse = (macro.overall_pulse || []).map((point) => `<span class="macro-pulse">${escapeHtml(point.label)} ${point.analysis_unit_count}</span>`).join("");
    const macroDirections = (macro.top_directions || []).map((item) => `<div class="macro-row"><b>${escapeHtml(item.name)}</b><span>${item.analysis_unit_count} 个分析单元 · ${escapeHtml(item.change_type)}</span></div>`).join("");
    const macroOrgs = (macro.top_organizations || []).map((item) => `<div class="macro-row"><b>${escapeHtml(item.name)}</b><span>${item.analysis_unit_count} 个分析单元</span></div>`).join("");
    const macroSection = `<section class="report-section macro-summary"><h3>宏观趋势总结</h3>${macro.narrative ? `<p class="macro-narrative">${escapeHtml(macro.narrative)}</p>` : `<p class="muted">暂无宏观总结。</p>`}<div class="macro-pulse-list">${macroPulse}</div>${macroDirections ? `<div class="macro-column"><h4>Top 方向</h4>${macroDirections}</div>` : ""}${macroOrgs ? `<div class="macro-column"><h4>Top 机构</h4>${macroOrgs}</div>` : ""}</section>`;
    $("report-view").innerHTML = `<div class="metric-grid">${metrics}</div>${macroSection}<section class="report-section"><h3>视图口径</h3><p class="muted">${escapeHtml(MODE_LABELS[report.mode_view.mode])} · 主轴 ${escapeHtml(report.mode_view.primary_axis)} · 次轴 ${escapeHtml(report.mode_view.secondary_axis || "无")}。${escapeHtml(report.mode_view.counting_disclosure)}</p></section><section class="report-section"><h3>趋势候选</h3><table class="trend-table"><thead><tr><th>技术方向</th><th>结论类型</th><th>时间序列（分析单元）</th><th>限制</th></tr></thead><tbody>${trends}</tbody></table></section><section class="report-section"><h3>代表专利</h3><div class="representative-list">${representatives}</div></section><section class="report-section"><h3>专利明细</h3><table class="patent-table"><thead><tr><th>专利</th><th>公开号</th><th>公开日</th><th>申请人</th><th>分类终态</th></tr></thead><tbody>${patents}</tbody></table></section><section class="report-section"><h3>限制</h3>${limitations}</section><section class="report-section"><h3>查询审计</h3>${audits}</section><div class="report-links"><a href="/api/landscape/runs/${encodeURIComponent(report.run_id)}/report.md">下载 Markdown</a></div>`;
  }
  async function loadReport(runId) { try { state.report = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}/report`); renderReport(state.report); } catch (error) { if (TERMINAL.has($("run-status").textContent)) $("form-message").textContent = error.message; } }
  async function loadHistory() {
    try {
      const body = await jsonRequest("/api/landscape/runs");
      $("history-list").innerHTML = body.runs.length ? body.runs.map((run) => { const title = run.scope?.technology_input || (run.scope?.companies || []).map((item) => item.display_name).join("、") || "专利态势分析"; return `<button class="history-item ${run.run_id === state.runId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}"><strong>${escapeHtml(title)}</strong><span class="history-meta"><span>${escapeHtml(run.status)}</span><span>${escapeHtml(run.publication_start)} 至 ${escapeHtml(run.publication_end)}</span></span></button>`; }).join("") : `<p class="muted">还没有分析任务。</p>`;
      document.querySelectorAll(".history-item").forEach((item) => item.addEventListener("click", () => openRun(item.dataset.runId)));
    } catch (error) { $("history-list").innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`; }
  }
  async function openRun(runId) { try { const run = await jsonRequest(`/api/landscape/runs/${encodeURIComponent(runId)}`); showRun(run); if (TERMINAL.has(run.status)) loadReport(runId); else connectEvents(runId); await loadHistory(); } catch (error) { $("form-message").textContent = error.message; } }
  async function cancelRun() { if (!state.runId) return; await jsonRequest(`/api/landscape/runs/${encodeURIComponent(state.runId)}/cancel`, { method: "POST", body: "{}" }); await openRun(state.runId); }
  async function decideScale() { await jsonRequest(`/api/landscape/runs/${encodeURIComponent(state.runId)}/scale-decision`, { method: "POST", body: JSON.stringify({ approve: true }) }); await attachCredentials(); connectEvents(state.runId); }
  async function attachCredentials() { await jsonRequest(`/api/landscape/runs/${encodeURIComponent(state.runId)}/credentials`, { method: "POST", body: JSON.stringify(collectRuntimeConfig()) }); await openRun(state.runId); }

  $("landscape-form").addEventListener("submit", submit); $("refresh-history").addEventListener("click", loadHistory); $("cancel-run").addEventListener("click", () => cancelRun().catch((error) => { $("form-message").textContent = error.message; }));
  $("period-preset").addEventListener("change", presetDates); $("technology-direction").addEventListener("input", updateMode); $("competitors").addEventListener("input", updateMode);
  $("resume-scope-expansion").addEventListener("click", () => expandScopeDraft().catch((error) => { $("scope-review-message").textContent = error.message; }));
  $("save-scope-review").addEventListener("click", () => saveScopeReview().catch((error) => { $("scope-review-message").textContent = error.message; })); $("confirm-scope").addEventListener("click", confirmScope);
  $("start-confirmed-run").addEventListener("click", startConfirmedScope);
  $("scope-review-panel").addEventListener("click", (event) => {
    try {
      const button = event.target.closest("button"); if (!button) return;
      const group = button.closest(".review-group");
      if (button.classList.contains("toggle-add")) { const picker = button.nextElementSibling; if (picker) picker.classList.toggle("hidden"); return; }
      if (button.classList.contains("pick-add")) { pickCandidate(group); return; }
      if (button.classList.contains("pick-custom")) { addCustomCandidate(group); return; }
      if (button.classList.contains("chip-remove")) { setCandidateStatus(button.dataset.id, "EXCLUDED", "NONE"); return; }
      if (button.classList.contains("chip-reject")) { setCandidateStatus(button.dataset.id, "EXCLUDED", "REJECT"); return; }
      if (button.classList.contains("chip-retire")) { setCandidateStatus(button.dataset.id, "EXCLUDED", "RETIRE"); return; }
    } catch (error) { $("scope-review-message").textContent = error.message; }
  });
  $("approve-scale").addEventListener("click", () => decideScale().catch((error) => { $("form-message").textContent = error.message; })); $("reject-scale").addEventListener("click", () => cancelRun().catch((error) => { $("form-message").textContent = error.message; })); $("attach-credentials").addEventListener("click", () => attachCredentials().catch((error) => { $("form-message").textContent = error.message; }));
  presetDates(); updateMode(); restoreScopeDraft(); loadHistory();
})();
