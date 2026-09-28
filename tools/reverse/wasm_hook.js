// tools/reverse/wasm_hook.js
// 浏览器端 WebAssembly 拦截与 Dump Hook
(function() {
    window.__CAPTURED_WASM__ = window.__CAPTURED_WASM__ || [];

    function recordWasm(bytes, source) {
        try {
            let u8 = null;
            if (bytes instanceof ArrayBuffer) {
                u8 = new Uint8Array(bytes);
            } else if (ArrayBuffer.isView(bytes)) {
                u8 = new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
            }
            if (u8 && u8.byteLength > 0) {
                let binary = '';
                const len = u8.byteLength;
                const chunkSize = 0x8000;
                for (let i = 0; i < len; i += chunkSize) {
                    binary += String.fromCharCode.apply(null, u8.subarray(i, i + chunkSize));
                }
                const b64 = btoa(binary);
                window.__CAPTURED_WASM__.push({
                    timestamp: Date.now(),
                    source: source,
                    size: len,
                    base64: b64
                });
                console.log(`[WASM HOOK] 成功截获 WebAssembly 模块 (${len} 字节, 来源: ${source})`);
            }
        } catch (e) {
            console.error('[WASM HOOK] 提取 wasm 失败:', e);
        }
    }

    if (window.WebAssembly) {
        const origInstantiate = WebAssembly.instantiate;
        WebAssembly.instantiate = async function(bufferSource, importObject) {
            recordWasm(bufferSource, 'WebAssembly.instantiate');
            return origInstantiate.apply(this, arguments);
        };

        const origCompile = WebAssembly.compile;
        WebAssembly.compile = async function(bufferSource) {
            recordWasm(bufferSource, 'WebAssembly.compile');
            return origCompile.apply(this, arguments);
        };

        if (WebAssembly.instantiateStreaming) {
            const origInstantiateStreaming = WebAssembly.instantiateStreaming;
            WebAssembly.instantiateStreaming = async function(source, importObject) {
                try {
                    const cloned = await source.clone();
                    const buf = await cloned.arrayBuffer();
                    recordWasm(buf, 'WebAssembly.instantiateStreaming');
                } catch (e) {}
                return origInstantiateStreaming.apply(this, arguments);
            };
        }
    }
})();
