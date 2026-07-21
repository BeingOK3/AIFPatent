const STEP_LABELS = {
  PREPARE_INPUT: "保存不可变输入",
  PARSE_IDEA: "解析技术特征",
  VALIDATE_IDEA_MODEL: "校验 IDEA 模型",
  PLAN_QUERIES: "规划检索式",
  RETRIEVE_CANDIDATES: "双路检索候选",
  NORMALIZE_AND_FETCH: "去重并抓取全文",
  ANALYZE_DOCUMENTS: "逐篇证据映射",
  DETERMINE_NOVELTY: "单篇新颖性裁决",
  ANALYZE_INVENTIVENESS: "多 D1 创造性分析",
  ASSESS_VALUE: "价值预评估",
  AUDIT_AND_REPORT: "证据审计与报告",
};

const MODE_DEFAULTS = {
  quick: { candidate_max: 30, deep_review_min: 10, deep_review_max: 10 },
  standard: { candidate_max: 80, deep_review_min: 10, deep_review_max: 20 },
  deep: { candidate_max: 150, deep_review_min: 20, deep_review_max: 40 },
};

const JUDGMENT_LABELS = {
  FILE: "建议申请",
  ADJUST_THEN_FILE: "调整后申请",
  WATCH: "继续观察",
  DO_NOT_FILE: "不建议申请",
  INVENTIVE: "具备创造性",
  NOT_INVENTIVE: "不具备创造性",
  NEED_MORE_EVIDENCE: "需要更多证据",
  UNCERTAIN: "结论不确定",
  DISCLOSED: "已披露",
  PARTIAL: "部分披露",
  NOT_DISCLOSED: "未披露",
  HIGH: "高相关",
  MEDIUM: "中等相关",
  LOW: "低相关",
  ANALYZED: "已分析",
  critical: "严重",
  warning: "警告",
  info: "信息",
  YES: "有组合动机",
  NO: "无组合动机",
};

const LIMITATION_LABELS = {
  PROVIDER_DEGRADED: "检索服务降级",
  DOCUMENT_FETCH_FAILED: "全文抓取失败",
  DEEP_REVIEW_FETCHED_BELOW_MINIMUM: "深读数量不足",
  DEEP_REVIEW_IDENTIFIER_MISSING: "候选缺少公开号",
  DUPLICATE_DEEP_REVIEW_SELECTION: "重复深读候选",
  DEEP_REVIEW_CANDIDATE_NOT_FOUND: "深读候选无法定位",
  INSUFFICIENT_RELEVANT_DEEP_REVIEWS: "相关文献不足",
  NOVELTY_LIMITATION: "新颖性分析限制",
  INVENTIVE_LIMITATION: "创造性分析限制",
  VALUE_LIMITATION: "价值分析限制",
  LIMITATION: "分析限制",
};

const AUDIT_CODE_LABELS = {
  AUDIT_COMPLETED: "审计完成",
  EVIDENCE_HASH_MISMATCH: "证据哈希不一致",
  MODEL_SEMANTIC_OVERSTATEMENT: "语义表述可能过度",
};

const state = {
  cases: [],
  selectedCase: null,
  selectedRun: null,
  report: null,
  eventSource: null,
  followupEventSource: null,
  debugTimer: null,
  activeTab: "overview",
  followupDocuments: [],
  followupThreads: [],
  followupThread: null,
  followupTurn: null,
};

const $ = (id) => document.getElementById(id);

function clearRuntimeApiConfig() {
  for (const id of ["modelBaseUrl", "apiToken", "modelName"]) {
    const input = $(id);
    if (input) input.value = "";
  }
}

window.addEventListener("pageshow", clearRuntimeApiConfig);
window.addEventListener("pagehide", clearRuntimeApiConfig);

document.addEventListener("DOMContentLoaded", async () => {
  clearRuntimeApiConfig();
  $("evaluationDate").value = new Date().toISOString().slice(0, 10);
  $("ideaText").addEventListener("input", () => {
    $("ideaCount").textContent = `${$("ideaText").value.length} 字符`;
  });
  $("searchMode").addEventListener("change", applyModeDefaults);
  $("runForm").addEventListener("submit", createRun);
  $("newCaseBtn").addEventListener("click", resetWorkspace);
  $("cancelRun").addEventListener("click", cancelSelectedRun);
  $("rerunBtn").addEventListener("click", rerunSelected);
  $("deleteRunBtn").addEventListener("click", deleteSelectedRun);
  $("createFollowupThread").addEventListener("click", createOrSelectFollowupThread);
  $("followupExistingThread").addEventListener("change", selectExistingFollowupThread);
  $("followupForm").addEventListener("submit", submitFollowupTurn);
  $("cancelFollowup").addEventListener("click", cancelFollowupTurn);
  await Promise.all([loadHealth(), loadCases()]);
  renderEmptyProgress();
  renderEmptyDebug();
});

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = body?.detail || body?.error || (typeof body === "string" ? body : response.statusText);
    throw new Error(Array.isArray(detail) ? detail.map((item) => item.msg).join("；") : detail);
  }
  return body;
}

async function loadHealth() {
  try {
    const [health, cache] = await Promise.all([api("/api/system/health"), api("/api/system/cache")]);
    $("healthDot").className = `health-dot ${health.ok ? "ok" : "bad"}`;
    const modelNeedsToken = health.components?.model?.status === "runtime_required";
    $("healthText").textContent = health.ok
      ? (modelNeedsToken ? "核心服务就绪 · 等待本页模型 API 配置" : "核心服务就绪")
      : "服务降级";
    $("cacheText").textContent = `缓存 ${formatBytes(cache.total_bytes || 0)} / ${formatBytes(cache.max_bytes)}`;
  } catch (error) {
    $("healthDot").className = "health-dot bad";
    $("healthText").textContent = "健康检查失败";
  }
}

async function loadCases(selectCaseId = state.selectedCase?.case_id) {
  const data = await api("/api/idea/cases");
  state.cases = data.cases;
  renderCases();
  if (selectCaseId && state.cases.some((item) => item.case_id === selectCaseId)) {
    await selectCase(selectCaseId, false);
  }
}

function renderCases() {
  const list = $("caseList");
  list.replaceChildren();
  if (!state.cases.length) {
    list.append(el("p", "small", "暂无历史。填写 Case 名称和 IDEA 后即可开始。"));
    return;
  }
  for (const item of state.cases) {
    const fragment = $("caseTemplate").content.cloneNode(true);
    const card = fragment.querySelector(".case-card");
    card.dataset.caseId = item.case_id;
    if (state.selectedCase?.case_id === item.case_id) card.classList.add("active");
    fragment.querySelector(".case-name").textContent = item.title;
    fragment.querySelector(".case-meta").textContent = `${item.run_count} 个不可变 Run · Case ${item.case_id.slice(0, 6)} · ${formatTime(item.latest_run_at || item.created_at)}`;
    fragment.querySelector(".case-main").addEventListener("click", () => selectCase(item.case_id, true, true));
    fragment.querySelector(".case-delete").addEventListener("click", (event) => {
      event.stopPropagation();
      deleteCase(item.case_id, item.title);
    });
    list.append(fragment);
  }
}

async function selectCase(caseId, render = true, activateLatest = false) {
  state.selectedCase = await api(`/api/idea/cases/${caseId}`);
  $("caseTitle").value = state.selectedCase.title;
  $("caseTitle").readOnly = true;
  $("activeCaseBadge").textContent = state.selectedCase.title;
  if (render) renderCases();
  const card = document.querySelector(`[data-case-id="${cssEscape(caseId)}"]`);
  if (!card) return;
  document.querySelectorAll(".case-card").forEach((item) => item.classList.remove("active"));
  card.classList.add("active");
  const runList = card.querySelector(".run-list");
  runList.replaceChildren();
  for (const run of state.selectedCase.runs) {
    const button = el("button", "run-item");
    button.type = "button";
    if (state.selectedRun?.run_id === run.run_id) button.classList.add("active");
    const dot = el("span", `run-dot ${statusClass(run.status)}`);
    const copy = el("span", "run-copy");
    copy.append(
      el("strong", "", `${statusLabel(run.status)} · ${truncateText(run.input_preview || "未保存输入摘要", 34)}`),
      el("span", "", `${run.evaluation_date} · ${run.model} · ${formatTime(run.created_at)}`),
    );
    button.title = `Run ${run.run_id}；输入快照 ${run.input_hash || "—"}`;
    button.append(dot, copy);
    button.dataset.runId = run.run_id;
    button.addEventListener("click", () => selectRun(run.run_id));
    runList.append(button);
  }
  const activeRunBelongsHere = state.selectedRun?.case_id === caseId;
  if (activateLatest && state.selectedCase.runs.length && !activeRunBelongsHere) {
    await selectRun(state.selectedCase.runs[0].run_id, false);
  } else if (activateLatest && !state.selectedCase.runs.length) {
    prepareEmptyCase();
  }
}

function resetWorkspace() {
  closeEvents();
  resetFollowupWorkspace();
  stopDebugPolling();
  state.selectedCase = null;
  state.selectedRun = null;
  state.report = null;
  state.activeTab = "overview";
  $("runForm").reset();
  $("caseTitle").readOnly = false;
  clearRuntimeApiConfig();
  $("evaluationDate").value = new Date().toISOString().slice(0, 10);
  $("searchMode").value = "standard";
  applyModeDefaults();
  $("ideaCount").textContent = "0 字符";
  $("activeCaseBadge").textContent = "将新建 Case";
  $("inputPanelTitle").textContent = "新建 IDEA Run";
  $("runSnapshotNotice").classList.add("hidden");
  $("runSnapshotNotice").textContent = "";
  $("runInputMode").textContent = "新 Run 输入；提交后形成不可变快照";
  $("submitRun").textContent = "开始受控评估";
  document.querySelectorAll(".case-card").forEach((item) => item.classList.remove("active"));
  $("cancelRun").classList.add("hidden");
  $("rerunBtn").classList.add("hidden");
  $("deleteRunBtn").classList.add("hidden");
  $("markdownLink").classList.add("hidden");
  $("reportView").classList.add("hidden");
  $("emptyResult").classList.remove("hidden");
  $("emptyResult").querySelector("h3").textContent = "选择历史 Run 或开始一次新评估";
  $("emptyResult").querySelector("p").textContent = "最终结论、原文证据、检索限制与版本信息会保存在本地，可随时复查。";
  setMessage("");
  renderEmptyProgress();
  renderEmptyDebug();
  $("caseTitle").focus();
}

function runtimeModelPayload() {
  const baseUrl = $("modelBaseUrl").value.trim();
  const apiKey = $("apiToken").value.trim();
  const model = $("modelName").value.trim();
  if (!baseUrl) throw new Error("请输入模型 API Base URL");
  if (!apiKey) throw new Error("请输入本页使用的 API Key");
  if (!model) throw new Error("请输入模型名称");
  return { base_url: baseUrl, api_key: apiKey, model };
}

async function createRun(event) {
  event.preventDefault();
  setMessage("");
  const submit = $("submitRun");
  submit.disabled = true;
  try {
    const runtimeModel = runtimeModelPayload();
    let caseId = state.selectedCase?.case_id;
    const caseTitle = $("caseTitle").value.trim();
    if (!caseId) {
      if (!caseTitle) throw new Error("请填写 Case 名称");
      const created = await api("/api/idea/cases", {
        method: "POST",
        body: JSON.stringify({ title: caseTitle }),
      });
      caseId = created.case_id;
      state.selectedCase = created;
    }
    const payload = {
      ...runtimeModel,
      input_text: $("ideaText").value.trim(),
      evaluation_date: $("evaluationDate").value,
      date_basis: $("dateBasis").value.trim() || "用户指定或提交日",
      analysis_scope: "full",
      settings: {
        search_mode: $("searchMode").value,
        candidate_max: numberValue("candidateMax"),
        deep_review_min: numberValue("deepMin"),
        deep_review_max: numberValue("deepMax"),
      },
    };
    if (payload.input_text.length < 10) throw new Error("IDEA 至少需要 10 个字符");
    const run = await api(`/api/idea/cases/${caseId}/runs`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await loadCases(caseId);
    await activateRun(run);
    subscribeToRun(run.run_id);
  } catch (error) {
    setMessage(error.message);
  } finally {
    submit.disabled = false;
  }
}

async function selectRun(runId, refreshCase = true) {
  closeEvents();
  resetFollowupWorkspace();
  stopDebugPolling();
  const run = await api(`/api/idea/runs/${runId}`);
  state.report = null;
  $("reportView").classList.add("hidden");
  $("emptyResult").classList.remove("hidden");
  await activateRun(run);
  if (isTerminal(run.status)) await loadReport(runId);
  else subscribeToRun(runId);
  if (refreshCase) await selectCase(run.case_id, true, false);
  document.querySelectorAll(".run-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.runId === runId);
  });
}

async function activateRun(run) {
  state.selectedRun = run;
  restoreRunSnapshot(run);
  renderProgress(run);
  renderRunActions(run);
  startDebugPolling(run.run_id, !isTerminal(run.status));
  if (!isTerminal(run.status)) {
    state.report = null;
    $("reportView").classList.add("hidden");
    $("emptyResult").classList.remove("hidden");
    $("emptyResult").querySelector("h3").textContent = "评估正在执行";
    $("emptyResult").querySelector("p").textContent = "进度由持久 Harness 状态驱动；关闭页面不会丢失任务。";
  }
}

function restoreRunSnapshot(run) {
  $("caseTitle").value = state.selectedCase?.title || $("caseTitle").value;
  $("caseTitle").readOnly = true;
  $("ideaText").value = run.input_text || "";
  $("ideaCount").textContent = `${$("ideaText").value.length} 字符`;
  $("evaluationDate").value = run.evaluation_date;
  $("dateBasis").value = run.date_basis || "用户指定或提交日";
  const mode = run.settings?.search_mode || "standard";
  $("searchMode").value = MODE_DEFAULTS[mode] ? mode : "standard";
  const defaults = MODE_DEFAULTS[$("searchMode").value];
  $("candidateMax").value = run.settings?.candidate_max ?? defaults.candidate_max;
  $("deepMin").value = run.settings?.deep_review_min ?? defaults.deep_review_min;
  $("deepMax").value = run.settings?.deep_review_max ?? defaults.deep_review_max;
  $("inputPanelTitle").textContent = "历史 Run 输入快照";
  $("runSnapshotNotice").textContent = `正在查看 Run ${run.run_id.slice(0, 8)} 的不可变输入。直接编辑下方内容并提交会在当前 Case 下创建新 Run，不会覆盖该历史记录；“重新运行”则复制原输入和预算。`;
  $("runSnapshotNotice").classList.remove("hidden");
  $("runInputMode").textContent = `历史快照 ${String(run.input_hash || "").slice(0, 12)}；编辑只影响新 Run`;
  $("submitRun").textContent = "以当前输入新建 Run";
}

function prepareEmptyCase() {
  closeEvents();
  stopDebugPolling();
  state.selectedRun = null;
  state.report = null;
  $("ideaText").value = "";
  $("ideaCount").textContent = "0 字符";
  $("evaluationDate").value = new Date().toISOString().slice(0, 10);
  $("dateBasis").value = "用户指定或提交日";
  $("searchMode").value = "standard";
  applyModeDefaults();
  $("inputPanelTitle").textContent = "在当前 Case 新建 Run";
  $("runSnapshotNotice").textContent = "这个 Case 还没有 Run。提交技术方案后将创建第一份不可变输入快照。";
  $("runSnapshotNotice").classList.remove("hidden");
  $("runInputMode").textContent = "新 Run 输入；提交后形成不可变快照";
  $("submitRun").textContent = "开始受控评估";
  $("cancelRun").classList.add("hidden");
  $("rerunBtn").classList.add("hidden");
  $("deleteRunBtn").classList.add("hidden");
  $("markdownLink").classList.add("hidden");
  $("reportView").classList.add("hidden");
  $("emptyResult").classList.remove("hidden");
  renderEmptyProgress();
  renderEmptyDebug();
}

function subscribeToRun(runId) {
  closeEvents();
  const source = new EventSource(`/api/idea/runs/${runId}/events`);
  state.eventSource = source;
  source.onmessage = async (event) => {
    const message = JSON.parse(event.data);
    if (!message.data) return;
    state.selectedRun = message.data;
    renderProgress(message.data);
    renderRunActions(message.data);
    if (message.type === "terminal") {
      closeEvents();
      stopDebugPolling();
      await loadDebug(runId);
      await loadCases(message.data.case_id);
      await loadReport(runId);
    }
  };
  source.onerror = () => {
    if (!isTerminal(state.selectedRun?.status)) setMessage("进度连接中断，可重新选择该 Run 恢复查看");
    closeEvents();
  };
}

function closeEvents() {
  state.eventSource?.close();
  state.eventSource = null;
}

function startDebugPolling(runId, keepPolling = true) {
  stopDebugPolling();
  loadDebug(runId);
  if (keepPolling) {
    state.debugTimer = window.setInterval(() => loadDebug(runId), 1000);
  }
}

function stopDebugPolling() {
  if (state.debugTimer !== null) window.clearInterval(state.debugTimer);
  state.debugTimer = null;
}

async function loadDebug(runId) {
  if (!runId || state.selectedRun?.run_id !== runId) return;
  try {
    const trace = await api(`/api/idea/runs/${runId}/debug`);
    if (state.selectedRun?.run_id !== runId) return;
    renderDebug(trace);
  } catch (error) {
    $("debugStatus").textContent = "读取失败";
    $("debugStatus").className = "status-pill failed";
    $("debugSummary").textContent = error.message;
  }
}

function renderDebug(trace) {
  const current = trace.run.progress.current_step;
  const calls = trace.tool_calls || [];
  const events = trace.events || [];
  $("debugStatus").textContent = statusLabel(trace.run.status);
  $("debugStatus").className = `status-pill ${statusClass(trace.run.status)}`;
  $("debugSummary").textContent = current
    ? `当前 Workflow：${STEP_LABELS[current] || current} · 已记录 ${calls.length} 次 Tool Call`
    : `Workflow 已结束 · 共记录 ${calls.length} 次 Tool Call`;

  const root = $("debugTimeline");
  root.replaceChildren();
  const eventBlock = el("section", "debug-block");
  eventBlock.append(el("h4", "", "最近运行事件"));
  for (const event of events.slice(-16).reverse()) {
    const card = el("article", "debug-entry");
    card.append(
      el("strong", "", debugEventLabel(event.event)),
      el("time", "", formatTime(event.timestamp_ms)),
      el("pre", "", formatDebugDetails(event.details)),
    );
    eventBlock.append(card);
  }
  if (!events.length) eventBlock.append(el("p", "small", "尚无 JSONL 运行事件。"));

  const callBlock = el("section", "debug-block");
  callBlock.append(el("h4", "", "Tool Calls"));
  for (const call of calls.slice(-20).reverse()) {
    const card = el("article", "debug-entry tool-entry");
    const title = `${call.provider} · ${call.operation}`;
    const summary = {
      step: call.step_name,
      status: call.status,
      result_count: call.result_count,
      duration_ms: call.duration_ms,
      request: call.request,
      response: call.response_summary,
      error_code: call.error_code,
      error_message: call.error_message,
    };
    card.append(
      el("strong", "", title),
      el("time", "", formatTime(call.created_at)),
      el("pre", "", formatDebugDetails(summary)),
    );
    callBlock.append(card);
  }
  if (!calls.length) callBlock.append(el("p", "small", "尚未产生 Tool Call。"));
  root.append(eventBlock, callBlock);
}

function renderEmptyDebug() {
  $("debugStatus").textContent = "等待 Run";
  $("debugStatus").className = "status-pill neutral";
  $("debugSummary").textContent = "开始评估后，这里会显示当前 Workflow、Tool Call、耗时和错误。";
  $("debugTimeline").replaceChildren();
}

function debugEventLabel(event) {
  return ({
    run_scheduled: "Run 已调度",
    workflow_started: "旧 Workflow 已开始",
    workflow_step_started: "步骤开始",
    workflow_step_completed: "步骤完成",
    workflow_step_failed: "步骤失败",
    tool_call_started: "Tool Call 开始",
    tool_call_completed: "Tool Call 完成",
    tool_call_failed: "Tool Call 失败",
    workflow_finished: "Workflow 已结束",
    workflow_failed: "Workflow 失败",
    workflow_cancelled: "Workflow 已取消",
    langgraph_started: "LangGraph 已开始",
    langgraph_node_started: "LangGraph 节点开始",
    langgraph_node_completed: "LangGraph 节点完成",
    langgraph_node_failed: "LangGraph 节点失败",
    langgraph_node_cancelled: "LangGraph 节点已取消",
    langgraph_finished: "LangGraph 已结束",
    langgraph_failed: "LangGraph 失败",
    langgraph_cancelled: "LangGraph 已取消",
    run_failed: "Run 失败",
    run_cancelled: "Run 已取消",
  })[event] || event;
}

function formatDebugDetails(details) {
  if (!details || !Object.keys(details).length) return "—";
  return JSON.stringify(details, null, 2);
}

function renderProgress(run) {
  const progress = run.progress;
  $("progressTitle").textContent = progress.current_step ? STEP_LABELS[progress.current_step] : "Workflow 已结束";
  $("runStatus").textContent = run.status;
  $("runStatus").className = `status-pill ${statusClass(run.status)}`;
  $("progressBar").style.width = `${Math.round(progress.completed_steps / progress.total_steps * 100)}%`;
  const list = $("stepList");
  list.replaceChildren();
  for (const step of progress.steps) {
    const item = el("li", step.status.toLowerCase(), STEP_LABELS[step.name] || step.name);
    if (step.attempt > 1) item.title = `第 ${step.attempt} 次尝试`;
    list.append(item);
  }
  if (run.error_message) setMessage(`${run.error_code || "ERROR"}：${run.error_message}`);
}

function renderEmptyProgress() {
  $("stepList").replaceChildren(...Object.values(STEP_LABELS).map((label) => el("li", "", label)));
}

function renderRunActions(run) {
  const terminal = isTerminal(run.status);
  $("cancelRun").classList.toggle("hidden", terminal);
  $("rerunBtn").classList.toggle("hidden", !terminal);
  $("deleteRunBtn").classList.toggle("hidden", !terminal);
  $("markdownLink").classList.toggle("hidden", !terminal || !state.report);
  $("markdownLink").href = `/api/idea/runs/${run.run_id}/report.md`;
}

async function loadReport(runId) {
  try {
    state.report = await api(`/api/idea/runs/${runId}/report`);
    state.activeTab = "overview";
    renderReport();
    renderRunActions(state.selectedRun);
    await loadFollowupWorkspace(runId);
  } catch (error) {
    state.report = null;
    $("reportView").classList.add("hidden");
    $("emptyResult").classList.remove("hidden");
    $("emptyResult").querySelector("h3").textContent = state.selectedRun?.status === "FAILED" ? "Run 未生成最终报告" : "报告暂不可用";
    $("emptyResult").querySelector("p").textContent = state.selectedRun?.error_message || error.message;
    resetFollowupWorkspace();
  }
}

function renderReport() {
  const report = state.report;
  $("emptyResult").classList.add("hidden");
  $("reportView").classList.remove("hidden");
  const overview = report.conclusion_overview;
  const card = $("conclusionCard");
  card.replaceChildren();
  card.className = `conclusion-card ${overview.novelty_code === "NOT_NOVEL" ? "not-novel" : overview.novelty_code === "UNCERTAIN" ? "uncertain" : ""}`;
  card.append(
    el("div", "conclusion-label", overview.novelty_label),
    el("div", "conclusion-meta", `置信度 ${formatNumber(overview.novelty_confidence)} · 申请建议 ${judgmentLabel(overview.filing_recommendation)}`),
    el("p", "conclusion-summary", overview.executive_summary),
  );
  const tabs = [
    ["overview", "总览"], ["features", "技术特征"], ["search", "检索与文献"],
    ["novelty", "新颖性"], ["inventive", "创造性"], ["value", "价值"],
    ["audit", "审计与限制"],
  ];
  $("resultTabs").replaceChildren(...tabs.map(([key, label]) => {
    const button = el("button", `tab-button ${state.activeTab === key ? "active" : ""}`, label);
    button.type = "button";
    button.addEventListener("click", () => { state.activeTab = key; renderReport(); });
    return button;
  }));
  renderActiveTab();
}

function renderActiveTab() {
  const report = state.report;
  const root = $("tabContent");
  root.replaceChildren();
  if (reportHasLegacyEnglish(report)) {
    root.append(section(
      "历史报告语言提示",
      "该 Run 由旧版本生成，部分判断原文为英文。为保持历史报告与 Manifest 不被改写，界面以中文说明替代这些旧文本；点击“重新运行”可生成通过中文硬校验的新报告。",
    ));
  }
  if (state.activeTab === "overview") renderOverview(root, report);
  if (state.activeTab === "features") renderFeatures(root, report);
  if (state.activeTab === "search") renderSearch(root, report);
  if (state.activeTab === "novelty") renderNovelty(root, report);
  if (state.activeTab === "inventive") renderInventive(root, report);
  if (state.activeTab === "value") renderValue(root, report);
  if (state.activeTab === "audit") renderAudit(root, report);
}

function renderOverview(root, report) {
  const facts = el("div", "fact-grid");
  const search = report.search_execution;
  facts.append(
    fact("评估日", report.evaluation.date), fact("候选文献", search.unique_candidate_count),
    fact("深度核验", search.deep_review_count), fact("原始命中", search.raw_hit_count),
    fact("模型", report.provenance.model), fact("Workflow", report.provenance.workflow_version),
  );
  root.append(section("执行摘要", report.conclusion_overview.executive_summary), facts);
  root.append(section("模拟审查意见", report.simulated_office_action));
}

function renderFeatures(root, report) {
  const table = makeTable(["ID", "必要", "来源", "技术特征"]);
  for (const feature of report.idea_features) {
    addRow(table, [feature.feature_id, feature.required ? "是" : "否", feature.source_type, feature.feature_text]);
  }
  root.append(table);
}

function renderSearch(root, report) {
  const execution = report.search_execution;
  root.append(section("检索执行", `共 ${execution.queries.length} 条检索式、${execution.provider_call_count} 次 Provider 调用；${execution.raw_hit_count} 条原始命中合并为 ${execution.unique_candidate_count} 个候选。`));
  const providerGrid = el("div", "fact-grid");
  for (const [name, status] of Object.entries(report.provider_status)) {
    providerGrid.append(fact(name, `${status.successes}/${status.calls} 成功 · ${status.result_count} 结果`));
  }
  root.append(providerGrid, el("div", "section-block"));
  const docs = el("div", "section-block");
  docs.append(el("h3", "", `深度核验文献（${report.deep_review_documents.length}）`));
  for (const doc of report.deep_review_documents) {
    const card = el("article", "doc-card");
    const header = el("header");
    header.append(patentLink(doc.publication_number, doc.url), tag(doc.relevance || "ANALYZED"));
    card.append(header, el("p", "", doc.title || "无标题"), el("span", "small", `${doc.publication_date || "日期未知"} · ${doc.assignee || "申请人未知"}`));
    const mappings = el("div");
    for (const mapping of doc.feature_mappings) mappings.append(tag(`${mapping.feature_id} ${judgmentLabel(mapping.status)}`, mapping.status));
    card.append(mappings);
    docs.append(card);
  }
  root.append(docs);
}

function renderNovelty(root, report) {
  const novelty = report.novelty;
  root.append(section("裁决理由", novelty.rationale));
  const facts = el("div", "fact-grid");
  facts.append(
    fact("最接近文献", patentLink(novelty.closest_publication_number)),
    fact("破坏性文献", novelty.destroying_publication_number ? patentLink(novelty.destroying_publication_number) : "无"),
    fact("缺失特征", novelty.missing_features.join(", ") || "无"),
  );
  root.append(facts, el("div", "section-block"));
  const table = makeTable(["单篇文献", "逐特征覆盖", "破坏新颖性"]);
  for (const matrix of novelty.matrices) {
    const mappings = matrix.mappings.map((item) => `${item.feature_id}:${judgmentLabel(item.status)}`).join(" · ");
    addRow(table, [patentLink(matrix.publication_number), mappings, matrix.destroys_novelty ? "是" : "否"]);
  }
  root.append(table);
}

function renderInventive(root, report) {
  if (!report.inventiveness.length) {
    root.append(section("创造性分析不适用", "已有单篇文献破坏新颖性，因此没有继续消耗模型调用构造 D1/D2 路线。"));
    return;
  }
  for (const route of report.inventiveness) {
    const card = el("article", "route-card");
    const header = el("header");
    const routeTitle = el("strong");
    routeTitle.append(document.createTextNode(`${route.route_id} · D1 `), patentLink(route.d1_publication_number));
    header.append(routeTitle, tag(route.status, route.status));
    card.append(
      header,
      el("p", "", chineseText(route.objective_technical_problem, "该历史 Run 的客观技术问题说明未按中文输出。")),
      el("p", "small", chineseText(route.overall_rationale, "该历史 Run 的创造性总体判断由旧版本以英文生成；请重新运行以获得中文判断。")),
    );
    for (const item of route.distinguishing_features) {
      const detail = el("p");
      detail.append(document.createTextNode(`${item.feature_id}：D2 `));
      if (item.d2_publication_numbers.length) {
        item.d2_publication_numbers.forEach((number, index) => {
          if (index) detail.append(document.createTextNode("、"));
          detail.append(patentLink(number));
        });
      } else {
        detail.append(document.createTextNode("无"));
      }
      detail.append(document.createTextNode(` · 组合动机：${judgmentLabel(item.motivation_to_combine)}`));
      card.append(
        detail,
        el("p", "small", chineseText(item.rationale, "该区别特征的旧版判断说明未按中文输出。")),
      );
    }
    root.append(card);
  }
}

function renderValue(root, report) {
  const value = report.value_assessment;
  const grid = el("div", "fact-grid");
  grid.append(
    valueFact("可取证性", value.detectability), valueFact("规避难度", value.workaround_difficulty),
    valueFact("技术/市场价值", value.technical_market_value),
  );
  root.append(grid, section("申请建议", `${judgmentLabel(value.recommendation)}：${chineseText(value.rationale, "该历史 Run 的价值判断由旧版本以英文生成；请重新运行以获得中文判断。")}`));
  const paths = el("ul", "limitation-list");
  for (const path of value.alternative_paths) {
    paths.append(el("li", "", chineseText(path, "该历史 Run 的替代路径由旧版本以英文生成；请重新运行以获得中文说明。")));
  }
  const block = el("div", "section-block");
  block.append(el("h3", "", "替代路径"), paths);
  root.append(block);
}

function renderAudit(root, report) {
  const counts = report.audit.counts;
  const facts = el("div", "fact-grid");
  facts.append(fact("严重", counts.critical), fact("警告", counts.warning), fact("信息", counts.info));
  root.append(facts, el("div", "section-block"));
  for (const finding of report.audit.findings) {
    const item = el("article", "finding");
    item.append(
      tag(finding.severity, finding.severity),
      el("strong", "", ` ${AUDIT_CODE_LABELS[finding.code] || finding.code}`),
      el("p", "", chineseText(finding.message, auditFallback(finding.code))),
    );
    root.append(item);
  }
  const limitations = el("ul", "limitation-list");
  for (const item of report.limitations) limitations.append(el("li", "", limitationText(item)));
  const block = el("div", "section-block");
  block.append(el("h3", "", "检索与分析局限"), limitations);
  root.append(block);
}

async function loadFollowupWorkspace(runId) {
  closeFollowupEvents();
  state.followupThread = null;
  state.followupTurn = null;
  try {
    const [documentData, threadData] = await Promise.all([
      api(`/api/idea/runs/${runId}/followups/documents`),
      api(`/api/idea/runs/${runId}/followups/threads`),
    ]);
    if (state.selectedRun?.run_id !== runId) return;
    state.followupDocuments = documentData.documents || [];
    state.followupThreads = threadData.threads || [];
    $("followupPanel").classList.remove("hidden");
    renderFollowupDocuments();
    renderFollowupThreadOptions();
    if (state.followupThreads.length) {
      await activateFollowupThread(state.followupThreads[0].thread_id);
    } else {
      $("followupSetup").classList.remove("hidden");
      $("followupConversation").classList.add("hidden");
      setFollowupStatus("未建立会话", "neutral");
    }
  } catch (_) {
    resetFollowupWorkspace();
  }
}

function renderFollowupDocuments() {
  const root = $("followupDocuments");
  root.replaceChildren();
  for (const item of state.followupDocuments) {
    const label = el("label", "followup-document");
    const input = window.document.createElement("input");
    input.type = "checkbox";
    input.value = item.publication_number;
    input.checked = true;
    input.dataset.followupDocument = "true";
    label.append(
      input,
      el("span", "", `${item.publication_number} · Version ${item.version_id.slice(0, 12)}`),
    );
    root.append(label);
  }
  if (!state.followupDocuments.length) {
    root.append(el("p", "small", "这个 Run 没有可追问的 READY 深读全文。"));
  }
}

function renderFollowupThreadOptions() {
  const select = $("followupExistingThread");
  select.replaceChildren(new Option("新建会话", ""));
  for (const thread of state.followupThreads) {
    select.append(new Option(`${thread.title} · ${thread.status}`, thread.thread_id));
  }
  select.value = state.followupThread?.thread_id || "";
}

async function selectExistingFollowupThread() {
  const threadId = $("followupExistingThread").value;
  if (threadId) await activateFollowupThread(threadId);
  else {
    state.followupThread = null;
    $("followupConversation").classList.add("hidden");
    $("followupSetup").classList.remove("hidden");
    setFollowupStatus("未建立会话", "neutral");
  }
}

async function createOrSelectFollowupThread() {
  const existing = $("followupExistingThread").value;
  if (existing) {
    await activateFollowupThread(existing);
    return;
  }
  const title = $("followupThreadTitle").value.trim();
  const publications = [...document.querySelectorAll('[data-followup-document="true"]:checked')]
    .map((item) => item.value);
  if (!title) return setFollowupMessage("请填写会话名称");
  if (!publications.length) return setFollowupMessage("请至少选择一篇深读文献");
  try {
    const thread = await api(
      `/api/idea/runs/${state.selectedRun.run_id}/followups/threads`,
      {
        method: "POST",
        body: JSON.stringify({ title, publication_numbers: publications }),
      },
    );
    state.followupThreads.unshift(thread);
    renderFollowupThreadOptions();
    await activateFollowupThread(thread.thread_id);
  } catch (error) {
    setFollowupMessage(error.message);
  }
}

async function activateFollowupThread(threadId) {
  closeFollowupEvents();
  try {
    state.followupThread = await api(`/api/idea/followups/threads/${threadId}`);
    state.followupTurn = [...(state.followupThread.turns || [])].reverse()
      .find((item) => !isTerminal(item.status)) || null;
    $("followupExistingThread").value = threadId;
    $("followupSetup").classList.add("hidden");
    $("followupConversation").classList.remove("hidden");
    $("followupScopeSummary").textContent =
      state.followupThread.scope.publication_numbers.join("、");
    renderFollowupTurns();
    if (state.followupTurn) subscribeToFollowupTurn(state.followupTurn.turn_id);
    else setFollowupStatus("会话就绪", "completed");
  } catch (error) {
    setFollowupMessage(error.message);
  }
}

async function submitFollowupTurn(event) {
  event.preventDefault();
  if (!state.followupThread) return setFollowupMessage("请先创建或选择追问会话");
  const question = $("followupQuestion").value.trim();
  if (question.length < 2) return setFollowupMessage("请输入至少 2 个字符的问题");
  const button = $("submitFollowup");
  button.disabled = true;
  setFollowupMessage("");
  try {
    const successful = [...(state.followupThread.turns || [])].reverse()
      .find((item) => ["COMPLETED", "COMPLETED_WITH_LIMITATIONS"].includes(item.status));
    const created = await api(
      `/api/idea/followups/threads/${state.followupThread.thread_id}/turns`,
      {
        method: "POST",
        body: JSON.stringify({
          ...runtimeModelPayload(),
          question,
          mode: $("followupMode").value,
          parent_turn_id: successful?.turn_id || null,
        }),
      },
    );
    state.followupThread.turns = [...(state.followupThread.turns || []), created];
    state.followupTurn = created;
    $("followupQuestion").value = "";
    renderFollowupTurns();
    subscribeToFollowupTurn(created.turn_id);
  } catch (error) {
    setFollowupMessage(error.message);
  } finally {
    button.disabled = false;
  }
}

function subscribeToFollowupTurn(turnId) {
  closeFollowupEvents();
  const source = new EventSource(`/api/idea/followups/turns/${turnId}/events`);
  state.followupEventSource = source;
  setFollowupStatus("正在检索与验证", "running");
  $("cancelFollowup").classList.remove("hidden");
  source.onmessage = async (event) => {
    const message = JSON.parse(event.data);
    if (!message.data) return;
    updateFollowupTurn(message.data);
    if (message.type === "terminal") {
      closeFollowupEvents();
      await activateFollowupThread(state.followupThread.thread_id);
    }
  };
  source.onerror = () => {
    closeFollowupEvents();
    setFollowupMessage("追问进度连接中断；重新选择该会话可恢复查看。 ");
  };
}

function updateFollowupTurn(value) {
  const turns = state.followupThread.turns || [];
  const index = turns.findIndex((item) => item.turn_id === value.turn_id);
  if (index >= 0) turns[index] = value;
  else turns.push(value);
  state.followupTurn = isTerminal(value.status) ? null : value;
  renderFollowupTurns();
}

function renderFollowupTurns() {
  const root = $("followupTurns");
  root.replaceChildren();
  const turns = state.followupThread?.turns || [];
  for (const item of turns) {
    const card = el("article", `followup-turn ${statusClass(item.status)}`);
    const header = el("header");
    header.append(
      el("p", "followup-question", item.question),
      el("span", `status-pill ${statusClass(item.status)}`, statusLabel(item.status)),
    );
    card.append(header);
    if (item.answer) renderFollowupAnswer(card, item.answer);
    if (item.error_message) card.append(el("p", "message", `${item.error_code || "ERROR"}：${item.error_message}`));
    if (item.limitations?.length) {
      const list = el("ul", "followup-list");
      item.limitations.forEach((value) => list.append(el("li", "", value)));
      card.append(list);
    }
    if (item.citations?.length) {
      const citations = el("div", "followup-citations");
      for (const citation of item.citations) {
        const detail = el("details", "followup-citation");
        detail.append(
          el("summary", "", `${citation.publication_number} · ${citation.section_label} · ${citation.answer_path}`),
          el("blockquote", "", citation.quote_text),
        );
        citations.append(detail);
      }
      card.append(citations);
    }
    root.append(card);
  }
  if (!turns.length) root.append(el("p", "small", "还没有追问。每一轮都会重新检索冻结文献并生成可展开 Citation。"));
  const active = [...turns].reverse().find((item) => !isTerminal(item.status));
  $("submitFollowup").disabled = Boolean(active);
  $("cancelFollowup").classList.toggle("hidden", !active);
  if (active) setFollowupStatus(statusLabel(active.status), statusClass(active.status));
  else if (turns.length) setFollowupStatus(statusLabel(turns.at(-1).status), statusClass(turns.at(-1).status));
}

function renderFollowupAnswer(root, answer) {
  root.append(el("p", "followup-answer", answer.direct_answer || "—"));
  const details = [];
  for (const item of answer.overlap_items || []) {
    details.push(`${item.feature_id} · ${judgmentLabel(item.overlap_level)}：${item.analysis}`);
  }
  for (const difference of answer.differences || []) details.push(`差异：${difference}`);
  for (const option of answer.design_around_options || []) {
    details.push(`规避候选「${option.title}」：${option.change}；${option.expected_effect}`);
  }
  if (details.length) {
    const list = el("ul", "followup-list");
    details.forEach((value) => list.append(el("li", "", value)));
    root.append(list);
  }
  if (answer.legal_boundary) root.append(el("p", "small", answer.legal_boundary));
}

async function cancelFollowupTurn() {
  if (!state.followupTurn) return;
  try {
    await api(`/api/idea/followups/turns/${state.followupTurn.turn_id}/cancel`, {
      method: "POST", body: "{}",
    });
  } catch (error) {
    setFollowupMessage(error.message);
  }
}

function closeFollowupEvents() {
  state.followupEventSource?.close();
  state.followupEventSource = null;
}

function resetFollowupWorkspace() {
  closeFollowupEvents();
  state.followupDocuments = [];
  state.followupThreads = [];
  state.followupThread = null;
  state.followupTurn = null;
  $("followupPanel")?.classList.add("hidden");
  $("followupConversation")?.classList.add("hidden");
  $("followupSetup")?.classList.remove("hidden");
  $("followupTurns")?.replaceChildren();
  setFollowupMessage("");
}

function setFollowupStatus(text, kind) {
  $("followupStatus").textContent = text;
  $("followupStatus").className = `status-pill ${kind}`;
}

function setFollowupMessage(message) {
  $("followupMessage").textContent = message || "";
}

async function cancelSelectedRun() {
  if (!state.selectedRun || isTerminal(state.selectedRun.status)) return;
  if (!confirm("确定取消当前 Run？已完成步骤和审计记录会保留。")) return;
  await api(`/api/idea/runs/${state.selectedRun.run_id}/cancel`, { method: "POST", body: "{}" });
}

async function rerunSelected() {
  if (!state.selectedRun) return;
  try {
    const runtimeModel = runtimeModelPayload();
    const run = await api(`/api/idea/runs/${state.selectedRun.run_id}/rerun`, {
      method: "POST",
      body: JSON.stringify(runtimeModel),
    });
    await loadCases(run.case_id);
    await activateRun(run);
    subscribeToRun(run.run_id);
  } catch (error) {
    setMessage(error.message);
  }
}

async function deleteSelectedRun() {
  if (!state.selectedRun || !confirm("永久删除这个 Run 的输入、结果和报告？此操作不可恢复。")) return;
  const caseId = state.selectedRun.case_id;
  await api(`/api/idea/runs/${state.selectedRun.run_id}`, { method: "DELETE", body: "{}" });
  closeEvents();
  stopDebugPolling();
  state.selectedRun = null;
  state.report = null;
  $("reportView").classList.add("hidden");
  $("emptyResult").classList.remove("hidden");
  renderEmptyProgress();
  renderEmptyDebug();
  await loadCases(caseId);
}

async function deleteCase(caseId, title) {
  if (!confirm(`永久删除 Case「${title}」及其全部 Run？`)) return;
  await api(`/api/idea/cases/${caseId}`, { method: "DELETE", body: "{}" });
  if (state.selectedCase?.case_id === caseId) {
    closeEvents();
    stopDebugPolling();
    state.selectedCase = null;
    state.selectedRun = null;
    state.report = null;
    $("activeCaseBadge").textContent = "未选择 Case";
    $("reportView").classList.add("hidden");
    $("emptyResult").classList.remove("hidden");
    renderEmptyProgress();
    renderEmptyDebug();
  }
  await loadCases();
}

function applyModeDefaults() {
  const values = MODE_DEFAULTS[$("searchMode").value];
  $("candidateMax").value = values.candidate_max;
  $("deepMin").value = values.deep_review_min;
  $("deepMax").value = values.deep_review_max;
}

function section(title, text) {
  const block = el("section", "section-block");
  block.append(el("h3", "", title), el("p", "", text || "—"));
  return block;
}

function fact(label, value) {
  const item = el("div", "fact");
  const content = el("strong");
  if (value instanceof Node) content.append(value);
  else content.textContent = String(value ?? "—");
  item.append(el("span", "", label), content);
  return item;
}

function valueFact(label, dimension) {
  const score = ({ LOW: 1, MEDIUM: 3, HIGH: 5 })[dimension.rating] ?? dimension.rating;
  const item = fact(label, `${score}/5`);
  item.append(el("p", "small", chineseText(dimension.rationale, `该历史 Run 的${label}评分理由未按中文输出。`)));
  return item;
}

function makeTable(headers) {
  const table = el("table", "data-table");
  const head = document.createElement("thead");
  const row = document.createElement("tr");
  headers.forEach((header) => row.append(el("th", "", header)));
  head.append(row);
  table.append(head, document.createElement("tbody"));
  return table;
}

function addRow(table, values) {
  const row = document.createElement("tr");
  values.forEach((value) => {
    const cell = el("td");
    if (value instanceof Node) cell.append(value);
    else cell.textContent = String(value ?? "—");
    row.append(cell);
  });
  table.querySelector("tbody").append(row);
}

function tag(text, kind = text) {
  return el("span", `tag ${String(kind).toLowerCase().replaceAll("_", "-")}`, judgmentLabel(text));
}

function judgmentLabel(value) {
  return JUDGMENT_LABELS[value] || value;
}

function patentLink(publicationNumber, explicitUrl = null) {
  const number = String(publicationNumber || "").trim();
  const link = el("a", "patent-link", number || "未标准化");
  link.href = safePatentUrl(number, explicitUrl);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.title = `打开专利 ${number}`;
  return link;
}

function safePatentUrl(publicationNumber, explicitUrl) {
  if (explicitUrl) {
    try {
      const parsed = new URL(explicitUrl, window.location.origin);
      if (["http:", "https:"].includes(parsed.protocol)) return parsed.href;
    } catch (_) {
      // Fall through to a deterministic public patent URL.
    }
  }
  const compact = publicationNumber.replaceAll(" ", "").toUpperCase();
  const language = compact.startsWith("CN") ? "zh" : "en";
  return `https://patents.google.com/patent/${encodeURIComponent(compact)}/${language}`;
}

function limitationText(item) {
  const code = item?.code || "LIMITATION";
  const label = LIMITATION_LABELS[code] || code;
  if (item?.message) return `${label}：${chineseText(item.message, limitationFallback(code))}`;
  if (code === "PROVIDER_DEGRADED") return `${label}：检索服务 ${item.provider} 本次调用全部失败，已按降级路径继续。`;
  if (code === "DOCUMENT_FETCH_FAILED") return `${label}：专利 ${item.publication_number || "未知"} 的全文抓取失败。`;
  if (code === "DEEP_REVIEW_FETCHED_BELOW_MINIMUM") return `${label}：成功获取 ${item.fetched} 篇全文，低于要求的 ${item.required} 篇。`;
  return `${label}：存在已记录的结构化限制，详细字段可在调试记录中查看。`;
}

function chineseText(value, fallback) {
  const text = String(value || "").trim();
  if (!text) return "—";
  return isPrimarilyChinese(text) ? text : fallback;
}

function isPrimarilyChinese(value) {
  const text = String(value || "");
  const cjkCount = (text.match(/[\u3400-\u4dbf\u4e00-\u9fff]/gu) || []).length;
  const latinCount = (text.match(/[A-Za-z]/g) || []).length;
  return cjkCount > 0 && cjkCount * 4 >= latinCount;
}

function limitationFallback(code) {
  return ({
    INVENTIVE_LIMITATION: "现有 D2 证据不足或可信度有限，创造性判断存在限制。",
    VALUE_LIMITATION: "现有证据不足以支持更确定的价值判断。",
    NOVELTY_LIMITATION: "现有检索与证据对新颖性判断形成限制。",
  })[code] || "该历史限制说明由旧版本以英文生成；请重新运行以获得中文说明。";
}

function auditFallback(code) {
  return ({
    AUDIT_COMPLETED: "确定性校验与语义证据审计均已完成，未发现问题。",
    MODEL_SEMANTIC_OVERSTATEMENT: "审计发现结论表述可能超出所引用证据的支持范围。",
  })[code] || "该历史审计说明由旧版本以英文生成；请重新运行以获得中文说明。";
}

function reportHasLegacyEnglish(report) {
  const texts = [];
  for (const route of report.inventiveness || []) {
    texts.push(route.objective_technical_problem, route.overall_rationale, ...(route.limitations || []));
    for (const feature of route.distinguishing_features || []) texts.push(feature.rationale);
  }
  const value = report.value_assessment || {};
  texts.push(
    value.rationale,
    value.detectability?.rationale,
    value.workaround_difficulty?.rationale,
    value.technical_market_value?.rationale,
    ...(value.alternative_paths || []),
    ...(value.limitations || []),
  );
  for (const finding of report.audit?.findings || []) texts.push(finding.message);
  for (const limitation of report.limitations || []) texts.push(limitation?.message);
  return texts.some((text) => text && !isPrimarilyChinese(text));
}

function el(tagName, className = "", text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function numberValue(id) {
  const value = Number($(id).value);
  return Number.isFinite(value) ? value : null;
}

function statusClass(status = "") {
  const value = status.toLowerCase();
  if (value === "queued") return "neutral";
  if (value === "completed_with_limitations") return "completed";
  return value;
}

function statusLabel(status = "") {
  return ({
    QUEUED: "排队中",
    RUNNING: "运行中",
    COMPLETED: "已完成",
    COMPLETED_WITH_LIMITATIONS: "完成但有限制",
    FAILED: "失败",
    CANCELLED: "已取消",
  })[status] || status || "—";
}

function isTerminal(status) {
  return ["COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"].includes(status);
}

function formatTime(milliseconds) {
  if (!milliseconds) return "—";
  return new Date(milliseconds).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatNumber(value) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

function truncateText(value, maximum) {
  const text = String(value || "").trim();
  return text.length > maximum ? `${text.slice(0, maximum)}…` : text;
}

function setMessage(message) { $("formMessage").textContent = message || ""; }
function cssEscape(value) { return window.CSS?.escape ? CSS.escape(value) : value.replaceAll('"', '\\"'); }
