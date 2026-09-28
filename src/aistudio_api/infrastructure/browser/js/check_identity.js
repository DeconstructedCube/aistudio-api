(args) => {
    try {
        const expectedEmail = typeof args === 'string' ? args : (args && args.email ? String(args.email) : '');
        const authUser = args && args.authUser !== undefined ? String(args.authUser) : '';
        if (!expectedEmail && !authUser) return true;

        if (authUser) {
            const path = window.location.pathname || '';
            const m = path.match(/\/u\/(\d+)\//);
            const currentAuthUser = m ? m[1] : '0';
            if (currentAuthUser !== authUser) {
                return false;
            }
        }

        if (!expectedEmail) return true;

        const switcherBtn = document.querySelector('ms-account-switcher button, button.account-switcher-button');
        if (switcherBtn) {
            const label = switcherBtn.getAttribute('aria-label') || '';
            const txt = switcherBtn.innerText || '';
            if (label.includes(expectedEmail) || txt.includes(expectedEmail)) return true;
        }

        if (document.body && document.body.innerText && document.body.innerText.includes(expectedEmail)) return true;
        const matched = document.querySelector(`[aria-label*="${expectedEmail}"], [title*="${expectedEmail}"], img[alt*="${expectedEmail}"]`);
        if (matched) return true;
        if (document.cookie && document.cookie.includes(expectedEmail)) return true;
        const globals = [window.WIZ_global_data, window.__ACCOUNT__, window.default_MakerSuite];
        for (const g of globals) {
            if (g && JSON.stringify(g).includes(expectedEmail)) return true;
        }
    } catch (e) {}
    return false;
}
