const $ = id => document.getElementById(id);
let setup = false;

async function post(path, body) {
    const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "inphai" },
        body: JSON.stringify(body),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
    return result;
}

function showError(message) {
    $("loginError").hidden = !message;
    $("loginError").textContent = message || "";
}

async function start() {
    const status = await fetch("/api/auth/status").then(r => r.json());
    if (status.user) {
        location.href = "/";
        return;
    }
    setup = status.needs_setup;
    if (setup) {
        document.title = "Set up · inphAI";
        $("loginTitle").textContent = "Create the admin account";
        $("loginNote").textContent = "This is the first account, so it can add other people later. "
            + "Anything already saved on this server becomes yours.";
        $("loginButton").textContent = "Create account";
        $("password").autocomplete = "new-password";
        $("confirmRow").hidden = false;
    }
    $("username").focus();
}

$("loginForm").addEventListener("submit", async event => {
    event.preventDefault();
    const username = $("username").value.trim();
    const password = $("password").value;
    if (!username || !password) return showError("Enter a username and password.");
    if (setup && password !== $("confirm").value) return showError("The two passwords don't match.");
    showError("");
    $("loginButton").disabled = true;
    try {
        await post(setup ? "/api/auth/setup" : "/api/auth/login", { username, password });
        location.href = "/";
    } catch (error) {
        showError(error.message);
        $("loginButton").disabled = false;
        $("password").select();
    }
});

start();
