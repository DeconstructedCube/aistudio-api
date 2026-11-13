"""AI Studio client gateway."""

from __future__ import annotations

from dataclasses import dataclass

from aistudio_api.config import DEFAULT_TEXT_MODEL
from aistudio_api.domain.errors import (
    SessionExpiredError,
    TransportError,
    classify_error,
    is_html_payload,
    is_login_redirect_payload,
)
from aistudio_api.domain.models import ModelOutput
from aistudio_api.infrastructure.gateway.headers import (
    merge_header_case_insensitive,
)
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
        self.last_prepared_request: PreparedRequest | None = None

    @property
    def session(self) -> GatewaySession:
        """Expose active gateway session facade."""
        return self._session

    async def warmup(self) -> None:
        """初始化活跃账号会话。"""
        if self._session is not None:
            await self._session.ensure_ready()
            logger.info("网关会话已就绪")

    async def switch_auth(self, auth_file: str | None) -> None:
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
        force_recreate_signer: bool = False,
    ) -> PreparedRequest:
        ctx = await self._session.ensure_ready()
        effective_contents = contents or [
            AistudioContent.from_user_prompt(prompt=prompt, images=images)
        ]
        if force_recreate_signer:
            snapshot = await self._session.generate_snapshot(
                effective_contents, ctx=ctx, model=model, force_recreate=True
            )
        else:
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
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_active_waa_server,
        )

        active_waa = get_active_waa_server()
        headers = dict(DEFAULT_UPSTREAM_HEADERS)
        if active_waa.get("user_agent"):
            merge_header_case_insensitive(
                headers, "User-Agent", str(active_waa["user_agent"])
            )
        server_hdrs = active_waa.get("headers")
        if isinstance(server_hdrs, dict):
            for k, v in server_hdrs.items():
                merge_header_case_insensitive(headers, str(k), str(v))
        dyn_headers = getattr(self._session, "last_signer_headers", None)
        if isinstance(dyn_headers, dict):
            for k, v in dyn_headers.items():
                if k.lower() not in ("cookie", "authorization"):
                    merge_header_case_insensitive(headers, str(k), str(v))
        merge_header_case_insensitive(headers, "X-Goog-AuthUser", ctx.auth_user)
        prepared = PreparedRequest(
            url=GENERATE_CONTENT_URL,
            headers=headers,
            body=body,
            model=model,
            snapshot=snapshot,
            ctx=ctx,
        )
        logger.debug(
            "请求签名完成: model=%s, proof_len=%d, body_len=%d",
            prepared.model,
            len(prepared.snapshot),
            len(prepared.body),
        )
        self.last_prepared_request = prepared
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
        force_recreate_signer: bool = False,
        prepared: PreparedRequest | None = None,
    ):
        effective_prep = prepared or await self.prepare_request(
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
            force_recreate_signer=force_recreate_signer,
        )
        async for event in self._streaming_gateway.stream_chat(
            request=effective_prep,
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
        force_recreate_signer: bool = False,
        prepared: PreparedRequest | None = None,
    ) -> ModelOutput:
        if prepared is not None:
            effective_prep = prepared
            logger.debug("重放已签名请求: model=%s", model)
        else:
            logger.debug("构建生成请求: model=%s", model)
            effective_prep = await self.prepare_request(
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
                force_recreate_signer=force_recreate_signer,
            )
        status, raw = await self._session.send_request(
            body=effective_prep.body,
            url=effective_prep.url,
            headers=effective_prep.headers,
            ctx=effective_prep.ctx,
        )
        raw_text = raw.decode("utf-8", errors="replace")
        if status != 200:
            raise classify_error(status, raw_text)
        if not raw_text.strip():
            raise TransportError("empty 200 body (connection truncated)")
        if is_login_redirect_payload(raw_text):
            raise SessionExpiredError("登录态已失效: 被重定向至 Google 登录页")
        if is_html_payload(raw_text):
            raise TransportError("upstream returned HTML error page on HTTP 200")
        output = parse_text_output(raw_text)
        output.model = model
        return output


__all__ = ["AIStudioClient", "PreparedRequest"]
