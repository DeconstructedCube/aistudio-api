"""Browser-backed AI Studio client facade."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from aistudio_api.config import (
    DEFAULT_BROWSER_PORT,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_TEXT_MODEL,
    settings,
)
from aistudio_api.domain.errors import RequestError, classify_error
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.gateway.capture import (
    CapturedRequest,
    RequestCaptureService,
)
from aistudio_api.infrastructure.gateway.model_defaults import resolve_model_defaults
from aistudio_api.infrastructure.gateway.replay import RequestReplayService
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.streaming import StreamingGateway
from aistudio_api.infrastructure.gateway.wire_codec import (
    TOOLS_TEMPLATES,
    build_image_generation_search_tool,
    build_tools_from_names,
)
from aistudio_api.infrastructure.gateway.wire_parser import (
    parse_image_output,
    parse_text_output,
)
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent, AistudioPart
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("client")


class AIStudioClient:
    IMAGE_SIZE_TO_OUTPUT_RESOLUTION: ClassVar[dict[str, list[str]]] = {
        # 1:1
        "512x512": ["1:1", "512"],
        "1024x1024": ["1:1", "1K"],
        "2048x2048": ["1:1", "2K"],
        "4096x4096": ["1:1", "4K"],
        # 16:9
        "1792x1024": ["16:9", "1K"],
        # 9:16
        "1024x1792": ["9:16", "1K"],
        # 4:3
        "1365x1024": ["4:3", "1K"],
        # 3:4
        "1024x1365": ["3:4", "1K"],
        # 3:2
        "1536x1024": ["3:2", "1K"],
        # 2:3
        "1024x1536": ["2:3", "1K"],
    }

    def __init__(self, port: int = DEFAULT_BROWSER_PORT):
        self.port = port
        self._captured: CapturedRequest | None = None
        self._session = BrowserSession(port=port)
        self._capture_service = RequestCaptureService(self._session)
        self._replay_service = RequestReplayService(session=self._session)

        self._streaming_gateway = StreamingGateway(session=self._session)

    async def warmup(self) -> None:
        """预热浏览器后端并加载 AI Studio 页面及捕获 BotGuard 服务。"""
        if self._session is not None:
            await self._session.ensure_botguard_service()
            logger.info("浏览器预热完成")

    async def switch_auth(self, auth_file: str | None) -> None:
        """切换账号的 auth 文件并清空模板缓存。"""
        self.clear_templates()
        if self._session is not None:
            await self._session.switch_auth(auth_file)

    def clear_templates(self) -> None:
        """清除捕获的请求模板。"""
        if getattr(self, "_capture_service", None) is not None:
            self._capture_service.clear_templates()

    def clear_snapshot_cache(self) -> None:
        """兼容历史接口：清除模板缓存（快照签名按请求实时生成，已无快照缓存）。"""
        self.clear_templates()

    async def close(self) -> None:
        """关闭浏览器后端。"""
        if self._session is not None:
            await self._session.close()

    def _dump_raw_exchange(
        self,
        *,
        kind: str,
        model: str,
        capture_prompt: str,
        modified_body: str,
        raw_response: str,
    ) -> None:
        if not settings.dump_raw_response:
            return

        out_dir = Path(settings.dump_raw_response_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_model = model.replace("/", "_")
        timestamp = __import__("time").strftime("%Y%m%d_%H%M%S")
        payload = {
            "kind": kind,
            "model": model,
            "capture_prompt": capture_prompt,
            "modified_body": json.loads(modified_body),
            "raw_response": raw_response,
        }
        path = out_dir / f"aistudio_{kind}_{safe_model}_{timestamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        logger.info("已落盘原始请求/响应: %s", path)

    async def capture_request(
        self,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction: str | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        force_refresh: bool = False,
    ) -> CapturedRequest | None:
        return await self._capture_service.capture(
            prompt=prompt,
            model=model,
            images=images,
            contents=contents,
            system_instruction=system_instruction,
            system_instruction_content=system_instruction_content,
            tools=tools,
            tool_config=tool_config,
            safety_settings=safety_settings,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            generation_config_overrides=generation_config_overrides,
            sanitize_plain_text=sanitize_plain_text,
            force_refresh=force_refresh,
        )

    async def replay(self, body: str, timeout: int = 120) -> tuple[int, bytes]:
        return await self._replay_service.replay(
            self._captured, body=body, timeout=timeout
        )

    async def stream_chat(
        self,
        *,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        images: list[str | tuple[str, str]] | None = None,
        system_instruction: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        tools: list[list] | None = None,
    ):
        merged_tools = list(tools or [])
        async for event in self.stream_generate_content(
            model=model,
            capture_prompt=prompt,
            capture_images=images,
            contents=[self._build_user_content(prompt=prompt, images=images)],
            system_instruction_content=(
                AistudioContent(
                    role="user", parts=[AistudioPart(text=system_instruction)]
                )
                if system_instruction
                else None
            ),
            tools=merged_tools or None,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
        ):
            yield event

    async def stream_generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
        force_refresh_capture: bool = False,
    ):
        captured = await self.capture_request(
            prompt=capture_prompt,
            model=model,
            images=capture_images,
            contents=contents,
            system_instruction_content=system_instruction_content,
            tools=tools,
            tool_config=tool_config,
            safety_settings=safety_settings,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            generation_config_overrides=generation_config_overrides,
            sanitize_plain_text=sanitize_plain_text,
            force_refresh=force_refresh_capture,
        )
        async for event in self._streaming_gateway.stream_chat(
            captured=captured,
            model=model,
            system_instruction=None,
            contents=contents,
            system_instruction_content=system_instruction_content,
            tools=tools,
            safety_settings=safety_settings,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            generation_config_overrides=generation_config_overrides,
            sanitize_plain_text=sanitize_plain_text,
        ):
            yield event

    async def chat(
        self,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        system_instruction: str | None = None,
        code_execution: bool = False,
        google_search: bool = False,
        images: list[str | tuple[str, str]] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        tools: list[list[object]] | None = None,
    ) -> ModelOutput:
        merged_tools: list[list[object]] = list(tools or [])
        if code_execution or google_search:
            if code_execution:
                merged_tools.append(TOOLS_TEMPLATES["code_execution"])
            if google_search:
                merged_tools.append(TOOLS_TEMPLATES["google_search"])

        return await self.generate_content(
            model=model,
            capture_prompt=prompt,
            capture_images=images,
            contents=[self._build_user_content(prompt=prompt, images=images)],
            system_instruction_content=(
                AistudioContent(
                    role="user", parts=[AistudioPart(text=system_instruction)]
                )
                if system_instruction
                else None
            ),
            tools=merged_tools or None,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
        )

    async def generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        capture_prompt: str,
        capture_images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        tool_config: list[object] | None = None,
        safety_settings: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
    ) -> ModelOutput:
        logger.info("拦截请求: %r", f"{capture_prompt[:20]}...")
        captured = await self.capture_request(
            prompt=capture_prompt,
            model=model,
            images=capture_images,
            contents=contents,
            system_instruction_content=system_instruction_content,
            tools=tools,
            tool_config=tool_config,
            safety_settings=safety_settings,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            generation_config_overrides=generation_config_overrides,
            sanitize_plain_text=sanitize_plain_text,
        )
        if not captured:
            raise RequestError(0, "无法拦截请求")

        modified_body = captured.body

        status, raw = await self._replay_service.replay(captured, body=modified_body)
        raw_text = raw.decode("utf-8", errors="replace")
        self._dump_raw_exchange(
            kind="generate_content",
            model=model,
            capture_prompt=capture_prompt,
            modified_body=modified_body,
            raw_response=raw_text,
        )
        if status != 200:
            raise classify_error(status, raw_text)
        output = parse_text_output(raw_text)
        output.model = model
        return output

    @classmethod
    def resolve_image_size(cls, size: str) -> list[str] | None:
        """将 OpenAI 风格的 size 映射为 AI Studio 的生图尺寸配置。"""
        return cls.IMAGE_SIZE_TO_OUTPUT_RESOLUTION.get(size)

    async def generate_image(
        self,
        prompt: str,
        model: str = DEFAULT_IMAGE_MODEL,
        save_path: str | None = None,
        size: str = "1024x1024",
        google_search: bool = False,
        image_search: bool = False,
        use_default_tools: bool = True,
        images: list[str | tuple[str, str]] | None = None,
        contents: list[AistudioContent] | None = None,
    ) -> ModelOutput:
        logger.info(
            "生图请求: %r, images=%s", f"{prompt[:20]}...", len(images) if images else 0
        )
        request_contents = contents or [
            self._build_user_content(prompt=prompt, images=images)
        ]
        generation_config_overrides = None
        output_resolution = self.resolve_image_size(size)
        if output_resolution is not None:
            generation_config_overrides = {"output_resolution": output_resolution}
        model_defaults = resolve_model_defaults(model)
        resolved_tools: list[list[object]] | None = None
        if google_search or image_search:
            tool = build_image_generation_search_tool(
                google_search=google_search,
                image_search=image_search,
            )
            if tool is not None:
                resolved_tools = [tool]
        elif use_default_tools and model_defaults.default_tools:
            resolved_tools = (
                build_tools_from_names(
                    model_defaults.default_tools,
                    model=model,
                    is_image_model=model_defaults.is_image_model,
                )
                or None
            )

        captured = await self.capture_request(
            prompt=prompt,
            model=model,
            images=images,
            contents=request_contents,
            tools=resolved_tools,
            generation_config_overrides=generation_config_overrides,
        )
        if not captured:
            raise RequestError(0, "无法拦截请求")

        modified_body = captured.body
        status, raw = await self._replay_service.replay(
            captured, body=modified_body, timeout=120
        )
        raw_text = raw.decode("utf-8", errors="replace")
        self._dump_raw_exchange(
            kind="generate_image",
            model=model,
            capture_prompt=prompt,
            modified_body=modified_body,
            raw_response=raw_text,
        )
        if status != 200:
            raise classify_error(status, raw_text)
        output = parse_image_output(raw_text)
        output.model = model

        if output.images and save_path:
            img = output.images[0]
            ext = "jpg" if "jpeg" in img.mime else "png"
            path = (
                Path(save_path)
                if save_path.endswith(f".{ext}")
                else Path(f"{save_path}.{ext}")
            )
            path.write_bytes(img.data)
            logger.info("图片已保存: %s (%s bytes)", path, img.size)

        return output

    def _build_user_content(
        self,
        prompt: str,
        images: list[str | tuple[str, str]] | None = None,
    ) -> AistudioContent:
        parts = []
        for item in images or []:
            if isinstance(item, tuple) and len(item) == 2:
                parts.append(AistudioPart(inline_data=item))
            elif isinstance(item, str):
                import base64
                import mimetypes

                mime = mimetypes.guess_type(item)[0] or "image/jpeg"
                data = Path(item).read_bytes()
                parts.append(
                    AistudioPart(
                        inline_data=(
                            mime,
                            base64.b64encode(data).decode("ascii"),
                        )
                    )
                )
        parts.append(AistudioPart(text=prompt))
        return AistudioContent(role="user", parts=parts)


__all__ = ["AIStudioClient", "CapturedRequest"]
