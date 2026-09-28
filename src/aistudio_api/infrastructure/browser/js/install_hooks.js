((() => {
    window.__AISTUDIO__ = window.__AISTUDIO__ || {};
    if (window.__AISTUDIO__.hooked && window.__AISTUDIO__.snapKey) return 'already_hooked';
    if (window.__bg_hooked && window.__snap_key) return 'already_hooked';

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

    // Hook snapshot function to capture service
    if (!dms[snapKey].__api_hooked) {
        const origSnap = dms[snapKey];
        dms[snapKey] = function(...args) {
            window.__AISTUDIO__.service = args[0];
            window.__bg_service = args[0];
            const result = origSnap.apply(this, args);
            if (result instanceof Promise) {
                return result.then(s => {
                    window.__AISTUDIO__.snapshot = s;
                    window.__bg_snapshot = s;
                    return s;
                });
            }
            window.__AISTUDIO__.snapshot = result;
            window.__bg_snapshot = result;
            return result;
        };
        dms[snapKey].__api_hooked = true;
    }

    // Intercept prompt save requests in-page to avoid polluting user account history
    if (!window.__api_fetch_hooked && typeof window.fetch === 'function') {
        const origFetch = window.fetch;
        window.fetch = function(...args) {
            try {
                const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url ? args[0].url : '');
                if (url.includes('SavePrompt') || url.includes('CreatePrompt')) {
                    return (async () => new Response(JSON.stringify([]), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json+protobuf' }
                    }))();
                }
            } catch(e) {}
            return origFetch.apply(this, args);
        };
        window.__api_fetch_hooked = true;
    }

    window.__AISTUDIO__.hooked = true;
    window.__AISTUDIO__.snapKey = snapKey;
    window.__bg_hooked = true;
    window.__snap_key = snapKey;
    return 'hooked:' + snapKey;
})())
