(function () {
  "use strict";

  // Pure helpers are exported for development tests; clinical state stays private.
  function nextRevision(local, remote) { return Math.max(Number(local) || 0, Number(remote) || 0) + 1; }
  function shouldAdoptDraft(remote, local, dirty, force) { return Boolean(force || (!dirty && Number(remote) > Number(local))); }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function buildDeveloperConfig(original, initialProfiles, currentProfiles, initialPrompts, currentPrompts) {
    const result = clone(original);
    result.custom_profiles = result.custom_profiles || {};
    result.custom_prompts = result.custom_prompts || {};
    for (const [key, value] of Object.entries(currentProfiles)) {
      const prior = initialProfiles[key];
      if (!prior || value.model !== prior.model || value.reasoning_effort !== prior.reasoning_effort || value.max_tokens !== prior.max_tokens) result.custom_profiles[key] = value;
    }
    for (const [key, value] of Object.entries(currentPrompts)) {
      if (value !== initialPrompts[key]) result.custom_prompts[key] = value;
    }
    return result;
  }
  function validPlotPoints(row) {
    if (!row || row.chartable !== true) return [];
    const points = (row.values || []).map(point => ({x: Date.parse(point.timestamp || ""), y: point.numeric}));
    if (points.length < 2 || points.some(point => !Number.isFinite(point.x) || typeof point.y !== "number" || !Number.isFinite(point.y))) return [];
    if (points.some((point, index) => index && point.x <= points[index - 1].x)) return [];
    return points;
  }
  function trendPresentation(row) {
    const all = row.all_values || row.values || [];
    const hasTime = point => Number.isFinite(Date.parse(point.timestamp || ""));
    const known = (row.values || []).filter(hasTime).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
    const allKnown = all.filter(hasTime).sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
    const unknown = row.unknown_values || all.filter(point => !hasTime(point));
    // A present null is deliberate (for example conflicting values at the same time).
    // Only old DTOs that omit the field may infer a latest known observation.
    const latest = Object.prototype.hasOwnProperty.call(row, "latest_known")
      ? row.latest_known && hasTime(row.latest_known) ? row.latest_known : null
      : allKnown[allKnown.length - 1] || null;
    const observations = new Map();
    for (const point of allKnown) {
      const time = Date.parse(point.timestamp);
      if (!observations.has(time)) observations.set(time, new Set());
      observations.get(time).add(point.value);
    }
    const hasConflicts = [...observations.values()].some(values => values.size > 1);
    const separators = known.map((point, index) => !index ? "" : Date.parse(point.timestamp) > Date.parse(known[index - 1].timestamp) ? "→" : "/");
    const latestConflict = Object.prototype.hasOwnProperty.call(row, "latest_known") && latest === null && allKnown.length > 0;
    const lastKnownTime = allKnown.length ? allKnown[allKnown.length - 1].time || "" : "";
    return {known, unknown, latest, separators, hasConflicts, latestConflict, lastKnownTime};
  }
  if (typeof module === "object" && module.exports) module.exports = {nextRevision, shouldAdoptDraft, buildDeveloperConfig, validPlotPoints, trendPresentation};
  if (typeof document === "undefined") return;

  const $ = id => document.getElementById(id);
  const md = window.WardLensMarkdown;
  let token = location.hash.slice(1);
  history.replaceState(null, "", location.pathname);
  let alive = true, state = null, requestBusy = false, pollTimer = null;
  let requestSequence = 0, appliedSequence = 0, settingsDirty = false, demoDirty = false, lookbackDirty = false;
  let renderedPatients = "", renderedOverview = "", renderedProfiles = "";
  let preview = null, previewWorkspace = "", previewMode = "send", browserReport = null;
  let developer = null, promptSelection = "rounding", developerDirty = false;
  let clipboardValue = null, clipboardTimer = null;
  const knownJobs = new Set(), adoptAfterJobs = new Set();
  const workspaces = {};
  const workflowLabels = {rounding: "查房整理", admission: "Admission Note", qa: "自由提問", emergency: "急症處置"};
  const answerLabels = {idle: "尚未生成", running: "生成中 · 目前內容仍可能不完整", complete: "生成完成 · AI 草稿請核對", incomplete: "生成中斷 · 內容不完整", cancelled: "已停止 · 內容不完整"};

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function idle(node) { node.dataset.idle = ""; return node; }
  function button(text, id, handler, className) {
    const node = element("button", text, className); node.type = "button";
    if (id) node.id = id;
    if (handler) node.addEventListener("click", handler);
    return node;
  }
  function setText(id, value) { const node = $(id); const text = String(value || ""); if (node && node.textContent !== text) node.textContent = text; }
  function notice(message, kind) {
    if (!alive) return;
    $("notice").hidden = !message;
    $("notice").className = "notice" + (kind ? " " + kind : "");
    setText("notice-text", message);
  }
  class ApiError extends Error { constructor(message, status, code) { super(message); this.status = status; this.code = code; } }
  async function api(path, payload) {
    if (!alive || !token) throw new ApiError("請由 WardLens 控制視窗重新開啟網頁。", 401);
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 20000);
    try {
      const headers = {"X-WardLens-Token": token};
      const options = {method: payload === undefined ? "GET" : "POST", headers, cache: "no-store", credentials: "omit", redirect: "error", mode: "same-origin", signal: controller.signal};
      if (payload !== undefined) { headers["Content-Type"] = "application/json"; options.body = JSON.stringify(payload); }
      const response = await fetch(path, options);
      let result;
      try { result = await response.json(); } catch (_) { throw new ApiError("本機服務回應不完整，請重新開啟介面。", response.status); }
      if (!response.ok || result.ok === false) {
        if (response.status === 401 || ["InvalidHost", "InvalidOrigin", "CrossSiteRequest"].includes(result.code)) disconnect("連線授權已失效", "請從 WardLens 控制視窗重新開啟瀏覽器介面。");
        throw new ApiError(typeof result.error === "string" ? result.error : "操作未完成，請重試。", response.status, result.code);
      }
      return result;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError("無法連到本機 WardLens，請確認控制視窗仍在執行。", 0);
    } finally { clearTimeout(timer); }
  }
  const action = (name, payload) => api("/api/" + name, payload || {});
  function showError(error) {
    const message = error.message || "操作失敗，請重試。";
    if ($("preview-dialog").open) { $("preview-error").hidden = false; setText("preview-error", message); }
    else notice(message, "error");
  }
  function bind(id, handler, event) {
    $(id).addEventListener(event || "click", ev => { ev.preventDefault(); Promise.resolve().then(() => handler(ev)).catch(showError); });
  }
  async function exclusive(operation) {
    if (requestBusy) return;
    requestBusy = true; updateControls();
    try { return await operation(); } finally { requestBusy = false; updateControls(); }
  }
  function closePreview() {
    preview = null; previewWorkspace = "";
    if ($("preview-dialog").open) $("preview-dialog").close();
    for (const id of ["preview-clinical", "preview-full", "preview-hash", "preview-meta", "preview-error"]) setText(id, "");
    $("preview-system").value = ""; $("preview-user").value = "";
  }
  function clearWorkspaceLocal(key) {
    const workspace = workspaces[key];
    clearTimeout(workspace.timer);
    workspace.data.value = ""; workspace.question.value = "";
    workspace.revision = 0; workspace.syncedRevision = -1; workspace.dirty = false; workspace.modified = false;
    workspace.answerText = ""; workspace.answerSignature = "";
    workspace.answer.replaceChildren(); workspace.raw.textContent = "";
    workspace.answerStatus.textContent = "尚未生成";
    workspace.draftStatus.textContent = "";
  }
  function disconnect(title, message) {
    alive = false; token = ""; clearTimeout(pollTimer); closePreview();
    for (const key of Object.keys(workspaces)) clearWorkspaceLocal(key);
    for (const node of document.querySelectorAll("input,textarea")) node.value = "";
    $("patient-list").replaceChildren(); $("overview").replaceChildren(); $("developer-profiles").replaceChildren();
    $("profile-summary").replaceChildren(); $("model-catalog").replaceChildren();
    setText("selected-patient", "資料已清除"); setText("snapshot", ""); setText("notice-text", ""); setText("status", ""); setText("list-status", "");
    renderedPatients = ""; renderedOverview = ""; renderedProfiles = "";
    state = null; developer = null; browserReport = null; knownJobs.clear(); adoptAfterJobs.clear();
    $("copy-text").value = ""; if ($("copy-dialog").open) $("copy-dialog").close();
    $("app-shell").hidden = true; $("connection-screen").hidden = false;
    setText("connection-title", title || "重新開啟 WardLens"); setText("connection-message", message || "請從 WardLens 控制視窗按「開啟瀏覽器介面」。");
    clearClipboardIfSame();
  }
  function updateControls() {
    const busy = !alive || requestBusy || Boolean(state && state.busy);
    document.querySelectorAll("[data-idle]").forEach(node => { node.disabled = busy; });
    if (!busy) {
      const demo = $("demo").checked;
      $("username").disabled = demo; $("password").disabled = demo;
      $("logout-button").disabled = !state || !state.logged_in;
      $("search-button").disabled = !state || !state.logged_in;
      $("load-patient").disabled = !state || !state.selected_id;
      $("export-docx").disabled = $("export-csv").disabled = !state || !(state.patients || []).length;
      $("developer-open").disabled = !state || !state.settings.developer_mode;
      $("key-delete").disabled = $("connection-check").disabled = !state || !state.has_api_key;
    }
    for (const workspace of Object.values(workspaces)) {
      workspace.cancel.disabled = !alive || !state || !state.busy || !Object.values(state.answers || {}).some(answer => answer.status === "running");
      workspace.copyAnswer.disabled = !workspace.answerText;
    }
    $("browser-save").disabled = !browserReport;
    updatePreviewControls();
  }
  function selectTab(name) {
    for (const node of document.querySelectorAll("[data-tab]")) {
      const selected = node.dataset.tab === name;
      node.classList.toggle("active", selected);
      if (selected) node.setAttribute("aria-current", "page"); else node.removeAttribute("aria-current");
    }
    for (const tab of ["rounding", "emergency", "settings", "browser"]) $("tab-" + tab).hidden = tab !== name;
  }
  function workspaceValues(key) {
    const workspace = workspaces[key];
    return {data: workspace.data.value, question: workspace.question.value, workflow: workspace.workflow ? workspace.workflow.value : "emergency", profile: workspace.profile.value};
  }
  function markDraft(key) {
    if (!state || state.busy || !alive) return;
    const workspace = workspaces[key];
    workspace.revision = nextRevision(workspace.revision, state.drafts[key].revision);
    workspace.dirty = true; workspace.modified = true;
    workspace.draftStatus.textContent = "正在更新草稿…";
    workspace.answerText = ""; workspace.answerSignature = ""; workspace.answer.replaceChildren(element("p", "資料或問題已修改，請重新生成。", "answer-placeholder")); workspace.raw.textContent = "";
    if (previewWorkspace === key) closePreview();
    clearTimeout(workspace.timer);
    workspace.timer = setTimeout(() => flushDraft(key).catch(error => notice(error.message, "error")), 400);
  }
  async function flushDraft(key) {
    const workspace = workspaces[key];
    clearTimeout(workspace.timer);
    // Serialize updates so an older slow request cannot arrive after a newer one.
    const epoch = state && state.epoch;
    workspace.pending = workspace.pending.catch(() => {}).then(async () => {
      if (!alive || !state || state.epoch !== epoch || !workspace.dirty) return;
      const revision = workspace.revision, values = workspaceValues(key);
      await action("draft", {epoch, workspace: key, revision, ...values});
      if (state && state.epoch === epoch && workspace.revision === revision) {
        workspace.dirty = false; workspace.syncedRevision = revision;
        workspace.draftStatus.textContent = "本次工作階段的草稿已更新";
      }
    });
    await workspace.pending;
    if (workspace.dirty && state && state.epoch === epoch) return flushDraft(key);
  }
  function createWorkspace(key) {
    const emergency = key === "emergency", section = element("section", null, "workspace");
    const heading = element("div", null, "workspace-heading"), title = element("div");
    title.append(element("h2", emergency ? "目前發生什麼事？" : "整理重點，或問一個問題"));
    title.append(element("p", emergency ? "輸入床邊變化與已做處置，產生急症 cognitive aid。" : "可先編輯帶入資料，再選擇查房整理、Admission Note 或自由提問。", "muted"));
    const options = element("div", null, "workspace-options");
    let workflow = null;
    if (!emergency) {
      const label = element("label", "工作"); workflow = idle(element("select")); workflow.id = key + "-workflow";
      for (const name of ["rounding", "admission", "qa"]) { const option = element("option", workflowLabels[name]); option.value = name; workflow.append(option); }
      label.append(workflow); options.append(label);
    }
    const profileLabel = element("label", "模型模式"), profile = idle(element("select")); profile.id = key + "-profile"; profileLabel.append(profile); options.append(profileLabel);
    heading.append(title, options); section.append(heading);
    const context = element("details", null, "context-editor"), contextSummary = element("summary", "編輯病人資料（只影響本次 AI input）");
    const data = idle(element("textarea")); data.id = key + "-data"; data.rows = 10; data.spellcheck = false; data.setAttribute("aria-label", "可編輯病人資料");
    const contextActions = element("div", null, "button-row");
    contextActions.append(idle(button("清空此頁資料", key + "-clear", () => {
      if (data.value && !window.confirm("清空這一頁的資料框？問題會保留，原始病歷仍可在來源明細核對。")) return;
      data.value = ""; markDraft(key);
    }, "quiet")));
    context.append(contextSummary, data, contextActions); section.append(context);
    const questionLabel = element("label", emergency ? "病況 / 已做處置 / 想確認的問題" : "問題或特別想追蹤的事", "question-label"); questionLabel.htmlFor = key + "-question";
    const question = idle(element("textarea", null, "question-box")); question.id = key + "-question"; question.rows = emergency ? 4 : 3;
    question.placeholder = emergency ? "例如：突然喘、目前生命徵象、氧氣裝置、已做處置與反應…" : "留白可整理今日查房重點；或輸入想確認的趨勢、報告、待辦…";
    section.append(questionLabel, question);
    const actions = element("div", null, "workspace-actions"), buttons = element("div", null, "button-row");
    const prepare = idle(button("預覽並送出", key + "-prepare", () => handlePreview(key, "send"), "primary"));
    const copyInput = idle(button("複製給其他 AI", key + "-copy-input", () => handlePreview(key, "copy")));
    const cancel = button("停止生成", key + "-cancel", () => action("cancel").then(refreshState).catch(error => notice(error.message, "error")), "quiet danger-text");
    buttons.append(prepare, copyInput, cancel);
    const draftStatus = element("span", "", "draft-status"); actions.append(buttons, draftStatus); section.append(actions);
    const answerSection = element("section", null, "answer"), answerHeading = element("div", null, "answer-heading"), tools = element("div", null, "answer-tools");
    answerHeading.append(element("h3", "AI 回答"));
    const citationLabel = element("label", null, "check"), citations = element("input"); citations.type = "checkbox"; citations.id = key + "-citations"; citationLabel.append(citations, document.createTextNode("顯示來源標記"));
    const copyAnswer = button("複製純文字", key + "-copy-answer", () => copyText(md.toPlainText(workspaces[key].answerText, {hideCitations: !citations.checked})).catch(error => notice(error.message, "error")), "quiet");
    tools.append(citationLabel, copyAnswer); answerHeading.append(tools);
    const answerStatus = element("p", "尚未生成", "answer-status"), answer = element("article", null, "markdown"); answer.id = key + "-answer"; answer.setAttribute("aria-label", "AI 回答內容");
    answer.append(element("p", "回答會顯示在這裡。", "answer-placeholder"));
    const rawDetails = element("details", null, "answer-raw"), raw = element("pre"); rawDetails.append(element("summary", "Markdown 原文"), raw);
    answerSection.append(answerHeading, answerStatus, answer, rawDetails); section.append(answerSection);
    $(key + "-workspace").append(section);
    workspaces[key] = {data, question, workflow, profile, cancel, copyAnswer, citations, answer, raw, answerStatus, draftStatus, revision: 0, syncedRevision: -1, dirty: false, modified: false, timer: null, pending: Promise.resolve(), answerText: "", answerSignature: ""};
    for (const field of [data, question]) field.addEventListener("input", () => markDraft(key));
    for (const field of [workflow, profile].filter(Boolean)) field.addEventListener("change", () => markDraft(key));
    citations.addEventListener("change", () => { workspaces[key].answerSignature = ""; if (state) renderAnswer(key, state.answers[key]); });
  }
  function hasDraftEdits() { return Object.values(workspaces).some(workspace => workspace.modified || workspace.question.value.trim()); }
  function renderPatients() {
    const query = $("patient-filter").value.trim().toLowerCase();
    const signature = JSON.stringify([state.patients, state.selected_id, query]);
    if (renderedPatients === signature) return;
    renderedPatients = signature;
    const list = $("patient-list"), nodes = [];
    for (const patient of state.patients || []) {
      if (query && ![patient.name, patient.location, patient.histno].join(" ").toLowerCase().includes(query)) continue;
      const node = idle(button("", null, () => exclusive(async () => {
        if (patient.id === state.selected_id) return;
        if (hasDraftEdits() && !window.confirm("切換病人會清除兩頁的編輯、問題與回答。確定切換？")) return;
        for (const workspace of Object.values(workspaces)) clearTimeout(workspace.timer);
        await Promise.all(Object.values(workspaces).map(workspace => workspace.pending.catch(() => {})));
        await action("select", {epoch: state.epoch, patient_id: patient.id}); closePreview(); await refreshState();
      }).catch(error => notice(error.message, "error")), "patient-button" + (patient.id === state.selected_id ? " active" : "")));
      node.dataset.patientId = patient.id; node.setAttribute("role", "listitem");
      node.setAttribute("aria-label", [patient.location, patient.name, patient.age, patient.sex].filter(Boolean).join(" "));
      if (patient.id === state.selected_id) node.setAttribute("aria-current", "true");
      const name = element("span", patient.name || "未提供姓名", "patient-name");
      if (patient.loaded) name.append(element("span", "已載入", "loaded-indicator"));
      node.append(name, element("span", patient.location || "床位未辨識", "patient-location"), element("span", [patient.histno, patient.age ? patient.age + " 歲" : "", patient.sex].filter(Boolean).join(" · "), "patient-meta"));
      nodes.push(node);
    }
    list.replaceChildren(...nodes);
    if (!nodes.length) list.append(element("p", query ? "沒有符合的病人。" : "登入後載入病人清單。", "muted"));
  }
  function shortList(title, values, empty, maximum) {
    const section = element("section", null, "brief-section"); section.append(element("h3", title));
    if (!values || !values.length) { section.append(element("p", empty, "muted")); return section; }
    const list = element("ul"); values.slice(0, maximum || 4).forEach(value => list.append(element("li", value)));
    section.append(list);
    if (values.length > (maximum || 4)) {
      const detail = element("details"), rest = element("ul"); detail.append(element("summary", "另 " + (values.length - (maximum || 4)) + " 項"));
      values.slice(maximum || 4).forEach(value => rest.append(element("li", value))); detail.append(rest); section.append(detail);
    }
    return section;
  }
  function sparkline(row) {
    const points = validPlotPoints(row);
    if (!points.length) return null;
    const xmin = points[0].x, xmax = points[points.length - 1].x;
    const ys = points.map(point => point.y), ymin = Math.min(...ys), ymax = Math.max(...ys);
    const plot = points.map(point => [4 + (point.x - xmin) / (xmax - xmin) * 102, ymax === ymin ? 13 : 23 - (point.y - ymin) / (ymax - ymin) * 20]);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 110 26"); svg.setAttribute("class", "sparkline"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", row.label + "數值趨勢；橫軸依實際時間間隔");
    const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline"); line.setAttribute("points", plot.map(point => point.join(",")).join(" ")); svg.append(line);
    plot.forEach(point => { const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle"); dot.setAttribute("cx", String(point[0])); dot.setAttribute("cy", String(point[1])); dot.setAttribute("r", "2"); svg.append(dot); });
    return svg;
  }
  function trendTable(rows) {
    const wrapper = element("div", null, "table-scroll"), table = element("table"), head = element("thead"), titles = element("tr"), body = element("tbody");
    for (const title of ["項目", "較早 → 最近", "最新提供值", "涵蓋期間", "明細"]) { const cell = element("th", title); cell.scope = "col"; titles.append(cell); }
    head.append(titles); table.append(head);
    for (const row of rows) {
      const presentation = trendPresentation(row), values = presentation.known, latest = presentation.latest;
      const uncertain = presentation.unknown.length > 0;
      const tr = element("tr"), label = element("td", row.label, "trend-label"); label.append(element("span", row.unit || "單位未提供", "trend-unit"));
      const trend = element("td"), chain = element("div", null, "trend-chain");
      values.forEach((point, index) => { if (index) chain.append(element("span", presentation.separators[index], "trend-arrow")); const value = element("span", point.value); value.title = point.time || "時間未辨識"; chain.append(value); });
      if (!values.length) chain.append(document.createTextNode("未辨識到可比較數值"));
      trend.append(chain); const chart = sparkline(row); if (chart) trend.append(chart);
      if (presentation.hasConflicts) trend.append(element("p", "同時點數值衝突，請核對", "trend-span warning"));
      if (uncertain) trend.append(element("p", "時間未明：" + presentation.unknown.map(point => point.value).join(" / "), "trend-span warning"));
      const last = element("td", latest ? latest.value : presentation.latestConflict ? "數值衝突，請核對" : "時間未明", "trend-last"); if (latest) last.title = latest.time || "時間未辨識";
      if (uncertain) last.append(element("span", "另有時間未明值", "trend-unit"));
      const span = element("td", row.span || "時間範圍未建立", "trend-span");
      if (latest && latest.time) span.append(element("p", "最後量測：" + latest.time));
      else if (presentation.latestConflict && presentation.lastKnownTime) span.append(element("p", "同時點衝突：" + presentation.lastKnownTime));
      if (row.note) span.append(element("p", row.note));
      const detailsCell = element("td"), details = element("details", null, "trend-details"), entries = element("ul"); details.append(element("summary", "時間 / 來源"));
      for (const point of row.all_values || values) {
        const li = element("li", (point.time || "時間未辨識") + "｜" + point.value + (row.unit ? " " + row.unit : ""));
        if (point.source_id) li.append(element("code", point.source_id)); entries.append(li);
      }
      details.append(entries); detailsCell.append(details); tr.append(label, trend, last, span, detailsCell); body.append(tr);
    }
    table.append(body); wrapper.append(table); return wrapper;
  }
  function renderOverview(view) {
    const signature = JSON.stringify([state.epoch, view]);
    if (signature === renderedOverview) return;
    renderedOverview = signature;
    const target = $("overview");
    if (!view) { const empty = element("div", null, "empty-state"); empty.append(element("h2", state.selected_id ? "載入資料後，先看今天的變化" : "先選取病人"), element("p", "檢驗表、待辦與原始來源會顯示在這裡。未載入不代表沒有異常。")); target.replaceChildren(empty); return; }
    const nodes = [];
    if (view.vitals && view.vitals.length) {
      const strip = element("div", null, "vitals-strip");
      for (const row of view.vitals) {
        const presentation = trendPresentation(row), latest = presentation.latest, cell = element("div", null, "vital");
        const value = element("div", latest ? latest.value : presentation.latestConflict ? "數值衝突，請核對" : presentation.unknown.length ? presentation.unknown.map(point => point.value).join(" / ") : "未提供", "vital-value"); if (row.unit && !presentation.latestConflict) value.append(element("small", row.unit));
        cell.append(element("div", row.label, "vital-label"), value, element("div", latest && latest.time ? latest.time : presentation.latestConflict ? presentation.lastKnownTime || "同時點數值衝突" : "量測時間未辨識", "vital-time"));
        if (presentation.hasConflicts && !presentation.latestConflict) cell.append(element("div", "同時點數值衝突，請核對", "vital-time warning"));
        if (presentation.unknown.length) cell.append(element("div", latest ? "時間未明另有：" + presentation.unknown.map(point => point.value).join(" / ") : "請展開來源核對時間", "vital-time warning"));
        strip.append(cell);
      }
      nodes.push(strip);
    }
    if (view.warnings && view.warnings.length) {
      const details = element("details", null, "overview-warnings"), list = element("ul");
      details.append(element("summary", "資料涵蓋與缺漏提醒（" + view.warnings.length + "）"));
      view.warnings.forEach(warning => list.append(element("li", warning))); details.append(list); nodes.push(details);
    }
    const briefs = element("div", null, "brief-grid");
    briefs.append(shortList(view.todo_kind === "excerpt" ? "入院紀錄頂端摘錄" : "待辦 / 追蹤", view.todos, "未辨識到明確待辦，請核對入院紀錄。", 5), shortList("近期報告", view.reports, "未取得報告內容，不代表沒有報告。", 4), shortList("用藥紀錄", view.medications, "尚無可讀用藥紀錄。", 4)); nodes.push(briefs);
    const rows = view.trends || [], cbc = rows.filter(row => row.group === "cbc"), chemistry = rows.filter(row => row.group === "chemistry"), other = rows.filter(row => !["cbc", "chemistry"].includes(row.group));
    for (const [title, selected] of [["血球 · WBC / Hb / PLT", cbc], ["生化與發炎指標", chemistry]]) {
      if (!selected.length) continue;
      const section = element("section", null, "trend-section"); section.append(element("h3", title), trendTable(selected)); nodes.push(section);
    }
    if (other.length) { const details = element("details", null, "section-details"); details.append(element("summary", "Differential / 其他檢驗（" + other.length + " 項）"), trendTable(other)); nodes.push(details); }
    if (!rows.length) nodes.push(element("p", "未可靠辨識出趨勢表；可展開原始來源核對，AI 不會替表格補造數值。", "muted"));
    const sources = element("details", null, "source-details"); sources.append(element("summary", "原始資料與完整時間（" + (view.sources || []).length + " 份來源）"));
    for (const source of view.sources || []) {
      const record = element("details", null, "source-record"); record.append(element("summary", [source.title || source.type, source.time].filter(Boolean).join(" · ")), element("p", [source.id, source.type, source.status].filter(Boolean).join(" · "), "muted"), element("pre", source.content || "未提供內容")); sources.append(record);
    }
    nodes.push(sources); target.replaceChildren(...nodes);
  }
  function adoptDraft(key, incoming, force) {
    if (!incoming) return;
    const workspace = workspaces[key];
    if (!shouldAdoptDraft(incoming.revision, workspace.revision, workspace.dirty, force)) return;
    clearTimeout(workspace.timer);
    workspace.data.value = incoming.data || ""; workspace.question.value = incoming.question || "";
    if (workspace.workflow) workspace.workflow.value = incoming.workflow || "rounding";
    workspace.profile.value = incoming.profile || (key === "emergency" ? "emergency_fast" : "fast");
    workspace.revision = Number(incoming.revision) || 0; workspace.syncedRevision = workspace.revision;
    workspace.dirty = false; workspace.modified = false; workspace.draftStatus.textContent = "";
  }
  function renderAnswer(key, answer) {
    const workspace = workspaces[key];
    if (workspace.dirty) return;
    answer = answer || {text: "", status: "idle"};
    const signature = JSON.stringify([answer, workspace.citations.checked]);
    if (workspace.answerSignature === signature) return;
    workspace.answerSignature = signature; workspace.answerText = answer.text || "";
    workspace.answerStatus.textContent = [answerLabels[answer.status] || "尚未生成", answer.model || "", answer.error || ""].filter(Boolean).join(" · ");
    workspace.answerStatus.className = "answer-status " + (answer.status || "idle");
    workspace.raw.textContent = workspace.answerText;
    if (workspace.answerText) md.render(workspace.answer, workspace.answerText, {hideCitations: !workspace.citations.checked});
    else workspace.answer.replaceChildren(element("p", answer.status === "running" ? "模型正在處理，文字會逐步出現在這裡。" : "回答會顯示在這裡。", "answer-placeholder"));
  }
  function renderProfiles(profiles) {
    const signature = JSON.stringify(profiles);
    if (signature === renderedProfiles) return;
    renderedProfiles = signature;
    for (const [key, workspace] of Object.entries(workspaces)) {
      const selected = workspace.profile.value || (state.drafts[key] && state.drafts[key].profile) || (key === "emergency" ? "emergency_fast" : "fast");
      workspace.profile.replaceChildren(...profiles.map(profile => { const option = element("option", profile.label); option.value = profile.key; option.title = profile.model; return option; }));
      workspace.profile.value = selected;
    }
    const list = element("div", null, "profile-list");
    for (const profile of profiles) {
      const row = element("div", null, "profile-line"); row.append(element("strong", profile.label), element("code", profile.model), element("small", profile.reasoning_effort + " · " + (profile.max_tokens == null ? "供應商預設" : profile.max_tokens + " tokens"))); list.append(row);
    }
    $("profile-summary").replaceChildren(list);
  }
  function processJob(job) {
    if (!job || job.status === "running" || knownJobs.has(job.id)) return false;
    knownJobs.add(job.id);
    if (knownJobs.size > 40) knownJobs.delete(knownJobs.values().next().value);
    const adopt = adoptAfterJobs.delete(job.id) && job.status === "complete";
    if (job.status === "failed") notice(job.error || "操作失敗，請重試。", "error");
    else if (job.status === "complete" && job.result) {
      const result = job.result;
      if (result.format && String(result.format).startsWith("WardLens-Browser-Diagnostics")) {
        browserReport = result; setText("browser-report", JSON.stringify(result, null, 2));
      } else if (Array.isArray(result.models)) {
        $("model-catalog").replaceChildren(...result.models.map(model => { const item = element("option", model.name || model.id); item.value = model.id; return item; }));
        setText("catalog-status", "已載入 " + result.models.length + " 個模型");
      } else if (typeof result.text === "string") notice(result.text, "success");
    }
    return adopt;
  }
  async function refreshState() {
    const sequence = ++requestSequence;
    const incoming = await api("/api/state");
    if (!alive || sequence < appliedSequence) return;
    if (state && incoming.epoch < state.epoch) return;
    appliedSequence = sequence;
    const epochChanged = !state || state.epoch !== incoming.epoch;
    const oldSettings = state && JSON.stringify(state.settings), previousLoggedIn = Boolean(state && state.logged_in);
    state = incoming;
    const jobNeedsAdoption = processJob(state.job), forceDrafts = epochChanged || jobNeedsAdoption;
    if (epochChanged) {
      closePreview(); for (const key of Object.keys(workspaces)) clearWorkspaceLocal(key);
      renderedOverview = ""; renderedPatients = "";
    }
    if (oldSettings && oldSettings !== JSON.stringify(state.settings)) closePreview();
    setText("version", "v" + state.version); setText("status", state.status);
    setText("login-state", state.logged_in ? (state.demo ? "Demo 已登入" : "院內系統已登入") : "登入");
    setText("list-status", state.list_status);
    if (!demoDirty) $("demo").checked = Boolean(state.demo);
    if (state.logged_in && !previousLoggedIn) $("login-panel").open = false;
    if (state.selected) setText("selected-patient", [state.selected.location, state.selected.name, state.selected.age ? state.selected.age + " 歲" : "", state.selected.sex].filter(Boolean).join(" · "));
    else setText("selected-patient", "選取病人，開始整理");
    const minutes = state.snapshot_age_minutes;
    setText("snapshot", minutes == null ? "尚未載入這位病人的資料；可直接輸入病況提問" : "資料擷取於 " + minutes + " 分鐘前 · 生命徵象以實際量測時間為準");
    $("snapshot").className = minutes != null && minutes >= 15 ? "muted warning" : "muted";
    renderProfiles(state.profiles || []);
    for (const key of Object.keys(workspaces)) { adoptDraft(key, state.drafts[key], forceDrafts); renderAnswer(key, state.answers[key]); }
    renderPatients(); renderOverview(state.overview);
    if (!settingsDirty) {
      $("external-ai").checked = Boolean(state.settings.external_ai_enabled); $("privacy-ack").checked = Boolean(state.settings.privacy_acknowledged);
      $("require-zdr").checked = Boolean(state.settings.require_zdr); $("developer-mode").checked = Boolean(state.settings.developer_mode);
    }
    if (!lookbackDirty || jobNeedsAdoption) { $("lookback").value = String(state.settings.lab_lookback_months || 3); lookbackDirty = false; }
    setText("key-state", state.has_api_key ? "Key 已儲存" : "尚未設定 Key");
    if (!state.settings.developer_mode) { $("developer-editor").hidden = true; developer = null; }
    updateControls();
  }
  async function poll() {
    if (!alive) return;
    try { await refreshState(); }
    catch (error) { if (alive) disconnect("本機連線已中斷", error.message + " 請由控制視窗重新開啟介面。"); }
    if (alive) pollTimer = setTimeout(poll, state && state.busy ? 700 : 5000);
  }
  async function startJob(name, payload, adopt) {
    const result = await action(name, payload || {});
    if (adopt && result.job_id) adoptAfterJobs.add(result.job_id);
    await refreshState();
    clearTimeout(pollTimer); if (alive) pollTimer = setTimeout(poll, 500);
    return result;
  }
  function displayPreview(result, key, mode) {
    preview = result; previewWorkspace = key; previewMode = mode;
    const blocked = Boolean(result.blocked);
    setText("preview-title", blocked ? "先修正疑似識別內容" : mode === "copy" ? "檢查要複製給其他 AI 的 input" : "確認將送出的資料");
    setText("preview-meta", [result.model || "", result.snapshot_age_minutes == null ? "手動提供資料" : "擷取約 " + result.snapshot_age_minutes + " 分鐘前"].filter(Boolean).join(" · "));
    $("preview-error").hidden = !blocked;
    setText("preview-error", blocked ? (result.error || "內容仍有疑似識別資料，請修正後重新檢查。") + (result.risks && result.risks.length ? "\n" + result.risks.join("、") : "") : "");
    $("preview-clinical").hidden = blocked; setText("preview-clinical", result.clinical || "");
    $("repair-user-section").hidden = !blocked; $("preview-user").value = result.user || "";
    $("preview-system").value = result.system || ""; $("preview-system").readOnly = !blocked;
    $("preview-system-details").open = blocked;
    $("preview-full-details").hidden = blocked; $("preview-full-details").open = false;
    setText("preview-full", "WARDLENS-PROMPT-V1\n<SYSTEM>\n" + (result.system || "").trim() + "\n</SYSTEM>\n<USER>\n" + (result.user || "").trim() + "\n</USER>");
    setText("preview-hash", result.sha256 ? "SHA-256 " + result.sha256 : "");
    $("preview-consent").hidden = blocked;
    for (const id of ["confirm-review", "confirm-stale", "confirm-slow", "confirm-zdr"]) $(id).checked = false;
    $("stale-row").hidden = !(result.snapshot_age_minutes >= 15) || mode === "copy";
    setText("stale-text", "資料已約 " + result.snapshot_age_minutes + " 分鐘；確認仍使用這份 snapshot");
    $("slow-row").hidden = !result.slow || mode === "copy";
    $("zdr-row").hidden = result.require_zdr !== false || mode === "copy";
    $("preview-repair").hidden = !blocked; $("preview-copy").hidden = blocked;
    $("preview-send").hidden = blocked || mode === "copy";
    setText("preview-help", blocked ? "請遮罩疑似識別資料。背景 prompt 也已展開供檢查；重新檢查通過前不會送出。" : "請閱讀本次去識別資料，確認後再送出或複製。背景 prompt 與完整 input 可展開核對。");
    setText("preview-destination", mode === "copy" ? "複製的是本次已檢查的完整 input。貼到其他服務前請再次確認資料使用權限。" : "確認送出後，內容將經 OpenRouter 與上游模型處理。");
    if (!$("preview-dialog").open) $("preview-dialog").showModal();
    updatePreviewControls();
  }
  function updatePreviewControls() {
    if (!preview) return;
    const busy = requestBusy || Boolean(state && state.busy);
    $("preview-repair").disabled = busy;
    $("preview-copy").disabled = busy || !$("confirm-review").checked;
    $("preview-send").disabled = busy || !$("confirm-review").checked ||
      (!$("stale-row").hidden && !$("confirm-stale").checked) || (!$("slow-row").hidden && !$("confirm-slow").checked) || (!$("zdr-row").hidden && !$("confirm-zdr").checked);
  }
  async function handlePreview(key, mode) {
    try {
      await exclusive(async () => {
        await flushDraft(key);
        const result = await action("prepare", {epoch: state.epoch, workspace: key, revision: workspaces[key].revision});
        displayPreview(result, key, mode);
      });
    } catch (error) { notice(error.message, "error"); }
  }
  async function copyText(text) {
    if (!text) return;
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);
      clipboardValue = text; clearTimeout(clipboardTimer);
      clipboardTimer = setTimeout(clearClipboardIfSame, 60000);
      notice("已複製；60 秒後會在剪貼簿仍未變更時嘗試清除。", "success");
    } catch (_) {
      $("copy-text").value = text; $("copy-dialog").showModal(); $("copy-text").focus(); $("copy-text").select();
    }
  }
  async function clearClipboardIfSame() {
    const previous = clipboardValue; clipboardValue = null; clearTimeout(clipboardTimer);
    if (!previous || !navigator.clipboard || !navigator.clipboard.readText) return;
    try { if (await navigator.clipboard.readText() === previous) await navigator.clipboard.writeText(""); } catch (_) { /* Browser permission may deny a background read. */ }
  }
  function download(name, mime, content) {
    const blob = new Blob([content], {type: mime}), url = URL.createObjectURL(blob), anchor = element("a");
    anchor.href = url; anchor.download = String(name).replace(/[\\/]/g, "_"); document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function exportList(kind) {
    if (!window.confirm("匯出檔包含姓名、病歷號與床位。請儲存於院方允許的位置；確認匯出？")) return;
    await exclusive(async () => {
      const result = await action("export", {kind, confirm: true});
      const bytes = Uint8Array.from(atob(result.data_base64), char => char.charCodeAt(0));
      download(result.filename, result.mime, bytes);
    });
  }
  function rememberPrompt() { if (developer) developer.workingPrompts[promptSelection] = $("prompt-editor").value; }
  function editorConfig() {
    if (!developer) throw new ApiError("請先開啟開發者編輯器。", 400);
    rememberPrompt();
    const profiles = {};
    for (const [key, inputs] of Object.entries(developer.inputs)) {
      const raw = inputs.tokens.value.trim(), number = raw === "" || raw === "0" ? null : Number(raw);
      if (number !== null && (!Number.isSafeInteger(number) || number <= 0)) throw new ApiError("Max tokens 請留白、填 0，或輸入正整數。", 400);
      profiles[key] = {model: inputs.model.value.trim(), reasoning_effort: inputs.effort.value, max_tokens: number};
    }
    return buildDeveloperConfig(developer.config, developer.initialProfiles, profiles, developer.prompts, developer.workingPrompts);
  }
  async function openDeveloper() {
    const result = await action("developer_get");
    developer = {config: clone(result.config), prompts: clone(result.prompts), workingPrompts: clone(result.prompts), initialProfiles: {}, inputs: {}};
    const table = element("table"), head = element("thead"), header = element("tr"), body = element("tbody");
    for (const title of ["模式", "OpenRouter 模型 ID", "Reasoning", "Max tokens"]) header.append(element("th", title));
    head.append(header); table.append(head);
    for (const profile of result.profiles || state.profiles) {
      developer.initialProfiles[profile.key] = {model: profile.model, reasoning_effort: profile.reasoning_effort, max_tokens: profile.max_tokens};
      const row = element("tr"), modelCell = element("td"), effortCell = element("td"), tokensCell = element("td");
      const model = idle(element("input")); model.value = profile.model; model.setAttribute("list", "model-catalog"); model.setAttribute("aria-label", profile.label + " 模型 ID"); model.spellcheck = false;
      const effort = idle(element("select")); effort.setAttribute("aria-label", profile.label + " reasoning");
      for (const value of ["none", "low", "medium", "high", "xhigh", "max"]) { const option = element("option", value); option.value = value; effort.append(option); } effort.value = profile.reasoning_effort;
      const tokens = idle(element("input")); tokens.type = "number"; tokens.min = "0"; tokens.step = "1"; tokens.placeholder = "供應商預設"; tokens.value = profile.max_tokens == null ? "" : String(profile.max_tokens); tokens.setAttribute("aria-label", profile.label + " max tokens");
      for (const input of [model, effort, tokens]) input.addEventListener("input", () => { developerDirty = true; });
      developer.inputs[profile.key] = {model, effort, tokens}; modelCell.append(model); effortCell.append(effort); tokensCell.append(tokens); row.append(element("td", profile.label), modelCell, effortCell, tokensCell); body.append(row);
    }
    table.append(body); $("developer-profiles").replaceChildren(table);
    promptSelection = $("prompt-workflow").value; $("prompt-editor").value = developer.workingPrompts[promptSelection] || "";
    developerDirty = false; $("developer-editor").hidden = false; updateControls();
  }

  createWorkspace("rounding"); createWorkspace("emergency");
  document.querySelectorAll("[data-tab]").forEach(node => node.addEventListener("click", () => selectTab(node.dataset.tab)));
  bind("dismiss-notice", () => notice(""));
  bind("demo", () => { demoDirty = true; updateControls(); }, "change");
  $("lookback").addEventListener("change", () => { lookbackDirty = true; });
  bind("login-form", () => exclusive(async () => {
    if (state && state.logged_in && !window.confirm("重新登入會清除目前病人與草稿，確定繼續？")) return;
    const payload = {demo: $("demo").checked, username: $("username").value.trim(), password: $("password").value};
    $("password").value = ""; closePreview(); await startJob("login", payload, true);
  }), "submit");
  bind("logout-button", () => exclusive(async () => {
    if (!window.confirm("登出並清除病人資料、問題與回答？")) return;
    await action("logout"); $("username").value = ""; $("password").value = ""; $("histnos").value = ""; $("patient-filter").value = ""; closePreview(); clearClipboardIfSame(); await refreshState(); $("login-panel").open = true;
  }));
  bind("search-form", () => exclusive(async () => {
    if (hasDraftEdits() && !window.confirm("重新查詢會清除目前草稿與回答，確定繼續？")) return;
    closePreview(); await startJob("search", {doctor_id: $("doctor-id").value.trim(), ward: $("ward").value.trim(), histnos: $("histnos").value.trim()}, true);
  }), "submit");
  bind("patient-filter", () => { if (state) { renderPatients(); updateControls(); } }, "input");
  bind("load-patient", () => exclusive(async () => {
    if (Object.values(workspaces).some(workspace => workspace.modified) && !window.confirm("成功載入後會取代兩頁已編輯的資料，問題會保留。確定更新？")) return;
    await Promise.all(Object.keys(workspaces).map(flushDraft)); closePreview();
    await startJob("load", {epoch: state.epoch, months: Number($("lookback").value)}, true);
  }));
  bind("export-docx", () => exportList("docx")); bind("export-csv", () => exportList("csv"));
  bind("preview-close", closePreview);
  $("preview-dialog").addEventListener("cancel", event => { event.preventDefault(); closePreview(); });
  for (const id of ["confirm-review", "confirm-stale", "confirm-slow", "confirm-zdr"]) $(id).addEventListener("change", updatePreviewControls);
  bind("preview-repair", () => exclusive(async () => {
    const key = previewWorkspace, result = await action("repair", {epoch: state.epoch, workspace: key, revision: workspaces[key].revision, system: $("preview-system").value, user: $("preview-user").value});
    displayPreview(result, key, previewMode);
  }));
  bind("preview-send", () => exclusive(async () => {
    if (!preview || preview.blocked || !$("confirm-review").checked) return;
    const key = previewWorkspace;
    const payload = {epoch: state.epoch, workspace: key, revision: workspaces[key].revision, preview_id: preview.preview_id, sha256: preview.sha256, confirm: true, confirm_stale: $("confirm-stale").checked, confirm_slow: $("confirm-slow").checked, confirm_zdr_off: $("confirm-zdr").checked};
    await startJob("generate", payload, false); closePreview();
  }));
  bind("preview-copy", () => exclusive(async () => {
    if (!preview || preview.blocked || !$("confirm-review").checked) return;
    const result = await action("copy_prompt", {epoch: state.epoch, workspace: previewWorkspace, revision: workspaces[previewWorkspace].revision, preview_id: preview.preview_id, sha256: preview.sha256, confirm: true});
    closePreview(); await copyText(result.text);
  }));
  bind("copy-close", () => { $("copy-text").value = ""; $("copy-dialog").close(); });
  $("copy-dialog").addEventListener("cancel", () => { $("copy-text").value = ""; });
  bind("oauth", () => exclusive(() => startJob("oauth")));
  bind("connection-check", () => exclusive(() => startJob("connection")));
  bind("key-form", () => exclusive(async () => { const key = $("api-key").value.trim(); $("api-key").value = ""; if (!key) return; await startJob("key_save", {key}); }), "submit");
  bind("key-delete", () => exclusive(async () => { if (!window.confirm("移除本機儲存的 OpenRouter key？這不會撤銷 OpenRouter 帳戶上的 key。")) return; await action("key_delete"); await refreshState(); }));
  for (const id of ["external-ai", "privacy-ack", "require-zdr", "developer-mode"]) $(id).addEventListener("change", () => { settingsDirty = true; });
  bind("privacy-form", () => exclusive(async () => {
    await action("settings", {external_ai_enabled: $("external-ai").checked, privacy_acknowledged: $("privacy-ack").checked, require_zdr: $("require-zdr").checked, developer_mode: $("developer-mode").checked});
    settingsDirty = false; closePreview(); await refreshState(); notice("設定已儲存。", "success");
  }), "submit");
  bind("developer-open", () => exclusive(async () => { if (developerDirty && !window.confirm("重新開啟會放棄編輯器內未儲存的變更，繼續？")) return; await openDeveloper(); }));
  bind("prompt-workflow", () => { rememberPrompt(); promptSelection = $("prompt-workflow").value; $("prompt-editor").value = developer ? developer.workingPrompts[promptSelection] || "" : ""; }, "change");
  $("prompt-editor").addEventListener("input", () => { developerDirty = true; });
  bind("developer-save", () => exclusive(async () => { const config = editorConfig(); await action("developer_save", {config}); closePreview(); await refreshState(); await openDeveloper(); notice("模型與 prompt 已儲存，下一次送出會重新預覽。", "success"); }));
  bind("developer-export", () => { const config = editorConfig(); download("wardlens-developer-config.json", "application/json", JSON.stringify(config, null, 2)); });
  bind("developer-import", () => $("developer-file").click());
  bind("developer-file", () => exclusive(async () => {
    const file = $("developer-file").files[0]; $("developer-file").value = ""; if (!file) return;
    if (file.size > 1000000) throw new ApiError("設定檔不得超過 1 MB。", 400);
    let config; try { config = JSON.parse(await file.text()); } catch (_) { throw new ApiError("無法讀取 JSON 設定檔。", 400); }
    if (!window.confirm("匯入將取代目前自訂模型與 prompt，確定繼續？")) return;
    await action("developer_save", {config}); closePreview(); await refreshState(); await openDeveloper(); notice("設定已匯入。", "success");
  }), "change");
  bind("models-zdr", () => exclusive(() => startJob("models", {zdr: true})));
  bind("models-all", () => exclusive(() => startJob("models", {zdr: false})));
  bind("browser-probe", () => exclusive(() => startJob("browser_probe")));
  bind("browser-prepare", () => exclusive(() => startJob("browser_prepare")));
  bind("browser-save", () => { if (browserReport) download("WardLens-Browser-Diagnostics.txt", "text/plain;charset=utf-8", JSON.stringify(browserReport, null, 2) + "\n"); });
  bind("close-app", async () => {
    if (!window.confirm("結束 WardLens 並清除本次病人資料？")) return;
    try { await action("close"); disconnect("WardLens 已結束", "本次網頁中的病人資料已清除。下次請重新開啟 WardLens.exe。"); }
    catch (_) { disconnect("網頁資料已清除", "未能確認本機服務是否關閉，請回到 WardLens 控制視窗檢查並結束程式。"); }
  });
  window.addEventListener("pagehide", () => disconnect("重新開啟 WardLens", "請從 WardLens 控制視窗重新開啟介面。"));
  window.addEventListener("pageshow", event => { if (event.persisted) disconnect("重新開啟 WardLens", "為避免恢復過期病人畫面，請從控制視窗重新開啟介面。"); });
  if (!token) disconnect("開啟 WardLens", "請從 WardLens 控制視窗按「開啟瀏覽器介面」。重新整理網頁後，也請由控制視窗重新開啟。");
  else poll();
})();
