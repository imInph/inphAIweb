import { $, api, storageGet, storageSet } from "./util.js";
import { state } from "./state.js";
import { renderMessages } from "./chat.js";
import { paintChatOptions } from "./options.js";

// Models

export function modelKey(backend, id) {
    return `${backend}\n${id}`;
}

export async function loadModels() {
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

export function pickModel(key) {
    const select = $("model");
    if (key && [...select.options].some(option => option.value === key)) select.value = key;
}

export function selectedModel() {
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
