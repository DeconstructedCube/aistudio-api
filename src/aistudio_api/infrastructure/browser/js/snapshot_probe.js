async ({ hash, timeoutMs }) => {
    const dms = window.default_MakerSuite;
    const aistudio = window.__AISTUDIO__ || {};
    const service = aistudio.service;
    const snapKey = aistudio.snapKey;
    if (!dms || !service || !snapKey || typeof dms[snapKey] !== 'function') {
        return { ok: false, reason: 'service_unavailable' };
    }
    const budget = Number(timeoutMs) > 0 ? Number(timeoutMs) : 12000;
    let timer;
    try {
        const proof = await Promise.race([
            Promise.resolve(dms[snapKey](service, hash)),
            new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error('proof_timeout')), budget);
            }),
        ]);
        if (typeof proof === 'string' && proof.length > 0) {
            return { ok: true, len: proof.length };
        }
        return { ok: false, reason: 'empty_snapshot' };
    } catch (err) {
        return { ok: false, reason: String((err && err.message) || err) };
    } finally {
        clearTimeout(timer);
    }
}
