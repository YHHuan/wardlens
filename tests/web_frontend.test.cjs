/* Run with node --test tests/web_frontend.test.cjs; no target-side Node required. */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
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
});
