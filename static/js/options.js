import { $, api } from "./util.js";
import { state } from "./state.js";
import { selectedModel } from "./models.js";
import { showSaved } from "./settings.js";

// Model options (the same fields in the chat panel and in Settings)

export const OPTION_FIELDS = [
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

export function renderOptions(container, values, onChange, backend, capabilities = null) {
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
        // Capabilities are null when the server doesn't say; then nothing is greyed out for them.
        const noThinking = field.name === "think" && capabilities && !capabilities.includes("thinking");
        label.classList.toggle("unused", Boolean((field.ollama && backend === "lmstudio") || noThinking));
        label.title = field.ollama && backend === "lmstudio" ? "Only Ollama uses this one"
            : noThinking ? "This model doesn't think, so this does nothing (and On makes Ollama refuse)" : "";
        label.append(caption, input);
        return label;
    }));
}

export function readOptions(container) {
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

/** What the picked model can do, or null when its server doesn't say. */
export function modelCapabilities(picked) {
    if (!picked) return null;
    const model = state.models.find(m => m.backend === picked.backend && m.id === picked.model);
    return model ? model.capabilities : null;
}

export function paintChatOptions() {
    const settings = state.chat || state.draft;
    const picked = selectedModel();
    const backend = picked ? picked.backend : null;
    renderOptions($("chatOptions"), settings.options || {}, saveChatOptions, backend, modelCapabilities(picked));
    $("chatOptionsNote").textContent = backend === "lmstudio"
        ? "Greyed-out ones are Ollama only."
        : "Empty means the model's own default.";
}

export async function saveChatOptions(options) {
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
