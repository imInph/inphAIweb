// A small Markdown renderer for chat replies. Every piece of text is escaped
// before it becomes HTML, so nothing a model writes can inject markup.

function escapeHtml(text) {
    return text.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function renderInline(text) {
    const saved = [];
    const keep = html => `\u0000${saved.push(html) - 1}\u0000`;
    let out = text.replace(/\u0000/g, "")
        .replace(/(`+)([\s\S]+?)\1/g, (_, ticks, code) => keep(`<code>${escapeHtml(code.trim())}</code>`))
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) =>
            keep(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${renderInline(label)}</a>`));
    out = escapeHtml(out)
        .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>")
        .replace(/__(?=\S)([\s\S]*?\S)__/g, "<strong>$1</strong>")
        .replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>")
        .replace(/(^|[^*\w])\*(?=\S)([^*]*?\S)\*(?!\w)/g, "$1<em>$2</em>")
        .replace(/(^|[^_\w])_(?=\S)([^_]*?\S)_(?!\w)/g, "$1<em>$2</em>");
    return out.replace(/\u0000(\d+)\u0000/g, (_, i) => saved[i]);
}

const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const indentOf = line => line.match(/^\s*/)[0].replace(/\t/g, "    ").length;

function isBlockStart(line) {
    return /^\s*(```|~~~)/.test(line) || /^#{1,6}\s/.test(line) || /^\s*>/.test(line)
        || LIST_ITEM.test(line) || /^\s*([-*_])(\s*\1){2,}\s*$/.test(line);
}

function tableCells(line) {
    return line.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim());
}

function renderMarkdown(source) {
    const lines = source.replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
        const line = lines[i];
        if (!line.trim()) {
            i++;
            continue;
        }

        const fence = line.match(/^\s*(```|~~~)\s*([^\s`]*)/);
        if (fence) {
            const code = [];
            i++;
            // While a reply streams, the closing fence may not have arrived yet.
            while (i < lines.length && !lines[i].trim().startsWith(fence[1])) code.push(lines[i++]);
            i++;
            out.push(`<div class="codeBlock"><div class="codeHead"><span>${escapeHtml(fence[2] || "code")}</span>`
                + `<button type="button" class="copyCode">Copy</button></div>`
                + `<pre><code>${escapeHtml(code.join("\n"))}</code></pre></div>`);
            continue;
        }

        const heading = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
        if (heading) {
            const level = Math.min(heading[1].length + 2, 6);
            out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
            i++;
            continue;
        }

        if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
            out.push("<hr>");
            i++;
            continue;
        }

        if (/^\s*>/.test(line)) {
            const quote = [];
            while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
            out.push(`<blockquote>${renderMarkdown(quote.join("\n"))}</blockquote>`);
            continue;
        }

        const first = line.match(LIST_ITEM);
        if (first) {
            const base = indentOf(line);
            const ordered = /\d/.test(first[2]);
            const start = ordered ? parseInt(first[2], 10) : 1;
            const items = [];
            while (i < lines.length) {
                const current = lines[i];
                const item = current.match(LIST_ITEM);
                if (item && indentOf(current) <= base + 1) {
                    items.push([item[3]]);
                    i++;
                } else if (current.trim() && indentOf(current) > base) {
                    items[items.length - 1].push(current.replace(new RegExp(`^ {0,${base + 4}}`), ""));
                    i++;
                } else if (!current.trim() && i + 1 < lines.length
                           && (indentOf(lines[i + 1]) > base || LIST_ITEM.test(lines[i + 1])) && lines[i + 1].trim()) {
                    i++;
                } else {
                    break;
                }
            }
            const tag = ordered ? "ol" : "ul";
            const body = items.map(parts => `<li>${parts.length === 1 ? renderInline(parts[0]) : renderMarkdown(parts.join("\n"))}</li>`);
            out.push(`<${tag}${ordered && start !== 1 ? ` start="${start}"` : ""}>${body.join("")}</${tag}>`);
            continue;
        }

        if (line.includes("|") && i + 1 < lines.length
            && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(lines[i + 1]) && lines[i + 1].includes("-")) {
            const head = tableCells(line);
            i += 2;
            const rows = [];
            while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(tableCells(lines[i++]));
            out.push("<table><thead><tr>" + head.map(cell => `<th>${renderInline(cell)}</th>`).join("")
                + "</tr></thead><tbody>" + rows.map(row => "<tr>" + head.map((_, c) =>
                    `<td>${renderInline(row[c] || "")}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
            continue;
        }

        const paragraph = [];
        while (i < lines.length && lines[i].trim() && !(paragraph.length && isBlockStart(lines[i]))) {
            paragraph.push(lines[i++]);
        }
        out.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
    }
    return out.join("");
}
