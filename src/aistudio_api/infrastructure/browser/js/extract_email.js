(() => {
    try {
        const EMAIL_RE = /[\w.+-]+@[\w-]+\.[a-zA-Z0-9.-]+/;

        // 1. Check all elements with aria-label containing @ (Google account buttons, avatars)
        const ariaEls = document.querySelectorAll('[aria-label*="@"]');
        for (const el of ariaEls) {
            const label = el.getAttribute("aria-label") || "";
            const m = label.match(EMAIL_RE);
            if (m) return m[0].trim().toLowerCase();
        }

        // 2. Check Google Account profile button / OneGoogleBar elements
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
                    el.getAttribute("data-email") || "",
                    el.getAttribute("aria-label") || "",
                    el.getAttribute("title") || "",
                    el.getAttribute("alt") || "",
                    el.innerText || "",
                    el.textContent || ""
                ].join(" ");
                const m = combined.match(EMAIL_RE);
                if (m) return m[0].trim().toLowerCase();
            }
        }

        // 3. MakerSuite / AI Studio account switcher text
        const switcherSelectors = [
            "ms-account-switcher .account-switcher-text",
            ".account-switcher-button .account-switcher-text",
            "ms-account-switcher button",
            "button.account-switcher-button",
            "ms-account-switcher"
        ];
        for (const sel of switcherSelectors) {
            const el = document.querySelector(sel);
            if (el) {
                const txt = [el.getAttribute("aria-label") || "", el.innerText || "", el.textContent || ""].join(" ");
                const m = txt.match(EMAIL_RE);
                if (m) return m[0].trim().toLowerCase();
            }
        }

        // 4. Check global Google account data structures
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
                    const str = typeof g === "string" ? g : JSON.stringify(g);
                    const m = str.match(EMAIL_RE);
                    if (m) return m[0].trim().toLowerCase();
                } catch (e) {}
            }
        }
    } catch (e) {}
    return null;
})()
