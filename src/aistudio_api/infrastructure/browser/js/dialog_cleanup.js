(async () => {
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    // 1. 自动接受 Google Cookie 提示栏
    const cookieSelectors = [
        '.glue-cookie-notification-bar__accept',
        'button[aria-label*="cookie" i]',
        'button[aria-label*="Cookie" i]',
        'button[data-glue-cookie-notification-bar-accept]',
    ];
    for (const sel of cookieSelectors) {
        const btn = document.querySelector(sel);
        if (btn) {
            try { btn.click(); } catch (e) {}
        }
    }

    // 2. 勾选所有未选中的条款/协议复选框 (支持原生 input、Angular Material mat-checkbox 及 MDC checkbox)
    let checkboxClicked = false;
    const uncheckedCheckboxes = document.querySelectorAll(
        'mat-checkbox:not(.mat-mdc-checkbox-checked):not(.mat-checkbox-checked), [role="checkbox"][aria-checked="false"], input[type="checkbox"]:not(:checked)'
    );
    uncheckedCheckboxes.forEach((cb) => {
        try {
            // 优先点击内层可交互元素或原生 input，以便触发 Angular 变更检测
            const target = cb.querySelector('input, label, .mdc-checkbox') || cb;
            target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            if (target !== cb) {
                cb.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            }
            const input = cb.tagName === 'INPUT' ? cb : cb.querySelector('input');
            if (input && !input.checked) {
                input.checked = true;
                input.dispatchEvent(new Event('change', { bubbles: true }));
                input.dispatchEvent(new Event('input', { bubbles: true }));
            }
            checkboxClicked = true;
        } catch (e) {}
    });

    // 若勾选了协议框，等待 150ms 让 Angular 表单模型更新并解除按钮的 disabled 状态
    if (checkboxClicked) {
        await sleep(150);
    }

    // 3. 点击协议/欢迎/开始使用按钮 (匹配中英文常见主操作按钮)
    const confirmKeywords = [
        '同意', '我同意', '继续', '确认', '开始', '开始构建', '开始使用', '接受',
        'agree', 'accept', 'i agree', 'continue', 'confirm', 'get started',
        'start building', 'code and chat', 'got it', 'ok', 'dismiss', 'close'
    ];

    const clickables = Array.from(document.querySelectorAll('mat-dialog-actions button, .mat-mdc-dialog-actions button, button, mat-card, a, [role="button"]'));
    for (const el of clickables) {
        // 跳过明显被禁用的非活动按钮
        if (el.hasAttribute('disabled') && el.getAttribute('disabled') !== 'false') {
            continue;
        }
        if (el.getAttribute('aria-disabled') === 'true') {
            continue;
        }
        const text = (el.textContent || el.innerText || '').trim().toLowerCase();
        if (confirmKeywords.some((kw) => text === kw || text.includes(kw))) {
            try {
                el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
                el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true }));
                el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                el.click();
                await sleep(100);
            } catch (e) {}
        }
    }

    // 4. 对无复选框的欢迎弹窗 (如 ms-g1-welcome-dialog) 确保触发关闭/继续
    const g1Dialog = document.querySelector('ms-g1-welcome-dialog, [id="g1-welcome-dialog"]');
    if (g1Dialog) {
        const btn = g1Dialog.querySelector('button');
        if (btn) {
            try { btn.click(); } catch (e) {}
        }
    }
})()
