export const state = {
    view: "chat",
    chats: [],
    chat: null,          // the open chat with its messages, or null for a new one
    draft: { system_prompt: "", options: {} },
    models: [],
    streaming: null,     // AbortController while a reply streams
    job: null,
    settings: null,      // server-side settings (GET /api/settings)
    jobTimer: null,
};
