(() => {
    try {
        // 1. Direct account switcher text in Google AI Studio left navigation
        const switcherText = document.querySelector("ms-account-switcher .account-switcher-text, .account-switcher-button .account-switcher-text");
        if (switcherText && switcherText.innerText && switcherText.innerText.includes("@")) {
            return switcherText.innerText.trim();
        }

        // 2. Account switcher button aria-label or innerText
        const switcherBtn = document.querySelector("ms-account-switcher button, button.account-switcher-button");
        if (switcherBtn) {
            const label = switcherBtn.getAttribute("aria-label") || "";
            const m = label.match(/[\w.+-]+@[\w-]+\.[a-zA-Z0-9.-]+/);
            if (m) return m[0];
            const txt = switcherBtn.innerText || "";
            const m2 = txt.match(/[\w.+-]+@[\w-]+\.[a-zA-Z0-9.-]+/);
            if (m2) return m2[0];
        }

        // 3. Fallback to entire ms-account-switcher element text
        const switcher = document.querySelector("ms-account-switcher");
        if (switcher) {
            const m = (switcher.innerText || "").match(/[\w.+-]+@[\w-]+\.[a-zA-Z0-9.-]+/);
            if (m) return m[0];
        }

        // 4. Any element with an aria-label containing an email
        const ariaEl = document.querySelector('[aria-label*="@"]');
        if (ariaEl) {
            const m = (ariaEl.getAttribute("aria-label") || "").match(/[\w.+-]+@[\w-]+\.[a-zA-Z0-9.-]+/);
            if (m) return m[0];
        }

        // 5. Check global Google account structures if present
        if (window.WIZ_global_data) {
            const str = JSON.stringify(window.WIZ_global_data);
            const m = str.match(/[\w.+-]+@[\w-]+\.[a-zA-Z0-9.-]+/);
            if (m) return m[0];
        }
    } catch (e) {}
    return null;
})()
