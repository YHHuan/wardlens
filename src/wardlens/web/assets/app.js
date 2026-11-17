(function () {
  "use strict";

  // Testable helpers are exported for development; clinical state stays private.
  function nextRevision(local, remote) { return Math.max(Number(local) || 0, Number(remote) || 0) + 1; }
  function shouldAdoptDraft(remote, local, dirty, force) { return Boolean(force || (!dirty && Number(remote) > Number(local))); }
  function shouldRenderAnswer(remote, local, dirty) { return !dirty && Number.isSafeInteger(remote) && remote === local; }
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
    if (!row || row.chartable !== true || row.has_time_conflict === true) return [];
    const observations = row.all_values || row.values || [];
    // The overview keeps only a few recent values; the chart must retain the full window.
    // Never combine unlike specimens/units or turn a comparator into an exact value.
    for (const field of ["unit", "specimen", "method", "time_basis"]) {
      const identities = new Set(observations.map(point => String(point[field] || row[field] || "").trim()));
      if (identities.size > 1) return [];
    }
    if (observations.some(point => {
      if (point.value == null) return false;
      // The backend validates clinical suffixes (units and H/L flags). Retain
      // those labels, but require the numeric prefix to agree with its value.
      const number = String(point.value).trim().match(/^[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?(?=\s|$)/i);
      return !number || Number(number[0].replace(/,/g, "")) !== point.numeric;
    })) return [];
    const points = observations.map(point => ({x: Date.parse(point.timestamp || ""), y: point.numeric, observation: point})).sort((a, b) => a.x - b.x);
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
    const hasTimeConflict = row.has_time_conflict === true;
    const hasConflicts = hasTimeConflict || [...observations.values()].some(values => values.size > 1);
    const separators = known.map((point, index) => !index ? "" : !hasTimeConflict && Date.parse(point.timestamp) > Date.parse(known[index - 1].timestamp) ? "→" : "/");
    const latestConflict = Object.prototype.hasOwnProperty.call(row, "latest_known") && latest === null && allKnown.length > 0;
    const lastKnownTime = allKnown.length ? allKnown[allKnown.length - 1].time || "" : "";
    return {known, unknown, latest, separators, hasConflicts, hasTimeConflict, latestConflict, lastKnownTime};
  }
  function sortedTrendRows(rows, query) {
    const tokens = String(query || "").trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const priority = row => ({wbc: 0, hb: 1, hgb: 1, hemoglobin: 1, plt: 2, platelet: 2, platelets: 2}[String(row.label).toLowerCase()] ?? 3);
    return (rows || []).filter(row => tokens.every(token => [row.label, row.unit, row.specimen, row.method, row.reference].filter(Boolean).join(" ").toLocaleLowerCase().includes(token)))
      .map((row, index) => ({row, index})).sort((a, b) => priority(a.row) - priority(b.row) || a.index - b.index).map(item => item.row);
  }
  function privacyPresentation(privacy) {
    if (!privacy || (!privacy.automatic && !privacy.checked)) return {text: "目前內容尚未重新檢查；預覽前會自動去識別化", warning: true};
    const count = Number.isSafeInteger(privacy.replacement_count) && privacy.replacement_count >= 0 ? privacy.replacement_count : null;
    const residual = Number.isSafeInteger(privacy.residual_count) && privacy.residual_count >= 0 ? privacy.residual_count : null;
    return {text: (count === null ? "自動去識別已檢查" : "自動去識別已處理 " + count + " 項") + (residual > 0 ? "；疑似剩餘 " + residual + " 項需檢查" : "；送出前仍可預覽核對"), warning: residual > 0};
  }
  function coveragePresentation(coverage) {
    if (!coverage || !Object.keys(coverage).length) return null;
    const count = key => Number.isSafeInteger(coverage[key]) && coverage[key] >= 0 ? coverage[key] : null;
    const loaded = count("report_loaded"), found = count("report_candidates"), messages = [];
    if (loaded !== null) messages.push("已讀取 " + loaded + " 份報告" + (found === null ? "" : " / 發現 " + found + " 份"));
    const labLoaded = count("lab_categories_loaded"), labDiscovered = count("lab_categories_discovered");
    if (labLoaded !== null) messages.push("已讀取 " + labLoaded + (labDiscovered === null ? "" : " / " + labDiscovered) + " 類檢驗");
    else if (labDiscovered !== null) messages.push("已發現 " + labDiscovered + " 類檢驗");
    if (coverage.lab_categories_complete === false) messages.push("檢驗類別尚未讀取完整");
    for (const [key, label] of [["report_failed", "讀取失敗"], ["report_skipped", "未讀取"]]) if (count(key) > 0) messages.push(label + " " + count(key) + " 份");
    const warning = coverage.cancelled === true || count("report_failed") > 0 || count("report_skipped") > 0 || coverage.report_discovery_complete === false || coverage.report_details_complete === false || coverage.lab_categories_complete === false;
    const complete = !warning && coverage.report_discovery_complete === true && coverage.report_details_complete === true;
    return {title: coverage.cancelled ? "已停止載入 · 保留已讀取資料" : complete ? "本次報告擷取完成" : "本次資料涵蓋", text: messages.join(" · "), warning};
  }
  function renderSections(target, sections, renderText) {
    const doc = target.ownerDocument;
    const create = (tag, text) => { const node = doc.createElement(tag); if (text != null) node.textContent = String(text); return node; };
    for (const section of sections || []) {
      if (section.kind === "table") {
        const rows = Array.isArray(section.rows) ? section.rows.filter(Array.isArray) : [], headers = Array.isArray(section.headers) ? section.headers : [];
        const width = rows.reduce((maximum, row) => Math.max(maximum, row.length), headers.length);
        if (!width) continue;
        const wrapper = create("div"), table = create("table"), head = create("thead"), header = create("tr"), body = create("tbody");
        wrapper.className = "table-scroll report-table";
        if (section.caption) table.appendChild(create("caption", section.caption));
        for (let i = 0; i < width; i++) { const cell = create("th", headers[i] || "欄 " + (i + 1) + "（原文未命名）"); cell.scope = "col"; header.appendChild(cell); }
        head.appendChild(header); table.appendChild(head);
        for (const row of rows) { const tr = create("tr"); for (let i = 0; i < width; i++) tr.appendChild(create("td", row[i] == null ? "" : row[i])); body.appendChild(tr); }
        table.appendChild(body); wrapper.appendChild(table); target.appendChild(wrapper);
      } else if (section.kind === "text" && section.text) {
        const text = create("div"); text.className = "report-text markdown";
        if (renderText) renderText(text, String(section.text));
        else { const paragraph = create("p", section.text); text.appendChild(paragraph); }
        target.appendChild(text);
      }
    }
  }
  if (typeof module === "object" && module.exports) module.exports = {nextRevision, shouldAdoptDraft, shouldRenderAnswer, buildDeveloperConfig, validPlotPoints, trendPresentation, sortedTrendRows, privacyPresentation, coveragePresentation, renderSections};
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
  const knownJobs = new Set(), adoptAfterJobs = new Set(), transformedDrafts = new Map(), sourceCache = new Map();
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
        if (response.status === 401 || ["InvalidHost", "InvalidOrigin", "CrossSiteRequest"].includes(result.code)) disconnect("連線授權已失效", "請從 WardLens 控制視窗按「開啟工作台」。");
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
    for (const id of ["preview-clinical", "preview-full", "preview-hash", "preview-meta", "preview-privacy", "preview-error"]) setText(id, "");
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
    workspace.privacyStatus.textContent = "載入後會自動去識別化 AI 資料";
    workspace.privacyStatus.className = "privacy-status";
  }
  function disconnect(title, message) {
    alive = false; token = ""; clearTimeout(pollTimer); closePreview();
    for (const key of Object.keys(workspaces)) clearWorkspaceLocal(key);
    for (const node of document.querySelectorAll("input,textarea")) node.value = "";
    $("patient-list").replaceChildren(); $("overview").replaceChildren(); $("developer-profiles").replaceChildren();
    $("profile-summary").replaceChildren(); $("model-catalog").replaceChildren();
    setText("selected-patient", "資料已清除"); setText("snapshot", ""); setText("notice-text", ""); setText("status", ""); setText("list-status", "");
    renderedPatients = ""; renderedOverview = ""; renderedProfiles = "";
    state = null; developer = null; browserReport = null; knownJobs.clear(); adoptAfterJobs.clear(); transformedDrafts.clear(); sourceCache.clear();
    $("copy-text").value = ""; if ($("copy-dialog").open) $("copy-dialog").close();
    $("app-shell").hidden = true; $("connection-screen").hidden = false;
    setText("connection-title", title || "重新開啟 WardLens"); setText("connection-message", message || "請從 WardLens 控制視窗按「開啟工作台」。");
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
    const loading = Boolean(state && state.busy && state.job && state.job.kind === "load");
    $("cancel-load").hidden = !loading;
    $("cancel-load").disabled = !alive || requestBusy || !loading;
    $("load-progress").hidden = !loading;
    setText("load-progress", loading ? state.status || "正在讀取檢驗與報告…" : "");
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
    workspace.privacyStatus.textContent = privacyPresentation(null).text;
    workspace.privacyStatus.className = "privacy-status warning";
    workspace.draftStatus.textContent = "正在更新草稿…";
    workspace.answerStatus.textContent = "輸入已更動，尚未生成";
    workspace.answerStatus.className = "answer-status idle";
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
    const privacyRow = element("div", null, "privacy-row"), privacyStatus = element("p", "載入後會自動去識別化 AI 資料", "privacy-status");
    privacyStatus.id = key + "-privacy"; privacyStatus.setAttribute("role", "status");
    const deidentify = idle(button("重新去識別化", key + "-deidentify", () => handleDeidentify(key), "quiet"));
    privacyRow.append(privacyStatus, deidentify); section.append(privacyRow);
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
    workspaces[key] = {data, question, workflow, profile, cancel, copyAnswer, citations, answer, raw, answerStatus, draftStatus, privacyStatus, revision: 0, syncedRevision: -1, dirty: false, modified: false, timer: null, pending: Promise.resolve(), answerText: "", answerSignature: ""};
    for (const field of [data, question]) field.addEventListener("input", () => markDraft(key));
    for (const field of [workflow, profile].filter(Boolean)) field.addEventListener("change", () => markDraft(key));
    citations.addEventListener("change", () => { workspaces[key].answerSignature = ""; if (state) renderAnswer(key, state.answers[key], state.drafts[key].revision); });
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
  function trendChart(row) {
    const points = validPlotPoints(row);
    if (!points.length) return null;
    const xs = points.map(point => point.x), ys = points.map(point => point.y);
    const xmin = xs[0], xmax = xs[xs.length - 1], ymin = Math.min(...ys), ymax = Math.max(...ys);
    const padding = ymax === ymin ? Math.max(Math.abs(ymin) * .1, 1) : (ymax - ymin) * .12;
    const low = ymin - padding, high = ymax + padding;
    const x = value => 76 + (value - xmin) / (xmax - xmin) * 580;
    const y = value => 175 - (value - low) / (high - low) * 145;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 720 235"); svg.setAttribute("class", "trend-chart"); svg.setAttribute("role", "img");
    const axis = row.time_basis === "report" ? "報告時間" : "量測 / 採檢時間";
    svg.setAttribute("aria-label", row.label + "，" + points.length + " 筆數值；橫軸依實際" + axis + "間隔，縱軸 " + row.unit + "。完整數值見下表。");
    const add = (tag, attributes, text, parent) => {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, String(value));
      if (text != null) node.textContent = String(text);
      (parent || svg).append(node); return node;
    };
    const number = value => Number(value.toPrecision(5)).toString();
    for (const value of [...new Set([ymin, (ymin + ymax) / 2, ymax])]) {
      add("line", {x1: 76, x2: 656, y1: y(value), y2: y(value), class: "chart-grid"});
      add("text", {x: 66, y: y(value) + 4, "text-anchor": "end", class: "chart-axis"}, number(value));
    }
    add("text", {x: 76, y: 16, class: "chart-axis"}, row.unit || "單位未提供");
    add("polyline", {points: points.map(point => x(point.x) + "," + y(point.y)).join(" "), class: "chart-line"});
    const labels = new Set([0, points.length - 1, ys.indexOf(ymin), ys.indexOf(ymax)]);
    points.forEach((point, index) => {
      const dot = add("circle", {cx: x(point.x), cy: y(point.y), r: 3, class: "chart-point"});
      add("title", {}, (point.observation.time || point.observation.timestamp) + " · " + point.observation.value + " " + (row.unit || ""), dot);
      if (labels.has(index)) add("text", {x: x(point.x), y: y(point.y) - 9, "text-anchor": index === 0 ? "start" : index === points.length - 1 ? "end" : "middle", class: "chart-value"}, point.observation.value == null ? number(point.y) : point.observation.value);
    });
    const crossYear = points[0].observation.timestamp.slice(0, 4) !== points[points.length - 1].observation.timestamp.slice(0, 4);
    for (const index of [0, points.length - 1]) {
      const point = points[index], stamp = point.observation.timestamp;
      const date = (crossYear ? stamp.slice(0, 10) : stamp.slice(5, 10)).replace(/-/g, "/");
      const time = point.observation.time_precision === "day" ? "" : stamp.slice(11, 16);
      add("text", {x: x(point.x), y: 198, "text-anchor": index === 0 ? "start" : "end", class: "chart-axis"}, date + (time ? " " + time : ""));
    }
    add("text", {x: 366, y: 223, "text-anchor": "middle", class: "chart-axis"}, axis + "（實際時間間隔）");
    const wrapper = element("div", null, "chart-scroll"); wrapper.append(svg); return wrapper;
  }
  function observationTable(row) {
    const wrapper = element("div", null, "table-scroll observation-table"), table = element("table"), head = element("thead"), titles = element("tr"), body = element("tbody");
    const timeLabel = row.time_basis === "report" ? "報告時間" : "量測 / 採檢時間";
    for (const title of [timeLabel, "結果" + (row.unit ? "（" + row.unit + "）" : ""), "時間精度"]) { const th = element("th", title); th.scope = "col"; titles.append(th); }
    head.append(titles); table.append(head);
    for (const point of row.all_values || row.values || []) {
      const tr = element("tr");
      tr.append(element("td", point.time || "時間未辨識"), element("td", point.value), element("td", !point.timestamp ? "未辨識；不判定先後" : point.time_precision === "day" ? "僅日期" : "日期與時間")); body.append(tr);
    }
    table.append(body); wrapper.append(table); return wrapper;
  }
  function trendTable(rows) {
    const wrapper = element("div", null, "table-scroll"), table = element("table", null, "trend-table"), head = element("thead"), titles = element("tr"), body = element("tbody");
    for (const title of ["檢驗項目", "最新已知值", "近期趨勢 · 較早 → 最近", "完整紀錄"]) { const cell = element("th", title); cell.scope = "col"; titles.append(cell); }
    head.append(titles); table.append(head);
    for (const row of rows) {
      const presentation = trendPresentation(row), latest = presentation.latest;
      const tr = element("tr"), label = element("th", row.label, "trend-label"); label.scope = "row";
      label.append(element("span", [row.unit || "單位未提供", row.specimen, row.method].filter(Boolean).join(" · "), "trend-unit"));
      const last = element("td", latest ? latest.value : presentation.latestConflict ? "待核對" : "時間未明", "trend-last");
      if (latest && latest.time) last.append(element("span", (row.time_basis === "report" ? "報告 " : "") + latest.time, "trend-unit"));
      const trend = element("td"), chain = element("div", null, "trend-chain");
      const values = presentation.known.length ? presentation.known : presentation.unknown.slice(-3);
      values.forEach((point, index) => { if (index) chain.append(element("span", presentation.known.length ? presentation.separators[index] : "/", "trend-arrow")); const value = element("span", point.value); value.title = point.time || "時間未辨識"; chain.append(value); });
      if (!values.length) chain.append(document.createTextNode("未提供"));
      trend.append(chain);
      if (presentation.hasConflicts) trend.append(element("span", "時間或數值衝突", "trend-flag warning"));
      if (presentation.unknown.length) trend.append(element("span", (presentation.known.length ? "另有 " : "") + presentation.unknown.length + " 筆時間未明", "trend-flag warning"));
      const detailsCell = element("td"), count = (row.all_values || row.values || []).length;
      const detailRow = element("tr", null, "trend-expanded-row"), detailCell = element("td"), content = element("div", null, "trend-expanded"); detailCell.colSpan = 4; detailRow.hidden = true;
      let initialized = false;
      const toggle = button(count + " 筆 · 展開", null, () => {
        detailRow.hidden = !detailRow.hidden; toggle.setAttribute("aria-expanded", String(!detailRow.hidden)); toggle.textContent = count + " 筆 · " + (detailRow.hidden ? "展開" : "收合");
        toggle.setAttribute("aria-label", (detailRow.hidden ? "展開 " : "收合 ") + row.label + " 的 " + count + " 筆完整紀錄");
        if (!detailRow.hidden && !initialized) {
          initialized = true;
          content.append(element("h4", row.label + (row.specimen ? " · " + row.specimen : "")));
          const chart = trendChart(row); if (chart) content.append(chart);
          content.append(element("p", [row.span, row.reference ? "參考範圍：" + row.reference : "", row.note].filter(Boolean).join(" · "), "muted"));
          if (row.time_basis === "report") content.append(element("p", "時間來自報告日期，不代表實際採檢時間。", "muted"));
          if (!chart) content.append(element("p", "以表格保留原值；比較符號、定性結果或不確定的時間不連成折線。", "muted"));
          content.append(observationTable(row));
        }
      }, "trend-toggle quiet");
      toggle.setAttribute("aria-expanded", "false"); toggle.setAttribute("aria-label", "展開 " + row.label + " 的 " + count + " 筆完整紀錄");
      detailsCell.append(toggle); tr.append(label, last, trend, detailsCell); body.append(tr); detailCell.append(content); detailRow.append(detailCell); body.append(detailRow);
    }
    table.append(body); wrapper.append(table); return wrapper;
  }
  function reportRecord(record, rawSource) {
    const details = element("details", null, "source-record"), heading = element("summary"), title = element("span", record.title || record.type || "未命名報告", "report-title");
    heading.append(title, element("span", [record.time, record.status].filter(Boolean).join(" · "), "report-meta")); details.append(heading);
    const content = element("div", null, "report-content"); details.append(content);
    let loaded = false, pending = false;
    details.addEventListener("toggle", async () => {
      if (!details.open || loaded || pending || !state) return;
      const epoch = state.epoch; pending = true; content.replaceChildren(element("p", "正在開啟報告…", "muted"));
      try {
        let complete = record;
        if (record.deferred) {
          const cacheKey = epoch + ":" + record.id;
          if (!sourceCache.has(cacheKey)) sourceCache.set(cacheKey, action("source_detail", {epoch, source_id: record.id}).then(result => result.source).catch(error => { sourceCache.delete(cacheKey); throw error; }));
          complete = await sourceCache.get(cacheKey);
        }
        if (!alive || !state || state.epoch !== epoch || !details.isConnected) return;
        if (!complete) throw new Error("尚未取得報告內容，請重新展開。");
        content.replaceChildren();
        renderSections(content, complete.sections, (target, text) => md.render(target, text, {hideCitations: true}));
        if (!(complete.sections || []).length) {
          if (complete.content) md.render(content, complete.content, {hideCitations: true});
          else content.append(element("p", "未提供可顯示內容；請核對原始系統。", "muted"));
        }
        if (complete.truncated || record.truncated) content.append(element("p", "這份顯示內容已截短，請查看原文或院內報告。", "warning"));
        if (complete.content) { const raw = element("details", null, "report-raw"); raw.append(element("summary", "查看原文" + (rawSource && complete.id ? " · " + complete.id : "")), element("pre", complete.content)); content.append(raw); }
        loaded = true;
      } catch (error) {
        if (alive && state && state.epoch === epoch && details.isConnected) content.replaceChildren(element("p", error.message || "報告讀取失敗，請收合後重試。", "warning"));
      } finally { pending = false; }
    });
    return details;
  }
  function reportBrowser(records, rawSource) {
    const section = element("section", null, "report-browser"), toolbar = element("div", null, "data-toolbar"), search = element("input"), results = element("div"), status = element("p", "", "muted result-count");
    const title = rawSource ? "原始資料" : "全部報告";
    search.type = "search"; search.placeholder = "搜尋報告名稱 / 摘要"; search.setAttribute("aria-label", title + "：搜尋報告名稱或摘要");
    let limit = 12, fullMatches = null, searchSequence = 0;
    const searchFull = button("搜尋全文", null, async () => {
      const query = search.value.trim(), epoch = state && state.epoch, sequence = ++searchSequence;
      if (!query) { fullMatches = null; render(); return; }
      searchFull.disabled = true; status.textContent = "正在搜尋本次已載入的內容…";
      try {
        const result = await action("source_search", {epoch, query});
        if (!alive || !state || state.epoch !== epoch || sequence !== searchSequence || search.value.trim() !== query || !section.isConnected) return;
        fullMatches = new Map((result.matches || []).map(match => [match.id, match.snippet || ""])); limit = 12; render();
      } catch (error) { if (state && state.epoch === epoch && sequence === searchSequence && section.isConnected) status.textContent = error.message; }
      finally { if (sequence === searchSequence) searchFull.disabled = false; }
    }, "quiet");
    const more = button("顯示更多", null, () => { limit += 12; render(); }, "quiet");
    function render() {
      const query = search.value.trim().toLocaleLowerCase();
      const matching = records.filter(record => fullMatches ? fullMatches.has(record.id) : !query || [record.title, record.type, record.time, record.preview].filter(Boolean).join(" ").toLocaleLowerCase().includes(query));
      const shown = matching.slice(0, limit);
      results.replaceChildren(...shown.map(record => {
        const node = reportRecord(record, rawSource), snippet = fullMatches && fullMatches.get(record.id);
        if (snippet) node.insertBefore(element("p", snippet, "report-snippet"), node.children[1]);
        return node;
      }));
      if (!matching.length) results.append(element("p", query ? "目前篩選沒有符合的報告；可改用全文搜尋，或清除搜尋查看全部。" : "未取得報告內容，不代表沒有報告。", "muted"));
      status.textContent = (fullMatches ? "全文搜尋 · " : "") + "顯示 " + shown.length + " / " + matching.length + " 份" + (query ? "（本次共 " + records.length + " 份）" : "");
      more.hidden = shown.length >= matching.length;
    }
    search.addEventListener("input", () => { searchSequence++; searchFull.disabled = false; fullMatches = null; limit = 12; render(); });
    toolbar.append(element("h3", title), search, searchFull); section.append(toolbar, status, results, more); render(); return section;
  }
  function labBrowser(rows) {
    const section = element("section", null, "lab-browser"), toolbar = element("div", null, "data-toolbar"), search = element("input"), status = element("p", "", "muted result-count"), results = element("div");
    search.id = "lab-filter"; search.type = "search"; search.placeholder = "搜尋所有檢驗、檢體或單位"; search.setAttribute("aria-label", "搜尋所有檢驗，包含其他與罕見檢驗");
    toolbar.append(element("h3", "檢驗趨勢"), search);
    function render() {
      const query = search.value.trim(), matching = sortedTrendRows(rows, query), groups = [];
      for (const [name, title] of [["cbc", "血球 · WBC / Hb / PLT 優先"], ["chemistry", "生化與發炎指標"], ["other", "Differential / 其他與特殊檢驗"]]) {
        const selected = matching.filter(row => name === "other" ? !["cbc", "chemistry"].includes(row.group) : row.group === name);
        if (!selected.length) continue;
        const group = element("details", null, "lab-group"); group.open = name !== "other" || Boolean(query);
        group.append(element("summary", title + "（" + selected.length + " 項）"));
        let built = false;
        const build = () => { if (group.open && !built) { built = true; group.append(trendTable(selected)); } };
        group.addEventListener("toggle", build); build(); groups.push(group);
      }
      results.replaceChildren(...groups);
      if (!matching.length) results.append(element("p", query ? "沒有符合的趨勢項目。特殊檢驗也可能只存在報告內，可使用下方「搜尋全文」。" : "未可靠辨識出趨勢表，可展開報告核對原值。", "muted"));
      status.textContent = "顯示 " + matching.length + " / " + rows.length + " 項 · 近期列出最近已知值；展開查看完整時間軸與表格";
    }
    search.addEventListener("input", render); section.append(toolbar, status, results); render(); return section;
  }
  function renderOverview(view) {
    const signature = JSON.stringify([state.epoch, view]);
    if (signature === renderedOverview) return;
    renderedOverview = signature;
    sourceCache.clear();
    const target = $("overview");
    if (!view) { const empty = element("div", null, "empty-state"); empty.append(element("h2", state.selected_id ? "載入資料後，先看今天的變化" : "先選取病人"), element("p", "檢驗表、待辦與原始來源會顯示在這裡。未載入不代表沒有異常。")); target.replaceChildren(empty); return; }
    const nodes = [];
    const coverage = coveragePresentation(view.coverage);
    if (coverage) { const banner = element("div", null, "coverage-banner" + (coverage.warning ? " warning" : "")); banner.append(element("strong", coverage.title), element("span", coverage.text)); nodes.push(banner); }
    if (view.vitals && view.vitals.length) {
      const strip = element("div", null, "vitals-strip");
      for (const row of view.vitals) {
        const presentation = trendPresentation(row), latest = presentation.latest, cell = element("div", null, "vital");
        const value = element("div", latest ? latest.value : presentation.latestConflict ? "時間或數值衝突，請核對" : presentation.unknown.length ? presentation.unknown.map(point => point.value).join(" / ") : "未提供", "vital-value"); if (row.unit && !presentation.latestConflict) value.append(element("small", row.unit));
        cell.append(element("div", row.label, "vital-label"), value, element("div", latest && latest.time ? latest.time : presentation.latestConflict ? presentation.lastKnownTime || "時間或數值衝突" : "量測時間未辨識", "vital-time"));
        if (presentation.hasConflicts && !presentation.latestConflict) cell.append(element("div", presentation.hasTimeConflict ? "時間或數值衝突，請核對" : "同時點數值衝突，請核對", "vital-time warning"));
        if (presentation.hasConflicts && row.note) cell.append(element("div", row.note, "vital-time warning"));
        if (presentation.unknown.length) cell.append(element("div", latest ? "時間未明另有：" + presentation.unknown.map(point => point.value).join(" / ") : "請展開來源核對時間", "vital-time warning"));
        strip.append(cell);
      }
      nodes.push(strip);
      const vitalTrends = element("details", null, "section-details"); vitalTrends.append(element("summary", "生命徵象趨勢與完整時間"));
      let built = false; vitalTrends.addEventListener("toggle", () => { if (vitalTrends.open && !built) { built = true; vitalTrends.append(trendTable(view.vitals)); } }); nodes.push(vitalTrends);
    }
    if (view.warnings && view.warnings.length) {
      const details = element("details", null, "overview-warnings"), list = element("ul");
      details.append(element("summary", "資料涵蓋與缺漏提醒（" + view.warnings.length + "）"));
      view.warnings.forEach(warning => list.append(element("li", warning))); details.append(list); nodes.push(details);
    }
    const briefs = element("div", null, "brief-grid");
    briefs.append(shortList(view.todo_kind === "excerpt" ? "入院紀錄頂端摘錄" : "待辦 / 追蹤", view.todos, "未辨識到明確待辦，請核對入院紀錄。", 5), shortList("用藥紀錄", view.medications, "尚無可讀用藥紀錄。", 4)); nodes.push(briefs);
    nodes.push(labBrowser(view.trends || []));
    const reports = Array.isArray(view.report_details) ? view.report_details : (view.sources || []).filter(source => /report|報告/i.test(source.type || ""));
    nodes.push(reportBrowser(reports, false));
    const sources = element("details", null, "source-details"); sources.append(element("summary", "原始資料與完整時間（" + (view.sources || []).length + " 份來源）"));
    let sourcesBuilt = false; sources.addEventListener("toggle", () => { if (sources.open && !sourcesBuilt) { sourcesBuilt = true; sources.append(reportBrowser(view.sources || [], true)); } });
    nodes.push(sources); target.replaceChildren(...nodes);
  }
  function adoptDraft(key, incoming, force) {
    if (!incoming) return;
    const workspace = workspaces[key];
    if (!workspace.dirty && incoming.revision === workspace.revision) renderPrivacy(key, incoming.privacy);
    if (!shouldAdoptDraft(incoming.revision, workspace.revision, workspace.dirty, force)) return;
    clearTimeout(workspace.timer);
    workspace.data.value = incoming.data || ""; workspace.question.value = incoming.question || "";
    if (workspace.workflow) workspace.workflow.value = incoming.workflow || "rounding";
    workspace.profile.value = incoming.profile || (key === "emergency" ? "emergency_fast" : "fast");
    workspace.revision = Number(incoming.revision) || 0; workspace.syncedRevision = workspace.revision;
    workspace.dirty = false; workspace.modified = false; workspace.draftStatus.textContent = "";
    renderPrivacy(key, incoming.privacy);
  }
  function renderPrivacy(key, privacy) {
    const status = privacyPresentation(privacy), workspace = workspaces[key];
    workspace.privacyStatus.textContent = status.text;
    workspace.privacyStatus.className = "privacy-status" + (status.warning ? " warning" : "");
  }
  async function handleDeidentify(key) {
    try {
      await exclusive(async () => {
        await flushDraft(key);
        const epoch = state.epoch, revision = workspaces[key].revision;
        await action("deidentify", {epoch, workspace: key, revision});
        if (!alive || !state || state.epoch !== epoch) return;
        transformedDrafts.set(key, {epoch, revision}); closePreview();
        await refreshState();
      });
    } catch (error) { notice(error.message, "error"); }
  }
  function renderAnswer(key, answer, revision) {
    const workspace = workspaces[key];
    // An old state poll may return after the newest draft was acknowledged.
    // Dirty alone is insufficient: the answer must belong to this exact revision.
    if (!shouldRenderAnswer(revision, workspace.revision, workspace.dirty)) return;
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
    const partialLoad = job.kind === "load" && job.status === "cancelled" && job.result && job.result.partial && state.overview && state.overview.coverage && state.overview.coverage.cancelled;
    const adopt = adoptAfterJobs.delete(job.id) && (job.status === "complete" || partialLoad);
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
      transformedDrafts.clear(); sourceCache.clear();
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
    for (const key of Object.keys(workspaces)) {
      const ticket = transformedDrafts.get(key), workspace = workspaces[key];
      const transformed = ticket && ticket.epoch === state.epoch && state.drafts[key].revision > ticket.revision && workspace.revision <= ticket.revision && !workspace.dirty;
      adoptDraft(key, state.drafts[key], forceDrafts || transformed);
      if (ticket && state.drafts[key].revision > ticket.revision) transformedDrafts.delete(key);
      renderAnswer(key, state.answers[key], state.drafts[key].revision);
    }
    renderPatients(); renderOverview(state.overview);
    if (!settingsDirty) {
      $("external-ai").checked = Boolean(state.settings.external_ai_enabled); $("privacy-ack").checked = Boolean(state.settings.privacy_acknowledged);
      $("require-zdr").checked = Boolean(state.settings.require_zdr); $("developer-mode").checked = Boolean(state.settings.developer_mode);
    }
    if (!lookbackDirty || jobNeedsAdoption) { $("lookback").value = String(state.settings.lab_lookback_months || 12); lookbackDirty = false; }
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
    const privacy = privacyPresentation(result.deidentification);
    setText("preview-privacy", privacy.text); $("preview-privacy").className = "privacy-status" + (privacy.warning ? " warning" : "");
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
  bind("cancel-load", () => exclusive(async () => { await action("cancel"); await refreshState(); }));
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
  if (!token) disconnect("開啟 WardLens", "請從 WardLens 控制視窗按「開啟工作台」。重新整理網頁後，也請由控制視窗重新開啟。");
  else poll();
})();
