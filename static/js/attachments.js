import { modelCapabilities } from "./options.js";
import { selectedModel } from "./models.js";
import { $, api, icon, iconButton, request } from "./util.js";
import { state } from "./state.js";

// Attachments

state.pending = [];

export function formatSize(bytes) {
    return bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function fileChip(file, onRemove) {
    const chip = Object.assign(document.createElement(file.url && !onRemove ? "a" : "div"), { className: "file-chip" });
    if (file.url && !onRemove) Object.assign(chip, { href: file.url, title: `Download ${file.name}` });
    if (file.status === "error") chip.classList.add("error");
    if (file.status === "uploading") chip.classList.add("loading");
    if (file.kind === "image" && (file.preview || file.url)) {
        chip.append(Object.assign(document.createElement("img"), { src: file.preview || file.url, alt: "" }));
    } else {
        const ext = (file.name.split(".").pop() || "file").slice(0, 4);
        chip.append(Object.assign(document.createElement("span"), { className: "file-icon", textContent: ext }));
    }
    const text = Object.assign(document.createElement("span"), { className: "file-text" });
    const detail = file.status === "uploading" ? "Uploading..." : file.status === "error" ? file.error
        : `${formatSize(file.size)}${file.truncated ? " · cut to fit" : ""}`;
    text.append(Object.assign(document.createElement("span"), { className: "file-name", textContent: file.name, title: file.name }),
        Object.assign(document.createElement("span"), { className: "file-size", textContent: detail }));
    chip.append(text);
    if (onRemove) chip.append(iconButton("close", `Remove ${file.name}`, onRemove));
    return chip;
}

export function renderPending() {
    const box = $("pendingFiles");
    box.hidden = !state.pending.length;
    box.replaceChildren(...state.pending.map(file => fileChip(file, () => removePending(file))));
    const capabilities = modelCapabilities(selectedModel());
    if (state.pending.some(file => file.kind === "image") && capabilities && !capabilities.includes("vision")) {
        box.append(Object.assign(document.createElement("p"), { className: "vision-warning",
            textContent: "This model can't see images. Pick one marked \"sees images\" in the model list." }));
    }
}

export function removePending(file) {
    state.pending = state.pending.filter(f => f !== file);
    if (file.id) api(`/api/uploads/${file.id}`, { method: "DELETE" }).catch(() => {});
    if (file.preview) URL.revokeObjectURL(file.preview);
    renderPending();
}

export async function addFiles(files) {
    for (const blob of files) {
        const file = { name: blob.name || "pasted-image.png", size: blob.size, status: "uploading",
                       kind: blob.type.startsWith("image/") ? "image" : "text",
                       preview: blob.type.startsWith("image/") ? URL.createObjectURL(blob) : null };
        state.pending.push(file);
        renderPending();
        const form = new FormData();
        form.append("file", blob, file.name);
        try {
            const response = await request("/api/uploads", { method: "POST", body: form });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.error || `Upload failed (${response.status})`);
            Object.assign(file, result, { status: "ready" });
            if (!state.pending.includes(file)) api(`/api/uploads/${file.id}`, { method: "DELETE" }).catch(() => {});
        } catch (error) {
            Object.assign(file, { status: "error", error: error.message });
        }
        renderPending();
    }
}

$("attachButton").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", () => {
    addFiles([...$("fileInput").files]);
    $("fileInput").value = "";
});
$("prompt").addEventListener("paste", event => {
    const files = [...(event.clipboardData?.files || [])];
    if (files.length) {
        event.preventDefault();
        addFiles(files);
    }
});

export let dragDepth = 0;
export const dragHasFiles = event => [...(event.dataTransfer?.types || [])].includes("Files");
$("view-chat").addEventListener("dragenter", event => {
    if (!dragHasFiles(event)) return;
    dragDepth++;
    $("dropOverlay").hidden = false;
});
$("view-chat").addEventListener("dragleave", () => {
    if (--dragDepth <= 0) { dragDepth = 0; $("dropOverlay").hidden = true; }
});
$("view-chat").addEventListener("dragover", event => { if (dragHasFiles(event)) event.preventDefault(); });
$("view-chat").addEventListener("drop", event => {
    if (!dragHasFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    $("dropOverlay").hidden = true;
    addFiles([...event.dataTransfer.files]);
});
