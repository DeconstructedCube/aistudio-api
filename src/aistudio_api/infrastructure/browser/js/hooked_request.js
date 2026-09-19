(args) => {
    return new Promise((resolve) => {
        const timeoutMs = (args.timeout || 60) * 1000;
        const controller = new AbortController();
        const timer = setTimeout(() => {
            controller.abort();
            resolve({status: 0, body: 'timeout'});
        }, timeoutMs);

        const headers = Object.assign({}, args.headers || {});

        window.fetch(args.url, {
            method: 'POST',
            headers: headers,
            body: args.body,
            credentials: 'include',
            signal: controller.signal,
        }).then(async (response) => {
            clearTimeout(timer);
            try {
                const text = await response.text();
                resolve({status: response.status, body: text});
            } catch (e) {
                resolve({status: response.status, body: ''});
            }
        }).catch((err) => {
            clearTimeout(timer);
            const msg = (err && err.name === 'AbortError') ? 'timeout' : 'network error';
            resolve({status: 0, body: msg});
        });
    });
}
