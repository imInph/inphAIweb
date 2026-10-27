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
    if (!state.settings) return;
    for (const kind of ["image", "video"]) {
        const box = $(`${kind}Negative`);
        if (force || !box.dataset.touched) {
            box.value = state.settings[`${kind}_negative_prompt`];
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
    try {
        state.job = await api(kind === "image" ? "/api/images" : "/api/videos", { method: "POST", body: settings });
        renderJob();
        pollJob();
    } catch (error) {
        state.job = { state: "error", kind, error: error.message, output: [] };
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
    if (job.state === "done") return `Done in ${Math.round(job.media.seconds)} s.`;
    if (job.state === "cancelled") return "Cancelled.";
    if (job.state === "error") return `Error: ${job.error}`;
    return "";
}

export function renderJob() {
    const job = state.job;
    const running = job && job.state === "running";
    for (const kind of ["image", "video"]) {
        const box = $(`${kind}Job`);
        const generateButton = $(`${kind}Generate`);
        generateButton.disabled = running;
        generateButton.textContent = running && job.kind === kind ? "Generating..." : "Generate";
        if (!job || job.kind !== kind || job.state === "idle") {
            box.hidden = !running;
            if (running) box.replaceChildren(Object.assign(document.createElement("p"), {
                textContent: `Waiting for the ${job.kind} that is generating to finish.` }));
            continue;
        }
        box.hidden = false;
        const message = Object.assign(document.createElement("p"), { textContent: jobMessage(job) });
        if (job.state === "error") message.className = "error";
        const parts = [message];
        if (running) {
            const row = Object.assign(document.createElement("div"), { className: "row" });
            row.append(progressBar(job), button("Cancel", () => api("/api/job/cancel", { method: "POST" }).then(pollJob), "btn btn-sm"));
            parts.push(row);
        }
        if (job.output && job.output.length && (running || job.state === "error")) {
            const details = document.createElement("details");
            details.append(Object.assign(document.createElement("summary"), { textContent: "Output" }),
                Object.assign(document.createElement("pre"), { textContent: job.output.join("\n") }));
            parts.push(details);
        }
        box.replaceChildren(...parts);
    }

    const pill = $("jobPill");
    pill.hidden = !running;
    if (running) {
        const link = Object.assign(document.createElement("a"), {
            href: job.kind === "image" ? "#images" : "#videos",
            textContent: `${job.kind === "image" ? "Image" : "Video"} · ${jobMessage(job)}`,
        });
        pill.replaceChildren(link, progressBar(job));
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
        const previous = state.job;
        const job = await api("/api/job");
        // Keep showing a failed start (e.g. a busy GPU) instead of the idle status.
        if (!(job.state === "idle" && previous && previous.state === "error")) state.job = job;
        renderJob();
        if (previous && previous.state === "running" && job.state !== "running") {
            loadGallery(job.kind);
        }
    } catch (error) {
        console.log("Could not check the job:", error);
    }
    if (state.job && state.job.state === "running") state.jobTimer = setTimeout(pollJob, 2000);
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
