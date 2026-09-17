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
    if (existing && existing.xhr && existing.xhr.readyState !== 4) {
        try { existing.xhr.abort(); } catch (e) {}
    }

    function cleanup() {
        try {
            if (window.__streams) delete window.__streams[rid];
            if (window.__stream_next) delete window.__stream_next[rid];
            if (window.__stream_abort) delete window.__stream_abort[rid];
        } catch (e) {}
    }

    const state = {
        xhr: null,
        events: [],
        waiter: null,
        recvPos: 0,
        statusSent: false,
        cleaned: false,
    };
    window.__streams[rid] = state;

    function push(event) {
        if (state.waiter) {
            const waiter = state.waiter;
            state.waiter = null;
            waiter(event);
            return;
        }
        state.events.push(event);
    }

    function pushStatus(xhr) {
        if (state.statusSent || xhr.readyState < 2) return;
        state.statusSent = true;
        push({type: 'status', status: xhr.status || 0});
    }

    function pushChunk(xhr) {
        if (xhr.readyState < 3) return;
        const chunk = xhr.responseText.substring(state.recvPos);
        if (!chunk) return;
        state.recvPos = xhr.responseText.length;
        push({type: 'chunk', text: chunk});
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
    window.__stream_abort[rid] = function() {
        if (state.xhr && state.xhr.readyState !== 4) {
            try { state.xhr.abort(); } catch (e) {}
        }
        cleanup();
    };

    var xhr = new XMLHttpRequest();
    xhr.open('POST', args.url);
    var h = args.headers || {};
    for (var k in h) {
        if (k.toLowerCase() === 'authorization') continue;
        xhr.setRequestHeader(k, h[k]);
    }
    xhr.withCredentials = true;
    xhr.timeout = args.timeout * 1000;

    xhr.onreadystatechange = function() {
        pushStatus(xhr);
        pushChunk(xhr);
    };
    xhr.onprogress = function() {
        pushStatus(xhr);
        pushChunk(xhr);
    };
    xhr.onload = function() {
        pushStatus(xhr);
        pushChunk(xhr);
        push({type: 'done'});
    };
    xhr.onerror = function() {
        push({type: 'error', message: 'network error'});
    };
    xhr.ontimeout = function() {
        push({type: 'error', message: 'timeout'});
    };
    xhr.onabort = function() {
        push({type: 'aborted'});
    };

    state.xhr = xhr;
    function getCookie(name) {
        var m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
        return m ? decodeURIComponent(m[1]) : '';
    }
    var sapisid = getCookie('SAPISID') || getCookie('__Secure-1PAPISID') || getCookie('__Secure-3PAPISID');
    if (sapisid && window.crypto && window.crypto.subtle) {
        var sapisid1p = getCookie('__Secure-1PAPISID') || sapisid;
        var sapisid3p = getCookie('__Secure-3PAPISID') || sapisid;
        var ts = Math.floor(Date.now() / 1000);
        var origin = 'https://aistudio.google.com';
        function sha1(str) {
            return crypto.subtle.digest('SHA-1', new TextEncoder().encode(str)).then(function(buf) {
                return Array.from(new Uint8Array(buf)).map(function(b) { return b.toString(16).padStart(2, '0'); }).join('');
            });
        }
        Promise.all([
            sha1(ts + ' ' + sapisid + ' ' + origin),
            sha1(ts + ' ' + sapisid1p + ' ' + origin),
            sha1(ts + ' ' + sapisid3p + ' ' + origin)
        ]).then(function(res) {
            var auth = 'SAPISIDHASH ' + ts + '_' + res[0] + ' SAPISID1PHASH ' + ts + '_' + res[1] + ' SAPISID3PHASH ' + ts + '_' + res[2];
            xhr.setRequestHeader('Authorization', auth);
            xhr.send(args.body);
        }).catch(function() {
            var fallbackAuth = h['Authorization'] || h['authorization'];
            if (fallbackAuth) xhr.setRequestHeader('Authorization', fallbackAuth);
            xhr.send(args.body);
        });
    } else {
        var fallbackAuth = h['Authorization'] || h['authorization'];
        if (fallbackAuth) xhr.setRequestHeader('Authorization', fallbackAuth);
        xhr.send(args.body);
    }
}"""

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
        var xhr = new XMLHttpRequest();
        xhr.open('POST', args.url);
        var h = args.headers || {};
        for (var k in h) {
            if (k.toLowerCase() === 'authorization') continue;
            xhr.setRequestHeader(k, h[k]);
        }
        xhr.withCredentials = true;
        xhr.timeout = args.timeout * 1000;
        xhr.onload = function() {
            resolve({status: xhr.status, body: xhr.responseText});
        };
        xhr.onerror = function() {
            resolve({status: 0, body: 'network error'});
        };
        xhr.ontimeout = function() {
            resolve({status: 0, body: 'timeout'});
        };
        function getCookie(name) {
            var m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
            return m ? decodeURIComponent(m[1]) : '';
        }
        var sapisid = getCookie('SAPISID') || getCookie('__Secure-1PAPISID') || getCookie('__Secure-3PAPISID');
        if (sapisid && window.crypto && window.crypto.subtle) {
            var sapisid1p = getCookie('__Secure-1PAPISID') || sapisid;
            var sapisid3p = getCookie('__Secure-3PAPISID') || sapisid;
            var ts = Math.floor(Date.now() / 1000);
            var origin = 'https://aistudio.google.com';
            function sha1(str) {
                return crypto.subtle.digest('SHA-1', new TextEncoder().encode(str)).then(function(buf) {
                    return Array.from(new Uint8Array(buf)).map(function(b) { return b.toString(16).padStart(2, '0'); }).join('');
                });
            }
            Promise.all([
                sha1(ts + ' ' + sapisid + ' ' + origin),
                sha1(ts + ' ' + sapisid1p + ' ' + origin),
                sha1(ts + ' ' + sapisid3p + ' ' + origin)
            ]).then(function(res) {
                var auth = 'SAPISIDHASH ' + ts + '_' + res[0] + ' SAPISID1PHASH ' + ts + '_' + res[1] + ' SAPISID3PHASH ' + ts + '_' + res[2];
                xhr.setRequestHeader('Authorization', auth);
                xhr.send(args.body);
            }).catch(function() {
                var fallbackAuth = h['Authorization'] || h['authorization'];
                if (fallbackAuth) xhr.setRequestHeader('Authorization', fallbackAuth);
                xhr.send(args.body);
            });
        } else {
            var fallbackAuth = h['Authorization'] || h['authorization'];
            if (fallbackAuth) xhr.setRequestHeader('Authorization', fallbackAuth);
            xhr.send(args.body);
        }
    });
}"""

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
