(args) => {
    return new Promise((resolve) => {
        const headers = Object.assign({}, args.headers || {});

        window.fetch(args.url, {
            method: 'POST',
            headers: headers,
            body: args.body,
            credentials: 'include',
        }).then(async (response) => {
            try {
                const text = await response.text();
                resolve({status: response.status, body: text});
            } catch (e) {
                const errMsg = (e && e.message) ? `response body read error: ${e.message}` : 'response body read error';
                resolve({status: response.status || 0, body: errMsg});
            }
        }).catch((err) => {
            const msg = (err && err.message) || 'network error';
            resolve({status: 0, body: msg});
        });
    });
}
