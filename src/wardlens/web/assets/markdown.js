/* Restricted Markdown renderer: untrusted text is never parsed as HTML. */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WardLensMarkdown = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  function cleanCitations(text, hide) {
    if (!hide) return String(text || "");
    return String(text || "").replace(/\[SRC-\d{3,}(?:(?:\s*[,;、]\s*|\s+)SRC-\d{3,})*\]/g, "");
  }
  function appendInline(parent, text, doc, depth) {
    // Only code/emphasis are interpreted. Links, images, HTML and SVG remain text.
    if ((depth || 0) > 6) { parent.appendChild(doc.createTextNode(text)); return; }
    const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_)/g;
    let cursor = 0, match;
    while ((match = pattern.exec(text))) {
      if (match.index > cursor) parent.appendChild(doc.createTextNode(text.slice(cursor, match.index)));
      const token = match[0];
      const code = token.startsWith("`");
      const strong = token.startsWith("**") || token.startsWith("__");
      const width = strong ? 2 : 1;
      const node = doc.createElement(code ? "code" : strong ? "strong" : "em");
      if (code) node.textContent = token.slice(1, -1);
      else appendInline(node, token.slice(width, -width), doc, (depth || 0) + 1);
      parent.appendChild(node);
      cursor = match.index + token.length;
    }
    if (cursor < text.length) parent.appendChild(doc.createTextNode(text.slice(cursor)));
  }
  function tableCells(line) {
    // Protect escaped pipes and pipes inside inline code when splitting cells.
    let source = line.trim();
    if (source.startsWith("|")) source = source.slice(1);
    if (source.endsWith("|") && !source.endsWith("\\|")) source = source.slice(0, -1);
    const cells = []; let cell = "", code = false;
    for (let index = 0; index < source.length; index += 1) {
      const char = source[index];
      if (char === "\\" && source[index + 1] === "|") { cell += "|"; index += 1; }
      else if (char === "`") { code = !code; cell += char; }
      else if (char === "|" && !code) { cells.push(cell.trim()); cell = ""; }
      else cell += char;
    }
    cells.push(cell.trim());
    return cells;
  }
  function tableSeparator(line) {
    const cells = tableCells(line || "");
    return cells.length > 1 && cells.every(cell => /^:?-{3,}:?$/.test(cell));
  }
  function render(target, input, options) {
    const opts = options || {}, doc = target.ownerDocument || document;
    const lines = cleanCitations(input, opts.hideCitations !== false).replace(/\r\n?/g, "\n").split("\n");
    const fragment = doc.createDocumentFragment();
    let index = 0;
    const inline = (node, text) => appendInline(node, text, doc, 0);
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) { index += 1; continue; }
      if (/^\s*```/.test(line)) {
        const body = []; index += 1;
        while (index < lines.length && !/^\s*```/.test(lines[index])) body.push(lines[index++]);
        if (index < lines.length) index += 1;
        const pre = doc.createElement("pre"), code = doc.createElement("code");
        code.textContent = body.join("\n"); pre.appendChild(code); fragment.appendChild(pre); continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
      if (heading) {
        const node = doc.createElement("h" + heading[1].length); inline(node, heading[2]);
        fragment.appendChild(node); index += 1; continue;
      }
      if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        fragment.appendChild(doc.createElement("hr")); index += 1; continue;
      }
      if (index + 1 < lines.length && line.includes("|") && tableSeparator(lines[index + 1])) {
        const wrapper = doc.createElement("div"); wrapper.className = "table-scroll";
        const table = doc.createElement("table"), head = doc.createElement("thead"), body = doc.createElement("tbody");
        const cells = tableCells(line), row = doc.createElement("tr");
        cells.forEach(cell => { const th = doc.createElement("th"); th.scope = "col"; inline(th, cell); row.appendChild(th); });
        head.appendChild(row); table.appendChild(head); index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
          const row = doc.createElement("tr"), rowCells = tableCells(lines[index++]);
          // Never throw away an overflowing cell from a partial/irregular table.
          const count = Math.max(cells.length, rowCells.length);
          for (let column = 0; column < count; column += 1) {
            const td = doc.createElement("td"); inline(td, rowCells[column] || ""); row.appendChild(td);
          }
          body.appendChild(row);
        }
        table.appendChild(body); wrapper.appendChild(table); fragment.appendChild(wrapper); continue;
      }
      const list = line.match(/^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/);
      if (list) {
        const ordered = Boolean(list[2]), node = doc.createElement(ordered ? "ol" : "ul");
        if (ordered && Number(list[2]) > 1) node.start = Number(list[2]);
        while (index < lines.length) {
          const item = lines[index].match(/^\s*(?:([-+*])|(\d+)[.)])\s+(.+)$/);
          if (!item || Boolean(item[2]) !== ordered) break;
          const li = doc.createElement("li"); inline(li, item[3]); node.appendChild(li); index += 1;
        }
        fragment.appendChild(node); continue;
      }
      if (/^\s*>\s?/.test(line)) {
        const node = doc.createElement("blockquote"), parts = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) parts.push(lines[index++].replace(/^\s*>\s?/, ""));
        inline(node, parts.join("\n")); fragment.appendChild(node); continue;
      }
      const node = doc.createElement("p"); inline(node, line);
      index += 1;
      while (index < lines.length && lines[index].trim() &&
        !/^(?:#{1,6}\s|\s*```|\s*>|\s*[-+*]\s|\s*\d+[.)]\s|\s*(?:-{3,}|\*{3,}|_{3,})\s*$)/.test(lines[index]) &&
        !(index + 1 < lines.length && lines[index].includes("|") && tableSeparator(lines[index + 1]))) {
        node.appendChild(doc.createElement("br")); inline(node, lines[index++]);
      }
      fragment.appendChild(node);
    }
    target.replaceChildren(fragment);
    return target;
  }
  function toPlainText(input, options) {
    const text = cleanCitations(input, !options || options.hideCitations !== false);
    return text.replace(/\r\n?/g, "\n").split("\n").filter(line => !tableSeparator(line))
      .map(line => {
        if (/^\s*```/.test(line) || /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) return "";
        let value = line.replace(/^#{1,6}\s+/, "").replace(/^\s*>\s?/, "");
        if (value.includes("|") && /^\s*\|/.test(value)) value = tableCells(value).join("\t");
        return value.replace(/\*\*([^*\n]+)\*\*|__([^_\n]+)__/g, (_, a, b) => a || b)
          .replace(/`([^`\n]+)`/g, "$1").replace(/\*([^*\n]+)\*/g, "$1");
      }).join("\n").replace(/\n{3,}/g, "\n\n").trim();
  }
  return {render, toPlainText, cleanCitations, tableCells, tableSeparator};
});
