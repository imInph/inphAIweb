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
loadChats();
pollJob();
