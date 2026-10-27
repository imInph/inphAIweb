export const state = {
    view: "chat",
    chats: [],
    folders: [],
    archivedCount: 0,
    showArchived: false,
    searchResults: null, // results while the sidebar search has text
    jumpTo: null,        // a message to show when the next chat opens (from search)
    chat: null,          // the open chat with its messages, or null for a new one
    draft: { system_prompt: "", options: {} },
    models: [],
    live: null,          // the answer(s) streaming in now: { replyTo, streams: [...] }
    compare: null,       // finished side-by-side answers, kept until one is picked
    editing: null,       // id of the message being edited in place
    jobs: [],            // this user's queued, running and just-finished GPU jobs
    busy: null,          // the kind of someone else's job holding the GPU
    startErrors: { image: null, video: null },
    me: null,            // GET /api/me: the user, their prefs, public server settings
    jobTimer: null,
};
