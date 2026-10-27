const $ = id => document.getElementById(id);
const state = {
    view: "chat",
    chats: [],
    chat: null,          // the open chat with its messages, or null for a new one
    draft: { system_prompt: "", temperature: null },
    models: [],
    streaming: null,     // AbortController while a reply streams
    job: null,
    jobTimer: null,
};
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
    state.view = ["chat", "images", "videos"].includes(view) ? view : "chat";
    for (const name of ["chat", "images", "videos"]) $(`view-${name}`).hidden = name !== state.view;
    document.querySelectorAll(".tabs a").forEach(a => a.classList.toggle("active", a.dataset.view === state.view));
    $("chatSide").hidden = state.view !== "chat";
    document.title = { chat: "inphAI", images: "Images · inphAI", videos: "Videos · inphAI" }[state.view];
    closeDrawer();
    if (state.view === "chat") {
        const chatId = Number(id) || null;
        if (chatId !== (state.chat && state.chat.id)) openChat(chatId);
    } else {
        loadGallery(state.view === "images" ? "image" : "video");
    }
}

function closeDrawer() {
    document.body.classList.remove("drawerOpen");
    $("backdrop").hidden = true;
}

$("menuButton").addEventListener("click", () => {
    document.body.classList.add("drawerOpen");
    $("backdrop").hidden = false;
});
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
        const rename = Object.assign(document.createElement("button"), { type: "button", textContent: "✎", title: "Rename" });
        rename.addEventListener("click", () => renameChat(chat));
        const remove = Object.assign(document.createElement("button"), { type: "button", textContent: "✕", title: "Delete" });
        remove.addEventListener("click", () => deleteChat(chat));
        item.append(link, rename, remove);
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

async function openChat(chatId) {
    if (state.streaming) state.streaming.abort();
    state.chat = null;
    state.draft = { system_prompt: "", temperature: null };
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
    $("temperature").value = settings.temperature === null ? "" : String(settings.temperature);
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
        const bubble = Object.assign(document.createElement("div"), { className: "bubble", textContent: message.content });
        box.append(bubble);
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
        if (message.role === "assistant" && message.model) meta.append(Object.assign(document.createElement("span"), { textContent: message.model }));
        const copy = Object.assign(document.createElement("button"), { type: "button", textContent: "Copy" });
        copy.addEventListener("click", () => copyText(splitThinking(message).content || message.content, copy));
        meta.append(copy);
        if (options.last && message.role === "assistant") {
            const again = Object.assign(document.createElement("button"), { type: "button", textContent: "Regenerate" });
            again.addEventListener("click", () => sendMessage(null));
            meta.append(again);
        }
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
            ? "Pick a model and send a message."
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
    const button = event.target.closest(".copyCode");
    if (button) copyText(button.closest(".codeBlock").querySelector("code").textContent, button);
});

async function sendMessage(content) {
    const picked = selectedModel();
    if (!picked) {
        alert("Pick a model first. If the list is empty, start LM Studio's server or Ollama and press ↻.");
        return;
    }
    if (!state.chat) {
        const temperature = $("temperature").value;
        state.chat = await api("/api/chats", {
            method: "POST",
            body: { ...picked, system_prompt: $("systemPrompt").value, temperature: temperature === "" ? null : Number(temperature) },
        });
        history.replaceState(null, "", `#chat/${state.chat.id}`);
    }
    const chat = state.chat;
    if (content !== null) {
        chat.messages.push({ role: "user", content });
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
        const response = await fetch(`/api/chats/${chat.id}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(content === null ? { ...picked, regenerate: true } : { ...picked, content }),
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
    if (state.chat !== chat) return;
    // Reload from the database so what's shown is exactly what was saved.
    try {
        state.chat = await api(`/api/chats/${chat.id}`);
    } catch { /* keep what we have */ }
    renderMessages();
    if (live.error) {
        const error = Object.assign(document.createElement("div"), { className: "message" });
        error.append(Object.assign(document.createElement("p"), { className: "error", textContent: live.error }));
        $("messages").append(error);
        $("messages").scrollTop = $("messages").scrollHeight;
    }
    loadChats();
}

function setSending(sending) {
    const button = $("sendButton");
    button.textContent = sending ? "Stop" : "Send";
    button.classList.toggle("stop", sending);
}

$("composer").addEventListener("submit", event => {
    event.preventDefault();
    if (state.streaming) {
        state.streaming.abort();
        return;
    }
    const content = $("prompt").value;
    if (!content.trim()) return;
    $("prompt").value = "";
    autoGrow($("prompt"));
    sendMessage(content).catch(error => alert(error.message));
});

$("prompt").addEventListener("keydown", event => {
    // Enter sends, Shift+Enter is a new line (and phones keep Enter as a new line).
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !matchMedia("(pointer: coarse)").matches) {
        event.preventDefault();
        if (!state.streaming) $("composer").requestSubmit();
    }
});

$("settingsButton").addEventListener("click", () => {
    const open = $("settings").hidden;
    $("settings").hidden = !open;
    $("settingsButton").setAttribute("aria-expanded", String(open));
});

function saveChatSettings() {
    const temperature = $("temperature").value === "" ? null : Number($("temperature").value);
    const fields = { system_prompt: $("systemPrompt").value, temperature };
    if (!state.chat) {
        state.draft = fields;
        return;
    }
    Object.assign(state.chat, fields);
    api(`/api/chats/${state.chat.id}`, { method: "PATCH", body: fields }).catch(error => alert(error.message));
}

$("systemPrompt").addEventListener("change", saveChatSettings);
$("temperature").addEventListener("change", saveChatSettings);

// Images and videos

function updateImageSteps() {
    const anime = $("imageModel").value === "anime";
    const choices = anime ? [20, 28, 30, 40, 50] : [4, 6, 9, 12];
    const recommended = anime ? 28 : 9;
    $("imageSteps").replaceChildren(...choices.map(value => {
        const text = value === recommended ? `${value} — Default` : String(value);
        return new Option(text, String(value), value === recommended, value === recommended);
    }));
}

function updateVideoLength() {
    const seconds = Number($("videoFrames").value) / Number($("videoFps").value);
    $("videoLength").textContent = `About ${seconds.toFixed(1)} seconds of video. Videos take a long time; you can leave this page while it runs.`;
}

$("imageModel").addEventListener("change", updateImageSteps);
$("videoFrames").addEventListener("change", updateVideoLength);
$("videoFps").addEventListener("change", updateVideoLength);

function seedFrom(select) {
    return select.value === "random" ? Math.floor(Math.random() * 2 ** 31) : Number(select.value);
}

function generationSettings(kind) {
    if (kind === "image") {
        const [width, height] = $("imageResolution").value.split("x").map(Number);
        return { model: $("imageModel").value, prompt: $("imagePrompt").value.trim(), width, height,
                 steps: Number($("imageSteps").value), seed: seedFrom($("imageSeed")) };
    }
    const [width, height] = $("videoResolution").value.split("x").map(Number);
    return { prompt: $("videoPrompt").value.trim(), width, height, frames: Number($("videoFrames").value),
             steps: Number($("videoSteps").value), fps: Number($("videoFps").value), seed: seedFrom($("videoSeed")) };
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

document.querySelectorAll(".generateButton").forEach(button =>
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
        const button = $(`${kind}Generate`);
        button.disabled = running;
        button.textContent = running && job.kind === kind ? "Generating..." : "Generate";
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
            const bar = Object.assign(document.createElement("div"), { className: "bar" });
            const fill = document.createElement("div");
            fill.style.width = job.total_steps ? `${Math.round(100 * job.completed_steps / job.total_steps)}%` : "0";
            bar.append(fill);
            const cancel = Object.assign(document.createElement("button"), { type: "button", className: "plainButton", textContent: "Cancel" });
            cancel.addEventListener("click", () => api("/api/job/cancel", { method: "POST" }).then(pollJob));
            parts.push(bar, cancel);
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
        pill.replaceChildren(Object.assign(document.createElement("a"), {
            href: job.kind === "image" ? "#images" : "#videos",
            textContent: `${job.kind === "image" ? "Image" : "Video"}: ${jobMessage(job)}`,
        }));
    }
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
        const card = Object.assign(document.createElement("div"), { className: "card" });
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
        const meta = Object.assign(document.createElement("div"), { className: "meta" });
        const s = item.settings;
        const details = kind === "image"
            ? `${s.model} · ${s.width}×${s.height} · ${s.steps} steps · seed ${s.seed}`
            : `${s.width}×${s.height} · ${s.frames} frames · ${s.fps} fps · seed ${s.seed}`;
        meta.append(Object.assign(document.createElement("span"), { textContent: `${details} · ${timeAgo(item.created_at)}` }));
        const reuse = Object.assign(document.createElement("button"), { type: "button", textContent: "Reuse" });
        reuse.addEventListener("click", () => reuseSettings(kind, s));
        const download = Object.assign(document.createElement("a"), { href: item.url, download: "", textContent: "Download" });
        const remove = Object.assign(document.createElement("button"), { type: "button", textContent: "Delete" });
        remove.addEventListener("click", async () => {
            if (!confirm(`Delete this ${kind}?`)) return;
            await api(`/api/media/${item.id}`, { method: "DELETE" }).catch(error => alert(error.message));
            loadGallery(kind);
        });
        meta.append(reuse, download, remove);
        caption.append(meta);
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
    if (kind === "image") {
        $("imageModel").value = s.model;
        updateImageSteps();
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
}

// Start

for (const id of ["prompt", "imagePrompt", "videoPrompt"]) {
    $(id).addEventListener("input", () => autoGrow($(id)));
}
updateImageSteps();
updateVideoLength();
showView();
loadModels();
loadChats();
pollJob();
