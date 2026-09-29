#!/usr/bin/env python3
"""Google AI Studio (AIS) 实时运行时内省脚本 (Attach 模式)。

直接连接至正在运行的 Chrome 实例 (默认 9222 端口)，进行无侵入的深入反射内省：
1. 探查 window.default_MakerSuite 中的全部导出方法、类与混淆键。
2. 扫描并测试 .snapshot() 签名函数原型与参数结构。
3. 探查 Angular 根注入器与 _.Vv (BotGuardService) 单例在 DI 容器中的位置。
4. 输出格式化的 JSON 审计报告，便于快速分析与定位。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aistudio_api.infrastructure.browser.cdp_client import CDPClient  # noqa: E402


async def inspect_live(port: int = 9222) -> None:
    print(f"[*] 正在连接正在运行的 Chrome CDP 端口 (127.0.0.1:{port})...")
    client = CDPClient(port=port)
    if not await client.is_endpoint_alive():
        print(f"[!] 无法连接到 127.0.0.1:{port}，请确保该端口已有浏览器在运行。")
        print(
            "    提示：可先启动主服务 `uv run python main.py server` 或运行 `uv run python tools/reverse/reverse_headless.py`。"
        )
        return

    page = await client.connect_page(block_assets=False)
    print(f"[+] 成功连接至页面: {page.url or '未知页面'}")

    inspect_script = """
    (() => {
        const out = {
            url: window.location.href,
            title: document.title,
            hasMakerSuite: !!window.default_MakerSuite,
            hookStatus: {
                hasBgService: !!window.__bg_service,
                hasSnapKey: !!window.__snap_key,
                bgServiceConstructor: window.__bg_service ? window.__bg_service.constructor?.name : null
            },
            makerSuiteSummary: {
                totalKeys: 0,
                functionCount: 0,
                objectCount: 0,
                snapshotCandidates: []
            },
            angularInfo: {
                hasNgGlobal: typeof window.ng !== 'undefined',
                appRootElement: null,
                injectorFound: false
            }
        };

        // 1. 分析 default_MakerSuite
        if (window.default_MakerSuite) {
            const dms = window.default_MakerSuite;
            const keys = Object.keys(dms);
            out.makerSuiteSummary.totalKeys = keys.length;

            for (const k of keys) {
                try {
                    const item = dms[k];
                    const t = typeof item;
                    if (t === 'function') {
                        out.makerSuiteSummary.functionCount++;
                        const src = item.toString();
                        if (src.includes('snapshot') || src.includes('e9b') || src.includes('yield')) {
                            out.makerSuiteSummary.snapshotCandidates.push({
                                key: k,
                                length: item.length,
                                name: item.name,
                                preview: src.substring(0, 160).replace(/\\s+/g, ' ')
                            });
                        }
                    } else if (t === 'object' && item !== null) {
                        out.makerSuiteSummary.objectCount++;
                    }
                } catch(e) {}
            }
        }

        // 2. 分析 Angular 根节点与注入器
        try {
            const root = document.querySelector('app-root') || document.querySelector('ms-app-root');
            if (root) {
                out.angularInfo.appRootElement = root.tagName;
                if (window.ng && typeof window.ng.getInjector === 'function') {
                    const injector = window.ng.getInjector(root);
                    if (injector) {
                        out.angularInfo.injectorFound = true;
                    }
                }
            }
        } catch(e) {}

        return out;
    })()
    """

    print("[*] 正在执行运行时内省反射...")
    result = await page.evaluate(inspect_script)
    print("\n" + "=" * 60)
    print("  Google AI Studio 运行时内省结果")
    print("=" * 60)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="AIS 运行时内省脚本 (Attach 模式)")
    parser.add_argument("--port", type=int, default=9222, help="CDP 端口 (默认 9222)")
    args = parser.parse_args()
    asyncio.run(inspect_live(port=args.port))


if __name__ == "__main__":
    main()
