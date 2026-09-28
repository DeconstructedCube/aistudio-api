async (hash) => {
    const dms = window.default_MakerSuite;
    const aistudio = window.__AISTUDIO__ || {};
    const service = aistudio.service || window.__bg_service;
    const snapKey = aistudio.snapKey || window.__snap_key;
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
    const prev = aistudio.snapQueue || window.__bg_snap_queue || Promise.resolve();
    const current = prev.catch(() => {}).then(run);
    aistudio.snapQueue = current;
    window.__bg_snap_queue = current;
    return await current;
}
