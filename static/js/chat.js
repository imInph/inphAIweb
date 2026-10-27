import { renderChatKnowledge, sourcesElement } from "./knowledge.js";
import { $, api, autoGrow, button, copyText, icon, iconButton, request } from "./util.js";
import { state } from "./state.js";
import { chatKey, pickModel, selectedModel } from "./models.js";
import { loadChats, renderChatList } from "./chats.js";
import { paintChatOptions } from "./options.js";
import { fileChip, renderPending } from "./attachments.js";
import { showSaved } from "./settings.js";
import { enhance, togglePreview } from "./render.js";

// One chat

export function newChatDefaults() {
    const defaults = state.me ? state.me.prefs : {};
    return { system_prompt: defaults.default_system_prompt || "", options: { ...(defaults.default_options || {}) }, knowledge_ids: [] };
}

export async function openChat(chatId) {
    stopStreams();
    state.live = null;
    state.compare = null;
    state.editing = null;
    state.chat = null;
    state.draft = newChatDefaults();
    if (chatId) {
        try {
            state.chat = await api(`/api/chats/${chatId}`);
            pickModel(chatKey(state.chat));
        } catch (error) {
            location.hash = "#chat";
            return;
        }
    }
    // From search: show the branch with the matched message, and point at it.
    const target = state.jumpTo;
    state.jumpTo = null;
    if (target && state.chat && !state.chat.messages.some(m => m.id === target)) {
        state.chat = await api(`/api/chats/${chatId}/branch`, { method: "POST", body: { message_id: target } }).catch(() => state.chat);
    }
    const settings = state.chat || state.draft;
    $("systemPrompt").value = settings.system_prompt;
    paintChatOptions();
    renderMessages();
    renderChatList();
    renderChatKnowledge();
    const found = target && $("messages").querySelector(`[data-id="${target}"]`);
    if (found) {
        found.scrollIntoView({ block: "center" });
        found.classList.add("flash");
        setTimeout(() => found.classList.remove("flash"), 2000);
    }
}

export function splitThinking(message) {
    // Some models write their reasoning inline as <think>...</think>.
    let { content, thinking = "" } = message;
    const inline = content.match(/^\s*<think>([\s\S]*?)(<\/think>|$)/);
    if (inline) {
        thinking = (thinking + "\n" + inline[1]).trim();
        content = content.slice(inline[0].length);
    }
    return { thinking: thinking.trim(), content: content.trim() };
}

// Messages: the shown branch, with a ‹ 2/3 › switcher wherever a message has other versions.

function siblingSwitcher(message) {
    if (!message.siblings || message.siblings.length < 2) return null;
    const box = Object.assign(document.createElement("span"), { className: "versions" });
    const index = message.sibling_index;
    const go = offset => () => switchBranch(message.siblings[index + offset]);
    const back = iconButton("left", "Previous version", go(-1));
    const next = iconButton("right", "Next version", go(1));
    back.disabled = index === 0 || Boolean(state.live);
    next.disabled = index === message.siblings.length - 1 || Boolean(state.live);
    box.append(back, Object.assign(document.createElement("span"), { textContent: `${index + 1}/${message.siblings.length}` }), next);
    return box;
}

async function switchBranch(messageId) {
    state.compare = null;
    state.chat = await api(`/api/chats/${state.chat.id}/branch`, { method: "POST", body: { message_id: messageId } });
    renderMessages();
}

function filesElement(files) {
    const list = Object.assign(document.createElement("div"), { className: "message-files" });
    list.append(...files.map(file => {
        if (file.kind !== "image") return fileChip(file);
        const link = Object.assign(document.createElement("a"), { className: "image-file", href: file.url || file.preview, target: "_blank" });
        link.append(Object.assign(document.createElement("img"), { src: file.url || file.preview, alt: file.name, loading: "lazy" }));
        return link;
    }));
    return list;
}

// Finished replies are rendered (and highlighted, and their diagrams drawn) once, then reused:
// while another reply streams, the whole list is repainted every frame.
const rendered = new Map();

function replyBody(message, streaming) {
    const key = !streaming && message.id > 0 ? `${message.id}\n${message.content}\n${message.thinking}` : null;
    if (key && rendered.has(key)) return rendered.get(key);
    const parts = [];
    const { thinking, content } = splitThinking(message);
    if (thinking) {
        const details = Object.assign(document.createElement("details"), { className: "thinking", open: Boolean(streaming && !content) });
        details.append(Object.assign(document.createElement("summary"), { textContent: streaming && !content ? "Thinking..." : "Thought process" }),
            Object.assign(document.createElement("div"), { textContent: thinking }));
        parts.push(details);
    }
    const reply = Object.assign(document.createElement("div"), { className: "reply" });
    reply.innerHTML = renderMarkdown(content);
    if (streaming) reply.classList.add("cursor");
    else enhance(reply);
    parts.push(reply);
    if (message.sources && message.sources.length) parts.push(sourcesElement(message.sources));
    if (key) {
        if (rendered.size > 500) rendered.clear();
        rendered.set(key, parts);
    }
    return parts;
}

function statsText(message) {
    const stats = message.stats;
    return message.model + (stats ? ` · ${stats.tokens_per_second} tok/s · ${stats.tokens} tokens` : "");
}

function editor(text, saveLabel, onSave, onCancel) {
    const box = Object.assign(document.createElement("div"), { className: "message-editor" });
    const area = Object.assign(document.createElement("textarea"), { value: text, rows: 3 });
    area.addEventListener("input", () => autoGrow(area));
    area.addEventListener("keydown", event => {
        if (event.key === "Escape") onCancel();
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) onSave(area.value);
    });
    const buttons = Object.assign(document.createElement("div"), { className: "row-buttons" });
    buttons.append(button(saveLabel, () => onSave(area.value), "btn btn-primary btn-sm"), button("Cancel", onCancel, "btn btn-sm"));
    box.append(area, buttons);
    requestAnimationFrame(() => { autoGrow(area); area.focus(); area.setSelectionRange(area.value.length, area.value.length); });
    return box;
}

export function messageElement(message) {
    const box = document.createElement("div");
    box.className = `message ${message.role}`;
    box.dataset.id = message.id;
    const editing = state.editing === message.id;
    const meta = Object.assign(document.createElement("div"), { className: "meta" });
    const switcher = siblingSwitcher(message);
    if (message.role === "user") {
        if (message.attachments && message.attachments.length) box.append(filesElement(message.attachments));
        if (editing) {
            box.append(editor(message.content, "Send", content => {
                state.editing = null;
                send({ content, edit: message.id });
            }, () => { state.editing = null; renderMessages(); }));
        } else if (message.content) {
            box.append(Object.assign(document.createElement("div"), { className: "bubble", textContent: message.content }));
        }
        if (switcher) meta.append(switcher);
        if (!editing && !state.live) {
            const copy = button("Copy", () => copyText(message.content, copy));
            meta.append(copy, button("Edit", () => { state.editing = message.id; renderMessages(); }),
                button("Delete", () => deleteMessage(message)));
        }
    } else {
        if (editing) {
            box.append(editor(message.content, "Save", async content => {
                state.editing = null;
                state.chat = await api(`/api/chats/${state.chat.id}/messages/${message.id}`, { method: "PATCH", body: { content } });
                renderMessages();
            }, () => { state.editing = null; renderMessages(); }));
        } else {
            box.append(...replyBody(message, false));
        }
        if (switcher) meta.append(switcher);
        if (message.model) meta.append(Object.assign(document.createElement("span"), { textContent: statsText(message) }));
        if (!editing && !state.live) {
            const copy = button("Copy", () => copyText(splitThinking(message).content || message.content, copy));
            meta.append(copy, button("Regenerate", () => send({ replyTo: message.parent_id })),
                button("Edit", () => { state.editing = message.id; renderMessages(); }),
                button("Delete", () => deleteMessage(message)));
        }
    }
    box.append(meta);
    return box;
}

async function deleteMessage(message) {
    const later = state.chat.messages.length - 1 - state.chat.messages.findIndex(m => m.id === message.id);
    const note = later > 0 ? ` and the ${later} message${later === 1 ? "" : "s"} after it` : "";
    if (!confirm(`Delete this message${note}? Other versions are kept.`)) return;
    state.chat = await api(`/api/chats/${state.chat.id}/messages/${message.id}`, { method: "DELETE" });
    renderMessages();
}

function liveColumn(stream, compare) {
    const box = Object.assign(document.createElement("div"), { className: "message assistant" });
    if (compare) box.append(Object.assign(document.createElement("div"), { className: "compare-model", textContent: stream.model }));
    if (stream.status && !stream.message.content && !stream.message.thinking) {
        box.append(Object.assign(document.createElement("p"), { className: "stream-status", textContent: stream.status }));
    }
    box.append(...replyBody(stream.message, !stream.finished));
    if (stream.error) box.append(Object.assign(document.createElement("p"), { className: "error", textContent: stream.error }));
    const meta = Object.assign(document.createElement("div"), { className: "meta" });
    if (stream.finished && stream.message.stats) meta.append(Object.assign(document.createElement("span"), { textContent: statsText(stream.message) }));
    if (compare && stream.finished && stream.messageId) {
        meta.append(button("Continue with this one", () => switchBranch(stream.messageId), "btn btn-sm btn-primary"));
    }
    box.append(meta);
    return box;
}

export function renderMessages() {
    const box = $("messages");
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    const messages = state.chat ? state.chat.messages : [];
    const live = state.live || state.compare;
    if (!messages.length && !live) {
        const empty = Object.assign(document.createElement("div"), { className: "empty" });
        empty.textContent = state.models.length
            ? "Pick a model and send a message. You can attach images, text files and PDFs."
            : "No models yet. Start LM Studio's server or Ollama, then press ↻.";
        box.replaceChildren(empty);
        return;
    }
    // While answering (or comparing), the branch is shown up to the message being answered.
    const shown = live ? messages.slice(0, messages.findIndex(m => m.id === live.replyTo) + 1 || messages.length) : messages;
    const nodes = shown.map(message => messageElement(message));
    if (live) {
        const compare = live.streams.length > 1;
        if (compare) {
            const grid = Object.assign(document.createElement("div"), { className: "compare" });
            grid.style.setProperty("--columns", live.streams.length);
            grid.append(...live.streams.map(stream => liveColumn(stream, true)));
            nodes.push(grid);
        } else {
            nodes.push(liveColumn(live.streams[0], false));
        }
    }
    box.replaceChildren(...nodes);
    if (nearBottom || !state.live) box.scrollTop = box.scrollHeight;
}

$("messages").addEventListener("click", event => {
    const copy = event.target.closest(".copy-code");
    if (copy) copyText(copy.closest(".code-block").querySelector("code").textContent, copy);
    const preview = event.target.closest(".preview-code");
    if (preview) togglePreview(preview.closest(".code-block"), preview);
});

// Sending

let paint = null;
const schedule = () => paint ??= requestAnimationFrame(() => { paint = null; renderMessages(); });

async function readStream(response, stream, onReplyTo) {
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
            if (event.reply_to) onReplyTo(event.reply_to);
            if (event.text) stream.message.content += event.text;
            if (event.think) stream.message.thinking += event.think;
            if (event.stats) stream.message.stats = event.stats;
            if (event.error) stream.error = event.error;
            if (event.message_id) stream.messageId = event.message_id;
            if (event.status) stream.status = event.status;
            if (event.sources) stream.message.sources = event.sources;
        }
        schedule();
    }
}

async function runStream(chatId, body, stream, onReplyTo = () => {}) {
    try {
        const response = await request(`/api/chats/${chatId}/messages`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...body, backend: stream.backend, model: stream.model }),
            signal: stream.controller.signal,
        });
        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || `Request failed (${response.status})`);
        }
        await readStream(response, stream, onReplyTo);
    } catch (error) {
        if (error.name !== "AbortError") stream.error = error.message;
    } finally {
        stream.finished = true;
        schedule();
    }
}

function newStream(model) {
    return { ...model, controller: new AbortController(), finished: false, error: null, messageId: null,
             message: { role: "assistant", content: "", thinking: "", model: model.model, stats: null } };
}

/**
 * Send something and stream the answer(s):
 * { content, files } a new message · { content, edit } a new version of a message · { replyTo } another answer.
 * With compare models picked, every model answers the same message side by side.
 */
export async function send({ content = "", files = [], edit = null, replyTo = null }) {
    const picked = selectedModel();
    if (!picked) {
        alert("Pick a model first. If the list is empty, start LM Studio's server or Ollama and press ↻.");
        return false;
    }
    if (!state.chat) {
        state.chat = await api("/api/chats", {
            method: "POST",
            body: { backend: picked.backend, model: picked.model, persona_id: picked.persona ? picked.persona.id : null,
                    system_prompt: $("systemPrompt").value, options: state.draft.options || {},
                    knowledge_ids: state.draft.knowledge_ids || [] },
        });
        history.replaceState(null, "", `#chat/${state.chat.id}`);
    }
    const chat = state.chat;
    state.compare = null;
    state.editing = null;
    const models = [{ backend: picked.backend, model: picked.model }, ...compareModels()];
    const streams = models.map(newStream);
    state.live = { replyTo, streams };
    // Show the message being sent straight away (the reload at the end replaces it with the saved one).
    if (replyTo === null) {
        const at = edit ? chat.messages.findIndex(m => m.id === edit) : chat.messages.length;
        const kept = edit ? chat.messages[at].attachments : files;
        chat.messages = chat.messages.slice(0, at);
        chat.messages.push({ id: -1, role: "user", content, attachments: kept });
        state.live.replyTo = -1;
    }
    setSending(true);
    renderMessages();

    const first = replyTo !== null ? { reply_to: replyTo }
        : edit ? { content, edit } : { content, attachments: files.map(file => file.id) };
    // The first request creates the message; the other models then answer that same message.
    let others = Promise.resolve();
    await runStream(chat.id, first, streams[0], userMessageId => {
        state.live.replyTo = userMessageId;
        const placeholder = chat.messages.find(m => m.id === -1);
        if (placeholder) placeholder.id = userMessageId;
        others = Promise.all(streams.slice(1).map(stream => runStream(chat.id, { reply_to: userMessageId }, stream)));
    });
    await others;
    if (paint) cancelAnimationFrame(paint);
    paint = null;
    setSending(false);
    if (state.chat !== chat) return true;
    // Reload from the database so what's shown is exactly what was saved.
    try {
        state.chat = await api(`/api/chats/${chat.id}`);
    } catch { /* keep what we have */ }
    const live = state.live;
    state.live = null;
    // Comparing: keep the answers side by side until one is picked.
    if (live.streams.length > 1 && live.streams.some(stream => stream.messageId)) state.compare = live;
    renderMessages();
    const failed = live.streams.length === 1 && live.streams[0].error;
    if (failed) {
        const error = Object.assign(document.createElement("div"), { className: "message" });
        error.append(Object.assign(document.createElement("div"), { className: "error", textContent: failed }));
        $("messages").append(error);
        $("messages").scrollTop = $("messages").scrollHeight;
    }
    loadChats();
    // The model writes a title after the first reply; pick it up when it's there.
    if (state.chat.messages.length === 2 && state.me && state.me.prefs.auto_title) {
        setTimeout(loadChats, 3000);
        setTimeout(loadChats, 10000);
    }
    return true;
}

export function stopStreams() {
    if (state.live) state.live.streams.forEach(stream => stream.controller.abort());
}

// Compare: up to two more models answer every message while it's on.

function compareModels() {
    if ($("comparePanel").hidden) return [];
    return [...$("comparePanel").querySelectorAll("select")]
        .map(select => select.value).filter(Boolean)
        .map(value => { const [backend, model] = value.split("\n"); return { backend, model }; });
}

export function fillCompareSelects() {
    for (const select of $("comparePanel").querySelectorAll("select")) {
        const previous = select.value;
        const groups = [...$("model").querySelectorAll("optgroup")].filter(group => group.label !== "Personas");
        select.replaceChildren(new Option("Nobody", ""), ...groups.map(group => group.cloneNode(true)));
        if ([...select.options].some(option => option.value === previous)) select.value = previous;
    }
}

$("compareButton").addEventListener("click", () => {
    const open = $("comparePanel").hidden;
    $("comparePanel").hidden = !open;
    $("compareButton").setAttribute("aria-pressed", String(open));
    if (open) fillCompareSelects();
});

export function setSending(sending) {
    const send = $("sendButton");
    send.innerHTML = icon(sending ? "stop" : "send", 20);
    send.title = sending ? "Stop" : "Send";
    send.setAttribute("aria-label", send.title);
    send.classList.toggle("stop", sending);
}

$("composer").addEventListener("submit", async event => {
    event.preventDefault();
    if (state.live) {
        stopStreams();
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
        if (!(await send({ content, files }))) throw new Error("not sent");
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

export function enterSends() {
    // Unset: on with a keyboard, off on phones (where Enter is how you get a new line).
    const saved = state.me ? state.me.prefs.enter_sends : null;
    return saved === null || saved === undefined ? !matchMedia("(pointer: coarse)").matches : saved;
}

$("prompt").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && enterSends()) {
        event.preventDefault();
        if (!state.live) $("composer").requestSubmit();
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
