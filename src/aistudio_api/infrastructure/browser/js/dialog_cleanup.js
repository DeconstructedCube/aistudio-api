(async () => {
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    // 1. 自动接受 Google Cookie 提示栏（若存在）
    const cookieBtn = document.querySelector(
        '.glue-cookie-notification-bar__accept, button[data-glue-cookie-notification-bar-accept], button[aria-label*="cookie" i]'
    );
    if (cookieBtn) {
        try { cookieBtn.click(); } catch (e) {}
    }

    // 2. 检查当前 DOM 中是否存在活动的协议/弹窗容器；若无弹窗，立即退出（0 耗时，绝不在页面全局乱点按钮）
    const dialogContainers = Array.from(document.querySelectorAll(
        'mat-dialog-container, cdk-dialog-container, ms-g1-welcome-dialog, [role="dialog"], .cdk-overlay-pane'
    ));
    if (dialogContainers.length === 0) {
        return;
    }

    for (const dialog of dialogContainers) {
        // 2.1 勾选弹窗内的未选中条款复选框
        let checkboxChecked = false;
        const unchecked = dialog.querySelectorAll(
            'mat-checkbox:not(.mat-mdc-checkbox-checked):not(.mat-checkbox-checked), [role="checkbox"][aria-checked="false"], input[type="checkbox"]:not(:checked)'
        );
        unchecked.forEach((cb) => {
            try {
                const target = cb.querySelector('input, label, .mdc-checkbox') || cb;
                target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                const input = cb.tagName === 'INPUT' ? cb : cb.querySelector('input');
                if (input && !input.checked) {
                    input.checked = true;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                }
                checkboxChecked = true;
            } catch (e) {}
        });

        if (checkboxChecked) {
            await sleep(100);
        }

        // 2.2 点击该弹窗内部的主操作确认按钮
        const confirmKeywords = [
            '同意', '我同意', '继续', '确认', '开始', '开始构建', '开始使用', '接受',
            'agree', 'accept', 'i agree', 'continue', 'confirm', 'get started',
            'start building', 'code and chat', 'got it', 'ok', 'dismiss', 'close'
        ];

        // 仅在弹窗内部查找操作按钮
        const buttons = Array.from(dialog.querySelectorAll(
            'mat-dialog-actions button, .mat-mdc-dialog-actions button, .mdc-dialog__actions button, button'
        ));
        for (const btn of buttons) {
            if (btn.hasAttribute('disabled') && btn.getAttribute('disabled') !== 'false') {
                continue;
            }
            if (btn.getAttribute('aria-disabled') === 'true') {
                continue;
            }
            const text = (btn.textContent || btn.innerText || '').trim().toLowerCase();
            if (confirmKeywords.some((kw) => text === kw || text.includes(kw))) {
                try {
                    btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
                    btn.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true }));
                    btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    btn.click();
                    break; // 成功点击主按钮后即结束该弹窗处理
                } catch (e) {}
            }
        }
    }
})()
