"""Captured request replay workflow."""

from __future__ import annotations

from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway.capture import CapturedRequest
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("replay")


class RequestReplayService:
    def __init__(self, session: BrowserSession):
        self._session = session

    async def replay(
        self, captured: CapturedRequest | None, body: str, timeout: int | None = None
    ) -> tuple[int, bytes]:
        if not captured:
            return 0, b""

        if timeout is None:
            timeout = settings.timeout_replay

        headers = {
            k: v
            for k, v in captured.headers.items()
            if k.lower() not in ("host", "content-length")
        }

        return await self._session.send_hooked_request(
            body=body,
            timeout_ms=timeout * 1000,
            url=captured.url,
            headers=headers,
        )
