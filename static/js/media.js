import { openChat, renderMessages } from "./chat.js";
import { renderPending } from "./attachments.js";
import { $, api, autoGrow, button, timeAgo } from "./util.js";
import { state } from "./state.js";

export const STAGES = {
    starting: "Starting...",
    loading_model: "Loading model...",
    generating: "Generating...",
    decoding: "Decoding...",
    saving: "Saving...",
    complete: "Finishing...",
};

// Images and videos

export function updateImageSteps() {
    const anime = $("imageModel").value === "anime";
    const choices = anime ? [20, 28, 30, 40, 50] : [4, 6, 9, 12];
    const recommended = anime ? 28 : 9;
    $("imageSteps").replaceChildren(...choices.map(value => {
        const text = value === recommended ? `${value} · default` : String(value);
        return new Option(text, String(value), value === recommended, value === recommended);
    }));
}

export function updateVideoLength() {
    const seconds = Number($("videoFrames").value) / Number($("videoFps").value);
    $("videoLength").textContent = `About ${seconds.toFixed(1)} s of video. It takes a while, and keeps going if you leave the page.`;
}

export function updateImageNegative() {
    // Z-Image-Turbo runs without a negative prompt; generate_image.py rejects one.
    const realistic = $("imageModel").value === "realistic";
    $("imageNegative").disabled = realistic;
    $("imageNegativeNote").textContent = realistic ? "· not used by Z-Image-Turbo" : "";
}

export function fillNegatives(force = false) {
    if (!state.me) return;
    for (const kind of ["image", "video"]) {
        const box = $(`${kind}Negative`);
        if (force || !box.dataset.touched) {
            box.value = state.me.server[`${kind}_negative_prompt`];
            autoGrow(box);
        }
    }
}

$("imageModel").addEventListener("change", () => {
    updateImageSteps();
    updateImageNegative();
});
for (const kind of ["image", "video"]) {
    $(`${kind}Negative`).addEventListener("input", event => event.target.dataset.touched = "1");
}
$("videoFrames").addEventListener("change", updateVideoLength);
$("videoFps").addEventListener("change", updateVideoLength);

export function seedFrom(select) {
    return select.value === "random" ? Math.floor(Math.random() * 2 ** 31) : Number(select.value);
}

export function generationSettings(kind) {
    if (kind === "image") {
        const [width, height] = $("imageResolution").value.split("x").map(Number);
        const settings = { model: $("imageModel").value, prompt: $("imagePrompt").value.trim(), width, height,
                           steps: Number($("imageSteps").value), seed: seedFrom($("imageSeed")) };
        if (settings.model === "anime") settings.negative_prompt = $("imageNegative").value.trim();
        return settings;
    }
    const [width, height] = $("videoResolution").value.split("x").map(Number);
    return { prompt: $("videoPrompt").value.trim(), negative_prompt: $("videoNegative").value.trim(), width, height,
             frames: Number($("videoFrames").value), steps: Number($("videoSteps").value),
             fps: Number($("videoFps").value), seed: seedFrom($("videoSeed")) };
}

export async function generate(kind) {
    const settings = generationSettings(kind);
    if (!settings.prompt) {
        $(`${kind}Prompt`).focus();
        return;
    }
    state.startErrors[kind] = null;
    try {
        const job = await api(kind === "image" ? "/api/images" : "/api/videos", { method: "POST", body: settings });
        state.jobs.push(job);
        renderJob();
        pollJob();
    } catch (error) {
        state.startErrors[kind] = error.message;
        renderJob();
    }
}

document.querySelectorAll(".generate-button").forEach(button =>
    button.addEventListener("click", () => generate(button.dataset.kind)));

export function jobMessage(job) {
    if (job.state === "running") {
        if (job.stage === "generating" && job.completed_steps > 0) {
            return `Generating: ${job.completed_steps} / ${job.total_steps} steps`;
        }
        return STAGES[job.stage] || "Generating...";
    }
    if (job.state === "queued") {
        return job.ahead ? `Waiting · ${job.ahead} job${job.ahead === 1 ? "" : "s"} ahead of it` : "Starting...";
    }
    if (job.state === "done") return `Done in ${Math.round(job.media ? job.media.seconds : 0)} s.`;
    if (job.state === "cancelled") return "Cancelled.";
    if (job.state === "error") return `Error: ${job.error}`;
    return "";
}

const active = job => job.state === "queued" || job.state === "running";

function cancelJob(job) {
    api(`/api/jobs/${job.id}/cancel`, { method: "POST" }).catch(error => alert(error.message)).then(pollJob);
}

function jobRow(job) {
    const row = Object.assign(document.createElement("div"), { className: `job-row ${job.state}` });
    const head = Object.assign(document.createElement("div"), { className: "row" });
    const text = Object.assign(document.createElement("div"), { className: "job-text" });
    text.append(Object.assign(document.createElement("p"), { textContent: jobMessage(job), className: job.state === "error" ? "error" : "" }),
        Object.assign(document.createElement("small"), { textContent: job.settings.prompt }));
    head.append(text);
    if (active(job)) head.append(button("Cancel", () => cancelJob(job), "btn btn-sm"));
    row.append(head);
    if (job.state === "running") row.append(progressBar(job));
    if (job.output && job.output.length) {
        const details = document.createElement("details");
        details.append(Object.assign(document.createElement("summary"), { textContent: "Output" }),
            Object.assign(document.createElement("pre"), { textContent: job.output.join("\n") }));
        row.append(details);
    }
    return row;
}

export function renderJob() {
    const mine = state.jobs;
    const running = mine.find(job => job.state === "running");
    for (const kind of ["image", "video"]) {
        const box = $(`${kind}Job`);
        const jobs = mine.filter(job => job.kind === kind);
        const busy = mine.some(active) || state.busy;
        $(`${kind}Generate`).textContent = busy ? "Add to queue" : "Generate";
        const parts = [];
        if (state.startErrors[kind]) parts.push(Object.assign(document.createElement("p"), { className: "error", textContent: state.startErrors[kind] }));
        if (state.busy && !running) {
            parts.push(Object.assign(document.createElement("p"), { className: "job-note",
                textContent: `The GPU is busy with someone else's ${state.busy}. Yours will wait its turn.` }));
        }
        parts.push(...jobs.map(jobRow));
        box.hidden = !parts.length;
        box.replaceChildren(...parts);
    }

    const pill = $("jobPill");
    const waiting = mine.filter(job => job.state === "queued").length;
    pill.hidden = !running && !waiting;
    if (!pill.hidden) {
        const job = running || mine.find(job => job.state === "queued");
        const label = running ? `${job.kind === "image" ? "Image" : "Video"} · ${jobMessage(job)}` : "Waiting for the GPU";
        const link = Object.assign(document.createElement("a"), {
            href: job.kind === "image" ? "#images" : "#videos",
            textContent: label + (waiting && running ? ` · ${waiting} more waiting` : ""),
        });
        pill.replaceChildren(link, ...(running ? [progressBar(running)] : []));
    }
}

export function progressBar(job) {
    const bar = Object.assign(document.createElement("div"), { className: "bar" });
    const fill = document.createElement("div");
    fill.style.width = job.total_steps ? `${Math.round(100 * job.completed_steps / job.total_steps)}%` : "0";
    bar.append(fill);
    return bar;
}

export async function pollJob() {
    clearTimeout(state.jobTimer);
    try {
        const before = new Map(state.jobs.map(job => [job.id, job.state]));
        const result = await api("/api/jobs");
        state.jobs = result.jobs;
        state.busy = result.busy;
        renderJob();
        // A job that just finished puts its file in the gallery (and in the chat that asked for it).
        const finished = result.jobs.filter(job => job.state === "done" && before.get(job.id) !== "done");
        for (const kind of new Set(finished.map(job => job.kind))) loadGallery(kind);
        if (state.chat && !state.live && result.jobs.some(job => job.chat_id === state.chat.id)) {
            // A chat waiting on a job: show its progress, and its file once it's done.
            if (finished.some(job => job.chat_id === state.chat.id)
                || result.jobs.some(job => job.chat_id === state.chat.id && !active(job) && before.get(job.id) && before.get(job.id) !== job.state)) {
                state.chat = await api(`/api/chats/${state.chat.id}`);
            }
            renderMessages();
        }
    } catch (error) {
        console.log("Could not check the jobs:", error);
    }
    if (state.jobs.some(active) || state.busy) state.jobTimer = setTimeout(pollJob, 2000);
}

export async function loadGallery(kind) {
    const gallery = $(`${kind}Gallery`);
    let items;
    try {
        items = (await api(`/api/media?kind=${kind}`)).media;
    } catch (error) {
        gallery.replaceChildren(Object.assign(document.createElement("p"), { className: "note", textContent: error.message }));
        return;
    }
    if (!items.length) {
        gallery.replaceChildren(Object.assign(document.createElement("p"), { className: "note",
            textContent: `Your ${kind}s will show up here.` }));
        return;
    }
    gallery.replaceChildren(...items.map(item => {
        const card = Object.assign(document.createElement("div"), { className: "card media-card" });
        let media;
        if (kind === "image") {
            media = Object.assign(document.createElement("img"), { src: item.url, alt: item.prompt, loading: "lazy" });
            const link = Object.assign(document.createElement("a"), { href: item.url, target: "_blank" });
            link.append(media);
            media = link;
        } else {
            media = Object.assign(document.createElement("video"), { src: item.url, controls: true, preload: "metadata", loop: true });
        }
        const caption = Object.assign(document.createElement("div"), { className: "caption" });
        caption.append(Object.assign(document.createElement("p"), { textContent: item.prompt, title: item.prompt }));
        const s = item.settings;
        const details = kind === "image"
            ? `${s.model} · ${s.width}×${s.height} · ${s.steps} steps · seed ${s.seed}`
            : `${s.width}×${s.height} · ${s.frames} frames · ${s.fps} fps · seed ${s.seed}`;
        caption.append(Object.assign(document.createElement("div"), { className: "details", textContent: `${details} · ${timeAgo(item.created_at)}` }));
        const actions = Object.assign(document.createElement("div"), { className: "actions" });
        const download = Object.assign(document.createElement("a"), { className: "btn btn-ghost btn-sm", href: item.url, download: "", textContent: "Download" });
        if (kind === "image") actions.append(button("Ask about this", () => askAbout(item)));
        actions.append(button("Reuse", () => reuseSettings(kind, s)), download, button("Delete", async () => {
            if (!confirm(`Delete this ${kind}?`)) return;
            await api(`/api/media/${item.id}`, { method: "DELETE" }).catch(error => alert(error.message));
            loadGallery(kind);
        }));
        caption.append(actions);
        card.append(media, caption);
        return card;
    }));
}

export function setSelect(select, value) {
    value = String(value);
    if (![...select.options].some(option => option.value === value)) select.append(new Option(value, value));
    select.value = value;
}

export function reuseSettings(kind, s) {
    const prompt = $(`${kind}Prompt`);
    prompt.value = s.prompt;
    autoGrow(prompt);
    const negative = $(`${kind}Negative`);
    if (typeof s.negative_prompt === "string" && s.negative_prompt) {
        negative.value = s.negative_prompt;
        negative.dataset.touched = "1";
        autoGrow(negative);
    }
    if (kind === "image") {
        $("imageModel").value = s.model;
        updateImageSteps();
        updateImageNegative();
        setSelect($("imageResolution"), `${s.width}x${s.height}`);
        setSelect($("imageSteps"), s.steps);
        setSelect($("imageSeed"), s.seed);
    } else {
        setSelect($("videoResolution"), `${s.width}x${s.height}`);
        setSelect($("videoFrames"), s.frames);
        setSelect($("videoSteps"), s.steps);
        setSelect($("videoFps"), s.fps);
        setSelect($("videoSeed"), s.seed);
        updateVideoLength();
    }
    $(`view-${kind}s`).scrollTop = 0;
    prompt.focus();
}

async function askAbout(item) {
    try {
        const attachment = await api(`/api/media/${item.id}/attach`, { method: "POST" });
        location.hash = "#chat";
        await openChat(null);
        state.pending.push({ ...attachment, status: "ready" });
        renderPending();
        $("prompt").focus();
    } catch (error) {
        alert(error.message);
    }
}
