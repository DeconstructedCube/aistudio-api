async (hash) => {
    const dms = window.default_MakerSuite;
    const service = window.__bg_service;
    const snapKey = window.__snap_key;
    if (!dms || !service || !snapKey || typeof dms[snapKey] !== 'function') {
        throw new Error('service_unavailable');
    }
    const run = async () => {
        const result = dms[snapKey](service, hash);
        const snapshot = await Promise.resolve(result);
        if (!snapshot || typeof snapshot !== 'string') {
            throw new Error('empty_snapshot');
        }
        return snapshot;
    };
    const prev = window.__bg_snap_queue || Promise.resolve();
    const current = prev.catch(() => {}).then(run);
    window.__bg_snap_queue = current;
    return await current;
}
