(rid) => {
    try {
        const abortFn = window.__AISTUDIO__?.streamAbort?.[rid];
        if (typeof abortFn === 'function') {
            abortFn();
        }
    } catch (e) {}
    try {
        if (window.__AISTUDIO__?.streams) delete window.__AISTUDIO__.streams[rid];
        if (window.__AISTUDIO__?.streamAbort) delete window.__AISTUDIO__.streamAbort[rid];
    } catch (e) {}
}
