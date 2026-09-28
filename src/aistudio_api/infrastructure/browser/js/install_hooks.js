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

    window.__bg_hooked = true;
    window.__snap_key = snapKey;
    return 'hooked:' + snapKey;
})())
