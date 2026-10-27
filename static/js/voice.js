import { $, api, autoGrow, button, request, storageGet, storageSet } from "./util.js";
import { state } from "./state.js";
import { splitThinking } from "./chat.js";
import { savePrefs } from "./settings.js";

// Voice: the microphone (Whisper on the server) and reading replies aloud (the browser's voices).

let recorder = null;
let chunks = [];
let timer = null;

export async function setUpVoice() {
    try {
        const { available } = await api("/api/transcribe");
        $("micButton").hidden = !available;
        $("micNote").textContent = !available
            ? "The microphone button appears once speech-to-text is set up on the server (bash scripts/setup.sh voice)."
            : window.isSecureContext ? "" : "Browsers only allow the microphone on HTTPS (or on the server itself). See the README to turn HTTPS on.";
    } catch {
        $("micButton").hidden = true;
    }
}

function recordingType() {
    for (const type of ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"]) {
        if (window.MediaRecorder && MediaRecorder.isTypeSupported(type)) return type;
    }
    return "";
}

function showRecording(text, kind = "") {
    $("recording").hidden = !text;
    $("recording").className = `recording ${kind}`;
    $("recording").textContent = text;
}

async function startRecording() {
    if (!window.isSecureContext || !navigator.mediaDevices) {
        alert("Your browser only allows the microphone on HTTPS (or on the server itself, at localhost). The README explains how to turn HTTPS on.");
        return;
    }
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
        alert(`The microphone isn't available: ${error.message}`);
        return;
    }
    chunks = [];
    // A local reference: stopRecording() clears `recorder` before the "stop" event arrives.
    const current = new MediaRecorder(stream, recordingType() ? { mimeType: recordingType() } : undefined);
    current.addEventListener("dataavailable", event => { if (event.data.size) chunks.push(event.data); });
    current.addEventListener("stop", () => {
        stream.getTracks().forEach(track => track.stop());
        transcribe(new Blob(chunks, { type: current.mimeType || "audio/webm" }));
    });
    current.start();
    recorder = current;
    const started = Date.now();
    const tick = () => {
        const seconds = Math.floor((Date.now() - started) / 1000);
        showRecording(`Recording ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} · tap the microphone to stop`, "live");
    };
    tick();
    timer = setInterval(tick, 500);
    $("micButton").setAttribute("aria-pressed", "true");
}

function stopRecording() {
    clearInterval(timer);
    $("micButton").setAttribute("aria-pressed", "false");
    if (recorder && recorder.state !== "inactive") recorder.stop();
    recorder = null;
}

async function transcribe(blob) {
    showRecording("Turning your words into text...");
    const form = new FormData();
    const extension = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
    form.append("audio", blob, `recording.${extension}`);
    try {
        const response = await request("/api/transcribe", { method: "POST", body: form });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Transcription failed");
        showRecording("");
        const box = $("prompt");
        if (result.text) {
            const before = box.value.slice(0, box.selectionStart), after = box.value.slice(box.selectionEnd);
            const joiner = before && !/\s$/.test(before) ? " " : "";
            box.value = before + joiner + result.text + after;
            autoGrow(box);
            box.focus();
            box.setSelectionRange((before + joiner + result.text).length, (before + joiner + result.text).length);
        } else {
            showRecording("No words were heard.", "error");
            setTimeout(() => showRecording(""), 2500);
        }
    } catch (error) {
        showRecording(error.message, "error");
        setTimeout(() => showRecording(""), 5000);
    }
}

$("micButton").addEventListener("click", () => (recorder ? stopRecording() : startRecording()));

// Reading aloud

let speaking = null;

function plainText(markdown) {
    return markdown
        .replace(/```[\s\S]*?```/g, " (code) ")
        .replace(/`([^`]+)`/g, "$1")
        .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
        .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
        .replace(/\$\$[\s\S]*?\$\$/g, " (formula) ")
        .replace(/^#+\s*/gm, "").replace(/^\s*[-*+]\s+/gm, "").replace(/^>\s?/gm, "")
        .replace(/[*_~|]/g, "").replace(/\[(\d+)\]/g, "")
        .replace(/\n{2,}/g, ". ").replace(/\s+/g, " ").trim();
}

function chosenVoice() {
    const name = storageGet("inphai.voice");
    return speechSynthesis.getVoices().find(voice => voice.name === name) || null;
}

export function speak(text, onEnd = () => {}) {
    if (!window.speechSynthesis) return alert("This browser can't read aloud.");
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(plainText(text));
    utterance.voice = chosenVoice();
    utterance.rate = state.me ? state.me.prefs.read_rate : 1;
    utterance.addEventListener("end", onEnd);
    utterance.addEventListener("error", onEnd);
    speechSynthesis.speak(utterance);
    return utterance;
}

/** The "Read aloud" button under a reply. */
export function readAloudButton(message) {
    const control = button("Read aloud", () => {
        if (speaking === control) {
            speechSynthesis.cancel();
            return;
        }
        if (speaking) speaking.textContent = "Read aloud";
        speaking = control;
        control.textContent = "Stop reading";
        speak(splitThinking(message).content, () => {
            if (speaking === control) speaking = null;
            control.textContent = "Read aloud";
        });
    });
    return control;
}

export function autoRead(message) {
    if (state.me && state.me.prefs.auto_read && message && message.content) speak(splitThinking(message).content);
}

// Settings: the voice list belongs to this device, so it's kept in this browser.

function fillVoices() {
    const select = $("readVoice");
    const voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
    select.replaceChildren(new Option("The browser's default", ""),
        ...voices.map(voice => new Option(`${voice.name} (${voice.lang})`, voice.name)));
    select.value = storageGet("inphai.voice") || "";
}

if (window.speechSynthesis) {
    speechSynthesis.addEventListener("voiceschanged", fillVoices);
    fillVoices();
}
$("readVoice").addEventListener("change", () => storageSet("inphai.voice", $("readVoice").value));
$("pref-read_rate").addEventListener("input", () => { $("readRateValue").textContent = `${Number($("pref-read_rate").value).toFixed(1)}×`; });
$("pref-read_rate").addEventListener("change", () => savePrefs({ read_rate: Number($("pref-read_rate").value) }, "voice"));
$("testVoice").addEventListener("click", () => speak("Hello! This is how replies will sound."));
export function paintVoiceSettings() {
    $("readRateValue").textContent = `${Number($("pref-read_rate").value || 1).toFixed(1)}×`;
    fillVoices();
}
