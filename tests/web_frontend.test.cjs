/* Run with node --test tests/web_frontend.test.cjs; no target-side Node required. */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assets = path.join(__dirname, "../src/wardlens/web/assets");
const markdown = require(path.join(assets, "markdown.js"));
const ui = require(path.join(assets, "app.js"));

class Node {
  constructor(tag, ownerDocument, text = "") { this.tagName = tag; this.ownerDocument = ownerDocument; this.children = []; this._text = text; }
  appendChild(node) { if (node.tagName === "#fragment") this.children.push(...node.children); else this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = []; this._text = ""; nodes.forEach(node => this.appendChild(node)); }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
  set textContent(value) { this.children = []; this._text = String(value); }
}
const document = {
  createElement(tag) { return new Node(tag, this); },
  createTextNode(text) { return new Node("#text", this, String(text)); },
  createDocumentFragment() { return new Node("#fragment", this); },
};
const descendants = node => [node, ...node.children.flatMap(descendants)];
function render(text, options) { const target = document.createElement("article"); markdown.render(target, text, options); return target; }

test("Chinese headings, lists, emphasis and tables render as readable DOM", () => {
  const result = render("## 今日重點\n\n- **CRP** 下降，不能直接等同改善。 [SRC-001]\n\n| 項目 | 較早 | 最近 |\n|---|---:|---:|\n| CRP | 12.0 | 1.5 |\n| Hb | 8.2 | 9.1 |");
  const tags = descendants(result).map(node => node.tagName);
  assert.ok(tags.includes("h2") && tags.includes("ul") && tags.includes("strong") && tags.includes("table"));
  assert.match(result.textContent, /CRP.*12\.0.*1\.5/s);
  assert.doesNotMatch(result.textContent, /SRC-001|##|\*\*/);
});

test("untrusted HTML, SVG, links and images cannot become executable elements", () => {
  const attack = '<img src="https://example.invalid/leak" onerror="alert(1)">\n<svg onload=alert(1)>x</svg>\n[click](javascript:alert(1))\n![patient](https://example.invalid/image)\n<script>steal()</script>';
  const result = render(attack);
  const tags = descendants(result).map(node => node.tagName);
  for (const dangerous of ["img", "svg", "script", "a", "iframe", "object"]) assert.ok(!tags.includes(dangerous));
  assert.match(result.textContent, /<script>steal\(\)<\/script>/);
  assert.match(result.textContent, /example\.invalid/);
});

test("source-token hiding does not remove doses, dates, comparisons or arbitrary brackets", () => {
  const text = "Hb 8.2 g/dL [SRC-001]；Na 130 mmol/L [SRC-002, SRC-003]；D-40 → D0；10/30 14:00；5 mg；<0.1；[baseline]";
  const result = markdown.cleanCitations(text, true);
  assert.doesNotMatch(result, /SRC-/);
  for (const retained of ["8.2", "130", "D-40", "D0", "10/30 14:00", "5 mg", "<0.1", "[baseline]"]) assert.ok(result.includes(retained));
  assert.equal(markdown.cleanCitations(text, false), text);
  assert.match(render(text, {hideCitations: false}).textContent, /SRC-001/);
});

test("partial streamed tables preserve overflowing cells and escaped/code pipes", () => {
  assert.deepEqual(markdown.tableCells("| a\\|b | `c|d` |"), ["a|b", "`c|d`"]);
  const result = render("| 項目 | 值 |\n|---|---|\n| CRP | <0.1 | 單位待核對 |\n| Hb | 8.");
  const body = descendants(result).find(node => node.tagName === "tbody");
  assert.equal(body.children[0].children.length, 3);
  assert.match(body.textContent, /單位待核對/);
  assert.match(body.textContent, /8\./);
});

test("plain-text copy retains clinical numbers while removing heading/table decorations", () => {
  const result = markdown.toPlainText("## 重點\n- **Hb** 8.2 g/dL [SRC-001]\n| 藥物 | 劑量 |\n|---|---|\n| generic | 5 mg |\n\n`10/30 14:00` <0.1");
  assert.match(result, /Hb 8\.2 g\/dL/);
  assert.match(result, /generic\t5 mg/);
  assert.match(result, /10\/30 14:00.*<0.1/);
  assert.doesNotMatch(result, /SRC-001|##|\*\*|\|---/);
});

test("unsaved drafts survive polling; only explicit replacement overrides them", () => {
  assert.equal(ui.shouldAdoptDraft(3, 4, true, false), false);
  assert.equal(ui.shouldAdoptDraft(20, 4, true, false), false);
  assert.equal(ui.shouldAdoptDraft(3, 4, false, false), false);
  assert.equal(ui.shouldAdoptDraft(4, 4, false, false), false);
  assert.equal(ui.shouldAdoptDraft(5, 4, false, false), true);
  assert.equal(ui.shouldAdoptDraft(5, 4, true, true), true);
  assert.equal(ui.nextRevision(4, 8), 9);
});

test("a delayed old answer cannot reappear after a newer draft acknowledgement", () => {
  assert.equal(ui.shouldRenderAnswer(1, 1, false), true);
  assert.equal(ui.shouldRenderAnswer(1, 2, true), false);
  // The rev2 draft POST is now acknowledged: dirty=false, but a rev1 GET arrives late.
  assert.equal(ui.shouldAdoptDraft(1, 2, false, false), false);
  assert.equal(ui.shouldRenderAnswer(1, 2, false), false);
  assert.equal(ui.shouldRenderAnswer(2, 2, false), true);
  assert.equal(ui.shouldRenderAnswer(2, 2, true), false);
  assert.equal(ui.shouldRenderAnswer(undefined, 2, false), false);
});

test("saving unchanged developer fields does not freeze every built-in prompt/model", () => {
  const original = {schema_version: 1, custom_profiles: {deep: {model: "provider/custom", reasoning_effort: "high", max_tokens: null}}, custom_prompts: {qa: "custom qa"}};
  const profiles = {fast: {model: "provider/default", reasoning_effort: "low", max_tokens: null}, deep: original.custom_profiles.deep};
  const prompts = {rounding: "built in", qa: "custom qa"};
  assert.deepEqual(ui.buildDeveloperConfig(original, profiles, profiles, prompts, prompts), original);
  const changed = ui.buildDeveloperConfig(original, profiles, {...profiles, fast: {...profiles.fast, model: "provider/new"}}, prompts, {...prompts, rounding: "short custom"});
  assert.equal(changed.custom_profiles.fast.model, "provider/new");
  assert.equal(changed.custom_prompts.rounding, "short custom");
  assert.equal(changed.custom_prompts.qa, "custom qa");
  assert.ok(!original.custom_profiles.fast);
});

test("graphs require trustworthy numeric/timestamp metadata and preserve unequal intervals", () => {
  const values = [
    {timestamp: "2026-09-20T08:00:00+08:00", numeric: 12},
    {timestamp: "2026-10-29T08:00:00+08:00", numeric: 4},
    {timestamp: "2026-10-30T08:00:00+08:00", numeric: 1.5},
  ];
  const plotted = ui.validPlotPoints({chartable: true, values});
  assert.equal((plotted[1].x - plotted[0].x) / (plotted[2].x - plotted[1].x), 39);
  assert.deepEqual(ui.validPlotPoints({chartable: false, values}), []);
  assert.deepEqual(ui.validPlotPoints({chartable: true, has_time_conflict: true, values}), []);
  assert.deepEqual(ui.validPlotPoints({chartable: true, values: [values[0], {...values[1], numeric: null}]}), []);
  assert.deepEqual(ui.validPlotPoints({chartable: true, values: [values[0], {...values[1], timestamp: ""}]}), []);
  assert.deepEqual(ui.validPlotPoints({chartable: true, values: [values[0], values[0]]}), []);
});

test("unknown-time values are never presented as the latest observation", () => {
  const earlier = {timestamp: "2026-10-29T08:00:00+08:00", time: "10/29 08:00", value: "4.0"};
  const latest = {timestamp: "2026-10-30T08:00:00+08:00", time: "10/30 08:00", value: "1.5"};
  const unknown = {timestamp: "", time: "未記載", value: "99"};
  const result = ui.trendPresentation({values: [earlier, latest, unknown], all_values: [earlier, latest, unknown]});
  assert.equal(result.latest, latest);
  assert.deepEqual(result.known, [earlier, latest]);
  assert.deepEqual(result.unknown, [unknown]);
  assert.equal(ui.trendPresentation({values: [unknown]}).latest, null);
});

test("explicitly unresolved latest observation never chooses one side of a timestamp conflict", () => {
  const earlier = {timestamp: "2026-10-29T08:00:00+08:00", value: "4.0"};
  const collisionA = {timestamp: "2026-10-30T08:00:00+08:00", time: "10/30 08:00", value: "1.5", source_id: "SRC-001"};
  const collisionB = {...collisionA, value: "8.0", source_id: "SRC-002"};
  const row = {values: [earlier, collisionA, collisionB], all_values: [earlier, collisionA, collisionB], latest_known: null};
  const result = ui.trendPresentation(row);
  assert.equal(result.latest, null);
  assert.equal(result.latestConflict, true);
  assert.equal(result.hasConflicts, true);
  assert.equal(result.lastKnownTime, "10/30 08:00");
  assert.deepEqual(result.separators, ["", "→", "/"]);
  assert.equal(ui.trendPresentation({...row, values: [collisionB, collisionA, earlier]}).latest, null);
  assert.equal(ui.trendPresentation({...row, latest_known: earlier}).latest, earlier);
  assert.equal(ui.trendPresentation({values: [earlier, collisionA]}).latest, collisionA);
  assert.equal(ui.trendPresentation({values: [], latest_known: null}).latestConflict, false);
});

test("incomplete chronology keeps known-time arrows but never orders equal-time values", () => {
  const first = {timestamp: "2026-10-29T08:00:00+08:00", value: "4.0"};
  const second = {timestamp: "2026-10-30T08:00:00+08:00", value: "1.5"};
  const unknown = {timestamp: "", value: "9.0"};
  const result = ui.trendPresentation({values: [first, second], all_values: [first, second, unknown], latest_known: second, chronology_complete: false});
  assert.deepEqual(result.separators, ["", "→"]);
  assert.equal(result.latest, second);
  assert.equal(result.latestConflict, false);
  assert.equal(result.hasConflicts, false);
  const duplicate = ui.trendPresentation({values: [first, first, second], latest_known: second});
  assert.deepEqual(duplicate.separators, ["", "/", "→"]);
  assert.equal(duplicate.hasConflicts, false);
});

test("compact deferred trends preserve conflict semantics and the full unknown-time count", () => {
  const first = {timestamp: "2026-10-29T08:00:00+08:00", value: "4.0"};
  const second = {timestamp: "2026-10-30T08:00:00+08:00", time: "10/30 08:00", value: "1.5"};
  const result = ui.trendPresentation({deferred: true, all_values: [], values: [first, second], unknown_values: [{timestamp: "", value: "9.0"}], unknown_count: 27, latest_known: null, has_time_conflict: true});
  assert.equal(result.latestConflict, true);
  assert.equal(result.lastKnownTime, "10/30 08:00");
  assert.equal(result.unknownCount, 27);
  assert.equal(result.unknown.length, 1);
  assert.deepEqual(result.separators, ["", "/"]);
});

test("server time-conflict flag prevents all arrows including mixed day-only and precise times", () => {
  const prior = {timestamp: "2026-10-29T08:00:00+08:00", value: "4.0", time_precision: "minute"};
  const dayOnly = {timestamp: "2026-10-30T00:00:00+08:00", value: "1.5", time_precision: "day"};
  const timed = {timestamp: "2026-10-30T08:00:00+08:00", value: "8.0", time_precision: "minute"};
  const result = ui.trendPresentation({values: [prior, dayOnly, timed], latest_known: null, has_time_conflict: true});
  assert.equal(result.hasTimeConflict, true);
  assert.equal(result.hasConflicts, true);
  assert.equal(result.latest, null);
  assert.equal(result.latestConflict, true);
  assert.deepEqual(result.separators, ["", "/", "/"]);
});

test("full-window graphs retain older results and refuse unlike specimens, methods and time bases", () => {
  const observations = Array.from({length: 8}, (_, index) => ({timestamp: `2026-10-${String(index + 1).padStart(2, "0")}T08:00:00+08:00`, value: String(index + 1), numeric: index + 1}));
  const row = {chartable: true, unit: "mg/L", specimen: "urine", method: "HPLC", time_basis: "report", values: observations.slice(-5), all_values: observations};
  assert.equal(ui.validPlotPoints(row).length, 8);
  assert.equal(ui.validPlotPoints(row)[0].y, 1);
  assert.equal(ui.validPlotPoints({...row, all_values: [...observations].reverse()})[0].y, 1);
  for (const change of [{unit: "mg/dL"}, {specimen: "blood"}, {method: "immunoassay"}, {time_basis: "collection"}, {value: "<0.1", numeric: .1}, {value: "Negative", numeric: 0}, {timestamp: ""}]) {
    assert.deepEqual(ui.validPlotPoints({...row, all_values: [{...observations[0], ...change}, ...observations.slice(1)]}), []);
  }
  assert.deepEqual(ui.validPlotPoints({...row, specimen: "", all_values: [{...observations[0], specimen: "urine"}, ...observations.slice(1)]}), []);
  for (const [value, numeric] of [["8.2 L", 8.2], ["1,234", 1234], ["8.2 mg/L", 8.2], ["8.2 mg/L H", 8.2], ["1e2 H", 100]]) {
    const valid = ui.validPlotPoints({...row, all_values: [{...observations[0], value, numeric}, ...observations.slice(1)]});
    assert.equal(valid.length, 8);
    assert.equal(valid[0].y, numeric);
  }
  for (const [value, numeric] of [[">8.2", 8.2], ["≤ 8.2", 8.2], ["8.2 H", 82], ["1,23", 123], ["8.2 H", Infinity], ["8.2 H", NaN]]) {
    assert.deepEqual(ui.validPlotPoints({...row, all_values: [{...observations[0], value, numeric}, ...observations.slice(1)]}), []);
  }
});

test("all-test search includes rare assays and preserves CBC priority without merging specimens", () => {
  const rows = [{label: "PLT", group: "cbc"}, {label: "PBG", unit: "mg/L", specimen: "urine", group: "other"}, {label: "Hb", group: "cbc"}, {label: "WBC", group: "cbc"}, {label: "PBG", unit: "nmol/L", specimen: "plasma", group: "other"}];
  assert.deepEqual(ui.sortedTrendRows(rows, "").map(row => row.label), ["WBC", "Hb", "PLT", "PBG", "PBG"]);
  assert.equal(ui.sortedTrendRows(rows, "pbg").length, 2);
  assert.equal(ui.sortedTrendRows(rows, "PBG urine")[0].unit, "mg/L");
  assert.equal(ui.sortedTrendRows(rows, "PLASMA")[0].unit, "nmol/L");
  assert.equal(rows[0].label, "PLT");
});

test("structured reports retain ragged cells, qualitative values and HTML as inert text", () => {
  const target = document.createElement("section");
  ui.renderSections(target, [
    {kind: "text", text: "## 特殊檢驗\nPBG trend 待核對。 [SRC-001]"},
    {kind: "table", caption: "尿液", headers: ["項目", "結果"], rows: [["PBG", "<0.1", "mg/L"], ["Porphyrins", "Negative"], ["<img src=x onerror=alert(1)>", "<script>bad()</script>"]]},
  ], (node, text) => markdown.render(node, text, {hideCitations: true}));
  const nodes = descendants(target), body = nodes.find(node => node.tagName === "tbody"), headers = nodes.filter(node => node.tagName === "th");
  assert.equal(body.children[0].children.length, 3);
  assert.equal(body.children[1].children.length, 3);
  assert.match(headers[2].textContent, /原文未命名/);
  for (const retained of ["<0.1", "mg/L", "Negative", "<script>bad()</script>"]) assert.ok(target.textContent.includes(retained));
  assert.ok(nodes.some(node => node.tagName === "h2"));
  assert.ok(!nodes.some(node => ["img", "script"].includes(node.tagName)));
  assert.doesNotMatch(target.textContent, /SRC-001/);
});

test("coverage never equates missing counters or cancelled partial results with completeness", () => {
  assert.equal(ui.coveragePresentation({}), null);
  const partial = ui.coveragePresentation({report_loaded: 18, report_candidates: 40, report_discovery_complete: false, report_details_complete: false, cancelled: true});
  assert.equal(partial.warning, true);
  assert.match(partial.title, /已停止/);
  assert.match(partial.text, /18.*40/);
  assert.doesNotMatch(partial.text, /(?:^|\s)0 (?:類|份)/);
  const incomplete = ui.coveragePresentation({report_loaded: 18});
  assert.doesNotMatch(incomplete.title, /完成/);
  assert.doesNotMatch(ui.coveragePresentation({report_discovery_complete: true, report_details_complete: true, report_failed: 1}).title, /完成/);
  assert.match(ui.coveragePresentation({report_discovery_complete: true, report_details_complete: true}).title, /完成/);
  const incompleteLabs = ui.coveragePresentation({report_discovery_complete: true, report_details_complete: true, lab_categories_loaded: 3, lab_categories_discovered: 4, lab_categories_complete: false});
  assert.equal(incompleteLabs.warning, true);
  assert.match(incompleteLabs.text, /3 \/ 4 類檢驗.*尚未讀取完整/);
  assert.doesNotMatch(incompleteLabs.title, /完成/);
  assert.match(ui.coveragePresentation({lab_categories_discovered: 4}).text, /已發現 4 類檢驗/);
  assert.doesNotMatch(ui.coveragePresentation({lab_categories_discovered: 4}).text, /已讀取|0/);
});

test("privacy summaries distinguish processed draft, residual findings and edited text", () => {
  assert.match(ui.privacyPresentation({automatic: true, replacement_count: 7, residual_count: 0}).text, /已處理 7 項/);
  assert.equal(ui.privacyPresentation({automatic: true, replacement_count: 7, residual_count: 0}).warning, false);
  assert.match(ui.privacyPresentation({checked: true, replacement_count: 3, residual_count: 2}).text, /疑似剩餘 2 項需檢查/);
  assert.equal(ui.privacyPresentation({checked: true, replacement_count: 3, residual_count: 2}).warning, true);
  assert.match(ui.privacyPresentation({automatic: false, replacement_count: 7}).text, /尚未重新檢查/);
  assert.doesNotMatch(ui.privacyPresentation({automatic: true}).text, /0 項/);
});

// Exercise the real event handlers and asynchronous API ordering without adding
// a browser dependency to the Windows application or requiring a hospital login.
async function browserHarness(handleAction) {
  const nodes = [], timers = new Map(), requests = [];
  let timerSequence = 0;
  class BrowserNode extends Node {
    constructor(tag, ownerDocument, text = "") {
      super(tag, ownerDocument, text); this.dataset = {}; this.attributes = {}; this.listeners = {}; this.value = ""; this.checked = false; this.hidden = false; this.open = false; this.isConnected = true;
      this.classList = {toggle() {}};
    }
    append(...children) { children.forEach(child => this.appendChild(typeof child === "string" ? this.ownerDocument.createTextNode(child) : child)); }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
    insertBefore(node, reference) { this.children.splice(Math.max(0, this.children.indexOf(reference)), 0, node); }
    showModal() { this.open = true; }
    close() { this.open = false; }
    async dispatch(name) { for (const handler of this.listeners[name] || []) await handler({preventDefault() {}}); }
  }
  const doc = {
    createElement(tag) { const node = new BrowserNode(tag, this); nodes.push(node); return node; },
    createElementNS(_namespace, tag) { return this.createElement(tag); },
    createTextNode(text) { return new BrowserNode("#text", this, String(text)); },
    createDocumentFragment() { return new BrowserNode("#fragment", this); },
    getElementById(id) { return nodes.find(node => node.id === id); },
    querySelectorAll(selector) { return nodes.filter(node => selector === "[data-idle]" ? "idle" in node.dataset : selector === "[data-tab]" ? "tab" in node.dataset : selector === "input,textarea" ? ["input", "textarea"].includes(node.tagName) : false); },
  };
  const html = fs.readFileSync(path.join(assets, "index.html"), "utf8");
  for (const match of html.matchAll(/<([a-z][\w-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const node = doc.createElement(match[1]); node.id = match[3];
    if (/\bdata-idle\b/.test(match[2])) node.dataset.idle = "";
    node.checked = /\bchecked\b/.test(match[2]);
  }
  const draft = profile => ({revision: 1, data: "existing data", question: "", workflow: "rounding", profile, privacy: {automatic: true, replacement_count: 2, residual_count: 0}});
  const server = {epoch: 4, version: "test", busy: false, logged_in: true, demo: true, selected_id: "patient-1", selected: {name: "Synthetic", age: "40"}, patients: [], list_status: "", status: "Ready", snapshot_age_minutes: 0, profiles: [{key: "fast", label: "Fast", model: "synthetic", reasoning_effort: "low"}, {key: "emergency_fast", label: "Emergency", model: "synthetic", reasoning_effort: "low"}], drafts: {rounding: draft("fast"), emergency: draft("emergency_fast")}, answers: {rounding: {status: "idle", text: ""}, emergency: {status: "idle", text: ""}}, settings: {lab_lookback_months: 12}, overview: null};
  const context = {
    document: doc, window: {WardLensMarkdown: markdown, addEventListener() {}, confirm: () => true}, location: {hash: "#synthetic-local-token", pathname: "/"}, history: {replaceState() {}}, navigator: {}, AbortController,
    setTimeout(fn, delay) { const id = ++timerSequence; timers.set(id, {fn, delay}); return id; }, clearTimeout(id) { timers.delete(id); },
    async fetch(url, options) {
      const body = options.body ? JSON.parse(options.body) : null; requests.push({url, body});
      let response;
      if (handleAction) response = await handleAction(url, body, server);
      if (response === undefined) {
        if (url === "/api/state") response = JSON.parse(JSON.stringify(server));
        else if (url === "/api/draft") { server.drafts[body.workspace] = {...body, privacy: {automatic: false}}; response = {ok: true}; }
        else if (url === "/api/deidentify") { const draft = server.drafts[body.workspace]; server.drafts[body.workspace] = {...draft, data: "[REDACTED DATA]", question: "[REDACTED QUESTION]", revision: draft.revision + 1, privacy: {automatic: true, replacement_count: 3, residual_count: 1}}; response = {ok: true}; }
        else response = {ok: true};
      }
      return {ok: true, status: 200, async json() { return response; }};
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(assets, "app.js"), "utf8"), context);
  const settle = () => new Promise(resolve => setImmediate(resolve));
  await settle(); await settle();
  return {doc, server, requests, settle, nodes, async poll() { const timer = [...timers.values()].find(item => item.delay === 5000 || item.delay === 700); assert.ok(timer); await timer.fn(); }};
}

test("repeat de-identification flushes both edited fields first and adopts the server-redacted revision", async () => {
  const browser = await browserHarness(), get = id => browser.doc.getElementById(id);
  get("rounding-data").value = "synthetic data with new identifier";
  await get("rounding-data").dispatch("input");
  get("rounding-question").value = "synthetic question with identifier";
  await get("rounding-question").dispatch("input");
  assert.match(get("rounding-privacy").textContent, /尚未重新檢查/);
  await get("rounding-deidentify").dispatch("click");
  const posted = browser.requests.filter(request => request.body);
  assert.equal(posted[0].url, "/api/draft");
  assert.equal(posted[0].body.data, "synthetic data with new identifier");
  assert.equal(posted[0].body.question, "synthetic question with identifier");
  assert.equal(posted[1].url, "/api/deidentify");
  assert.equal(posted[1].body.revision, posted[0].body.revision);
  assert.equal(posted[1].body.epoch, 4);
  assert.equal(get("rounding-data").value, "[REDACTED DATA]");
  assert.equal(get("rounding-question").value, "[REDACTED QUESTION]");
  assert.match(get("rounding-privacy").textContent, /已處理 3 項.*剩餘 1 項/);
  get("rounding-question").value = "a newer bedside update";
  await get("rounding-question").dispatch("input");
  await browser.poll();
  assert.equal(get("rounding-question").value, "a newer bedside update");
  assert.match(get("rounding-privacy").textContent, /尚未重新檢查/);
});

test("deferred report contents load only on expansion and stale responses cannot enter another patient", async () => {
  let resolveReport;
  const browser = await browserHarness((url, body, server) => {
    if (url === "/api/state" && !server.overview) server.overview = {report_details: [{id: "report-1", title: "Rare assay", time: "11/01", deferred: true}], trends: [], sources: []};
    if (url === "/api/source_detail") return new Promise(resolve => { resolveReport = resolve; });
  });
  assert.ok(!browser.requests.some(request => request.url === "/api/source_detail"));
  const report = browser.nodes.find(node => node.className === "source-record");
  report.open = true;
  const pending = report.dispatch("toggle");
  await browser.settle();
  assert.equal(browser.requests.filter(request => request.url === "/api/source_detail").length, 1);
  assert.deepEqual(browser.requests.find(request => request.url === "/api/source_detail").body, {epoch: 4, source_id: "report-1"});
  browser.server.epoch = 5; browser.server.overview = {report_details: [], trends: [], sources: []};
  await browser.poll();
  resolveReport({source: {sections: [{kind: "text", text: "OLD PATIENT RESULT"}], content: "OLD PATIENT RESULT"}});
  await pending;
  assert.doesNotMatch(report.textContent, /OLD PATIENT RESULT/);
  assert.doesNotMatch(browser.doc.getElementById("overview").textContent, /OLD PATIENT RESULT/);
});

test("expanded charts show the complete window with value and report-time labels", async () => {
  const points = Array.from({length: 8}, (_, index) => ({timestamp: `2026-10-${String(index + 1).padStart(2, "0")}T08:00:00+08:00`, time: `10/${String(index + 1).padStart(2, "0")} 08:00`, value: String(index + 1), numeric: index + 1, time_precision: "minute"}));
  const browser = await browserHarness((url, _body, server) => {
    if (url === "/api/state" && !server.overview) server.overview = {trends: [{label: "CRP", group: "chemistry", unit: "mg/L", chartable: true, time_basis: "report", all_values: points, values: points.slice(-5), latest_known: points[7]}], report_details: [], sources: []};
  });
  assert.ok(!browser.nodes.some(node => node.tagName === "svg"));
  const expand = browser.nodes.find(node => node.className === "trend-toggle quiet");
  await expand.dispatch("click");
  const svg = browser.nodes.find(node => node.tagName === "svg");
  assert.ok(svg);
  assert.equal(descendants(svg).filter(node => node.tagName === "circle").length, 8);
  for (const label of ["mg/L", "10/01 08:00", "10/08 08:00", "報告時間（實際時間間隔）"]) assert.ok(svg.textContent.includes(label));
  const expanded = browser.nodes.find(node => node.className === "trend-expanded");
  assert.match(expanded.textContent, /不代表實際採檢時間/);
  const body = descendants(expanded).find(node => node.tagName === "tbody");
  assert.equal(body.children.length, 8);
});

test("deferred trends fetch the full window only on expansion and reuse its in-memory cache", async () => {
  const points = Array.from({length: 8}, (_, index) => ({timestamp: `2026-10-${String(index + 1).padStart(2, "0")}T08:00:00+08:00`, time: `10/${String(index + 1).padStart(2, "0")} 08:00`, value: String(index + 1), numeric: index + 1, time_precision: "minute"}));
  const complete = {id: "trend-4", label: "CRP", group: "chemistry", unit: "mg/L", chartable: true, all_values: points, values: points.slice(-5), latest_known: points[7], point_count: 8, unknown_values: [], unknown_count: 0};
  const browser = await browserHarness((url, _body, server) => {
    if (url === "/api/state" && !server.overview) server.overview = {trends: [{...complete, deferred: true, all_values: []}], report_details: [], sources: []};
    if (url === "/api/trend_detail") return {trend: complete};
  });
  assert.ok(!browser.requests.some(request => request.url === "/api/trend_detail"));
  const expand = browser.nodes.find(node => node.className === "trend-toggle quiet");
  assert.equal(expand.textContent, "8 筆 · 展開");
  await expand.dispatch("click");
  assert.deepEqual(browser.requests.find(request => request.url === "/api/trend_detail").body, {epoch: 4, trend_id: "trend-4"});
  const svg = browser.nodes.find(node => node.tagName === "svg");
  assert.equal(descendants(svg).filter(node => node.tagName === "circle").length, 8);
  const body = descendants(browser.nodes.find(node => node.className === "trend-expanded")).find(node => node.tagName === "tbody");
  assert.equal(body.children.length, 8);
  await expand.dispatch("click"); await expand.dispatch("click");
  const filter = browser.doc.getElementById("lab-filter"); filter.value = "CRP"; await filter.dispatch("input");
  const rebuiltExpand = browser.nodes.filter(node => node.className === "trend-toggle quiet").at(-1);
  await rebuiltExpand.dispatch("click");
  assert.equal(browser.requests.filter(request => request.url === "/api/trend_detail").length, 1);
});

test("deferred counts are not mistaken for the five displayed unknown-time samples", async () => {
  const samples = Array.from({length: 5}, (_, index) => ({value: String(index), timestamp: ""}));
  const browser = await browserHarness((url, _body, server) => {
    if (url === "/api/state" && !server.overview) server.overview = {trends: [{id: "trend-1", label: "CRP", group: "chemistry", unit: "mg/L", deferred: true, all_values: [], values: [], unknown_values: samples, unknown_count: 73, point_count: 73, latest_known: null}], report_details: [], sources: []};
  });
  const overview = browser.doc.getElementById("overview");
  assert.match(overview.textContent, /73 筆時間未明/);
  assert.match(overview.textContent, /73 筆 · 展開/);
  assert.doesNotMatch(overview.textContent, /5 筆時間未明/);
});

test("a trend response from the previous snapshot is discarded after reload", async () => {
  let resolveTrend;
  const browser = await browserHarness((url, _body, server) => {
    if (url === "/api/state" && !server.overview) server.overview = {trends: [{id: "trend-1", label: "CRP", group: "chemistry", unit: "mg/L", deferred: true, all_values: [], values: [], point_count: 2}], report_details: [], sources: []};
    if (url === "/api/trend_detail") return new Promise(resolve => { resolveTrend = resolve; });
  });
  const expand = browser.nodes.find(node => node.className === "trend-toggle quiet"), content = browser.nodes.find(node => node.className === "trend-expanded");
  const pending = expand.dispatch("click"); await browser.settle();
  browser.server.epoch = 5; browser.server.overview = {trends: [], report_details: [], sources: []};
  await browser.poll();
  resolveTrend({trend: {label: "OLD SNAPSHOT RESULT", all_values: [{value: "OLD SNAPSHOT RESULT"}]}});
  await pending;
  assert.doesNotMatch(content.textContent, /OLD SNAPSHOT RESULT/);
  assert.doesNotMatch(browser.doc.getElementById("overview").textContent, /OLD SNAPSHOT RESULT/);
});

test("rare assays can be found through all locally loaded report bodies beyond the display page", async () => {
  const browser = await browserHarness((url, _body, server) => {
    if (url === "/api/state" && !server.overview) server.overview = {trends: [], sources: [], report_details: Array.from({length: 30}, (_, index) => ({id: "report-" + index, title: "General report " + index, deferred: true}))};
    if (url === "/api/source_search") return {matches: [{id: "report-28", snippet: "Urine PBG 3.2 mg/L"}], total: 1};
  });
  const overview = browser.doc.getElementById("overview");
  assert.match(overview.textContent, /顯示 12 \/ 30 份/);
  const search = browser.nodes.find(node => node.placeholder === "搜尋報告名稱 / 摘要");
  search.value = "PBG"; await search.dispatch("input");
  assert.match(overview.textContent, /沒有符合的報告/);
  const fullSearch = browser.nodes.find(node => node.tagName === "button" && node.textContent === "搜尋全文");
  await fullSearch.dispatch("click");
  assert.match(overview.textContent, /General report 28/);
  assert.match(overview.textContent, /全文搜尋 · 顯示 1 \/ 1 份/);
  assert.equal(browser.requests.find(request => request.url === "/api/source_search").body.query, "PBG");
  assert.ok(!browser.requests.some(request => request.url === "/api/source_detail"));
});

test("bundled frontend has no browser persistence, HTML sink or remote asset dependency", () => {
  const app = fs.readFileSync(path.join(assets, "app.js"), "utf8");
  const renderer = fs.readFileSync(path.join(assets, "markdown.js"), "utf8");
  const html = fs.readFileSync(path.join(assets, "index.html"), "utf8");
  const css = fs.readFileSync(path.join(assets, "app.css"), "utf8");
  assert.doesNotMatch(app + renderer, /\.innerHTML\s*=|insertAdjacentHTML|eval\(|localStorage|sessionStorage|indexedDB|serviceWorker/);
  assert.match(app, /X-WardLens-Token/);
  assert.match(app, /history\.replaceState/);
  assert.match(app, /credentials:\s*"omit"/);
  assert.doesNotMatch(html, /(?:src|href)\s*=\s*["']https?:/);
  assert.doesNotMatch(html, /\son\w+\s*=/);
  assert.doesNotMatch(css, /@import|url\(/);
  assert.doesNotMatch(html + app, /開啟瀏覽器介面/);
  assert.match(html, /開啟工作台/);
});
