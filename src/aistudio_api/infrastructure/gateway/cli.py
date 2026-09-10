"""CLI entrypoint for the AI Studio client."""

from __future__ import annotations

import argparse
import asyncio

from aistudio_api.config import DEFAULT_IMAGE_MODEL, DEFAULT_TEXT_MODEL, settings
from aistudio_api.domain.errors import AistudioError, UsageLimitExceeded


async def _run_cli(args):
    from aistudio_api.infrastructure.gateway.client import AIStudioClient

    client = AIStudioClient(port=args.port)

    try:
        if args.image:
            model = args.model if args.model != DEFAULT_TEXT_MODEL else DEFAULT_IMAGE_MODEL
            output = await client.generate_image(args.prompt, model=model, save_path=args.save)
            print(f"生成成功: {len(output.images)} 张图片")
            if output.text:
                print(f"描述: {output.text[:200]}")
        else:
            output = await client.chat(
                args.prompt,
                model=args.model,
                system_instruction=args.system,
                google_search=args.search,
                code_execution=args.code,
                images=args.attach,
            )
            print(output.text)
    except UsageLimitExceeded as exc:
        print(f"错误: {exc}")
    except AistudioError as exc:
        print(f"错误: {exc}")
    finally:
        await client.close()


def cli_main():
    parser = argparse.ArgumentParser(description="AI Studio 独立客户端")
    parser.add_argument("prompt", nargs="?", default="你好", help="用户消息")
    parser.add_argument("--model", "-m", default=DEFAULT_TEXT_MODEL, help="模型名称")
    parser.add_argument("--system", "-s", help="系统指令")
    parser.add_argument("--search", action="store_true", help="启用 Google Search")
    parser.add_argument("--code", action="store_true", help="启用 Code Execution")
    parser.add_argument("--image", action="store_true", help="生图模式")
    parser.add_argument("--save", help="图片保存路径")
    parser.add_argument("--attach", "-a", nargs="+", help="附加图片（文件路径）")
    parser.add_argument("--port", type=int, default=settings.browser_port, help="浏览器调试端口（仅 Camoufox 后端使用）")
    args = parser.parse_args()
    asyncio.run(_run_cli(args))
