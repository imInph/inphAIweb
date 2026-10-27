import { $, api, autoGrow, button, copyText, icon, request } from "./util.js";
import { state } from "./state.js";
import { modelKey, pickModel, selectedModel } from "./models.js";
import { loadChats, renderChatList } from "./chats.js";
import { paintChatOptions } from "./options.js";
import { fileChip, renderPending } from "./attachments.js";
import { showSaved } from "./settings.js";

// One chat

export function newChatDefaults() {
    const defaults = state.me ? state.me.prefs : {};
    return { system_prompt: defaults.default_system_prompt || "", options: { ...(defaults.default_options || {}) } };
}

export async function openChat(chatId) {
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

export function messageElement(message, options = {}) {
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

export function renderMessages(live) {
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

export async function sendMessage(content, files = []) {
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
        const response = await request(`/api/chats/${chat.id}/messages`, {
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

export function setSending(sending) {
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

export function enterSends() {
    // Unset: on with a keyboard, off on phones (where Enter is how you get a new line).
    const saved = state.me ? state.me.prefs.enter_sends : null;
    return saved === null || saved === undefined ? !matchMedia("(pointer: coarse)").matches : saved;
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
