
"""回归：会话恢复阶段的异常必须被接住，不能穿透掉整个重试循环。

背景：`is_browser_session_hang_error` 分支里调用 `try_switch_account()` 切号，
切号本身会触发 BotGuard 重握手，这一步可能再抛 RuntimeError / TimeoutError。
旧实现没有 try/except，异常会直接穿透出去，连后面的「关掉浏览器重建」兜底
都执行不到，第一次尝试就放弃重试。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from aistudio_api.application import api_service_gemini as api


def _client_with_session(in_flight: int = 0) -> MagicMock:
    client = MagicMock()
    client.clear_templates = MagicMock()
    session = MagicMock()
    session._in_flight = in_flight
    session._close_internal = AsyncMock()
    client._session = session
    return client


@pytest.mark.asyncio
async def test_recovery_failure_falls_back_to_browser_restart(monkeypatch):
    """切号恢复抛异常时，应落到关闭浏览器重建，并返回 True 继续重试。"""
    client = _client_with_session(in_flight=0)

    async def _boom(**_kwargs):
        raise RuntimeError("BotGuard 重握手超时")

    monkeypatch.setattr(api, "try_switch_account", _boom)

    should_retry = await api.handle_attempt_exception(
        RuntimeError("botguard handshake timeout"),
        attempt=0,
        model_path="gemini-3.7-flash",
        normalized_model="models/gemini-3.7-flash",
        client=client,
        has_yielded_data=False,
    )

    assert should_retry is True
    client.clear_templates.assert_called_once()
    client._session._close_internal.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_failure_does_not_leak_runtime_error(monkeypatch):
    """重试已耗尽时，恢复异常也不能原样穿透，必须收敛成 HTTPException。"""
    client = _client_with_session()
    client._session = None  # 不允许走重建兜底

    async def _boom(**_kwargs):
        raise TimeoutError("BotGuard 恢复超时")

    monkeypatch.setattr(api, "try_switch_account", _boom)

    with pytest.raises(HTTPException) as exc_info:
        await api.handle_attempt_exception(
            RuntimeError("botguard handshake timeout"),
            attempt=5,
            model_path="gemini-3.7-flash",
            normalized_model="models/gemini-3.7-flash",
            client=client,
            has_yielded_data=False,
        )

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_recovery_failure_skipped_when_data_already_yielded(monkeypatch):
    """已经向客户端吐过数据时不允许重试，也不会去调切号恢复。"""
    client = _client_with_session()
    called = False

    async def _boom(**_kwargs):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(api, "try_switch_account", _boom)

    with pytest.raises(HTTPException):
        await api.handle_attempt_exception(
            RuntimeError("botguard handshake timeout"),
            attempt=0,
            model_path="gemini-3.7-flash",
            normalized_model="models/gemini-3.7-flash",
            client=client,
            has_yielded_data=True,
        )

    assert called is False
