const button = document.getElementById("generateButton");
const video = document.getElementById("resultVideo");
const statusText = document.getElementById("status");
const promptInput = document.getElementById("prompt");

promptInput.addEventListener("input", function() {
    this.style.height = "auto";
    this.style.height = this.scrollHeight + "px";
});

function showStatus(message) {
    if (statusText) statusText.textContent = message;
    console.log(message);
}

button.addEventListener("click", async () => {
    const prompt = document.getElementById("prompt").value.trim();

    if (!prompt) {
        showStatus("Enter a prompt first.");
        return;
    }

    const [width, height] = document
        .getElementById("resolution")
        .value.split("x")
        .map(Number);

    const data = {
        prompt,
        width,
        height,
        seed: Number(document.getElementById("seed").value),
        steps: Number(document.getElementById("steps").value),
        fps: Number(document.getElementById("fps").value)
    };

    button.disabled = true;
    button.textContent = "Generating...";
    showStatus("Starting generation...");

    // Read the script's output while the generation request runs.
    let polling = true;

    async function checkStatus() {
        try {
            const response = await fetch("/status", {
                cache: "no-store"
            });

            if (response.ok) {
                const result = await response.json();
                const lines = result.output || [];

                if (polling && result.state === "running" && lines.length) {
                    showStatus(lines[lines.length - 1]);
                }
            }
        } catch (error) {
            console.log("Could not check status:", error);
        } finally {
            if (polling) {
                timer = setTimeout(checkStatus, 2000);
            }
        }
    }

    let timer = setTimeout(checkStatus, 2000);

    try {
        const response = await fetch("/generate", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (!response.ok) {
            console.error(result.output);
            throw new Error(result.error || "Generation failed.");
        }

        if (!result.video_url) {
            throw new Error("The server did not return a video URL.");
        }

        video.src = result.video_url;
        video.controls = true;
        video.load();

        showStatus("Done! Your video is ready.");
    } catch (error) {
        showStatus(`Error: ${error.message}`);
    } finally {
        polling = false;
        clearTimeout(timer);
        button.disabled = false;
        button.textContent = "Generate";
    }
});