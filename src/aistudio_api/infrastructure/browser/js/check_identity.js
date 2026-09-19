(expectedEmail) => {
    try {
        if (!expectedEmail) return true;
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
