(() => {

    document.querySelectorAll('ms-chat-turn-options, ms-chat-loading-indicator').forEach(el => el.remove());
    document.querySelectorAll('.cdk-overlay-backdrop, ms-updates, ms-nav-popover').forEach(el => el.remove());
    document.querySelectorAll('canvas, video, audio').forEach(el => el.remove());
    const turns = document.querySelectorAll('ms-chat-turn');
    if (turns.length > 2) {
        for (let i = 0; i < turns.length - 1; i++) {
            try { turns[i].remove(); } catch (e) {}
        }
    }

    try {
        document.querySelectorAll('img[src^="blob:"]').forEach(img => {
            try { URL.revokeObjectURL(img.src); } catch (e) {}
            img.src = '';
            img.remove();
        });
    } catch (e) {}

    const ta = document.querySelector('textarea');
    if (ta) { ta.value = ''; }
    try {
        if (typeof window.gc === 'function') {
            window.gc();
        }
    } catch (e) {}
    if (!document.getElementById('__aistudio_perf_style__')) {
        try {
            const style = document.createElement('style');
            style.id = '__aistudio_perf_style__';
            style.textContent = '* { animation: none !important; transition: none !important; }';
            (document.head || document.documentElement).appendChild(style);
        } catch(e) {}
    }
})()
