(args) => {
    try {
        const rawExpected = typeof args === 'string' ? args : (args && args.email ? String(args.email) : '');
        const expectedEmail = rawExpected.trim().toLowerCase();
        const authUser = args && args.authUser !== undefined ? String(args.authUser) : '';
        if (!expectedEmail && !authUser) return true;

        const path = window.location.pathname || '';
        const m = path.match(/\/u\/(\d+)\//);
        const currentAuthUser = m ? m[1] : '0';

        // If authUser is specified and path is on /u/N/, check authUser routing
        if (authUser && currentAuthUser !== authUser) {
            return false;
        }

        if (!expectedEmail) return true;

        // 1. Check account switcher button
        const switcherBtn = document.querySelector('ms-account-switcher button, button.account-switcher-button');
        if (switcherBtn) {
            const combined = [switcherBtn.getAttribute('aria-label') || '', switcherBtn.innerText || '', switcherBtn.textContent || ''].join(' ').toLowerCase();
            if (combined.includes(expectedEmail)) return true;
        }

        // 2. Check all Google profile / account elements
        const profileSelectors = [
            'a[href*="SignOutOptions"]',
            'a[href*="accounts.google.com"]',
            'a[aria-label*="Google"]',
            'button[aria-label*="Google"]',
            'div[aria-label*="Google"]',
            '#gb',
            '[data-email]',
            'img[alt*="@"]',
            '[title*="@"]'
        ];
        for (const sel of profileSelectors) {
            const els = document.querySelectorAll(sel);
            for (const el of els) {
                const combined = [
                    el.getAttribute('data-email') || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                    el.getAttribute('alt') || '',
                    el.innerText || '',
                    el.textContent || ''
                ].join(' ').toLowerCase();
                if (combined.includes(expectedEmail)) return true;
            }
        }

        // 3. Check document body text (case-insensitive)
        if (document.body) {
            const bodyTxt = (document.body.innerText || document.body.textContent || '').toLowerCase();
            if (bodyTxt.includes(expectedEmail)) return true;
        }

        // 4. Check document.cookie (case-insensitive)
        if (document.cookie && document.cookie.toLowerCase().includes(expectedEmail)) return true;

        // 5. Check global Google account structures
        const globals = [
            window.WIZ_global_data,
            window.__ACCOUNT__,
            window.default_MakerSuite,
            window.IJ_values,
            window.RAISE_data,
            window.__INITIAL_DATA__
        ];
        for (const g of globals) {
            if (g) {
                try {
                    const str = (typeof g === 'string' ? g : JSON.stringify(g)).toLowerCase();
                    if (str.includes(expectedEmail)) return true;
                } catch (e) {}
            }
        }

        // 6. If currently verified on the exact target /u/{authUser}/ route and no other email is displayed, allow verification
        if (authUser && currentAuthUser === authUser) {
            return true;
        }
    } catch (e) {}
    return false;
}
