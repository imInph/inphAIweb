export const $ = id => document.getElementById(id);
export const ICONS = {
    send: '<path d="M12 19V5M5.5 11.5L12 5l6.5 6.5"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/>',
    edit: '<path d="M12 20.5h8"/><path d="M16.2 3.8a2 2 0 0 1 2.9 2.9L7.5 18.3l-3.8 1 1-3.8z"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l8.5-8.5M16 7l2.5 2.5M14 9l2 2"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
    unlock: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 7.7-1.5"/>',
    more: '<circle cx="5.5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/><circle cx="18.5" cy="12" r="1.3" fill="currentColor"/>',
    pin: '<path d="M9 4.5h6M10 4.5v5l-3 4h10l-3-4v-5M12 13.5v6"/>',
    folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    chevron: '<path d="M9.5 6l6 6-6 6"/>',
    book: '<path d="M12 6.5c-1.8-1.6-4.6-2.2-8-2v13.2c3.4-.2 6.2.4 8 2 1.8-1.6 4.6-2.2 8-2V4.5c-3.4-.2-6.2.4-8 2z"/><path d="M12 6.5v13.2"/>',
    refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>',
    left: '<path d="M14.5 6l-6 6 6 6"/>',
    right: '<path d="M9.5 6l6 6-6 6"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>',
    trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.5h6a1.5 1.5 0 0 0 1.5-1.5l1-12.5"/>',
};

// Every request: the header the server checks against cross-site forms, and a signed-out
// session goes back to the login page.
export async function request(path, init = {}) {
    const response = await fetch(path, { ...init, headers: { ...(init.headers || {}), "X-Requested-With": "inphai" } });
    if (response.status === 401) {
        location.href = "/login";
        throw new Error("Signed out");
    }
    return response;
}

export async function api(path, options = {}) {
    const init = { ...options, headers: { "Content-Type": "application/json" } };
    if (options.body !== undefined) init.body = JSON.stringify(options.body);
    const response = await request(path, init);
    if (response.status === 204) return null;
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
    return result;
}

export function icon(name, size = 18) {
    return `<svg class="ico" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
}

export function button(label, onClick, className = "btn btn-ghost btn-sm") {
    const element = Object.assign(document.createElement("button"), { type: "button", className, textContent: label });
    element.addEventListener("click", onClick);
    return element;
}

export function iconButton(name, label, onClick) {
    const element = button("", onClick, "btn btn-ghost btn-sm btn-icon");
    element.innerHTML = icon(name, 16);
    element.title = label;
    element.setAttribute("aria-label", label);
    return element;
}

export function autoGrow(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = textarea.scrollHeight + "px";
    textarea.style.overflowY = textarea.scrollHeight > textarea.clientHeight + 2 ? "auto" : "hidden";
}

export function storageGet(key) {
    try { return localStorage.getItem(key); } catch { return null; }
}

export function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch { /* private mode */ }
}

export function timeAgo(seconds) {
    const diff = Date.now() / 1000 - seconds;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
    return new Date(seconds * 1000).toLocaleDateString();
}

export function copyText(text, button) {
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

// A small menu that opens under a button; closes on a pick, Escape or a click elsewhere.
let openMenuElement = null;

export function closeMenu() {
    if (openMenuElement) openMenuElement.remove();
    openMenuElement = null;
}

export function openMenu(anchor, items) {
    closeMenu();
    const menu = Object.assign(document.createElement("div"), { className: "menu", role: "menu" });
    for (const item of items) {
        if (item === "-") {
            menu.append(Object.assign(document.createElement("hr")));
            continue;
        }
        const entry = Object.assign(document.createElement("button"), { type: "button", role: "menuitem", textContent: item.label });
        if (item.danger) entry.classList.add("danger");
        entry.addEventListener("click", () => { closeMenu(); item.action(); });
        menu.append(entry);
    }
    document.body.append(menu);
    const box = anchor.getBoundingClientRect();
    const width = menu.offsetWidth, height = menu.offsetHeight;
    menu.style.left = `${Math.max(8, Math.min(box.right - width, innerWidth - width - 8))}px`;
    menu.style.top = `${box.bottom + 4 + height > innerHeight ? Math.max(8, box.top - height - 4) : box.bottom + 4}px`;
    openMenuElement = menu;
    menu.querySelector("button")?.focus();
}

document.addEventListener("pointerdown", event => {
    if (openMenuElement && !openMenuElement.contains(event.target)) closeMenu();
}, true);
document.addEventListener("keydown", event => { if (event.key === "Escape") closeMenu(); });

export function escapeText(text) {
    return text.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
