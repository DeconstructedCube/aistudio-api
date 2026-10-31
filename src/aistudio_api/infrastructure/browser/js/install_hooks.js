((() => {
    window.__AISTUDIO__ = window.__AISTUDIO__ || {};
    window.__AISTUDIO__.interceptBootstrap = true;
    window.__AISTUDIO__.capturedTemplate = null;
    if (window.__AISTUDIO__.hooked && window.__AISTUDIO__.snapKey) return 'already_hooked';

    const dms = window.default_MakerSuite;
    if (!dms) return 'no_default_MakerSuite';

    const isSnapCandidate = (fn) => {
        if (typeof fn !== 'function') return false;
        try {
            const s = fn.toString();
            return (s.includes('.snapshot(') || s.includes('snapshot({') || s.includes('.snapshot)')) &&
                   (s.includes('content') || s.includes('e9b') || s.includes('Bcc') || s.includes('yield') || s.includes('Promise'));
        } catch (e) {
            return false;
        }
    };

    // Auto-detect snapshot function via multi-variant feature matching
    let snapKey = null;
    for (const k of Object.keys(dms)) {
        try {
            if (isSnapCandidate(dms[k])) {
                snapKey = k;
                break;
            }
        } catch(e) {}
    }
    if (!snapKey) return 'no_snapshot_fn';

    // Hook snapshot function to capture real service
    if (!dms[snapKey].__api_hooked) {
        const origSnap = dms[snapKey];
        dms[snapKey] = function(...args) {
            const svc = args[0];
            if (svc && (typeof svc === 'object' || typeof svc === 'function')) {
                window.__AISTUDIO__.service = svc;
            }
            const result = origSnap.apply(this, args);
            if (result instanceof Promise) {
                return result.then(s => {
                    window.__AISTUDIO__.snapshot = s;
                    return s;
                });
            }
            window.__AISTUDIO__.snapshot = result;
            return result;
        };
        dms[snapKey].__api_hooked = true;
    }

    // Intercept prompt save requests and one-shot bootstrap GenerateContent in-page
    if (!window.__api_fetch_hooked && typeof window.fetch === 'function') {
        const origFetch = window.fetch;
        window.fetch = function(...args) {
            try {
                const req = args[0];
                const init = args[1] || {};
                const url = typeof req === 'string' ? req : (req && req.url ? req.url : '');
                if (url.includes('SavePrompt') || url.includes('CreatePrompt')) {
                    return (async () => new Response(JSON.stringify([]), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json+protobuf' }
                    }))();
                }
                if (
                    window.__AISTUDIO__ &&
                    window.__AISTUDIO__.interceptBootstrap &&
                    url.includes('GenerateContent') &&
                    !url.includes('Count')
                ) {
                    const rawBody = init.body !== undefined ? init.body : '';
                    const bodyStr = typeof rawBody === 'string' ? rawBody : '';
                    if (bodyStr) {
                        const headersObj = { 'Referer': 'https://aistudio.google.com/' };
                        const rawHeaders = init.headers || (req && req.headers);
                        if (rawHeaders) {
                            if (typeof rawHeaders.forEach === 'function') {
                                rawHeaders.forEach((v, k) => { headersObj[String(k)] = String(v); });
                            } else if (Array.isArray(rawHeaders)) {
                                for (const pair of rawHeaders) {
                                    if (pair && pair.length >= 2) {
                                        headersObj[String(pair[0])] = String(pair[1]);
                                    }
                                }
                            } else if (typeof rawHeaders === 'object') {
                                for (const k of Object.keys(rawHeaders)) {
                                    headersObj[String(k)] = String(rawHeaders[k]);
                                }
                            }
                        }
                        window.__AISTUDIO__.capturedTemplate = {
                            url: url,
                            headers: headersObj,
                            body: bodyStr
                        };
                        window.__AISTUDIO__.interceptBootstrap = false;
                        return Promise.reject(new DOMException('Bootstrap GenerateContent intercepted', 'AbortError'));
                    }
                }
            } catch(e) {}
            return origFetch.apply(this, args);
        };
        window.__api_fetch_hooked = true;
    }

    if (!window.__api_xhr_hooked && typeof window.XMLHttpRequest === 'function') {
        const proto = window.XMLHttpRequest.prototype;
        const origOpen = proto.open;
        const origSetHeader = proto.setRequestHeader;
        const origSend = proto.send;

        proto.open = function(method, url, ...rest) {
            try {
                this.__aistudio_method = String(method || 'GET');
                this.__aistudio_url = String(url || '');
                this.__aistudio_headers = { 'Referer': 'https://aistudio.google.com/' };
            } catch(e) {}
            return origOpen.call(this, method, url, ...rest);
        };

        proto.setRequestHeader = function(name, value) {
            try {
                if (!this.__aistudio_headers) {
                    this.__aistudio_headers = { 'Referer': 'https://aistudio.google.com/' };
                }
                this.__aistudio_headers[String(name)] = String(value);
            } catch(e) {}
            return origSetHeader.call(this, name, value);
        };

        proto.send = function(body) {
            try {
                const url = this.__aistudio_url || '';
                if (url.includes('SavePrompt') || url.includes('CreatePrompt')) {
                    setTimeout(() => { try { this.abort(); } catch(e) {} }, 0);
                    return;
                }
                if (
                    window.__AISTUDIO__ &&
                    window.__AISTUDIO__.interceptBootstrap &&
                    url.includes('GenerateContent') &&
                    !url.includes('Count')
                ) {
                    const bodyStr = typeof body === 'string' ? body : '';
                    if (bodyStr) {
                        window.__AISTUDIO__.capturedTemplate = {
                            url: url,
                            headers: Object.assign({}, this.__aistudio_headers || {}),
                            body: bodyStr
                        };
                        window.__AISTUDIO__.interceptBootstrap = false;
                        setTimeout(() => { try { this.abort(); } catch(e) {} }, 0);
                        return;
                    }
                }
            } catch(e) {}
            return origSend.call(this, body);
        };
        window.__api_xhr_hooked = true;
    }
    window.__AISTUDIO__.hooked = true;
    window.__AISTUDIO__.snapKey = snapKey;
    return 'hooked:' + snapKey;
})())
