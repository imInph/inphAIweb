export const state = {
    view: "chat",
    chats: [],
    chat: null,          // the open chat with its messages, or null for a new one
    draft: { system_prompt: "", options: {} },
    models: [],
    streaming: null,     // AbortController while a reply streams
    jobs: [],            // this user's queued, running and just-finished GPU jobs
    busy: null,          // the kind of someone else's job holding the GPU
    startErrors: { image: null, video: null },
    me: null,            // GET /api/me: the user, their prefs, public server settings
    jobTimer: null,
};
