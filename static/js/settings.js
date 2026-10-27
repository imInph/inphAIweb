import { $, api, autoGrow, iconButton, request } from "./util.js";
import { state } from "./state.js";
import { setTheme } from "./shell.js";
import { loadModels } from "./models.js";
import { loadChats } from "./chats.js";
import { enterSends, newChatDefaults } from "./chat.js";
import { paintChatOptions, renderOptions } from "./options.js";
import { fillNegatives } from "./media.js";

// Settings page: your account and preferences for everyone, server settings and users for admins.

export const SAVED_TIMERS = {};

export function showSaved(group, message, error = false) {
    const label = document.querySelector(`[data-saved="${group}"]`);
    if (!label) return;
    label.textContent = message;
    label.classList.toggle("error", error);
    clearTimeout(SAVED_TIMERS[group]);
    if (!error) SAVED_TIMERS[group] = setTimeout(() => label.textContent = "", 1800);
}

export const isAdmin = () => Boolean(state.me && state.me.user.role === "admin");

/** Who's signed in, their preferences, and the server defaults everyone may read. */
export async function loadSettings() {
    try {
        state.me = await api("/api/me");
    } catch (error) {
        console.error(error);
        return null;
    }
    const { user, prefs } = state.me;
    $("accountRowName").textContent = user.username;
    $("accountAvatar").textContent = user.username.slice(0, 1).toUpperCase();
    document.querySelectorAll(".admin-only").forEach(element => element.hidden = !isAdmin());
    // The account's theme wins over this browser's copy of it.
    if (prefs.theme !== document.documentElement.getAttribute("data-theme")) setTheme(prefs.theme, false);
    if (!state.chat) {
        state.draft = newChatDefaults();
        $("systemPrompt").value = state.draft.system_prompt;
        paintChatOptions();
    }
    fillNegatives();
    return state.me;
}

export async function savePrefs(fields, group) {
    try {
        state.me.prefs = (await api("/api/me/prefs", { method: "PATCH", body: fields })).prefs;
        if (group) showSaved(group, "Saved");
    } catch (error) {
        if (group) showSaved(group, error.message, true);
        else console.error(error);
    }
}

export function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

function fillFields(selector, attribute, values) {
    for (const field of document.querySelectorAll(selector)) {
        const value = values[field.dataset[attribute]];
        if (field.type === "checkbox") field.checked = Boolean(value);
        else field.value = value === null || value === undefined ? "" : String(value);
        if (field.tagName === "TEXTAREA") autoGrow(field);
    }
}

function renderStats(element, stats) {
    element.replaceChildren(...stats.map(([name, value]) => {
        const item = document.createElement("div");
        item.append(Object.assign(document.createElement("dt"), { textContent: name }),
            Object.assign(document.createElement("dd"), { textContent: String(value) }));
        return item;
    }));
}

export async function loadSettingsPage() {
    if (!(await loadSettings())) return;
    const { user, prefs } = state.me;
    $("accountName").textContent = `Signed in as ${user.username}${user.role === "admin" ? " (admin)" : ""}.`;
    fillFields("[data-pref]", "pref", prefs);
    $("enterSends").checked = enterSends();
    renderOptions($("defaultOptions"), prefs.default_options, options => savePrefs({ default_options: options }, "chat"), null);
    setTheme(prefs.theme, false);

    const mine = await api("/api/me/data");
    state.myData = mine;
    renderStats($("dataStats"), [["Chats", mine.counts.chats], ["Messages", mine.counts.messages],
        ["Files", mine.counts.files], ["Images", mine.counts.images], ["Videos", mine.counts.videos],
        ["Chat files", formatBytes(mine.disk.files)], ["Images & videos", formatBytes(mine.disk.media)]]);

    if (isAdmin()) {
        const server = await api("/api/settings");
        state.server = server;
        fillFields("[data-setting]", "setting", server.settings);
        const disk = server.disk;
        $("serverDisk").textContent = `The whole server uses ${formatBytes(disk.database)} for the database, `
            + `${formatBytes(disk.files)} for chat files and ${formatBytes(disk.images + disk.videos)} for images and videos.`;
        loadUsers();
    }
}

// Your account

$("passwordForm").addEventListener("submit", async event => {
    event.preventDefault();
    const current = $("currentPassword").value, password = $("newPassword").value;
    if (password !== $("newPassword2").value) return showSaved("account", "The new passwords don't match", true);
    try {
        await api("/api/me/password", { method: "POST", body: { current, password } });
        $("passwordForm").reset();
        showSaved("account", "Password changed. Other devices are signed out.");
    } catch (error) {
        showSaved("account", error.message, true);
    }
});

$("signOut").addEventListener("click", async () => {
    await request("/api/auth/logout", { method: "POST" }).catch(() => {});
    location.href = "/login";
});

document.querySelectorAll("[data-pref]").forEach(field => field.addEventListener("change", () =>
    savePrefs({ [field.dataset.pref]: field.type === "checkbox" ? field.checked : field.value }, field.dataset.group)));

$("importChats").addEventListener("click", () => $("importFile").click());
$("importFile").addEventListener("change", async () => {
    const file = $("importFile").files[0];
    $("importFile").value = "";
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    $("importNote").textContent = "Importing...";
    try {
        const response = await request("/api/chats/import", { method: "POST", body: form });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Import failed");
        $("importNote").textContent = `Imported ${result.imported} chat${result.imported === 1 ? "" : "s"}.`;
        loadChats();
        loadSettingsPage();
    } catch (error) {
        $("importNote").textContent = error.message;
    }
});
$("enterSends").addEventListener("change", () => savePrefs({ enter_sends: $("enterSends").checked }, "chat"));
document.querySelectorAll("[data-theme-choice]").forEach(choice =>
    choice.addEventListener("click", () => setTheme(choice.dataset.themeChoice)));

$("deleteAllChats").addEventListener("click", async () => {
    const count = state.myData ? state.myData.counts.chats : 0;
    if (!count) return;
    if (!confirm(`Delete all ${count} of your chat${count === 1 ? "" : "s"}? This can't be undone.`)) return;
    await api("/api/chats", { method: "DELETE" }).catch(error => alert(error.message));
    state.chat = null;
    loadChats();
    loadSettingsPage();
});

// Server settings (admin)

export async function saveSetting(field) {
    const key = field.dataset.setting;
    const value = field.type === "checkbox" ? field.checked : field.type === "number" ? Number(field.value) : field.value;
    try {
        state.server.settings = (await api("/api/settings", { method: "PATCH", body: { [key]: value } })).settings;
        showSaved(field.dataset.group, "Saved");
        if (key.endsWith("_negative_prompt")) {
            state.me.server[key] = value;
            fillNegatives();
        }
        if (key.endsWith("_url")) loadModels();
    } catch (error) {
        showSaved(field.dataset.group, error.message, true);
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

$("resetNegatives").addEventListener("click", async () => {
    const defaults = state.server.defaults;
    const fields = { image_negative_prompt: defaults.image_negative_prompt, video_negative_prompt: defaults.video_negative_prompt };
    try {
        state.server.settings = (await api("/api/settings", { method: "PATCH", body: fields })).settings;
        Object.assign(state.me.server, fields);
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

// Users (admin)

async function loadUsers() {
    const { users } = await api("/api/users");
    $("userList").replaceChildren(...users.map(user => {
        const self = user.id === state.me.user.id;
        const item = Object.assign(document.createElement("li"), { className: "user-row" });
        item.classList.toggle("disabled", user.disabled);
        const name = Object.assign(document.createElement("div"), { className: "user-name" });
        name.append(Object.assign(document.createElement("strong"), { textContent: user.username + (self ? " (you)" : "") }),
            Object.assign(document.createElement("small"), {
                textContent: `${user.chats} chat${user.chats === 1 ? "" : "s"} · ${user.media} image${user.media === 1 ? "" : "s"}/video${user.media === 1 ? "" : "s"}${user.disabled ? " · disabled" : ""}` }));
        const role = document.createElement("select");
        role.append(new Option("User", "user"), new Option("Admin", "admin"));
        role.value = user.role;
        role.disabled = self;
        role.setAttribute("aria-label", `Role of ${user.username}`);
        role.addEventListener("change", () => editUser(user, { role: role.value }));
        const actions = Object.assign(document.createElement("div"), { className: "user-actions" });
        actions.append(iconButton("key", "Set a new password", () => {
            const password = prompt(`New password for ${user.username} (8 characters or more)`);
            if (password) editUser(user, { password }, "Password set. They're signed out everywhere.");
        }));
        if (!self) {
            actions.append(iconButton(user.disabled ? "unlock" : "lock", user.disabled ? "Enable" : "Disable",
                () => editUser(user, { disabled: !user.disabled })));
            actions.append(iconButton("trash", "Delete", async () => {
                if (!confirm(`Delete ${user.username} with all their chats, files, images and videos? This can't be undone.`)) return;
                try {
                    await api(`/api/users/${user.id}`, { method: "DELETE" });
                    showSaved("users", "Deleted");
                    loadUsers();
                } catch (error) {
                    showSaved("users", error.message, true);
                }
            }));
        }
        item.append(name, role, actions);
        return item;
    }));
}

async function editUser(user, fields, message = "Saved") {
    try {
        await api(`/api/users/${user.id}`, { method: "PATCH", body: fields });
        showSaved("users", message);
    } catch (error) {
        showSaved("users", error.message, true);
    }
    loadUsers();
}

$("addUserForm").addEventListener("submit", async event => {
    event.preventDefault();
    try {
        await api("/api/users", { method: "POST", body: {
            username: $("newUsername").value.trim(), password: $("newUserPassword").value, role: $("newUserRole").value } });
        $("addUserForm").reset();
        showSaved("users", "User added");
        loadUsers();
    } catch (error) {
        showSaved("users", error.message, true);
    }
});
