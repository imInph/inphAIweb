import { $, api, escapeText, icon, iconButton, openMenu } from "./util.js";
import { state } from "./state.js";
import { openChat } from "./chat.js";
import { closeDrawer } from "./shell.js";

// The sidebar: search, pinned chats, folders (drag a chat onto one), the rest, and archived chats.

export async function loadChats() {
    try {
        const result = await api(`/api/chats${state.showArchived ? "?archived=1" : ""}`);
        state.chats = result.chats;
        state.folders = result.folders;
        state.archivedCount = result.archived_count;
    } catch (error) {
        console.error(error);
    }
    renderChatList();
}

function sectionHead(text, onDrop) {
    const head = Object.assign(document.createElement("li"), { className: "list-head", textContent: text });
    if (onDrop) dropTarget(head, onDrop);
    return head;
}

function dropTarget(element, onDrop) {
    element.addEventListener("dragover", event => {
        if (!event.dataTransfer.types.includes("application/x-inphai-chat")) return;
        event.preventDefault();
        element.classList.add("drop");
    });
    element.addEventListener("dragleave", () => element.classList.remove("drop"));
    element.addEventListener("drop", event => {
        event.preventDefault();
        element.classList.remove("drop");
        onDrop(Number(event.dataTransfer.getData("application/x-inphai-chat")));
    });
}

function chatItem(chat) {
    const item = Object.assign(document.createElement("li"), { className: "chat-item" });
    item.classList.toggle("active", Boolean(state.chat && state.chat.id === chat.id));
    item.draggable = !chat.archived;
    item.addEventListener("dragstart", event => {
        event.dataTransfer.setData("application/x-inphai-chat", String(chat.id));
        event.dataTransfer.effectAllowed = "move";
    });
    const link = Object.assign(document.createElement("a"), { href: `#chat/${chat.id}`, title: chat.title });
    if (chat.pinned) link.insertAdjacentHTML("beforeend", icon("pin", 14));
    link.append(Object.assign(document.createElement("span"), { textContent: chat.title }));
    const more = iconButton("more", "More", () => openMenu(more, chatMenu(chat)));
    item.append(link, more);
    return item;
}

function chatMenu(chat) {
    const items = [
        { label: "Rename", action: () => renameChat(chat) },
        { label: chat.pinned ? "Unpin" : "Pin to the top", action: () => patchChat(chat, { pinned: !chat.pinned }) },
    ];
    if (!chat.archived) {
        for (const folder of state.folders) {
            if (folder.id !== chat.folder_id) items.push({ label: `Move to ${folder.name}`, action: () => patchChat(chat, { folder_id: folder.id }) });
        }
        if (chat.folder_id) items.push({ label: "Take out of the folder", action: () => patchChat(chat, { folder_id: null }) });
    }
    items.push("-",
        { label: "Export as Markdown", action: () => { location.href = `/api/chats/${chat.id}/export?format=md`; } },
        { label: "Export as JSON", action: () => { location.href = `/api/chats/${chat.id}/export?format=json`; } },
        "-",
        { label: chat.archived ? "Unarchive" : "Archive", action: () => patchChat(chat, { archived: !chat.archived }) },
        { label: "Delete", danger: true, action: () => deleteChat(chat) });
    return items;
}

function folderItem(folder, chats) {
    const head = Object.assign(document.createElement("li"), { className: "folder-head" });
    head.classList.toggle("collapsed", folder.collapsed);
    const toggle = Object.assign(document.createElement("button"), { type: "button", className: "folder-toggle" });
    toggle.innerHTML = icon("chevron", 14) + icon("folder", 16);
    toggle.append(Object.assign(document.createElement("span"), { textContent: folder.name }),
        Object.assign(document.createElement("small"), { textContent: chats.length ? String(chats.length) : "" }));
    toggle.setAttribute("aria-expanded", String(!folder.collapsed));
    toggle.addEventListener("click", async () => {
        folder.collapsed = !folder.collapsed;
        renderChatList();
        api(`/api/folders/${folder.id}`, { method: "PATCH", body: { collapsed: folder.collapsed } }).catch(console.error);
    });
    const more = iconButton("more", "Folder options", () => openMenu(more, [
        { label: "Rename", action: () => renameFolder(folder) },
        { label: "Delete the folder (keeps its chats)", danger: true, action: () => deleteFolder(folder) },
    ]));
    head.append(toggle, more);
    dropTarget(head, chatId => moveChat(chatId, folder.id));
    const nodes = [head];
    if (!folder.collapsed) {
        const children = chats.map(chatItem);
        children.forEach(child => child.classList.add("in-folder"));
        nodes.push(...children);
    }
    return nodes;
}

export function renderChatList() {
    const list = $("chatList");
    if (state.searchResults) return renderSearch();
    const chats = state.chats || [];
    const folders = state.folders || [];
    const nodes = [];
    if (state.showArchived) {
        nodes.push(sectionHead("Archived"), ...chats.map(chatItem));
        if (!chats.length) nodes.push(Object.assign(document.createElement("li"), { className: "list-note", textContent: "Nothing archived." }));
    } else {
        const pinned = chats.filter(chat => chat.pinned);
        if (pinned.length) nodes.push(sectionHead("Pinned"), ...pinned.map(chatItem));
        for (const folder of folders) nodes.push(...folderItem(folder, chats.filter(chat => !chat.pinned && chat.folder_id === folder.id)));
        const rest = chats.filter(chat => !chat.pinned && !chat.folder_id);
        if (pinned.length || folders.length) nodes.push(sectionHead("Chats", chatId => moveChat(chatId, null)));
        nodes.push(...rest.map(chatItem));
    }
    list.replaceChildren(...nodes);
    const toggle = $("archivedToggle");
    toggle.hidden = !state.showArchived && !state.archivedCount;
    toggle.textContent = state.showArchived ? "← Back to chats" : `Archived chats (${state.archivedCount})`;
}

$("archivedToggle").addEventListener("click", () => {
    state.showArchived = !state.showArchived;
    loadChats();
});

async function patchChat(chat, fields) {
    try {
        await api(`/api/chats/${chat.id}`, { method: "PATCH", body: fields });
        if (state.chat && state.chat.id === chat.id) Object.assign(state.chat, fields);
    } catch (error) {
        alert(error.message);
    }
    loadChats();
}

function moveChat(chatId, folderId) {
    const chat = state.chats.find(c => c.id === chatId);
    if (chat && chat.folder_id !== folderId) patchChat(chat, { folder_id: folderId, pinned: false });
}

export async function renameChat(chat) {
    const title = prompt("Rename chat", chat.title);
    if (!title || !title.trim()) return;
    await patchChat(chat, { title: title.trim() });
}

export async function deleteChat(chat) {
    if (!confirm(`Delete "${chat.title}"?`)) return;
    await api(`/api/chats/${chat.id}`, { method: "DELETE" });
    if (state.chat && state.chat.id === chat.id) location.hash = "#chat";
    loadChats();
}

async function renameFolder(folder) {
    const name = prompt("Rename folder", folder.name);
    if (!name || !name.trim()) return;
    await api(`/api/folders/${folder.id}`, { method: "PATCH", body: { name: name.trim() } }).catch(error => alert(error.message));
    loadChats();
}

async function deleteFolder(folder) {
    if (!confirm(`Delete the folder "${folder.name}"? Its chats stay, outside any folder.`)) return;
    await api(`/api/folders/${folder.id}`, { method: "DELETE" });
    loadChats();
}

$("newFolder").addEventListener("click", async () => {
    const name = prompt("New folder name");
    if (!name || !name.trim()) return;
    await api("/api/folders", { method: "POST", body: { name: name.trim() } }).catch(error => alert(error.message));
    state.showArchived = false;
    loadChats();
});

$("newChat").addEventListener("click", () => {
    if (location.hash === "#chat") openChat(null);
    location.hash = "#chat";
    $("prompt").focus();
});

// Search: titles and every message (all branches); a hit opens the chat on that message.

let searchTimer = null;

$("chatSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    const query = $("chatSearch").value.trim();
    if (!query) {
        state.searchResults = null;
        renderChatList();
        return;
    }
    searchTimer = setTimeout(async () => {
        try {
            const { results } = await api(`/api/search?q=${encodeURIComponent(query)}`);
            if ($("chatSearch").value.trim() === query) {
                state.searchResults = results;
                renderChatList();
            }
        } catch (error) {
            console.error(error);
        }
    }, 200);
});

$("chatSearch").addEventListener("keydown", event => {
    if (event.key === "Escape") {
        $("chatSearch").value = "";
        $("chatSearch").dispatchEvent(new Event("input"));
        $("chatSearch").blur();
    }
    if (event.key === "Enter") $("chatList").querySelector("a")?.click();
});

function highlighted(snippet) {
    // The server marks matches with \x01 … \x02; escape everything else.
    return escapeText(snippet).replace(/\x01/g, "<mark>").replace(/\x02/g, "</mark>");
}

function renderSearch() {
    const results = state.searchResults;
    const nodes = results.map(result => {
        const item = Object.assign(document.createElement("li"), { className: "search-result" });
        const link = Object.assign(document.createElement("a"), { href: `#chat/${result.chat_id}` });
        link.append(Object.assign(document.createElement("strong"), { textContent: result.title }));
        if (result.snippet) {
            const snippet = document.createElement("small");
            snippet.innerHTML = highlighted(result.snippet);
            link.append(snippet);
        }
        link.addEventListener("click", event => {
            event.preventDefault();
            jumpTo(result.chat_id, result.message_id);
        });
        item.append(link);
        return item;
    });
    if (!nodes.length) nodes.push(Object.assign(document.createElement("li"), { className: "list-note", textContent: "No matches." }));
    $("chatList").replaceChildren(...nodes);
    $("archivedToggle").hidden = true;
}

async function jumpTo(chatId, messageId) {
    state.jumpTo = messageId;
    closeDrawer();
    if (location.hash === `#chat/${chatId}`) await openChat(chatId);
    else location.hash = `#chat/${chatId}`;
}
