(args) => {
    const rid = args.rid;
    if (!window.__streams) window.__streams = {};
    if (!window.__stream_abort) window.__stream_abort = {};

    const existing = window.__streams[rid];
    if (existing && existing.abort) {
        try { existing.abort(); } catch (e) {}
    }

    const abortController = new AbortController();
    const state = {
        reader: null,
        abortController: abortController,
    };
    window.__streams[rid] = state;

    function push(event) {
        event.rid = rid;
        if (typeof window.__aistudio_stream_push__ === 'function') {
            try {
                window.__aistudio_stream_push__(JSON.stringify(event));
            } catch (e) {}
        }
    }

    const timeoutMs = (args.timeout || 60) * 1000;
    let timeoutId = setTimeout(() => {
        push({type: 'error', message: 'timeout'});
        doAbort();
    }, timeoutMs);

    function resetTimeout() {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => {
            push({type: 'error', message: 'timeout'});
            doAbort();
        }, timeoutMs);
    }

    function cleanup() {
        if (timeoutId) {
            clearTimeout(timeoutId);
            timeoutId = null;
        }
        try {
            if (window.__streams) delete window.__streams[rid];
            if (window.__stream_abort) delete window.__stream_abort[rid];
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
    window.__stream_abort[rid] = doAbort;
    state.abort = doAbort;

    const headers = Object.assign({}, args.headers || {});

    window.fetch(args.url, {
        method: 'POST',
        headers: headers,
        body: args.body,
        credentials: 'include',
        signal: abortController.signal,
    }).then(response => {
        resetTimeout();
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
                    resetTimeout();
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
