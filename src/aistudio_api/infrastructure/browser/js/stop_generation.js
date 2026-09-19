(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const stopBtn = buttons.find(b => {
        const t = (b.innerText || b.textContent || '').trim();
        return t === 'Stop' || t.startsWith('Stop') || b.classList.contains('stop-button');
    });
    if (stopBtn) {
        try { stopBtn.click(); return true; } catch(e) {}
    }
    return false;
})()
