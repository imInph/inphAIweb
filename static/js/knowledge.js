import { $, api, button, icon, iconButton, request } from "./util.js";
import { state } from "./state.js";
import { showSaved, formatBytes } from "./settings.js";

// Knowledge: collections of documents a chat can draw on (# in the composer), and the page that manages them.

let openCollection = null;
let pollTimer = null;

export async function loadCollections() {
    try {
        state.collections = (await api("/api/knowledge")).items;
    } catch (error) {
        console.error(error);
    }
    renderCollectionList();
    renderChatKnowledge();
    document.dispatchEvent(new Event("inphai:collections"));
}

function renderCollectionList() {
    const list = $("collectionList");
    if (!list) return;
    list.replaceChildren(...(state.collections.length ? state.collections.map(collection => {
        const item = Object.assign(document.createElement("li"), { className: "item-row collection-row" });
        item.classList.toggle("active", openCollection && openCollection.id === collection.id);
        const text = Object.assign(document.createElement("button"), { type: "button", className: "item-text" });
        text.append(Object.assign(document.createElement("strong"), { textContent: collection.name }),
            Object.assign(document.createElement("small"), { textContent:
                `${collection.documents} document${collection.documents === 1 ? "" : "s"}${collection.indexing ? ` · ${collection.indexing} indexing` : ""}` }));
        text.addEventListener("click", () => openKnowledge(collection.id));
        item.append(text);
        return item;
    }) : [Object.assign(document.createElement("li"), { className: "list-note", textContent: "No collections yet." })]));
}

export async function loadKnowledgePage() {
    await loadCollections();
    if (openCollection) openKnowledge(openCollection.id);
    else if (state.collections.length) openKnowledge(state.collections[0].id);
    else $("collectionDetail").hidden = true;
}

async function openKnowledge(id) {
    clearTimeout(pollTimer);
    try {
        openCollection = await api(`/api/knowledge/${id}`);
    } catch {
        openCollection = null;
        $("collectionDetail").hidden = true;
        return;
    }
    renderCollectionList();
    renderCollection();
    if (openCollection.documents.some(d => d.status === "queued" || d.status === "indexing") && state.view === "knowledge") {
        pollTimer = setTimeout(() => { openKnowledge(id); loadCollections(); }, 1500);
    }
}

const STATUS = { queued: "waiting to be indexed", indexing: "indexing...", ready: "ready", error: "failed" };

function renderCollection() {
    const collection = openCollection;
    $("collectionDetail").hidden = false;
    $("collectionName").textContent = collection.name;
    if (document.activeElement !== $("collectionDescription")) $("collectionDescription").value = collection.description;
    $("documentList").replaceChildren(...(collection.documents.length ? collection.documents.map(document_ => {
        const item = Object.assign(document.createElement("li"), { className: `item-row document-row ${document_.status}` });
        const text = Object.assign(document.createElement("div"), { className: "item-text" });
        const detail = document_.status === "ready" ? `${formatBytes(document_.size)} · ${document_.chunks} passage${document_.chunks === 1 ? "" : "s"}`
            : document_.status === "error" ? document_.error : `${formatBytes(document_.size)} · ${STATUS[document_.status]}`;
        const link = Object.assign(document.createElement("a"), { href: `/api/knowledge/${collection.id}/documents/${document_.id}/file`,
                                                                   textContent: document_.name, target: "_blank" });
        const strong = document.createElement("strong");
        strong.append(link);
        text.append(strong, Object.assign(document.createElement("small"), { textContent: detail, className: document_.status === "error" ? "error-text" : "" }));
        item.append(text, iconButton("refresh", "Index again", () => reindex(document_)), iconButton("trash", "Delete", () => removeDocument(document_)));
        return item;
    }) : [Object.assign(document.createElement("li"), { className: "list-note", textContent: "No documents yet." })]));
}

async function uploadDocuments(files) {
    if (!openCollection || !files.length) return;
    const form = new FormData();
    for (const file of files) form.append("file", file, file.name);
    showSaved("knowledge", `Uploading ${files.length} file${files.length === 1 ? "" : "s"}...`);
    try {
        const response = await request(`/api/knowledge/${openCollection.id}/documents`, { method: "POST", body: form });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Upload failed");
        const failed = result.results.filter(r => r.error);
        if (failed.length) showSaved("knowledge", failed.map(r => `${r.name}: ${r.error}`).join(" · "), true);
        else showSaved("knowledge", "Added; indexing now");
    } catch (error) {
        showSaved("knowledge", error.message, true);
    }
    openKnowledge(openCollection.id);
    loadCollections();
}

async function reindex(document_) {
    await api(`/api/knowledge/${openCollection.id}/documents/${document_.id}/reindex`, { method: "POST" }).catch(error => alert(error.message));
    openKnowledge(openCollection.id);
}

async function removeDocument(document_) {
    if (!confirm(`Remove ${document_.name} from ${openCollection.name}?`)) return;
    await api(`/api/knowledge/${openCollection.id}/documents/${document_.id}`, { method: "DELETE" }).catch(error => alert(error.message));
    openKnowledge(openCollection.id);
    loadCollections();
}

$("newCollectionForm").addEventListener("submit", async event => {
    event.preventDefault();
    const name = $("newCollectionName").value.trim();
    if (!name) return;
    try {
        const created = await api("/api/knowledge", { method: "POST", body: { name } });
        $("newCollectionName").value = "";
        openCollection = created;
        await loadCollections();
        openKnowledge(created.id);
    } catch (error) {
        alert(error.message);
    }
});

$("renameCollection").addEventListener("click", async () => {
    const name = prompt("Rename collection", openCollection.name);
    if (!name || !name.trim()) return;
    await api(`/api/knowledge/${openCollection.id}`, { method: "PATCH", body: { name: name.trim() } }).catch(error => alert(error.message));
    openKnowledge(openCollection.id);
    loadCollections();
});

$("deleteCollection").addEventListener("click", async () => {
    if (!confirm(`Delete ${openCollection.name} and all its documents?`)) return;
    await api(`/api/knowledge/${openCollection.id}`, { method: "DELETE" }).catch(error => alert(error.message));
    openCollection = null;
    loadKnowledgePage();
});

$("collectionDescription").addEventListener("change", async () => {
    try {
        await api(`/api/knowledge/${openCollection.id}`, { method: "PATCH", body: { description: $("collectionDescription").value } });
        showSaved("knowledge", "Saved");
    } catch (error) {
        showSaved("knowledge", error.message, true);
    }
});

$("addDocuments").addEventListener("click", () => $("documentInput").click());
$("documentInput").addEventListener("change", () => {
    uploadDocuments([...$("documentInput").files]);
    $("documentInput").value = "";
});
const drop = $("documentDrop");
drop.addEventListener("dragover", event => { event.preventDefault(); drop.classList.add("drop"); });
drop.addEventListener("dragleave", () => drop.classList.remove("drop"));
drop.addEventListener("drop", event => {
    event.preventDefault();
    drop.classList.remove("drop");
    uploadDocuments([...event.dataTransfer.files]);
});

$("trySearchForm").addEventListener("submit", async event => {
    event.preventDefault();
    const query = $("trySearch").value.trim();
    if (!query || !openCollection) return;
    $("tryResults").replaceChildren(Object.assign(document.createElement("li"), { className: "list-note", textContent: "Searching..." }));
    try {
        const { results } = await api("/api/knowledge/search", { method: "POST", body: { knowledge_ids: [openCollection.id], query } });
        $("tryResults").replaceChildren(...(results.length ? results.map(passageItem)
            : [Object.assign(document.createElement("li"), { className: "list-note", textContent: "Nothing found (is anything indexed yet?)." })]));
    } catch (error) {
        $("tryResults").replaceChildren(Object.assign(document.createElement("li"), { className: "list-note error-text", textContent: error.message }));
    }
});

function passageItem(passage) {
    const item = document.createElement("li");
    item.append(Object.assign(document.createElement("strong"), { textContent: passage.document }),
        Object.assign(document.createElement("p"), { textContent: passage.text }));
    return item;
}

// A chat's collections: chips above the composer; "#" adds one.

function chatKnowledgeIds() {
    return (state.chat ? state.chat.knowledge_ids : state.draft.knowledge_ids) || [];
}

async function setChatKnowledge(ids) {
    if (state.chat) {
        try {
            const saved = await api(`/api/chats/${state.chat.id}`, { method: "PATCH", body: { knowledge_ids: ids } });
            state.chat.knowledge_ids = saved.knowledge_ids;
        } catch (error) {
            alert(error.message);
        }
    } else {
        state.draft.knowledge_ids = ids;
    }
    renderChatKnowledge();
}

export function renderChatKnowledge() {
    const box = $("chatKnowledge");
    const ids = chatKnowledgeIds();
    const attached = ids.map(id => state.collections.find(c => c.id === id)).filter(Boolean);
    box.hidden = !attached.length;
    box.replaceChildren(...attached.map(collection => {
        const chip = Object.assign(document.createElement("div"), { className: "file-chip knowledge-chip", title: "The model searches this collection for every message" });
        chip.insertAdjacentHTML("beforeend", `<span class="file-icon">${icon("book", 16)}</span>`);
        const text = Object.assign(document.createElement("span"), { className: "file-text" });
        text.append(Object.assign(document.createElement("span"), { className: "file-name", textContent: collection.name }),
            Object.assign(document.createElement("span"), { className: "file-size", textContent: `${collection.documents} documents` }));
        chip.append(text, iconButton("close", `Stop using ${collection.name}`, () => setChatKnowledge(ids.filter(i => i !== collection.id))));
        return chip;
    }));
}

let hashMatches = [];
let hashIndex = 0;

function hashQuery() {
    const match = $("prompt").value.slice(0, $("prompt").selectionStart).match(/(^|\s)#([\w-]*)$/);
    return match ? match[2].toLowerCase() : null;
}

function renderHash() {
    const query = hashQuery();
    const menu = $("hashMenu");
    const attached = chatKnowledgeIds();
    hashMatches = query === null ? [] : state.collections.filter(c => !attached.includes(c.id) && c.name.toLowerCase().includes(query));
    menu.hidden = !hashMatches.length;
    if (menu.hidden) return;
    hashIndex = Math.min(hashIndex, hashMatches.length - 1);
    menu.replaceChildren(...hashMatches.map((collection, i) => {
        const option = Object.assign(document.createElement("button"), { type: "button", role: "option", className: "slash-option" });
        option.classList.toggle("active", i === hashIndex);
        option.append(Object.assign(document.createElement("strong"), { textContent: `#${collection.name}` }),
            Object.assign(document.createElement("span"), { textContent: `${collection.documents} documents` }));
        option.addEventListener("pointerdown", event => { event.preventDefault(); useCollection(collection); });
        return option;
    }));
}

function useCollection(collection) {
    const box = $("prompt");
    const before = box.value.slice(0, box.selectionStart).replace(/(^|\s)#[\w-]*$/, "$1");
    const after = box.value.slice(box.selectionStart);
    box.value = before + after;
    box.setSelectionRange(before.length, before.length);
    $("hashMenu").hidden = true;
    setChatKnowledge([...chatKnowledgeIds(), collection.id]);
    box.focus();
}

$("prompt").addEventListener("input", () => { hashIndex = 0; renderHash(); });
$("prompt").addEventListener("blur", () => setTimeout(() => { $("hashMenu").hidden = true; }, 150));
$("prompt").addEventListener("keydown", event => {
    if ($("hashMenu").hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        hashIndex = (hashIndex + (event.key === "ArrowDown" ? 1 : -1) + hashMatches.length) % hashMatches.length;
        renderHash();
    } else if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        event.stopImmediatePropagation();
        useCollection(hashMatches[hashIndex]);
    } else if (event.key === "Escape") {
        $("hashMenu").hidden = true;
    }
}, true);

/** Tick boxes for picking collections (the persona form). */
export function renderKnowledgeChecks(container, selected = []) {
    container.replaceChildren(...(state.collections.length ? state.collections.map(collection => {
        const label = Object.assign(document.createElement("label"), { className: "check-row" });
        const box = Object.assign(document.createElement("input"), { type: "checkbox", value: String(collection.id), checked: selected.includes(collection.id) });
        label.append(box, Object.assign(document.createElement("span"), { textContent: collection.name }));
        return label;
    }) : [Object.assign(document.createElement("p"), { className: "card-note", textContent: "No collections yet (make one on the Knowledge page)." })]));
}

export function readKnowledgeChecks(container) {
    return [...container.querySelectorAll("input:checked")].map(box => Number(box.value));
}

/** The Sources list under a reply. */
export function sourcesElement(sources) {
    const details = Object.assign(document.createElement("details"), { className: "sources" });
    details.append(Object.assign(document.createElement("summary"), { textContent: `Sources (${sources.length})` }));
    const list = document.createElement("ol");
    for (const source of sources) {
        const item = document.createElement("li");
        const head = Object.assign(document.createElement("button"), { type: "button", className: "source-head" });
        let where = source.knowledge;
        if (source.kind === "web") {
            try { where = new URL(source.url).hostname.replace(/^www\./, ""); } catch { where = source.url; }
        }
        head.append(Object.assign(document.createElement("strong"), { textContent: source.title || source.document }),
            Object.assign(document.createElement("small"), { textContent: where }));
        const text = Object.assign(document.createElement("p"), { textContent: source.text, hidden: true });
        head.addEventListener("click", () => { text.hidden = !text.hidden; });
        item.append(head);
        if (source.kind === "web" && /^https?:\/\//.test(source.url)) {
            item.append(Object.assign(document.createElement("a"), { className: "source-link", href: source.url, target: "_blank",
                                                                     rel: "noopener noreferrer", textContent: "open" }));
        }
        item.append(text);
        list.append(item);
    }
    details.append(list);
    return details;
}
