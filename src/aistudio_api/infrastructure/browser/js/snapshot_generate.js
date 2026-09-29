async (hash) => {
    const dms = window.default_MakerSuite;
    const aistudio = window.__AISTUDIO__ || {};
    const service = aistudio.service;
    const snapKey = aistudio.snapKey;
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
    const prev = aistudio.snapQueue || Promise.resolve();
    const current = prev.catch(() => {}).then(run);
    aistudio.snapQueue = current;
    return await current;
}
