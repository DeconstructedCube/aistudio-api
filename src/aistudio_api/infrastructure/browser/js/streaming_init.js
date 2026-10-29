(args) => {
    const rid = args.rid;
    window.__AISTUDIO__ = window.__AISTUDIO__ || {};
    const streams = window.__AISTUDIO__.streams = window.__AISTUDIO__.streams || {};
    const aborts = window.__AISTUDIO__.streamAbort = window.__AISTUDIO__.streamAbort || {};
    const existing = streams[rid];
    if (existing && existing.abort) {
        try { existing.abort(); } catch (e) {}
    }

    const timeoutMs = (args.timeout || 600) * 1000;
    const abortController = new AbortController();
    const state = {
        reader: null,
        abortController: abortController,
        idleTimer: null,
    };
    streams[rid] = state;

    function push(event) {
        event.rid = rid;
        if (typeof window.__aistudio_stream_push__ === 'function') {
            try {
                window.__aistudio_stream_push__(JSON.stringify(event));
            } catch (e) {}
        }
    }

    function clearIdleTimer() {
        if (state.idleTimer) {
            clearTimeout(state.idleTimer);
            state.idleTimer = null;
        }
    }

    function cleanup() {
        clearIdleTimer();
        try {
            if (window.__AISTUDIO__?.streams) delete window.__AISTUDIO__.streams[rid];
            if (window.__AISTUDIO__?.streamAbort) delete window.__AISTUDIO__.streamAbort[rid];
        } catch (e) {}
    }
    const doAbort = function() {
        cleanup();
        if (state.reader) {
            try { state.reader.cancel(); } catch (e) {}
        }
        if (state.abortController) {
            try { state.abortController.abort(); } catch (e) {}
        }
    };
    aborts[rid] = doAbort;
    state.abort = doAbort;

    function resetIdleTimer() {
        clearIdleTimer();
        if (timeoutMs > 0) {
            state.idleTimer = setTimeout(() => {
                push({type: 'error', message: 'timeout'});
                doAbort();
            }, timeoutMs);
        }
    }
    resetIdleTimer();
    const headers = Object.assign({}, args.headers || {});

    window.fetch(args.url, {
        method: 'POST',
        headers: headers,
        body: args.gzip ? Uint8Array.from(atob(args.body), c => c.charCodeAt(0)) : args.body,
        credentials: 'include',
        signal: abortController.signal,
    }).then(response => {
        // Status received
        resetIdleTimer();
        push({type: 'status', status: response.status || 0});

        if (!response.body) {
            push({type: 'done'});
            cleanup();
            return;
        }

        const reader = response.body.getReader();
        state.reader = reader;
        const decoder = new TextDecoder('utf-8');

        function readLoop() {
            reader.read().then(({done, value}) => {
                if (done) {
                    try {
                        const remaining = decoder.decode();
                        if (remaining) {
                            push({type: 'chunk', text: remaining});
                        }
                    } catch (e) {}
                    push({type: 'done'});
                    cleanup();
                    return;
                }
                if (value) {
                    resetIdleTimer();
                    const text = decoder.decode(value, {stream: true});
                    if (text) {
                        push({type: 'chunk', text: text});
                    }
                }
                readLoop();
            }).catch(err => {
                if (err && err.name === 'AbortError') {
                    push({type: 'aborted'});
                } else {
                    push({type: 'error', message: (err && err.message) || 'stream read error'});
                }
                cleanup();
            });
        }
        readLoop();
    }).catch(err => {
        if (err && err.name === 'AbortError') {
            push({type: 'aborted'});
        } else {
            push({type: 'error', message: (err && err.message) || 'network error'});
        }
        cleanup();
    });
}
