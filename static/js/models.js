import { $, api, storageGet, storageSet } from "./util.js";
import { state } from "./state.js";
import { fillCompareSelects, renderMessages } from "./chat.js";
import { paintChatOptions } from "./options.js";
import { showSaved } from "./settings.js";
import { renderPending } from "./attachments.js";

// The model picker: your personas first, then every model LM Studio and Ollama have.

export function modelKey(backend, id) {
    return `${backend}\n${id}`;
}

export const personaKey = persona => `persona\n${persona.id}`;

/** The key the picker should show for a chat: its persona if it has one, else its model. */
export function chatKey(chat) {
    const persona = chat.persona_id && state.personas.find(p => p.id === chat.persona_id);
    return persona ? personaKey(persona) : modelKey(chat.backend, chat.model);
}

export async function loadModels() {
    const select = $("model");
    select.replaceChildren(new Option("Loading models...", ""));
    try {
        const result = await api("/api/models");
        state.models = result.models;
        state.modelErrors = Object.values(result.errors);
    } catch (error) {
        state.models = [];
        state.modelErrors = [error.message];
    }
    renderModelSelect();
    if (!state.chat) renderMessages();
}

export function renderModelSelect() {
    const select = $("model");
    const previous = select.value && select.value !== "" ? select.value : storageGet("inphai.model");
    const groups = [];
    if (state.personas.length) {
        const group = Object.assign(document.createElement("optgroup"), { label: "Personas" });
        group.append(...state.personas.map(persona => new Option(persona.name, personaKey(persona))));
        groups.push(group);
    }
    const byBackend = {};
    for (const model of state.models) {
        byBackend[model.backend_name] ??= Object.assign(document.createElement("optgroup"), { label: model.backend_name });
        const notes = (model.capabilities || []).filter(c => c === "vision" || c === "thinking")
            .map(c => (c === "vision" ? "sees images" : "thinks"));
        byBackend[model.backend_name].append(new Option(model.id + (notes.length ? ` · ${notes.join(", ")}` : ""), modelKey(model.backend, model.id)));
    }
    groups.push(...Object.values(byBackend));
    select.replaceChildren(...groups);
    if (!state.models.length && !state.personas.length) select.replaceChildren(new Option("No models found", ""));
    $("backendNote").hidden = !state.modelErrors.length;
    $("backendNote").textContent = state.modelErrors.join(" · ");
    pickModel(state.chat ? chatKey(state.chat) : previous);
    fillCompareSelects();
}

export function pickModel(key) {
    const select = $("model");
    if (key && [...select.options].some(option => option.value === key)) select.value = key;
}

/** { backend, model } to send to, plus the persona when one is picked. */
export function selectedModel() {
    const [kind, id] = ($("model").value || "\n").split("\n");
    if (kind === "persona") {
        const persona = state.personas.find(p => String(p.id) === id);
        return persona ? { backend: persona.backend, model: persona.model, persona } : null;
    }
    return kind && id ? { backend: kind, model: id } : null;
}

$("refreshModels").addEventListener("click", loadModels);
$("model").addEventListener("change", async () => {
    storageSet("inphai.model", $("model").value);
    const picked = selectedModel();
    if (!picked) return;
    const persona = picked.persona;
    // A persona brings its own instructions and options with it.
    const fields = { backend: picked.backend, model: picked.model, persona_id: persona ? persona.id : null,
                     ...(persona ? { system_prompt: persona.system_prompt, options: persona.options } : {}) };
    if (!state.chat) {
        if (persona) {
            state.draft = { ...state.draft, system_prompt: persona.system_prompt, options: { ...persona.options } };
            $("systemPrompt").value = persona.system_prompt;
        }
    } else {
        try {
            state.chat = { ...state.chat, ...(await api(`/api/chats/${state.chat.id}`, { method: "PATCH", body: fields })) };
            $("systemPrompt").value = state.chat.system_prompt;
            if (persona) showSaved("chatOptions", `Using ${persona.name}`);
        } catch (error) {
            showSaved("chatOptions", error.message, true);
        }
    }
    paintChatOptions();
    renderPending();
});
