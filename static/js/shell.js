import { $, autoGrow, storageSet } from "./util.js";
import { state } from "./state.js";
import { openChat } from "./chat.js";
import { loadGallery } from "./media.js";
import { loadSettingsPage } from "./settings.js";

export const TITLES = { chat: "Chat", images: "Images", videos: "Videos", settings: "Settings" };
export const VIEWS = Object.keys(TITLES);

// Views

export function showView() {
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

export function closeDrawer() {
    document.body.classList.remove("drawer-open");
    $("backdrop").hidden = true;
}

$("menuButton").addEventListener("click", () => {
    document.body.classList.add("drawer-open");
    $("backdrop").hidden = false;
});

// Topbar: greeting, clock, theme

export function greetingFor(hour) {
    return hour < 5 ? "Late night" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

export function tick() {
    const now = new Date();
    $("greeting").textContent = greetingFor(now.getHours());
    $("clock").textContent = now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

export function setTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    document.querySelector('meta[name="theme-color"]').setAttribute("content", theme === "dark" ? "#0b0d12" : "#f4f6fa");
    storageSet("inphai.theme", theme);
    document.querySelectorAll("[data-theme-choice]").forEach(b => b.classList.toggle("on", b.dataset.themeChoice === theme));
}

$("themeButton").addEventListener("click", () =>
    setTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
$("backdrop").addEventListener("click", closeDrawer);
window.addEventListener("hashchange", showView);
