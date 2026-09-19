(rid) => {
    try {
        if (window.__stream_abort && window.__stream_abort[rid]) {
            window.__stream_abort[rid]();
        }
    } catch (e) {}
    try {
        if (window.__streams) delete window.__streams[rid];
        if (window.__stream_abort) delete window.__stream_abort[rid];
    } catch (e) {}
}
