import { $, api, iconButton } from "./util.js";
import { state } from "./state.js";
import { openChat } from "./chat.js";

// Chat list

export async function loadChats() {
    try {
        state.chats = (await api("/api/chats")).chats;
    } catch (error) {
        console.error(error);
    }
    renderChatList();
}

export function renderChatList() {
    const list = $("chatList");
    list.replaceChildren(...state.chats.map(chat => {
        const item = document.createElement("li");
        item.classList.toggle("active", Boolean(state.chat && state.chat.id === chat.id));
        const link = Object.assign(document.createElement("a"), { href: `#chat/${chat.id}`, textContent: chat.title });
        link.title = chat.title;
        item.append(link, iconButton("edit", "Rename", () => renameChat(chat)),
            iconButton("trash", "Delete", () => deleteChat(chat)));
        return item;
    }));
}

export async function renameChat(chat) {
    const title = prompt("Rename chat", chat.title);
    if (!title || !title.trim()) return;
    await api(`/api/chats/${chat.id}`, { method: "PATCH", body: { title } });
    if (state.chat && state.chat.id === chat.id) state.chat.title = title.trim();
    loadChats();
}

export async function deleteChat(chat) {
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
