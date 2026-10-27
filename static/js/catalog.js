import { $, api, button, iconButton } from "./util.js";
import { state } from "./state.js";
import { isAdmin, showSaved, formatBytes } from "./settings.js";
import { loadModels } from "./models.js";

// The Models page: Ollama's loaded and installed models (pull and delete for admins), LM Studio's list.

const CAPABILITY_LABELS = { vision: "sees images", thinking: "thinks", tools: "uses tools", embedding: "embedding" };
let pollTimer = null;

export function capabilityBadges(capabilities) {
    const box = Object.assign(document.createElement("span"), { className: "badges" });
    for (const capability of capabilities || []) {
        if (CAPABILITY_LABELS[capability]) {
            box.append(Object.assign(document.createElement("span"), { className: `badge badge-${capability}`, textContent: CAPABILITY_LABELS[capability] }));
        }
    }
    return box;
}

function relativeTime(iso) {
    const minutes = Math.round((new Date(iso) - Date.now()) / 60000);
    if (!Number.isFinite(minutes)) return "";
    // Ollama reports a far-future time for "keep loaded forever" (keep_alive -1).
    if (minutes > 60 * 24 * 365) return "stays loaded";
    if (minutes <= 0) return "unloading";
    return minutes < 60 ? `unloads in ${minutes} min` : `unloads in ${Math.round(minutes / 60)} h`;
}

function row(title, details, extra = [], actions = []) {
    const item = Object.assign(document.createElement("li"), { className: "item-row model-row" });
    const text = Object.assign(document.createElement("div"), { className: "item-text" });
    const head = Object.assign(document.createElement("div"), { className: "model-title" });
    head.append(Object.assign(document.createElement("strong"), { textContent: title }), ...extra);
    text.append(head);
    if (details) text.append(Object.assign(document.createElement("small"), { textContent: details }));
    item.append(text, ...actions);
    return item;
}

export async function loadCatalog() {
    clearTimeout(pollTimer);
    $("pullForm").hidden = !isAdmin();
    await Promise.all([loadOllama(), loadLmStudio()]);
}

async function loadOllama() {
    let data;
    try {
        const response = await api("/api/catalog/ollama");
        data = response;
        $("ollamaError").hidden = true;
    } catch (error) {
        $("ollamaError").hidden = false;
        $("ollamaError").textContent = error.message;
        data = { installed: [], loaded: [], pulls: [] };
    }
    $("ollamaLoadedBox").hidden = !data.loaded.length;
    $("ollamaLoaded").replaceChildren(...data.loaded.map(model => row(model.name,
        `${formatBytes(model.vram || 0)} in GPU memory${model.size > model.vram ? ` (${formatBytes(model.size - model.vram)} in RAM)` : ""}`
        + (model.context ? ` · context ${model.context}` : "") + ` · ${relativeTime(model.expires_at)}`, [],
        [button("Unload", () => unload(model.name), "btn btn-sm")])));
    $("ollamaInstalled").replaceChildren(...(data.installed.length ? data.installed.map(installedRow)
        : [Object.assign(document.createElement("li"), { className: "list-note", textContent: data.error ? "" : "No models installed yet." })]));
    renderPulls(data.pulls || []);
    if ((data.pulls || []).some(pull => !pull.done) && state.view === "models") pollTimer = setTimeout(loadOllama, 1000);
}

function installedRow(model) {
    const details = [model.parameters, model.quantization, formatBytes(model.size || 0), model.family].filter(Boolean).join(" · ");
    const actions = [iconButton("more", "Details", () => toggleDetails(item, model))];
    if (isAdmin()) actions.push(iconButton("trash", "Delete", () => remove(model.name)));
    const item = row(model.name, details, [capabilityBadges(model.capabilities)], actions);
    return item;
}

async function toggleDetails(item, model) {
    const existing = item.querySelector(".model-details");
    if (existing) return existing.remove();
    const box = Object.assign(document.createElement("div"), { className: "model-details" });
    box.textContent = "Loading...";
    item.append(box);
    try {
        const shown = await api("/api/catalog/ollama/show", { method: "POST", body: { name: model.name } });
        box.replaceChildren();
        if (shown.parameters) box.append(Object.assign(document.createElement("h4"), { textContent: "Default parameters" }),
            Object.assign(document.createElement("pre"), { textContent: shown.parameters }));
        if (shown.template) box.append(Object.assign(document.createElement("h4"), { textContent: "Prompt template" }),
            Object.assign(document.createElement("pre"), { textContent: shown.template }));
        if (!shown.parameters && !shown.template) box.textContent = "Ollama gave no further details.";
    } catch (error) {
        box.textContent = error.message;
    }
}

function renderPulls(pulls) {
    $("ollamaPulls").replaceChildren(...pulls.map(pull => {
        const percent = pull.total ? Math.round(100 * pull.completed / pull.total) : 0;
        const status = pull.error ? pull.error : pull.done ? "Downloaded" : pull.total
            ? `${pull.status} · ${formatBytes(pull.completed)} of ${formatBytes(pull.total)} (${percent}%)` : pull.status;
        const item = row(pull.done && !pull.error ? pull.name : `Pulling ${pull.name}`, status);
        if (pull.error) item.querySelector("small").classList.add("error-text");
        if (!pull.done) {
            const bar = Object.assign(document.createElement("div"), { className: "bar" });
            const fill = document.createElement("div");
            fill.style.width = `${percent}%`;
            bar.append(fill);
            item.querySelector(".item-text").append(bar);
        }
        return item;
    }));
}

async function unload(name) {
    try {
        await api("/api/catalog/ollama/unload", { method: "POST", body: { name } });
        showSaved("ollama", `Unloaded ${name}`);
    } catch (error) {
        showSaved("ollama", error.message, true);
    }
    loadOllama();
}

async function remove(name) {
    if (!confirm(`Delete ${name} from Ollama? You can pull it again later.`)) return;
    try {
        await api("/api/catalog/ollama/delete", { method: "POST", body: { name } });
        showSaved("ollama", `Deleted ${name}`);
    } catch (error) {
        showSaved("ollama", error.message, true);
    }
    loadOllama();
    loadModels();
}

$("pullForm").addEventListener("submit", async event => {
    event.preventDefault();
    const name = $("pullName").value.trim();
    if (!name) return;
    try {
        await api("/api/catalog/ollama/pull", { method: "POST", body: { name } });
        $("pullName").value = "";
    } catch (error) {
        showSaved("ollama", error.message, true);
    }
    await loadOllama();
    // Refresh the model picker once the pull is done.
    const waitForPull = async () => {
        const { pulls } = await api("/api/catalog/ollama").catch(() => ({ pulls: [] }));
        if ((pulls || []).some(pull => pull.name === name && !pull.done)) setTimeout(waitForPull, 2000);
        else loadModels();
    };
    waitForPull();
});

async function loadLmStudio() {
    try {
        const { models, detailed } = await api("/api/catalog/lmstudio");
        $("lmstudioError").hidden = true;
        $("lmstudioModels").replaceChildren(...(models.length ? models.map(model => {
            const details = [model.type === "vlm" ? "vision model" : model.type === "embeddings" ? "embedding model" : model.type,
                model.quantization, model.max_context ? `context ${model.max_context}` : null, model.arch].filter(Boolean).join(" · ");
            const state = model.state ? [Object.assign(document.createElement("span"), {
                className: `badge ${model.state === "loaded" ? "badge-loaded" : ""}`, textContent: model.state === "loaded" ? "loaded" : "not loaded" })] : [];
            return row(model.id, details || (detailed ? "" : "This LM Studio only reports model names."), state);
        }) : [Object.assign(document.createElement("li"), { className: "list-note", textContent: "No models downloaded in LM Studio." })]));
    } catch (error) {
        $("lmstudioError").hidden = false;
        $("lmstudioError").textContent = error.message;
        $("lmstudioModels").replaceChildren();
    }
}
