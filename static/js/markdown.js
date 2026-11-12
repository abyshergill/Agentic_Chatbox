// Markdown renderer for chat replies. All model text is escaped first, so it cannot inject markup.
// Supports headings, paragraphs, lists (nested by indent), task lists, code fences (with a copy button), tables,
// blockquotes, rules, bold/italic/strike, inline code and links (http/https only).

const escapeHtml = (text) =>
  text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function inline(text) {
  const codes = [];
  let out = escapeHtml(text).replace(/`([^`\n]+)`/g, (_, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  out = out
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,:;!?])/g, "$1<em>$2</em>")
    .replace(/~~([^~\n]+)~~/g, "<del>$1</del>")
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,:;!?])/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}

const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitRow(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|")) row = row.slice(0, -1);
  return row.split("|").map((cell) => cell.trim());
}

function renderList(items) {
  // items: [{ indent, ordered, text }]; nests deeper indents under the previous item.
  let html = "";
  const stack = [];
  for (const item of items) {
    while (stack.length && item.indent < stack[stack.length - 1].indent) {
      html += `</li></${stack.pop().tag}>`;
    }
    const top = stack[stack.length - 1];
    if (!top || item.indent > top.indent) {
      const tag = item.ordered ? "ol" : "ul";
      stack.push({ indent: item.indent, tag });
      html += `<${tag}>`;
    } else {
      html += "</li>";
    }
    const task = item.text.match(/^\[([ xX])\]\s+(.*)$/);
    html += task
      ? `<li class="task"><span class="check${task[1] === " " ? "" : " done"}" aria-hidden="true"></span>${inline(task[2])}`
      : `<li>${inline(item.text)}`;
  }
  while (stack.length) html += `</li></${stack.pop().tag}>`;
  return html;
}

export function renderMarkdown(source) {
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let paragraph = [];
  const flush = () => {
    if (paragraph.length) out.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
    paragraph = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];

    const fence = line.match(/^\s*(```|~~~)\s*([\w+#.-]*)\s*$/);
    if (fence) {
      flush();
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      const lang = fence[2] || "";
      out.push(`<div class="code-block"><div class="code-head"><span>${escapeHtml(lang || "text")}</span>`
        + `<button type="button" class="code-copy" data-copy>Copy</button></div>`
        + `<pre><code>${escapeHtml(body.join("\n"))}</code></pre></div>`);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (heading) {
      flush();
      const level = Math.min(heading[1].length + 1, 5); // h2..h5: replies never need a page title.
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }

    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flush();
      out.push("<hr>");
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      flush();
      const head = splitRow(line);
      const align = splitRow(lines[i + 1]).map((cell) =>
        cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : "");
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(splitRow(lines[i++]));
      i -= 1;
      const cell = (tag, text, index) =>
        `<${tag}${align[index] ? ` style="text-align:${align[index]}"` : ""}>${inline(text || "")}</${tag}>`;
      out.push(`<div class="table-wrap"><table><thead><tr>${head.map((t, n) => cell("th", t, n)).join("")}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${head.map((_, n) => cell("td", r[n], n)).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }

    if (/^\s*>/.test(line)) {
      flush();
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
      i -= 1;
      out.push(`<blockquote>${renderMarkdown(quote.join("\n"))}</blockquote>`);
      continue;
    }

    if (LIST_ITEM.test(line)) {
      flush();
      const items = [];
      while (i < lines.length) {
        const match = lines[i].match(LIST_ITEM);
        if (match) {
          items.push({ indent: match[1].replace(/\t/g, "    ").length, ordered: /\d/.test(match[2]), text: match[3] });
        } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) {
          items[items.length - 1].text += ` ${lines[i].trim()}`; // A wrapped continuation line.
        } else {
          break;
        }
        i += 1;
      }
      i -= 1;
      out.push(renderList(items));
      continue;
    }

    if (!line.trim()) {
      flush();
      continue;
    }
    paragraph.push(line);
  }
  flush();
  return out.join("");
}
