#!/usr/bin/env python3
"""Google AI Studio (AIS) 前端 JS 与 Wasm 自动化无头逆向工具。

运行无头 CloakBrowser，自动化完成以下操作：
1. 注入 Wasm 字节流与方法拦截 Hook。
2. 监听 CDP 网络流量，抓取并保存页面加载的全部 JS Bundle、Chunk、Wasm 模块。
3. 深入探查运行时内存：window.default_MakerSuite、Angular DI 容器、混淆类。
4. 调用 bun x prettier 自动格式化解包的代码。
5. 【重要】操作完毕或异常退出时，严格在 finally 中彻底杀掉浏览器进程，杜绝内存残留。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from aistudio_api.infrastructure.browser.browser_engine import (  # noqa: E402
    find_chromium_executable,
)
from aistudio_api.infrastructure.browser.cdp_client import (  # noqa: E402
    CDPClient,
    CDPPage,
)


async def run_reverse(
    port: int = 9333,
    headless: bool = True,
    timeout_s: float = 30.0,
    out_dir: Path | None = None,
    format_code: bool = True,
    target_url: str = "https://aistudio.google.com/",
) -> None:
    out_dir = out_dir or (Path(__file__).resolve().parent / "output")
    bundles_dir = out_dir / "bundles"
    wasm_dir = out_dir / "wasm"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    wasm_dir.mkdir(parents=True, exist_ok=True)

    hook_file = Path(__file__).resolve().parent / "wasm_hook.js"
    hook_js = hook_file.read_text(encoding="utf-8") if hook_file.exists() else ""

    temp_profile_dir = tempfile.mkdtemp(prefix="ais_reverse_profile_")
    browser_proc: subprocess.Popen | None = None
    cdp_client: CDPClient | None = None
    page: CDPPage | None = None

    captured_responses: dict[str, dict[str, str]] = {}
    saved_files: list[dict[str, str | int]] = []

    print("=" * 70)
    print("  Google AI Studio 前端 JS / Wasm 自动化逆向工具")
    print(f"  目标 URL: {target_url}")
    print(f"  无头模式: {headless} | 调试端口: {port}")
    print(f"  输出目录: {out_dir}")
    print("=" * 70)

    try:
        # 1. 查找并启动 CloakBrowser
        chrome_exe = find_chromium_executable()
        print(f"\n[1/5] 启动 CloakBrowser: {chrome_exe}")

        cmd = [
            chrome_exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={temp_profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-translate",
            "--window-size=1440,900",
            "about:blank",
        ]
        if headless:
            cmd.insert(3, "--headless=new")

        browser_proc = subprocess.Popen(  # noqa: ASYNC220
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"      浏览器进程已启动 (PID: {browser_proc.pid})")

        # 2. 连接 CDP
        print(f"\n[2/5] 建立 CDP WebSocket 连接 (127.0.0.1:{port})...")
        cdp_client = CDPClient(port=port)
        page = await cdp_client.connect_page(block_assets=False)

        # 注入 Wasm Hook
        if hook_js:
            await page.cdp.send(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": hook_js},
            )
            print("      已注入 WebAssembly 拦截钩子")

        # 启用网络拦截以捕获 JS/Wasm 响应体
        await page.cdp.send("Network.enable")

        def on_response(params: dict[str, object]) -> None:
            req_id = str(params.get("requestId") or "")
            resp = params.get("response")
            if isinstance(resp, dict):
                url = str(resp.get("url") or "")
                mime = str(resp.get("mimeType") or "").lower()
                status = int(resp.get("status") or 0)
                if status == 200 and (
                    "javascript" in mime
                    or "wasm" in mime
                    or url.endswith((".js", ".wasm"))
                    or "makersuite" in url
                    or "alkali" in url
                ):
                    captured_responses[req_id] = {
                        "url": url,
                        "mime": mime,
                    }

        def on_loading_finished(params: dict[str, object]) -> None:
            req_id = str(params.get("requestId") or "")
            if req_id in captured_responses:
                # 记录以便稍后提取
                pass

        page.cdp.on("Network.responseReceived", on_response)
        page.cdp.on("Network.loadingFinished", on_loading_finished)

        # 3. 导航到目标页面并等待资源加载
        print(f"\n[3/5] 导航至 {target_url} 并等待页面加载...")
        await page.goto(target_url, timeout_s=timeout_s)

        print("      等待 SPA 模块与脚本初始化 (10 秒)...")
        for i in range(10):
            await asyncio.sleep(1.0)
            if browser_proc.poll() is not None:
                raise RuntimeError("浏览器进程在加载期间异常退出")
            sys.stdout.write(f"\r      已等待 {i + 1}/10 秒...")
            sys.stdout.flush()
        print()

        # 4. 下载并提取全部捕获的 JS / Wasm 资源
        print(
            f"\n[4/5] 提取捕获的静态资源 (共 {len(captured_responses)} 个候选请求)..."
        )
        for req_id, meta in captured_responses.items():
            url = meta["url"]
            parsed = urlparse(url)
            filename = Path(parsed.path).name or "index.js"
            if not filename.endswith((".js", ".wasm")):
                filename += ".js"

            try:
                res = await page.cdp.send(
                    "Network.getResponseBody",
                    {"requestId": req_id},
                    timeout_s=5.0,
                )
                raw_body = res.get("body")
                body = str(raw_body) if isinstance(raw_body, str) else ""
                is_base64 = bool(res.get("base64Encoded", False))

                if not body:
                    continue

                if is_base64:
                    raw_bytes = base64.b64decode(body)
                    dest_file = (
                        (wasm_dir / filename)
                        if filename.endswith(".wasm")
                        else (bundles_dir / filename)
                    )
                    dest_file.write_bytes(raw_bytes)
                    saved_files.append(
                        {
                            "name": filename,
                            "type": "binary",
                            "size": len(raw_bytes),
                            "url": url,
                        }
                    )
                else:
                    dest_file = bundles_dir / filename
                    dest_file.write_text(body, encoding="utf-8")
                    saved_files.append(
                        {
                            "name": filename,
                            "type": "text",
                            "size": len(body),
                            "url": url,
                        }
                    )
                print(f"      [✓ 保存] {filename} ({dest_file.stat().st_size} 字节)")
            except Exception:
                # 忽略极小部分已被丢弃的短暂资源
                continue

        # 5. 探查运行时内存对象 (MakerSuite, Angular, WebAssembly)
        print("\n[5/5] 探查运行时内存与全局对象结构...")
        introspection_js = """
        (() => {
            const report = {
                title: document.title,
                url: window.location.href,
                hasMakerSuite: !!window.default_MakerSuite,
                makerSuiteKeys: [],
                snapshotCandidateKeys: [],
                angularRoots: [],
                capturedWasmCount: (window.__CAPTURED_WASM__ || []).length,
                customWindowGlobals: []
            };

            // 1. 探查 default_MakerSuite
            if (window.default_MakerSuite) {
                const dms = window.default_MakerSuite;
                report.makerSuiteKeys = Object.keys(dms);
                for (const key of report.makerSuiteKeys) {
                    try {
                        const val = dms[key];
                        if (typeof val === 'function') {
                            const src = val.toString();
                            if (src.includes('snapshot') || src.includes('e9b') || src.includes('yield')) {
                                report.snapshotCandidateKeys.push({
                                    key: key,
                                    length: val.length,
                                    preview: src.substring(0, 150).replace(/\\s+/g, ' ')
                                });
                            }
                        }
                    } catch(e) {}
                }
            }

            // 2. 探查 Angular 根组件
            try {
                const appRoot = document.querySelector('app-root') || document.querySelector('ms-app-root') || document.querySelector('body > *');
                if (appRoot) {
                    report.angularRoots.push({
                        tagName: appRoot.tagName,
                        hasNg: typeof window.ng !== 'undefined',
                        attributes: Array.from(appRoot.attributes).map(a => a.name)
                    });
                }
            } catch(e) {}

            // 3. 收集非标准全局对象
            const stdGlobals = new Set([
                'window','self','document','name','location','customElements','history',
                'locationbar','menubar','personalbar','scrollbars','statusbar','toolbar',
                'status','closed','frames','length','top','opener','parent','frameElement',
                'navigator','origin','external','screen','innerWidth','innerHeight','scrollX',
                'pageXOffset','scrollY','pageYOffset','visualViewport','screenX','screenY',
                'outerWidth','outerHeight','devicePixelRatio','clientInformation','screenLeft',
                'screenTop','defaultStatus','defaultstatus','styleMedia','onsearch','isSecureContext',
                'performance','console','sessionStorage','localStorage','crypto','indexedDB'
            ]);
            for (const k of Object.keys(window)) {
                if (!stdGlobals.has(k) && !k.startsWith('on') && !k.startsWith('webkit')) {
                    report.customWindowGlobals.push({
                        name: k,
                        type: typeof window[k]
                    });
                }
            }

            return report;
        })()
        """
        report_data = await page.evaluate(introspection_js)

        # 检查是否有从 Hook 截获到的 Wasm 字节流
        captured_wasm_list = await page.evaluate("() => window.__CAPTURED_WASM__ || []")
        if isinstance(captured_wasm_list, list) and captured_wasm_list:
            print(f"      [★ 发现] 截获到 {len(captured_wasm_list)} 个 Wasm 模块实例！")
            for idx, w in enumerate(captured_wasm_list):
                raw_b64 = w.get("base64") if isinstance(w, dict) else None
                if isinstance(raw_b64, str) and raw_b64:
                    wasm_bytes = base64.b64decode(raw_b64)
                    wasm_path = wasm_dir / f"botguard_module_{idx}.wasm"
                    wasm_path.write_bytes(wasm_bytes)
                    print(
                        f"      [✓ 导出 Wasm] {wasm_path.name} ({len(wasm_bytes)} 字节, 来源: {w.get('source')})"
                    )

        # 保存分析报告
        report_file = out_dir / "runtime_report.json"
        report_file.write_text(
            json.dumps(report_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"      [✓ 保存] 运行时分析报告: {report_file.name}")

        # 6. 自动美化格式化 JS
        if format_code and saved_files:
            js_files = list(bundles_dir.glob("*.js"))
            if js_files:
                print(
                    f"\n[*] 正在使用 bun x prettier 美化格式化 {len(js_files)} 个 JS 文件..."
                )
                try:
                    subprocess.run(  # noqa: ASYNC221
                        [
                            "bun",
                            "x",
                            "prettier",
                            "--write",
                            str(bundles_dir / "*.js"),
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        cwd=str(PROJECT_ROOT),
                    )
                    print(
                        "      [✓ 完成] 所有 JS 文件已完成排版美化，可直接在编辑器中全文搜索！"
                    )
                except Exception as e:
                    print(f"      [!] 调用 prettier 格式化提示: {e}")

        print("\n" + "=" * 70)
        print("  逆向抓取与分析成功完成！")
        print(f"  保存 JS / Wasm 资源数量: {len(saved_files)}")
        print(f"  产物目录: {out_dir}")
        print("=" * 70)

    except Exception as exc:
        print(f"\n[!] 逆向分析过程发生异常: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()

    finally:
        # 【关键保护：保证无论成功还是异常，绝对彻底关闭无头浏览器进程释放内存】
        print("\n[*] 正在清理环境并彻底关闭无头浏览器进程...")
        if page and not page.is_closed():
            with contextlib.suppress(Exception):
                await page.close()
        if cdp_client:
            with contextlib.suppress(Exception):
                await cdp_client.page.close() if cdp_client.page else None
        if browser_proc:
            pid = browser_proc.pid
            print(f"[*] 正在终止 CloakBrowser 进程 (PID: {pid})...")
            try:
                browser_proc.terminate()
                try:
                    browser_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    browser_proc.kill()
                    browser_proc.wait(timeout=2)
            except Exception as e:
                print(f"[!] 终止进程提示: {e}")

            # 在 Windows 上额外检查并清理该 PID 的任何残留子进程树
            if platform.system() == "Windows":
                with contextlib.suppress(Exception):
                    subprocess.run(  # noqa: ASYNC221
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

            print(f"[+] 进程 {pid} 已完全退出，内存已全部释放！")

        if Path(temp_profile_dir).exists():
            with contextlib.suppress(Exception):
                shutil.rmtree(temp_profile_dir, ignore_errors=True)
            print("[+] 临时 User Data 缓存目录已清理完毕。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google AI Studio JS / Wasm 无头逆向抓取与内省工具"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9333,
        help="CDP 远程调试端口 (默认 9333，避免与 9222 冲突)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="禁用无头模式（打开浏览器图形窗口观察）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=35.0,
        help="页面导航超时时间 (秒)",
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://aistudio.google.com/",
        help="逆向目标网址 (默认 https://aistudio.google.com/)",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="输出产物目录 (默认 tools/reverse/output)",
    )
    parser.add_argument(
        "--no-format",
        action="store_true",
        help="禁用 Prettier 代码自动美化",
    )

    args = parser.parse_args()
    out_dir = Path(args.out_dir) if args.out_dir else None

    asyncio.run(
        run_reverse(
            port=args.port,
            headless=not args.no_headless,
            timeout_s=args.timeout,
            out_dir=out_dir,
            format_code=not args.no_format,
            target_url=args.url,
        )
    )


if __name__ == "__main__":
    main()
