const button = document.getElementById("generateButton");
const image = document.getElementById("resultImage");
const statusText = document.getElementById("status");
const promptInput = document.getElementById("prompt");
const modelInput = document.getElementById("model");
const stepsInput = document.getElementById("steps");
const controls = [...document.querySelectorAll("textarea, select")];

promptInput.addEventListener("input", () => {
    promptInput.style.height = "auto";
    promptInput.style.height = promptInput.scrollHeight + "px";
});

function updateSteps() {
    const anime = modelInput.value === "anime";
    const choices = anime ? [20, 28, 30, 40, 50] : [4, 6, 9, 12];
    const recommended = anime ? 28 : 9;
    stepsInput.replaceChildren(...choices.map(value => {
        const text = value === recommended ? `${value} — Default` : String(value);
        return new Option(text, String(value), value === recommended, value === recommended);
    }));
}

modelInput.addEventListener("change", updateSteps);
updateSteps();

function showStatus(message) {
    statusText.textContent = message;
}

function progressMessage(result) {
    switch (result.stage) {
        case "starting": return "Starting generation...";
        case "loading_model": return "Loading model...";
        case "generating":
            return result.completed_steps > 0
                ? `Generating: ${result.completed_steps} / ${result.total_steps} steps`
                : "Encoding prompt and starting generation...";
        case "decoding": return "Decoding image...";
        case "saving": return "Saving image...";
        case "complete": return "Finishing...";
        default: return "Generating...";
    }
}

button.addEventListener("click", async () => {
    const prompt = promptInput.value.trim();
    if (!prompt) {
        showStatus("Enter a prompt first.");
        promptInput.focus();
        return;
    }

    const [width, height] = document.getElementById("resolution").value.split("x").map(Number);
    const data = {
        model: modelInput.value,
        prompt,
        width,
        height,
        seed: Number(document.getElementById("seed").value),
        steps: Number(stepsInput.value)
    };

    button.disabled = true;
    button.textContent = "Generating...";
    controls.forEach(control => control.disabled = true);
    showStatus("Starting generation...");

    let polling = true;
    let timer;
    async function checkStatus() {
        try {
            const response = await fetch("/status", { cache: "no-store" });
            if (response.ok) {
                const result = await response.json();
                if (polling && result.state === "running") {
                    showStatus(progressMessage(result));
                }
            }
        } catch (error) {
            console.log("Could not check status:", error);
        } finally {
            if (polling) timer = setTimeout(checkStatus, 2000);
        }
    }
    timer = setTimeout(checkStatus, 2000);

    try {
        const response = await fetch("/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        });
        const result = await response.json();
        if (!response.ok) {
            console.error(result.output);
            throw new Error(result.error || "Generation failed.");
        }
        if (!result.image_url) throw new Error("The server did not return an image URL.");

        image.src = result.image_url;
        image.hidden = false;
        showStatus("Done! Your image is ready.");
    } catch (error) {
        showStatus(`Error: ${error.message}`);
    } finally {
        polling = false;
        clearTimeout(timer);
        button.disabled = false;
        button.textContent = "Generate";
        controls.forEach(control => control.disabled = false);
    }
});
