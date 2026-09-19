(() => {
    const cookieBtn = document.querySelector('.glue-cookie-notification-bar__accept');
    if (cookieBtn) { try { cookieBtn.click(); } catch(e) {} }
    document.querySelectorAll('button, mat-card, a, [role="button"]').forEach((el) => {
        const text = (el.textContent || el.innerText || '').trim().toLowerCase();
        if (['dismiss', 'close', 'accept', 'ok', 'agree', 'got it', 'start building', 'code and chat'].some(w => text.includes(w))) {
            try { el.click(); } catch(e) {}
        }
    });
    document.querySelectorAll('.cdk-overlay-backdrop').forEach((node) => node.remove());
    document.querySelectorAll('.cdk-overlay-container').forEach((node) => node.remove());
})()
