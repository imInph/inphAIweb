import { setUpVoice } from "./voice.js";
import { loadCollections } from "./knowledge.js";
import { $, autoGrow } from "./util.js";
import { showView, tick } from "./shell.js";
import { loadModels } from "./models.js";
import { loadChats } from "./chats.js";
import { setSending } from "./chat.js";
import { pollJob, updateImageNegative, updateImageSteps, updateVideoLength } from "./media.js";
import { loadSettings } from "./settings.js";
import { loadLibrary } from "./library.js";

// Start

for (const id of ["prompt", "imagePrompt", "videoPrompt", "imageNegative", "videoNegative",
                  "pref-default_system_prompt", "set-image_negative_prompt", "set-video_negative_prompt"]) {
    $(id).addEventListener("input", () => autoGrow($(id)));
}
updateImageSteps();
updateImageNegative();
updateVideoLength();
loadSettings();
setSending(false);
tick();
setInterval(tick, 1000);
showView();
loadModels();
loadLibrary();
loadCollections();
setUpVoice();
loadChats();
pollJob();

// Installable as an app (needs HTTPS, or localhost). The worker keeps only the app's own files.
if ("serviceWorker" in navigator && isSecureContext) {
    navigator.serviceWorker.register("/sw.js").catch(error => console.log("No service worker:", error));
    navigator.serviceWorker.addEventListener("message", event => {
        if (event.data && event.data.type === "open") location.hash = new URL(event.data.url, location.href).hash;
    });
}
