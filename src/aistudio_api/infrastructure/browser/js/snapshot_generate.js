async ({ prompt, hash }) => {
    const dms = window.default_MakerSuite;
    const aistudio = window.__AISTUDIO__ || (window.__AISTUDIO__ = {});
    const service = aistudio.service;
    const snapKey = aistudio.snapKey;
    if (!dms || !service || !snapKey || typeof dms[snapKey] !== 'function') {
        throw new Error('service_unavailable');
    }

    const isShown = (el) => {
        try { return el.checkVisibility({ visibilityProperty: true }); } catch (e) { return true; }
    };
    const promptBox = () => {
        const boxes = Array.from(document.querySelectorAll('ms-prompt-box textarea')).filter(isShown);
        if (boxes.length) return boxes[0];
        const any = Array.from(document.querySelectorAll('textarea')).filter(isShown);
        return any[0] || null;
    };

    const box = promptBox();
    const desc = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
    const setValue = (desc && desc.set) ? desc.set : function(val) { this.value = val; };

    if (box && typeof prompt === 'string' && prompt.length > 0) {
        setValue.call(box, prompt);
        box.dispatchEvent(new window.InputEvent('input', { bubbles: true, inputType: 'insertText', data: prompt }));
        box.dispatchEvent(new window.Event('change', { bubbles: true }));
    }

    const run = async () => {
        const result = dms[snapKey](service, hash);
        const snapshot = await Promise.resolve(result);
        if (!snapshot || typeof snapshot !== 'string') {
            throw new Error('empty_snapshot');
        }
        aistudio.snapshot = snapshot;
        return snapshot;
    };

    try {
        const prev = aistudio.snapQueue || Promise.resolve();
        const current = prev.catch(() => {}).then(run);
        aistudio.snapQueue = current;
        return await current;
    } finally {
        if (box) {
            setValue.call(box, '');
            box.dispatchEvent(new window.InputEvent('input', { bubbles: true, inputType: 'deleteContent' }));
            box.dispatchEvent(new window.Event('change', { bubbles: true }));
        }
    }
}
