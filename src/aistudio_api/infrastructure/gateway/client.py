"""AI Studio client gateway."""

from __future__ import annotations

from dataclasses import dataclass

from aistudio_api.config import DEFAULT_TEXT_MODEL
from aistudio_api.domain.errors import (
    TransportError,
    classify_error,
)
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.gateway.session import (
    DEFAULT_UPSTREAM_HEADERS,
    GENERATE_CONTENT_URL,
    GatewaySession,
    SessionContext,
)
from aistudio_api.infrastructure.gateway.wire_codec import encode_request_body
from aistudio_api.infrastructure.gateway.wire_parser import parse_text_output
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("client")


@dataclass(slots=True)
class PreparedRequest:
    url: str
    headers: dict[str, str]
    body: str
    model: str = ""
    snapshot: str = ""
    ctx: SessionContext | None = None


class AIStudioClient:
    def __init__(self):
        from aistudio_api.infrastructure.gateway.streaming import StreamingGateway

        self._session = GatewaySession()
        self._streaming_gateway = StreamingGateway(session=self._session)

    @property
    def session(self) -> GatewaySession:
        """Expose active gateway session facade."""
        return self._session

    async def warmup(self) -> None:
        """预热活动账号会话与签名服务。"""
        if self._session is not None:
            await self._session.ensure_ready()
            logger.info("网关会话预热就绪")

    async def switch_auth(self, auth_file: str | None) -> None:
        """切换账号的 auth 文件。"""
        if self._session is not None:
            await self._session.switch_auth(auth_file)

    async def close(self) -> None:
        """关闭会话与传输层连接。"""
        if self._session is not None:
            await self._session.close()

    async def prepare_request(
        self,
        prompt: str = "",
        model: str = DEFAULT_TEXT_MODEL,
        images: list[tuple[str, str]] | None = None,
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
    ) -> PreparedRequest:
        ctx = await self._session.ensure_ready()
        effective_contents = contents or [
            AistudioContent.from_user_prompt(prompt=prompt, images=images)
        ]
        snapshot = await self._session.generate_snapshot(
            effective_contents, ctx=ctx, model=model
        )
        body = encode_request_body(
            model=model,
            prompt=prompt,
            contents=effective_contents,
            system_instruction=system_instruction,
            system_instruction_content=system_instruction_content,
            tools=tools,
            tool_config=tool_config,
            safety_settings=safety_settings,
            images=images,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            generation_config_overrides=generation_config_overrides,
            snapshot=snapshot,
        )
        headers = dict(DEFAULT_UPSTREAM_HEADERS)
        headers["X-Goog-AuthUser"] = ctx.auth_user
        prepared = PreparedRequest(
            url=GENERATE_CONTENT_URL,
            headers=headers,
            body=body,
            model=model,
            snapshot=snapshot,
            ctx=ctx,
        )
        logger.info(
            "构建签名请求成功: model=%s, 快照长度=%d, 请求体长度=%d",
            prepared.model,
            len(prepared.snapshot),
            len(prepared.body),
        )
        return prepared

    async def stream_generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
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
    ):
        prepared = await self.prepare_request(
            model=model,
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
        )
        async for event in self._streaming_gateway.stream_chat(
            request=prepared,
            model=model,
        ):
            yield event

    async def generate_content(
        self,
        *,
        model: str = DEFAULT_TEXT_MODEL,
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
    ) -> ModelOutput:
        logger.debug("开始构建请求: model=%s", model)
        prepared = await self.prepare_request(
            model=model,
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
        )

        status, raw = await self._session.send_request(
            body=prepared.body,
            url=prepared.url,
            headers=prepared.headers,
            ctx=prepared.ctx,
        )
        raw_text = raw.decode("utf-8", errors="replace")
        if status != 200:
            raise classify_error(status, raw_text)
        if not raw_text.strip():
            raise TransportError("empty 200 body (connection truncated)")
        output = parse_text_output(raw_text)
        output.model = model
        return output


__all__ = ["AIStudioClient", "PreparedRequest"]
