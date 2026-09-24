(() => {

    document.querySelectorAll('ms-chat-session, ms-chat-turn, ms-chat-turn-options, ms-chat-loading-indicator, ms-prompt-chunk, ms-chunk, ms-response-chunk, .chat-turn, .history-container').forEach(el => el.remove());
    document.querySelectorAll('.cdk-overlay-backdrop, .cdk-overlay-container, mat-menu, ms-updates, ms-nav-popover').forEach(el => el.remove());
    document.querySelectorAll('canvas, video, audio').forEach(el => el.remove());

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
