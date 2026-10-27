const $ = id => document.getElementById(id);
const state = {
    view: "chat",
    chats: [],
    chat: null,          // the open chat with its messages, or null for a new one
    draft: { system_prompt: "", options: {} },
    models: [],
    streaming: null,     // AbortController while a reply streams
    job: null,
    settings: null,      // server-side settings (GET /api/settings)
    jobTimer: null,
};
const ICONS = {
    send: '<path d="M12 19V5M5.5 11.5L12 5l6.5 6.5"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/>',
    edit: '<path d="M12 20.5h8"/><path d="M16.2 3.8a2 2 0 0 1 2.9 2.9L7.5 18.3l-3.8 1 1-3.8z"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.5h6a1.5 1.5 0 0 0 1.5-1.5l1-12.5"/>',
};
const TITLES = { chat: "Chat", images: "Images", videos: "Videos", settings: "Settings" };
const VIEWS = Object.keys(TITLES);
const STAGES = {
    starting: "Starting...",
    loading_model: "Loading model...",
    generating: "Generating...",
    decoding: "Decoding...",
    saving: "Saving...",
    complete: "Finishing...",
};

async function api(path, options = {}) {
    const init = { ...options, headers: { "Content-Type": "application/json" } };
    if (options.body !== undefined) init.body = JSON.stringify(options.body);
    const response = await fetch(path, init);
    if (response.status === 204) return null;
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
    return result;
}

function icon(name, size = 18) {
    return `<svg class="ico" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
}

function button(label, onClick, className = "btn btn-ghost btn-sm") {
    const element = Object.assign(document.createElement("button"), { type: "button", className, textContent: label });
    element.addEventListener("click", onClick);
    return element;
}

function iconButton(name, label, onClick) {
    const element = button("", onClick, "btn btn-ghost btn-sm btn-icon");
    element.innerHTML = icon(name, 16);
    element.title = label;
    element.setAttribute("aria-label", label);
    return element;
}

function autoGrow(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = textarea.scrollHeight + "px";
    textarea.style.overflowY = textarea.scrollHeight > textarea.clientHeight + 2 ? "auto" : "hidden";
}

function storageGet(key) {
    try { return localStorage.getItem(key); } catch { return null; }
}

function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch { /* private mode */ }
}

function timeAgo(seconds) {
    const diff = Date.now() / 1000 - seconds;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
    return new Date(seconds * 1000).toLocaleDateString();
}

function copyText(text, button) {
    const done = () => {
        const label = button.textContent;
        button.textContent = "Copied";
        setTimeout(() => button.textContent = label, 1200);
    };
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done);
        return;
    }
    // Plain http on another device: the clipboard API isn't available there.
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    done();
}

// Views

function showView() {
    const [view, id] = location.hash.slice(1).split("/");
    state.view = VIEWS.includes(view) ? view : "chat";
    for (const name of VIEWS) $(`view-${name}`).hidden = name !== state.view;
    document.querySelectorAll(".nav-item").forEach(a => a.classList.toggle("active", a.dataset.view === state.view));
    $("pageTitle").textContent = TITLES[state.view];
    // Heights measured while a view was hidden come out as 0, so re-measure on show.
    $(`view-${state.view}`).querySelectorAll("textarea").forEach(autoGrow);
    document.title = state.view === "chat" ? "inphAI" : `${TITLES[state.view]} · inphAI`;
    closeDrawer();
    if (state.view === "chat") {
        const chatId = Number(id) || null;
        if (chatId !== (state.chat && state.chat.id)) openChat(chatId);
    } else if (state.view === "settings") {
        loadSettingsPage();
    } else {
        loadGallery(state.view === "images" ? "image" : "video");
    }
}

function closeDrawer() {
    document.body.classList.remove("drawer-open");
    $("backdrop").hidden = true;
}

$("menuButton").addEventListener("click", () => {
    document.body.classList.add("drawer-open");
    $("backdrop").hidden = false;
});

// Topbar: greeting, clock, theme

function greetingFor(hour) {
    return hour < 5 ? "Late night" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

function tick() {
    const now = new Date();
    $("greeting").textContent = greetingFor(now.getHours());
    $("clock").textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.querySelector('meta[name="theme-color"]').setAttribute("content", theme === "dark" ? "#0b0d12" : "#f4f6fa");
    storageSet("inphai.theme", theme);
    document.querySelectorAll("[data-theme-choice]").forEach(b => b.classList.toggle("on", b.dataset.themeChoice === theme));
}

$("themeButton").addEventListener("click", () =>
    setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
$("backdrop").addEventListener("click", closeDrawer);
window.addEventListener("hashchange", showView);

// Models

function modelKey(backend, id) {
    return `${backend}\n${id}`;
}

async function loadModels() {
    const select = $("model");
    const previous = select.value || storageGet("inphai.model");
    select.replaceChildren(new Option("Loading models...", ""));
    try {
        const result = await api("/api/models");
        state.models = result.models;
        const groups = {};
        for (const model of result.models) {
            groups[model.backend_name] ??= Object.assign(document.createElement("optgroup"), { label: model.backend_name });
            groups[model.backend_name].append(new Option(model.id, modelKey(model.backend, model.id)));
        }
        select.replaceChildren(...Object.values(groups));
        if (!result.models.length) select.replaceChildren(new Option("No models found", ""));
        const errors = Object.values(result.errors);
        $("backendNote").hidden = !errors.length;
        $("backendNote").textContent = errors.join(" · ");
        pickModel(state.chat ? modelKey(state.chat.backend, state.chat.model) : previous);
    } catch (error) {
        select.replaceChildren(new Option("Could not load models", ""));
        $("backendNote").hidden = false;
        $("backendNote").textContent = error.message;
    }
    if (!state.chat) renderMessages();
}

function pickModel(key) {
    const select = $("model");
    if (key && [...select.options].some(option => option.value === key)) select.value = key;
}

function selectedModel() {
    const [backend, model] = ($("model").value || "\n").split("\n");
    return backend && model ? { backend, model } : null;
}

$("refreshModels").addEventListener("click", loadModels);
$("model").addEventListener("change", () => {
    storageSet("inphai.model", $("model").value);
    paintChatOptions();
    const picked = selectedModel();
    if (state.chat && picked) api(`/api/chats/${state.chat.id}`, { method: "PATCH", body: picked }).catch(() => {});
});

// Chat list

async function loadChats() {
    try {
        state.chats = (await api("/api/chats")).chats;
    } catch (error) {
        console.error(error);
    }
    renderChatList();
}

function renderChatList() {
    const list = $("chatList");
    list.replaceChildren(...state.chats.map(chat => {
        const item = document.createElement("li");
        item.classList.toggle("active", Boolean(state.chat && state.chat.id === chat.id));
        const link = Object.assign(document.createElement("a"), { href: `#chat/${chat.id}`, textContent: chat.title });
        link.title = chat.title;
        item.append(link, iconButton("edit", "Rename", () => renameChat(chat)),
            iconButton("trash", "Delete", () => deleteChat(chat)));
        return item;
    }));
}

async function renameChat(chat) {
    const title = prompt("Rename chat", chat.title);
    if (!title || !title.trim()) return;
    await api(`/api/chats/${chat.id}`, { method: "PATCH", body: { title } });
    if (state.chat && state.chat.id === chat.id) state.chat.title = title.trim();
    loadChats();
}

async function deleteChat(chat) {
    if (!confirm(`Delete "${chat.title}"?`)) return;
    await api(`/api/chats/${chat.id}`, { method: "DELETE" });
    if (state.chat && state.chat.id === chat.id) location.hash = "#chat";
    loadChats();
}

$("newChat").addEventListener("click", () => {
    if (location.hash === "#chat") openChat(null);
    location.hash = "#chat";
    $("prompt").focus();
});

// One chat

function newChatDefaults() {
    const defaults = state.settings || {};
    return { system_prompt: defaults.default_system_prompt || "", options: { ...(defaults.default_options || {}) } };
}

// Model options (the same fields in the chat panel and in Settings)

const OPTION_FIELDS = [
    { name: "temperature", label: "Temperature", kind: "float", min: 0, max: 2, step: 0.05, hint: "0–2" },
    { name: "top_p", label: "Top P", kind: "float", min: 0, max: 1, step: 0.01, hint: "0–1" },
    { name: "top_k", label: "Top K", kind: "int", min: 0, max: 1000, step: 1, hint: "e.g. 40" },
    { name: "min_p", label: "Min P", kind: "float", min: 0, max: 1, step: 0.01, hint: "0–1", ollama: true },
    { name: "repeat_penalty", label: "Repeat penalty", kind: "float", min: 0, max: 3, step: 0.05, hint: "e.g. 1.1" },
    { name: "seed", label: "Seed", kind: "int", min: 0, step: 1, hint: "random" },
    { name: "max_tokens", label: "Max tokens", kind: "int", min: 1, step: 1, hint: "no limit" },
    { name: "num_ctx", label: "Context length", kind: "int", min: 256, step: 256, hint: "e.g. 8192", ollama: true },
    { name: "keep_alive", label: "Keep loaded", kind: "text", hint: "5m, 1h, -1", ollama: true },
    { name: "think", label: "Thinking", kind: "bool", ollama: true },
];

function renderOptions(container, values, onChange, backend) {
    container.replaceChildren(...OPTION_FIELDS.map(field => {
        const label = document.createElement("label");
        const caption = document.createElement("span");
        caption.append(field.label);
        if (field.ollama) caption.append(Object.assign(document.createElement("em"), { className: "tag", textContent: "Ollama" }));
        let input;
        if (field.kind === "bool") {
            input = document.createElement("select");
            input.append(new Option("Model default", ""), new Option("On", "true"), new Option("Off", "false"));
            input.value = values[field.name] === undefined ? "" : String(values[field.name]);
        } else {
            input = Object.assign(document.createElement("input"), {
                type: field.kind === "text" ? "text" : "number", placeholder: field.hint, spellcheck: false,
            });
            if (field.kind !== "text") Object.assign(input, { step: field.step, min: field.min ?? "", max: field.max ?? "" });
            input.inputMode = field.kind === "int" ? "numeric" : field.kind === "float" ? "decimal" : "text";
            input.value = values[field.name] ?? "";
        }
        input.dataset.option = field.name;
        input.addEventListener("change", () => onChange(readOptions(container)));
        label.classList.toggle("unused", Boolean(field.ollama && backend === "lmstudio"));
        label.title = field.ollama && backend === "lmstudio" ? "Only Ollama uses this one" : "";
        label.append(caption, input);
        return label;
    }));
}

function readOptions(container) {
    const options = {};
    for (const input of container.querySelectorAll("[data-option]")) {
        const field = OPTION_FIELDS.find(f => f.name === input.dataset.option);
        const raw = input.value.trim();
        if (raw === "") continue;
        if (field.kind === "bool") options[field.name] = raw === "true";
        else if (field.kind === "text") options[field.name] = raw;
        else options[field.name] = field.kind === "int" ? Math.round(Number(raw)) : Number(raw);
    }
    return options;
}

function paintChatOptions() {
    const settings = state.chat || state.draft;
    const picked = selectedModel();
    const backend = picked ? picked.backend : null;
    renderOptions($("chatOptions"), settings.options || {}, saveChatOptions, backend);
    $("chatOptionsNote").textContent = backend === "lmstudio"
        ? "Greyed-out ones are Ollama only."
        : "Empty means the model's own default.";
}

async function saveChatOptions(options) {
    if (!state.chat) {
        state.draft.options = options;
        return;
    }
    try {
        const saved = await api(`/api/chats/${state.chat.id}`, { method: "PATCH", body: { options } });
        state.chat.options = saved.options;
        showSaved("chatOptions", "Saved");
    } catch (error) {
        showSaved("chatOptions", error.message, true);
    }
}

// Attachments

state.pending = [];

function formatSize(bytes) {
    return bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function fileChip(file, onRemove) {
    const chip = Object.assign(document.createElement(file.url && !onRemove ? "a" : "div"), { className: "file-chip" });
    if (file.url && !onRemove) Object.assign(chip, { href: file.url, title: `Download ${file.name}` });
    if (file.status === "error") chip.classList.add("error");
    if (file.status === "uploading") chip.classList.add("loading");
    if (file.kind === "image" && (file.preview || file.url)) {
        chip.append(Object.assign(document.createElement("img"), { src: file.preview || file.url, alt: "" }));
    } else {
        const ext = (file.name.split(".").pop() || "file").slice(0, 4);
        chip.append(Object.assign(document.createElement("span"), { className: "file-icon", textContent: ext }));
    }
    const text = Object.assign(document.createElement("span"), { className: "file-text" });
    const detail = file.status === "uploading" ? "Uploading..." : file.status === "error" ? file.error
        : `${formatSize(file.size)}${file.truncated ? " · cut to fit" : ""}`;
    text.append(Object.assign(document.createElement("span"), { className: "file-name", textContent: file.name, title: file.name }),
        Object.assign(document.createElement("span"), { className: "file-size", textContent: detail }));
    chip.append(text);
    if (onRemove) chip.append(iconButton("close", `Remove ${file.name}`, onRemove));
    return chip;
}

function renderPending() {
    const box = $("pendingFiles");
    box.hidden = !state.pending.length;
    box.replaceChildren(...state.pending.map(file => fileChip(file, () => removePending(file))));
}

function removePending(file) {
    state.pending = state.pending.filter(f => f !== file);
    if (file.id) api(`/api/uploads/${file.id}`, { method: "DELETE" }).catch(() => {});
    if (file.preview) URL.revokeObjectURL(file.preview);
    renderPending();
}

async function addFiles(files) {
    for (const blob of files) {
        const file = { name: blob.name || "pasted-image.png", size: blob.size, status: "uploading",
                       kind: blob.type.startsWith("image/") ? "image" : "text",
                       preview: blob.type.startsWith("image/") ? URL.createObjectURL(blob) : null };
        state.pending.push(file);
        renderPending();
        const form = new FormData();
        form.append("file", blob, file.name);
        try {
            const response = await fetch("/api/uploads", { method: "POST", body: form });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || `Upload failed (${response.status})`);
            Object.assign(file, result, { status: "ready" });
            if (!state.pending.includes(file)) api(`/api/uploads/${file.id}`, { method: "DELETE" }).catch(() => {});
        } catch (error) {
            Object.assign(file, { status: "error", error: error.message });
        }
        renderPending();
    }
}

$("attachButton").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", () => {
    addFiles([...$("fileInput").files]);
    $("fileInput").value = "";
});
$("prompt").addEventListener("paste", event => {
    const files = [...(event.clipboardData?.files || [])];
    if (files.length) {
        event.preventDefault();
        addFiles(files);
    }
});

let dragDepth = 0;
const dragHasFiles = event => [...(event.dataTransfer?.types || [])].includes("Files");
$("view-chat").addEventListener("dragenter", event => {
    if (!dragHasFiles(event)) return;
    dragDepth++;
    $("dropOverlay").hidden = false;
});
$("view-chat").addEventListener("dragleave", () => {
    if (--dragDepth <= 0) { dragDepth = 0; $("dropOverlay").hidden = true; }
});
$("view-chat").addEventListener("dragover", event => { if (dragHasFiles(event)) event.preventDefault(); });
$("view-chat").addEventListener("drop", event => {
    if (!dragHasFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    $("dropOverlay").hidden = true;
    addFiles([...event.dataTransfer.files]);
});

// One chat

async function openChat(chatId) {
    if (state.streaming) state.streaming.abort();
    state.chat = null;
    state.draft = newChatDefaults();
    if (chatId) {
        try {
            state.chat = await api(`/api/chats/${chatId}`);
            pickModel(modelKey(state.chat.backend, state.chat.model));
        } catch (error) {
            location.hash = "#chat";
            return;
        }
    }
    const settings = state.chat || state.draft;
    $("systemPrompt").value = settings.system_prompt;
    paintChatOptions();
    renderMessages();
    renderChatList();
}

function splitThinking(message) {
    // Some models write their reasoning inline as <think>...</think>.
    let { content, thinking = "" } = message;
    const inline = content.match(/^\s*<think>([\s\S]*?)(<\/think>|$)/);
    if (inline) {
        thinking = (thinking + "\n" + inline[1]).trim();
        content = content.slice(inline[0].length);
    }
    return { thinking: thinking.trim(), content: content.trim() };
}

function messageElement(message, options = {}) {
    const box = document.createElement("div");
    box.className = `message ${message.role}`;
    if (message.role === "user") {
        const files = message.attachments || [];
        if (files.length) {
            const list = Object.assign(document.createElement("div"), { className: "message-files" });
            list.append(...files.map(file => {
                if (file.kind !== "image") return fileChip(file);
                const link = Object.assign(document.createElement("a"), { className: "image-file", href: file.url || file.preview, target: "_blank" });
                link.append(Object.assign(document.createElement("img"), { src: file.url || file.preview, alt: file.name, loading: "lazy" }));
                return link;
            }));
            box.append(list);
        }
        if (message.content) box.append(Object.assign(document.createElement("div"), { className: "bubble", textContent: message.content }));
    } else {
        const { thinking, content } = splitThinking(message);
        if (thinking) {
            const details = Object.assign(document.createElement("details"), { className: "thinking", open: Boolean(options.streaming && !content) });
            details.append(Object.assign(document.createElement("summary"), { textContent: options.streaming && !content ? "Thinking..." : "Thought process" }),
                Object.assign(document.createElement("div"), { textContent: thinking }));
            box.append(details);
        }
        const reply = Object.assign(document.createElement("div"), { className: "reply" });
        reply.innerHTML = renderMarkdown(content);
        if (options.streaming) reply.classList.add("cursor");
        box.append(reply);
        if (options.error) box.append(Object.assign(document.createElement("p"), { className: "error", textContent: options.error }));
    }
    if (!options.streaming) {
        const meta = Object.assign(document.createElement("div"), { className: "meta" });
        if (message.role === "assistant" && message.model) {
            const stats = message.stats;
            const detail = stats ? ` · ${stats.tokens_per_second} tok/s · ${stats.tokens} tokens` : "";
            meta.append(Object.assign(document.createElement("span"), { textContent: message.model + detail }));
        }
        if (message.content || message.role === "assistant") {
            const copy = button("Copy", () => copyText(splitThinking(message).content || message.content, copy));
            meta.append(copy);
        }
        if (options.last && message.role === "assistant") meta.append(button("Regenerate", () => sendMessage(null)));
        box.append(meta);
    }
    return box;
}

function renderMessages(live) {
    const box = $("messages");
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    const messages = state.chat ? state.chat.messages : [];
    if (!messages.length && !live) {
        const empty = Object.assign(document.createElement("div"), { className: "empty" });
        empty.textContent = state.models.length
            ? "Pick a model and send a message. You can attach images, text files and PDFs."
            : "No models yet. Start LM Studio's server or Ollama, then press ↻.";
        box.replaceChildren(empty);
        return;
    }
    const nodes = messages.map((message, i) => messageElement(message, { last: !live && i === messages.length - 1 }));
    if (live) nodes.push(messageElement(live.message, live));
    box.replaceChildren(...nodes);
    if (nearBottom || !live) box.scrollTop = box.scrollHeight;
}

$("messages").addEventListener("click", event => {
    const copy = event.target.closest(".copy-code");
    if (copy) copyText(copy.closest(".code-block").querySelector("code").textContent, copy);
});

async function sendMessage(content, files = []) {
    const picked = selectedModel();
    if (!picked) {
        alert("Pick a model first. If the list is empty, start LM Studio's server or Ollama and press ↻.");
        return false;
    }
    if (!state.chat) {
        state.chat = await api("/api/chats", {
            method: "POST",
            body: { ...picked, system_prompt: $("systemPrompt").value, options: state.draft.options || {} },
        });
        history.replaceState(null, "", `#chat/${state.chat.id}`);
    }
    const chat = state.chat;
    if (content !== null) {
        chat.messages.push({ role: "user", content, attachments: files });
    } else {
        while (chat.messages.length && chat.messages[chat.messages.length - 1].role === "assistant") chat.messages.pop();
    }

    const controller = new AbortController();
    state.streaming = controller;
    setSending(true);
    const live = { message: { role: "assistant", content: "", thinking: "", model: picked.model }, streaming: true };
    renderMessages(live);
    let paint = null;
    const schedule = () => paint ??= requestAnimationFrame(() => { paint = null; renderMessages(live); });

    try {
        const body = content === null ? { ...picked, regenerate: true }
            : { ...picked, content, attachments: files.map(file => file.id) };
        const response = await fetch(`/api/chats/${chat.id}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
            signal: controller.signal,
        });
        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || `Request failed (${response.status})`);
        }
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.trim()) continue;
                const event = JSON.parse(line);
                if (event.text) live.message.content += event.text;
                if (event.think) live.message.thinking += event.think;
                if (event.error) live.error = event.error;
            }
            schedule();
        }
    } catch (error) {
        if (error.name !== "AbortError") live.error = error.message;
    } finally {
        if (paint) cancelAnimationFrame(paint);
        if (state.streaming === controller) state.streaming = null;
        setSending(false);
    }
    if (state.chat !== chat) return true;
    // Reload from the database so what's shown is exactly what was saved.
    try {
        state.chat = await api(`/api/chats/${chat.id}`);
    } catch { /* keep what we have */ }
    renderMessages();
    if (live.error) {
        const error = Object.assign(document.createElement("div"), { className: "message" });
        error.append(Object.assign(document.createElement("div"), { className: "error", textContent: live.error }));
        $("messages").append(error);
        $("messages").scrollTop = $("messages").scrollHeight;
    }
    loadChats();
    return true;
}

function setSending(sending) {
    const send = $("sendButton");
    send.innerHTML = icon(sending ? "stop" : "send", 20);
    send.title = sending ? "Stop" : "Send";
    send.setAttribute("aria-label", send.title);
    send.classList.toggle("stop", sending);
}

$("composer").addEventListener("submit", async event => {
    event.preventDefault();
    if (state.streaming) {
        state.streaming.abort();
        return;
    }
    if (state.pending.some(file => file.status === "uploading")) return;
    const content = $("prompt").value;
    const files = state.pending.filter(file => file.status === "ready");
    if (!content.trim() && !files.length) return;
    $("prompt").value = "";
    autoGrow($("prompt"));
    const sentPending = state.pending;
    state.pending = [];
    renderPending();
    try {
        if (!(await sendMessage(content, files))) throw new Error("not sent");
        sentPending.filter(file => file.status === "error").forEach(file => file.preview && URL.revokeObjectURL(file.preview));
    } catch (error) {
        // Nothing was sent: put the message and its files back.
        $("prompt").value = content;
        autoGrow($("prompt"));
        state.pending = sentPending;
        renderPending();
        if (error.message !== "not sent") alert(error.message);
    }
});

function enterSends() {
    // Unset: on with a keyboard, off on phones (where Enter is how you get a new line).
    const saved = storageGet("inphai.enterSends");
    return saved === null ? !matchMedia("(pointer: coarse)").matches : saved === "on";
}

$("prompt").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && enterSends()) {
        event.preventDefault();
        if (!state.streaming) $("composer").requestSubmit();
    }
});

$("settingsButton").addEventListener("click", () => {
    const open = $("settings").hidden;
    $("settings").hidden = !open;
    $("settingsButton").setAttribute("aria-expanded", String(open));
    if (open) autoGrow($("systemPrompt"));
});

$("systemPrompt").addEventListener("change", () => {
    const system_prompt = $("systemPrompt").value;
    if (!state.chat) {
        state.draft.system_prompt = system_prompt;
        return;
    }
    state.chat.system_prompt = system_prompt;
    api(`/api/chats/${state.chat.id}`, { method: "PATCH", body: { system_prompt } })
        .then(() => showSaved("chatOptions", "Saved"))
        .catch(error => showSaved("chatOptions", error.message, true));
});

// Images and videos

function updateImageSteps() {
    const anime = $("imageModel").value === "anime";
    const choices = anime ? [20, 28, 30, 40, 50] : [4, 6, 9, 12];
    const recommended = anime ? 28 : 9;
    $("imageSteps").replaceChildren(...choices.map(value => {
        const text = value === recommended ? `${value} · default` : String(value);
        return new Option(text, String(value), value === recommended, value === recommended);
    }));
}

function updateVideoLength() {
    const seconds = Number($("videoFrames").value) / Number($("videoFps").value);
    $("videoLength").textContent = `About ${seconds.toFixed(1)} s of video. It takes a while, and keeps going if you leave the page.`;
}

function updateImageNegative() {
    // Z-Image-Turbo runs without a negative prompt; generate_image.py rejects one.
    const realistic = $("imageModel").value === "realistic";
    $("imageNegative").disabled = realistic;
    $("imageNegativeNote").textContent = realistic ? "· not used by Z-Image-Turbo" : "";
}

function fillNegatives(force = false) {
    if (!state.settings) return;
    for (const kind of ["image", "video"]) {
        const box = $(`${kind}Negative`);
        if (force || !box.dataset.touched) {
            box.value = state.settings[`${kind}_negative_prompt`];
            autoGrow(box);
        }
    }
}

$("imageModel").addEventListener("change", () => {
    updateImageSteps();
    updateImageNegative();
});
for (const kind of ["image", "video"]) {
    $(`${kind}Negative`).addEventListener("input", event => event.target.dataset.touched = "1");
}
$("videoFrames").addEventListener("change", updateVideoLength);
$("videoFps").addEventListener("change", updateVideoLength);

function seedFrom(select) {
    return select.value === "random" ? Math.floor(Math.random() * 2 ** 31) : Number(select.value);
}

function generationSettings(kind) {
    if (kind === "image") {
        const [width, height] = $("imageResolution").value.split("x").map(Number);
        const settings = { model: $("imageModel").value, prompt: $("imagePrompt").value.trim(), width, height,
                           steps: Number($("imageSteps").value), seed: seedFrom($("imageSeed")) };
        if (settings.model === "anime") settings.negative_prompt = $("imageNegative").value.trim();
        return settings;
    }
    const [width, height] = $("videoResolution").value.split("x").map(Number);
    return { prompt: $("videoPrompt").value.trim(), negative_prompt: $("videoNegative").value.trim(), width, height,
             frames: Number($("videoFrames").value), steps: Number($("videoSteps").value),
             fps: Number($("videoFps").value), seed: seedFrom($("videoSeed")) };
}

async function generate(kind) {
    const settings = generationSettings(kind);
    if (!settings.prompt) {
        $(`${kind}Prompt`).focus();
        return;
    }
    try {
        state.job = await api(kind === "image" ? "/api/images" : "/api/videos", { method: "POST", body: settings });
        renderJob();
        pollJob();
    } catch (error) {
        state.job = { state: "error", kind, error: error.message, output: [] };
        renderJob();
    }
}

document.querySelectorAll(".generate-button").forEach(button =>
    button.addEventListener("click", () => generate(button.dataset.kind)));

function jobMessage(job) {
    if (job.state === "running") {
        if (job.stage === "generating" && job.completed_steps > 0) {
            return `Generating: ${job.completed_steps} / ${job.total_steps} steps`;
        }
        return STAGES[job.stage] || "Generating...";
    }
    if (job.state === "done") return `Done in ${Math.round(job.media.seconds)} s.`;
    if (job.state === "cancelled") return "Cancelled.";
    if (job.state === "error") return `Error: ${job.error}`;
    return "";
}

function renderJob() {
    const job = state.job;
    const running = job && job.state === "running";
    for (const kind of ["image", "video"]) {
        const box = $(`${kind}Job`);
        const generateButton = $(`${kind}Generate`);
        generateButton.disabled = running;
        generateButton.textContent = running && job.kind === kind ? "Generating..." : "Generate";
        if (!job || job.kind !== kind || job.state === "idle") {
            box.hidden = !running;
            if (running) box.replaceChildren(Object.assign(document.createElement("p"), {
                textContent: `Waiting for the ${job.kind} that is generating to finish.` }));
            continue;
        }
        box.hidden = false;
        const message = Object.assign(document.createElement("p"), { textContent: jobMessage(job) });
        if (job.state === "error") message.className = "error";
        const parts = [message];
        if (running) {
            const row = Object.assign(document.createElement("div"), { className: "row" });
            row.append(progressBar(job), button("Cancel", () => api("/api/job/cancel", { method: "POST" }).then(pollJob), "btn btn-sm"));
            parts.push(row);
        }
        if (job.output && job.output.length && (running || job.state === "error")) {
            const details = document.createElement("details");
            details.append(Object.assign(document.createElement("summary"), { textContent: "Output" }),
                Object.assign(document.createElement("pre"), { textContent: job.output.join("\n") }));
            parts.push(details);
        }
        box.replaceChildren(...parts);
    }

    const pill = $("jobPill");
    pill.hidden = !running;
    if (running) {
        const link = Object.assign(document.createElement("a"), {
            href: job.kind === "image" ? "#images" : "#videos",
            textContent: `${job.kind === "image" ? "Image" : "Video"} · ${jobMessage(job)}`,
        });
        pill.replaceChildren(link, progressBar(job));
    }
}

function progressBar(job) {
    const bar = Object.assign(document.createElement("div"), { className: "bar" });
    const fill = document.createElement("div");
    fill.style.width = job.total_steps ? `${Math.round(100 * job.completed_steps / job.total_steps)}%` : "0";
    bar.append(fill);
    return bar;
}

async function pollJob() {
    clearTimeout(state.jobTimer);
    try {
        const previous = state.job;
        const job = await api("/api/job");
        // Keep showing a failed start (e.g. a busy GPU) instead of the idle status.
        if (!(job.state === "idle" && previous && previous.state === "error")) state.job = job;
        renderJob();
        if (previous && previous.state === "running" && job.state !== "running") {
            loadGallery(job.kind);
        }
    } catch (error) {
        console.log("Could not check the job:", error);
    }
    if (state.job && state.job.state === "running") state.jobTimer = setTimeout(pollJob, 2000);
}

async function loadGallery(kind) {
    const gallery = $(`${kind}Gallery`);
    let items;
    try {
        items = (await api(`/api/media?kind=${kind}`)).media;
    } catch (error) {
        gallery.replaceChildren(Object.assign(document.createElement("p"), { className: "note", textContent: error.message }));
        return;
    }
    if (!items.length) {
        gallery.replaceChildren(Object.assign(document.createElement("p"), { className: "note",
            textContent: `Your ${kind}s will show up here.` }));
        return;
    }
    gallery.replaceChildren(...items.map(item => {
        const card = Object.assign(document.createElement("div"), { className: "card media-card" });
        let media;
        if (kind === "image") {
            media = Object.assign(document.createElement("img"), { src: item.url, alt: item.prompt, loading: "lazy" });
            const link = Object.assign(document.createElement("a"), { href: item.url, target: "_blank" });
            link.append(media);
            media = link;
        } else {
            media = Object.assign(document.createElement("video"), { src: item.url, controls: true, preload: "metadata", loop: true });
        }
        const caption = Object.assign(document.createElement("div"), { className: "caption" });
        caption.append(Object.assign(document.createElement("p"), { textContent: item.prompt, title: item.prompt }));
        const s = item.settings;
        const details = kind === "image"
            ? `${s.model} · ${s.width}×${s.height} · ${s.steps} steps · seed ${s.seed}`
            : `${s.width}×${s.height} · ${s.frames} frames · ${s.fps} fps · seed ${s.seed}`;
        caption.append(Object.assign(document.createElement("div"), { className: "details", textContent: `${details} · ${timeAgo(item.created_at)}` }));
        const actions = Object.assign(document.createElement("div"), { className: "actions" });
        const download = Object.assign(document.createElement("a"), { className: "btn btn-ghost btn-sm", href: item.url, download: "", textContent: "Download" });
        actions.append(button("Reuse", () => reuseSettings(kind, s)), download, button("Delete", async () => {
            if (!confirm(`Delete this ${kind}?`)) return;
            await api(`/api/media/${item.id}`, { method: "DELETE" }).catch(error => alert(error.message));
            loadGallery(kind);
        }));
        caption.append(actions);
        card.append(media, caption);
        return card;
    }));
}

function setSelect(select, value) {
    value = String(value);
    if (![...select.options].some(option => option.value === value)) select.append(new Option(value, value));
    select.value = value;
}

function reuseSettings(kind, s) {
    const prompt = $(`${kind}Prompt`);
    prompt.value = s.prompt;
    autoGrow(prompt);
    const negative = $(`${kind}Negative`);
    if (typeof s.negative_prompt === "string" && s.negative_prompt) {
        negative.value = s.negative_prompt;
        negative.dataset.touched = "1";
        autoGrow(negative);
    }
    if (kind === "image") {
        $("imageModel").value = s.model;
        updateImageSteps();
        updateImageNegative();
        setSelect($("imageResolution"), `${s.width}x${s.height}`);
        setSelect($("imageSteps"), s.steps);
        setSelect($("imageSeed"), s.seed);
    } else {
        setSelect($("videoResolution"), `${s.width}x${s.height}`);
        setSelect($("videoFrames"), s.frames);
        setSelect($("videoSteps"), s.steps);
        setSelect($("videoFps"), s.fps);
        setSelect($("videoSeed"), s.seed);
        updateVideoLength();
    }
    $(`view-${kind}s`).scrollTop = 0;
    prompt.focus();
}

// Settings page

const SAVED_TIMERS = {};

function showSaved(group, message, error = false) {
    const label = document.querySelector(`[data-saved="${group}"]`);
    if (!label) return;
    label.textContent = message;
    label.classList.toggle("error", error);
    clearTimeout(SAVED_TIMERS[group]);
    if (!error) SAVED_TIMERS[group] = setTimeout(() => label.textContent = "", 1800);
}

async function loadSettings() {
    try {
        const result = await api("/api/settings");
        state.settings = result.settings;
        state.settingsInfo = result;
        if (!state.chat) {
            state.draft = newChatDefaults();
            $("systemPrompt").value = state.draft.system_prompt;
            paintChatOptions();
        }
        fillNegatives();
        return result;
    } catch (error) {
        console.error(error);
        return null;
    }
}

function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

async function loadSettingsPage() {
    const result = await loadSettings();
    if (!result) return;
    for (const field of document.querySelectorAll("[data-setting]")) {
        const value = result.settings[field.dataset.setting];
        if (field.type === "checkbox") field.checked = Boolean(value);
        else field.value = value === null ? "" : String(value);
        if (field.tagName === "TEXTAREA") autoGrow(field);
    }
    $("enterSends").checked = enterSends();
    renderOptions($("defaultOptions"), result.settings.default_options, saveDefaultOptions, null);
    setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light");
    const { counts, disk } = result;
    const stats = [["Chats", counts.chats], ["Messages", counts.messages], ["Files", counts.files],
                   ["Images", counts.images], ["Videos", counts.videos], ["Database", formatBytes(disk.database)],
                   ["Chat files", formatBytes(disk.files)], ["Media", formatBytes(disk.images + disk.videos)]];
    $("dataStats").replaceChildren(...stats.map(([name, value]) => {
        const item = document.createElement("div");
        item.append(Object.assign(document.createElement("dt"), { textContent: name }),
            Object.assign(document.createElement("dd"), { textContent: String(value) }));
        return item;
    }));
}

async function saveSetting(field) {
    const key = field.dataset.setting;
    let value = field.type === "checkbox" ? field.checked : field.value;
    try {
        state.settings = (await api("/api/settings", { method: "PATCH", body: { [key]: value } })).settings;
        showSaved(field.dataset.group, "Saved");
        if (key.endsWith("_negative_prompt")) fillNegatives();
        if (key.endsWith("_url")) loadModels();
    } catch (error) {
        showSaved(field.dataset.group, error.message, true);
    }
}

async function saveDefaultOptions(options) {
    try {
        state.settings = (await api("/api/settings", { method: "PATCH", body: { default_options: options } })).settings;
        showSaved("chat", "Saved");
    } catch (error) {
        showSaved("chat", error.message, true);
    }
}

document.querySelectorAll("[data-setting]").forEach(field =>
    field.addEventListener("change", () => saveSetting(field)));

document.querySelectorAll("[data-test]").forEach(testButton => testButton.addEventListener("click", async () => {
    const backend = testButton.dataset.test;
    const output = $(`test-${backend}`);
    output.className = "test-result";
    output.textContent = "Checking...";
    try {
        const result = await api("/api/settings/test", { method: "POST", body: { backend, url: $(`set-${backend}_url`).value } });
        output.classList.add(result.ok ? "good" : "bad");
        output.textContent = result.ok
            ? `Connected · ${result.models} model${result.models === 1 ? "" : "s"}`
            : result.error;
    } catch (error) {
        output.classList.add("bad");
        output.textContent = error.message;
    }
}));

$("enterSends").addEventListener("change", () => storageSet("inphai.enterSends", $("enterSends").checked ? "on" : "off"));
document.querySelectorAll("[data-theme-choice]").forEach(choice =>
    choice.addEventListener("click", () => setTheme(choice.dataset.themeChoice)));

$("resetNegatives").addEventListener("click", async () => {
    const defaults = state.settingsInfo.defaults;
    const fields = { image_negative_prompt: defaults.image_negative_prompt, video_negative_prompt: defaults.video_negative_prompt };
    try {
        state.settings = (await api("/api/settings", { method: "PATCH", body: fields })).settings;
        for (const key of Object.keys(fields)) {
            $(`set-${key}`).value = fields[key];
            autoGrow($(`set-${key}`));
        }
        fillNegatives(true);
        showSaved("generation", "Restored");
    } catch (error) {
        showSaved("generation", error.message, true);
    }
});

$("deleteAllChats").addEventListener("click", async () => {
    if (!state.settingsInfo || !state.settingsInfo.counts.chats) return;
    const count = state.settingsInfo.counts.chats;
    if (!confirm(`Delete all ${count} chat${count === 1 ? "" : "s"}? This can't be undone.`)) return;
    await api("/api/chats", { method: "DELETE" }).catch(error => alert(error.message));
    state.chat = null;
    loadChats();
    loadSettingsPage();
});

// Start

for (const id of ["prompt", "imagePrompt", "videoPrompt", "imageNegative", "videoNegative",
                  "set-default_system_prompt", "set-image_negative_prompt", "set-video_negative_prompt"]) {
    $(id).addEventListener("input", () => autoGrow($(id)));
}
updateImageSteps();
updateImageNegative();
updateVideoLength();
loadSettings();
setSending(false);
tick();
setInterval(tick, 1000);
showView();
loadModels();
loadChats();
pollJob();
