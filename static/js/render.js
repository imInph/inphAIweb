// What a finished reply gets after its Markdown is in the page: highlighted code,
// Mermaid diagrams, and a sandboxed preview for HTML/SVG code blocks.

let mermaidLoading = null;
let diagrams = 0;

function loadScript(src) {
    return new Promise((resolve, reject) => {
        const script = Object.assign(document.createElement("script"), { src, onload: resolve, onerror: reject });
        document.head.append(script);
    });
}

async function mermaid() {
    // 5 MB, so only fetched the first time a reply contains a diagram.
    mermaidLoading ??= loadScript("/static/vendor/mermaid/mermaid.min.js");
    await mermaidLoading;
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "default" });
    return window.mermaid;
}

async function renderDiagram(block) {
    const source = block.querySelector("code").textContent;
    const figure = Object.assign(document.createElement("div"), { className: "diagram" });
    block.after(figure);
    block.classList.add("diagram-source");
    try {
        const { svg } = await (await mermaid()).render(`diagram-${++diagrams}`, source);
        // Mermaid's strict mode sanitises the SVG it builds.
        figure.innerHTML = svg;
    } catch (error) {
        figure.remove();
        block.classList.remove("diagram-source");
    }
}

/** Run once per finished reply element. */
export function enhance(reply) {
    if (reply.dataset.enhanced) return;
    reply.dataset.enhanced = "1";
    for (const block of reply.querySelectorAll(".code-block")) {
        const lang = block.dataset.lang;
        if (lang === "mermaid") {
            renderDiagram(block);
            continue;
        }
        const code = block.querySelector("code");
        if (window.hljs && lang && window.hljs.getLanguage(lang)) window.hljs.highlightElement(code);
    }
}

/** Show (or hide) an HTML/SVG code block rendered in an iframe that can't reach this page. */
export function togglePreview(block, button) {
    const existing = block.querySelector("iframe");
    if (existing) {
        existing.remove();
        button.textContent = "Preview";
        return;
    }
    const code = block.querySelector("code").textContent;
    // allow-scripts without allow-same-origin: the page runs in its own origin, with no access to
    // inphAI's cookies, storage or DOM.
    const frame = Object.assign(document.createElement("iframe"), { className: "preview-frame", title: "Preview" });
    frame.setAttribute("sandbox", "allow-scripts");
    frame.srcdoc = block.dataset.lang === "html" ? code : `<!doctype html><body style="margin:0">${code}</body>`;
    block.append(frame);
    button.textContent = "Hide preview";
}

export function setCodeTheme(dark) {
    const link = document.getElementById("codeTheme");
    if (link) link.href = `/static/vendor/highlight/${dark ? "github-dark" : "github"}.css`;
}
