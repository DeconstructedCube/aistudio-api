(rid) => {
    try {
        const abortFn = (window.__AISTUDIO__ && window.__AISTUDIO__.streamAbort && window.__AISTUDIO__.streamAbort[rid]) ||
                        (window.__stream_abort && window.__stream_abort[rid]);
        if (typeof abortFn === 'function') {
            abortFn();
        }
    } catch (e) {}
    try {
        if (window.__AISTUDIO__ && window.__AISTUDIO__.streams) delete window.__AISTUDIO__.streams[rid];
        if (window.__AISTUDIO__ && window.__AISTUDIO__.streamAbort) delete window.__AISTUDIO__.streamAbort[rid];
        if (window.__streams) delete window.__streams[rid];
        if (window.__stream_abort) delete window.__stream_abort[rid];
    } catch (e) {}
}
