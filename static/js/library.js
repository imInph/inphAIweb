import { readKnowledgeChecks, renderKnowledgeChecks } from "./knowledge.js";
import { $, api, autoGrow, copyText, iconButton } from "./util.js";
import { state } from "./state.js";
import { renderModelSelect } from "./models.js";
import { readOptions, renderOptions } from "./options.js";
import { showSaved } from "./settings.js";
import { splitThinking, stopStreams } from "./chat.js";

// Saved prompts (/command), personas and memories, and the keyboard shortcuts.

export async function loadLibrary() {
    try {
        const [prompts, personas, memories] = await Promise.all(
            ["prompts", "personas", "memories"].map(kind => api(`/api/${kind}`)));
        state.prompts = prompts.items;
        state.personas = personas.items;
        state.memories = memories.items;
    } catch (error) {
        console.error(error);
    }
    renderModelSelect();
    renderLibrary();
}

function itemRow(title, detail, onEdit, onDelete, swatch) {
    const item = Object.assign(document.createElement("li"), { className: "item-row" });
    if (swatch) item.append(Object.assign(document.createElement("span"), { className: `swatch swatch-${swatch}` }));
    const text = Object.assign(document.createElement("div"), { className: "item-text" });
    text.append(Object.assign(document.createElement("strong"), { textContent: title }));
    if (detail) text.append(Object.assign(document.createElement("small"), { textContent: detail }));
    item.append(text);
    if (onEdit) item.append(iconButton("edit", "Edit", onEdit));
    item.append(iconButton("trash", "Delete", onDelete));
    return item;
}

function emptyNote(text) {
    return Object.assign(document.createElement("li"), { className: "list-note", textContent: text });
}

function renderLibrary() {
    $("promptList").replaceChildren(...(state.prompts.length ? state.prompts.map(prompt =>
        itemRow(`/${prompt.command}`, prompt.title, () => editPrompt(prompt), () => removeItem("prompts", prompt)))
        : [emptyNote("No saved prompts yet.")]));
    $("personaList").replaceChildren(...(state.personas.length ? state.personas.map(persona =>
        itemRow(persona.name, persona.model, () => editPersona(persona), () => removeItem("personas", persona), persona.color))
        : [emptyNote("No personas yet.")]));
    $("memoryList").replaceChildren(...(state.memories.length ? state.memories.map(memory =>
        itemRow(memory.content, "", null, () => removeItem("memories", memory)))
        : [emptyNote("Nothing to remember yet.")]));
    fillPersonaModels();
    if (!$("personaOptions").children.length) renderOptions($("personaOptions"), {}, () => {}, null);
    if (!editingPersona) renderKnowledgeChecks($("personaKnowledge"), readKnowledgeChecks($("personaKnowledge")));
}

async function removeItem(kind, item) {
    const label = { prompts: `/${item.command}`, personas: item.name, memories: "this memory" }[kind];
    if (!confirm(`Delete ${label}?`)) return;
    try {
        await api(`/api/${kind}/${item.id}`, { method: "DELETE" });
        showSaved(kind, "Deleted");
    } catch (error) {
        showSaved(kind, error.message, true);
    }
    loadLibrary();
}

async function saveItem(kind, id, body) {
    try {
        await api(id ? `/api/${kind}/${id}` : `/api/${kind}`, { method: id ? "PATCH" : "POST", body });
        showSaved(kind, id ? "Saved" : "Added");
        return true;
    } catch (error) {
        showSaved(kind, error.message, true);
        return false;
    } finally {
        loadLibrary();
    }
}

// Prompts

let editingPrompt = null;

function editPrompt(prompt) {
    editingPrompt = prompt.id;
    $("promptCommand").value = prompt.command;
    $("promptTitle").value = prompt.title;
    $("promptContent").value = prompt.content;
    autoGrow($("promptContent"));
    $("promptSave").textContent = "Save changes";
    $("promptCancel").hidden = false;
    $("promptCommand").focus();
}

function resetPromptForm() {
    editingPrompt = null;
    $("promptForm").reset();
    $("promptSave").textContent = "Add prompt";
    $("promptCancel").hidden = true;
}

$("promptCancel").addEventListener("click", resetPromptForm);
$("promptForm").addEventListener("submit", async event => {
    event.preventDefault();
    const ok = await saveItem("prompts", editingPrompt, {
        command: $("promptCommand").value, title: $("promptTitle").value, content: $("promptContent").value });
    if (ok) resetPromptForm();
});

// Personas

let editingPersona = null;

function fillPersonaModels() {
    const select = $("personaModel");
    const previous = select.value;
    const groups = {};
    for (const model of state.models) {
        groups[model.backend_name] ??= Object.assign(document.createElement("optgroup"), { label: model.backend_name });
        groups[model.backend_name].append(new Option(model.id, `${model.backend}\n${model.id}`));
    }
    select.replaceChildren(...Object.values(groups));
    if (!state.models.length) select.replaceChildren(new Option("No models found", ""));
    if (previous) setPersonaModel(previous);
}

function setPersonaModel(value) {
    const select = $("personaModel");
    // A persona can point at a model that isn't running right now: keep it selectable.
    if (value && ![...select.options].some(option => option.value === value)) {
        select.append(new Option(`${value.split("\n")[1]} (not available now)`, value));
    }
    select.value = value;
}

function editPersona(persona) {
    editingPersona = persona.id;
    $("personaName").value = persona.name;
    $("personaColor").value = persona.color;
    setPersonaModel(`${persona.backend}\n${persona.model}`);
    $("personaPrompt").value = persona.system_prompt;
    autoGrow($("personaPrompt"));
    renderOptions($("personaOptions"), persona.options, () => {}, persona.backend);
    renderKnowledgeChecks($("personaKnowledge"), persona.knowledge_ids || []);
    $("personaSave").textContent = "Save changes";
    $("personaCancel").hidden = false;
    $("personaName").focus();
}

function resetPersonaForm() {
    editingPersona = null;
    $("personaForm").reset();
    renderOptions($("personaOptions"), {}, () => {}, null);
    renderKnowledgeChecks($("personaKnowledge"), []);
    fillPersonaModels();
    $("personaSave").textContent = "Add persona";
    $("personaCancel").hidden = true;
}

$("personaCancel").addEventListener("click", resetPersonaForm);
// Collections load separately; redraw the persona's tick boxes when they arrive (keeping what's ticked).
document.addEventListener("inphai:collections", () =>
    renderKnowledgeChecks($("personaKnowledge"), readKnowledgeChecks($("personaKnowledge"))));
$("personaForm").addEventListener("submit", async event => {
    event.preventDefault();
    const [backend, model] = ($("personaModel").value || "\n").split("\n");
    const ok = await saveItem("personas", editingPersona, {
        name: $("personaName").value, color: $("personaColor").value, backend, model,
        system_prompt: $("personaPrompt").value, options: readOptions($("personaOptions")),
        knowledge_ids: readKnowledgeChecks($("personaKnowledge")) });
    if (ok) resetPersonaForm();
});

// Memories

$("memoryForm").addEventListener("submit", async event => {
    event.preventDefault();
    const content = $("memoryInput").value.trim();
    if (!content) return;
    if (await saveItem("memories", null, { content })) $("memoryInput").value = "";
});

// The / menu: at the start of a message, "/" lists your saved prompts.

let slashMatches = [];
let slashIndex = 0;

function slashQuery() {
    const match = $("prompt").value.slice(0, $("prompt").selectionStart).match(/^\/([\w-]*)$/);
    return match ? match[1].toLowerCase() : null;
}

function renderSlash() {
    const query = slashQuery();
    const menu = $("slashMenu");
    const builtIn = [{ command: "image", title: "Make an anime-style image (WAI)", builtIn: true },
                     { command: "photo", title: "Make a realistic image (Z-Image)", builtIn: true },
                     { command: "video", title: "Make a short video (Wan2.2)", builtIn: true }];
    slashMatches = query === null ? [] : [...builtIn, ...state.prompts].filter(p =>
        p.command.startsWith(query) || p.title.toLowerCase().includes(query));
    menu.hidden = !slashMatches.length;
    if (menu.hidden) return;
    slashIndex = Math.min(slashIndex, slashMatches.length - 1);
    menu.replaceChildren(...slashMatches.map((prompt, i) => {
        const option = Object.assign(document.createElement("button"), { type: "button", role: "option", className: "slash-option" });
        option.classList.toggle("active", i === slashIndex);
        option.append(Object.assign(document.createElement("strong"), { textContent: `/${prompt.command}` }),
            Object.assign(document.createElement("span"), { textContent: prompt.title }));
        option.addEventListener("pointerdown", event => { event.preventDefault(); usePrompt(prompt); });
        return option;
    }));
}

async function fillVariables(text) {
    const now = new Date();
    let clipboard = "";
    if (text.includes("{{clipboard}}")) {
        try { clipboard = await navigator.clipboard.readText(); } catch { /* needs HTTPS and permission */ }
    }
    return text.replaceAll("{{date}}", now.toLocaleDateString()).replaceAll("{{time}}", now.toLocaleTimeString())
        .replaceAll("{{clipboard}}", clipboard).replaceAll("{{user}}", state.me ? state.me.user.username : "");
}

async function usePrompt(prompt) {
    const box = $("prompt");
    if (prompt.builtIn) {
        box.value = `/${prompt.command} `;
        $("slashMenu").hidden = true;
        box.focus();
        return;
    }
    const rest = box.value.slice(box.selectionStart);
    box.value = (await fillVariables(prompt.content)) + rest;
    $("slashMenu").hidden = true;
    autoGrow(box);
    box.focus();
    box.setSelectionRange(box.value.length - rest.length, box.value.length - rest.length);
}

$("prompt").addEventListener("input", () => { slashIndex = 0; renderSlash(); });
$("prompt").addEventListener("blur", () => setTimeout(() => { $("slashMenu").hidden = true; }, 150));
// Registered before the composer's own Enter handler would see the key: capture phase.
$("prompt").addEventListener("keydown", event => {
    if ($("slashMenu").hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        slashIndex = (slashIndex + (event.key === "ArrowDown" ? 1 : -1) + slashMatches.length) % slashMatches.length;
        renderSlash();
    } else if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        event.stopImmediatePropagation();
        usePrompt(slashMatches[slashIndex]);
    } else if (event.key === "Escape") {
        $("slashMenu").hidden = true;
    }
}, true);

// Keyboard shortcuts (the ? sheet lists them).

const typing = () => ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)
    || document.activeElement?.isContentEditable;

document.addEventListener("keydown", event => {
    const mod = event.ctrlKey || event.metaKey;
    const key = event.key.toLowerCase();
    if (mod && event.shiftKey && key === "o") {
        event.preventDefault();
        $("newChat").click();
    } else if (mod && event.shiftKey && key === "c") {
        const last = state.chat && [...state.chat.messages].reverse().find(m => m.role === "assistant");
        if (last) {
            event.preventDefault();
            const button = document.createElement("button");
            copyText(splitThinking(last).content, button);
        }
    } else if (event.key === "Escape" && event.shiftKey) {
        event.preventDefault();
        location.hash.startsWith("#chat") ? $("prompt").focus() : (location.hash = "#chat");
    } else if (event.key === "Escape" && state.live && $("slashMenu").hidden) {
        stopStreams();
    } else if (event.key === "?" && !typing() && !mod) {
        event.preventDefault();
        $("shortcuts").showModal();
    }
});

$("shortcutsClose").addEventListener("click", () => $("shortcuts").close());
$("shortcuts").addEventListener("click", event => { if (event.target === $("shortcuts")) $("shortcuts").close(); });
