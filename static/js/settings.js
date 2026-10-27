import { $, api, autoGrow, storageSet } from "./util.js";
import { state } from "./state.js";
import { setTheme } from "./shell.js";
import { loadModels } from "./models.js";
import { loadChats } from "./chats.js";
import { enterSends, newChatDefaults } from "./chat.js";
import { paintChatOptions, renderOptions } from "./options.js";
import { fillNegatives } from "./media.js";

// Settings page

export const SAVED_TIMERS = {};

export function showSaved(group, message, error = false) {
    const label = document.querySelector(`[data-saved="${group}"]`);
    if (!label) return;
    label.textContent = message;
    label.classList.toggle("error", error);
    clearTimeout(SAVED_TIMERS[group]);
    if (!error) SAVED_TIMERS[group] = setTimeout(() => label.textContent = "", 1800);
}

export async function loadSettings() {
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

export function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export async function loadSettingsPage() {
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

export async function saveSetting(field) {
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

export async function saveDefaultOptions(options) {
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
