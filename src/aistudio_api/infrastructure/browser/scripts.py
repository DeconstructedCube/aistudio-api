"""Browser automation JavaScript scripts and parameterization utilities."""

from __future__ import annotations

INSTALL_HOOKS_JS = r"""
((() => {
    if (window.__bg_hooked && window.__snap_key) return 'already_hooked';

    const dms = window.default_MakerSuite;
    if (!dms) return 'no_default_MakerSuite';

    // Auto-detect snapshot function via feature matching
    let snapKey = null;
    for (const k of Object.keys(dms)) {
        try {
            if (typeof dms[k] !== 'function') continue;
            const src = dms[k].toString();
            if (src.includes('.snapshot({') && src.includes('content') && src.includes('yield')) {
                snapKey = k;
                break;
            }
        } catch(e) {}
    }
    if (!snapKey) return 'no_snapshot_fn';

    // Hook snapshot function to capture service
    if (!dms[snapKey].__api_hooked) {
        const origSnap = dms[snapKey];
        dms[snapKey] = function(...args) {
            window.__bg_service = args[0];
            const result = origSnap.apply(this, args);
            if (result instanceof Promise) return result.then(s => { window.__bg_snapshot = s; return s; });
            window.__bg_snapshot = result;
            return result;
        };
        dms[snapKey].__api_hooked = true;
    }

    window.__bg_hooked = true;
    window.__snap_key = snapKey;
    return 'hooked:' + snapKey;
})())
"""

DIALOG_CLEANUP_JS = """(() => {
    const cookieBtn = document.querySelector('.glue-cookie-notification-bar__accept');
    if (cookieBtn) { try { cookieBtn.click(); } catch(e) {} }
    document.querySelectorAll('button, mat-card, a, [role="button"]').forEach((el) => {
        const text = (el.textContent || el.innerText || '').trim().toLowerCase();
        if (['dismiss', 'close', 'accept', 'ok', 'agree', 'got it', 'start building', 'code and chat'].some(w => text.includes(w))) {
            try { el.click(); } catch(e) {}
        }
    });
    document.querySelectorAll('.cdk-overlay-backdrop').forEach((node) => node.remove());
    document.querySelectorAll('.cdk-overlay-container').forEach((node) => node.remove());
})()"""

STREAMING_INIT_JS = """(args) => {
    const rid = args.rid;
    if (!window.__streams) window.__streams = {};

    const existing = window.__streams[rid];
    if (existing && existing.abort) {
        try { existing.abort(); } catch (e) {}
    }

    function cleanup() {
        try {
            if (window.__streams) delete window.__streams[rid];
            if (window.__stream_next) delete window.__stream_next[rid];
            if (window.__stream_abort) delete window.__stream_abort[rid];
        } catch (e) {}
    }

    const abortController = new AbortController();
    const state = {
        reader: null,
        abortController: abortController,
        events: [],
        waiter: null,
        statusSent: false,
        cleaned: false,
    };
    window.__streams[rid] = state;

    function push(event) {
        event.rid = rid;
        if (typeof window.__aistudio_stream_push__ === 'function') {
            try {
                window.__aistudio_stream_push__(JSON.stringify(event));
            } catch (e) {}
        }
        if (state.waiter) {
            const waiter = state.waiter;
            state.waiter = null;
            waiter(event);
            return;
        }
        state.events.push(event);
    }

    function isTerminalEvent(ev) {
        if (!ev) return false;
        if (ev.type === 'done' || ev.type === 'aborted' || ev.type === 'error') return true;
        if (ev.type === 'batch' && Array.isArray(ev.events)) {
            return ev.events.some(e => e && (e.type === 'done' || e.type === 'aborted' || e.type === 'error'));
        }
        return false;
    }

    if (!window.__stream_next) window.__stream_next = {};
    window.__stream_next[rid] = function(timeoutMs) {
        if (state.events.length) {
            var batch = state.events.slice();
            state.events.length = 0;
            var res = {type: 'batch', events: batch};
            if (isTerminalEvent(res)) {
                setTimeout(cleanup, 0);
            }
            return Promise.resolve(res);
        }
        return new Promise((resolve) => {
            let done = false;
            const timer = setTimeout(() => {
                if (done) return;
                done = true;
                if (state.waiter === finish) state.waiter = null;
                resolve({type: 'idle'});
            }, timeoutMs);
            const finish = (event) => {
                if (done) return;
                done = true;
                clearTimeout(timer);
                var resultEvent;
                if (state.events.length) {
                    var batch = [event].concat(state.events);
                    state.events.length = 0;
                    resultEvent = {type: 'batch', events: batch};
                } else {
                    resultEvent = event;
                }
                if (isTerminalEvent(resultEvent)) {
                    setTimeout(cleanup, 0);
                }
                resolve(resultEvent);
            };
            state.waiter = finish;
        });
    };

    if (!window.__stream_abort) window.__stream_abort = {};
    const doAbort = function() {
        if (state.reader) {
            try { state.reader.cancel(); } catch (e) {}
        }
        if (state.abortController) {
            try { state.abortController.abort(); } catch (e) {}
        }
        cleanup();
    };
    window.__stream_abort[rid] = doAbort;
    state.abort = doAbort;

    const timeoutMs = (args.timeout || 60) * 1000;
    const timeoutId = setTimeout(() => {
        push({type: 'error', message: 'timeout'});
        doAbort();
    }, timeoutMs);

    const headers = Object.assign({}, args.headers || {});

    window.fetch(args.url, {
        method: 'POST',
        headers: headers,
        body: args.body,
        credentials: 'include',
        signal: abortController.signal,
    }).then(response => {
        state.statusSent = true;
        push({type: 'status', status: response.status || 0});

        if (!response.body) {
            clearTimeout(timeoutId);
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
                    clearTimeout(timeoutId);
                    push({type: 'done'});
                    cleanup();
                    return;
                }
                if (value) {
                    const text = decoder.decode(value, {stream: true});
                    if (text) {
                        push({type: 'chunk', text: text});
                    }
                }
                readLoop();
            }).catch(err => {
                clearTimeout(timeoutId);
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
        clearTimeout(timeoutId);
        if (err && err.name === 'AbortError') {
            push({type: 'aborted'});
        } else {
            push({type: 'error', message: (err && err.message) || 'network error'});
        }
        cleanup();
    });
};
"""

STREAM_POLL_JS = """(rid) => window.__stream_next && window.__stream_next[rid] ? window.__stream_next[rid](250) : {type: 'error', message: 'stream_session_lost'}"""

STREAM_CLEANUP_JS = """(rid) => {
    try {
        if (window.__stream_abort && window.__stream_abort[rid]) {
            window.__stream_abort[rid]();
        }
    } catch (e) {}
    try {
        if (window.__streams) delete window.__streams[rid];
        if (window.__stream_next) delete window.__stream_next[rid];
        if (window.__stream_abort) delete window.__stream_abort[rid];
    } catch (e) {}
}"""

HOOKED_REQUEST_JS = """(args) => {
    return new Promise((resolve) => {
        const timeoutMs = (args.timeout || 60) * 1000;
        const controller = new AbortController();
        const timer = setTimeout(() => {
            controller.abort();
            resolve({status: 0, body: 'timeout'});
        }, timeoutMs);

        const headers = Object.assign({}, args.headers || {});

        window.fetch(args.url, {
            method: 'POST',
            headers: headers,
            body: args.body,
            credentials: 'include',
            signal: controller.signal,
        }).then(async (response) => {
            clearTimeout(timer);
            try {
                const text = await response.text();
                resolve({status: response.status, body: text});
            } catch (e) {
                resolve({status: response.status, body: ''});
            }
        }).catch((err) => {
            clearTimeout(timer);
            const msg = (err && err.name === 'AbortError') ? 'timeout' : 'network error';
            resolve({status: 0, body: msg});
        });
    });
};
"""

SNAPSHOT_GENERATE_JS = """
async (hash) => {
    const dms = window.default_MakerSuite;
    const service = window.__bg_service;
    const snapKey = window.__snap_key;
    if (!dms || !service || !snapKey || typeof dms[snapKey] !== 'function') {
        throw new Error('service_unavailable');
    }
    const result = dms[snapKey](service, hash);
    const snapshot = await Promise.resolve(result);
    if (!snapshot || typeof snapshot !== 'string') {
        throw new Error('empty_snapshot');
    }
    return snapshot;
}
"""

CHECK_IDENTITY_JS = """
(expectedEmail) => {
    try {
        if (!expectedEmail) return true;
        if (document.body && document.body.innerText && document.body.innerText.includes(expectedEmail)) return true;
        const matched = document.querySelector(`[aria-label*="${expectedEmail}"], [title*="${expectedEmail}"], img[alt*="${expectedEmail}"]`);
        if (matched) return true;
        if (document.cookie && document.cookie.includes(expectedEmail)) return true;
        const globals = [window.WIZ_global_data, window.__ACCOUNT__, window.default_MakerSuite];
        for (const g of globals) {
            if (g && JSON.stringify(g).includes(expectedEmail)) return true;
        }
    } catch (e) {}
    return false;
}
"""


def build_streaming_init_args(
    *, url: str, headers: dict[str, str], body: str, timeout_s: float, rid: str
) -> dict[str, object]:
    """Prepare argument object for STREAMING_INIT_JS evaluation."""
    return {
        "url": url,
        "headers": headers,
        "body": body,
        "timeout": timeout_s,
        "rid": rid,
    }


def build_hooked_request_args(
    *, url: str, headers: dict[str, str], body: str, timeout_s: float
) -> dict[str, object]:
    """Prepare argument object for HOOKED_REQUEST_JS evaluation."""
    return {
        "url": url,
        "headers": headers,
        "body": body,
        "timeout": timeout_s,
    }


STOP_GENERATION_JS = """(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const stopBtn = buttons.find(b => {
        const t = (b.innerText || b.textContent || '').trim();
        return t === 'Stop' || t.startsWith('Stop') || b.classList.contains('stop-button');
    });
    if (stopBtn) {
        try { stopBtn.click(); return true; } catch(e) {}
    }
    return false;
})()"""

DOM_GC_CLEANUP_JS = """(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const stopBtn = buttons.find(b => {
        const t = (b.innerText || b.textContent || '').trim();
        return t === 'Stop' || t.startsWith('Stop') || b.classList.contains('stop-button');
    });
    if (stopBtn) { try { stopBtn.click(); } catch(e) {} }

    document.querySelectorAll('ms-chat-session, ms-chat-turn, ms-chat-turn-options, ms-chat-loading-indicator, ms-prompt-chunk, ms-chunk, ms-response-chunk, .chat-turn, .history-container').forEach(el => el.remove());
    document.querySelectorAll('.cdk-overlay-backdrop, .cdk-overlay-container, mat-menu, ms-updates, ms-nav-popover').forEach(el => el.remove());
    document.querySelectorAll('canvas, video, audio').forEach(el => el.remove());

    const ta = document.querySelector('textarea');
    if (ta) { ta.value = ''; }

    if (!document.getElementById('__aistudio_perf_style__')) {
        try {
            const style = document.createElement('style');
            style.id = '__aistudio_perf_style__';
            style.textContent = '* { animation: none !important; transition: none !important; }';
            (document.head || document.documentElement).appendChild(style);
        } catch(e) {}
    }
})()"""
