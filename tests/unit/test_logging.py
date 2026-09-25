"""Comprehensive unit tests for unified logging and request dump subsystem."""

from __future__ import annotations

import io
import logging
import os
from unittest.mock import patch

import httpx
import pytest

from aistudio_api.api.app import app
from aistudio_api.infrastructure.utils.logger import (
    UnifiedFormatter,
    dump_request_exchange,
    get_log_level,
    get_logger,
    is_debug_env_active,
    is_dump_requests_enabled,
    set_dump_requests,
    set_log_level,
    setup_logging,
)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def test_unified_formatter_plain_and_colored():
    plain_formatter = UnifiedFormatter(use_color=False)
    color_formatter = UnifiedFormatter(use_color=True)

    record = logging.LogRecord(
        name="aistudio.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="这是一条中文测试日志",
        args=(),
        exc_info=None,
    )

    plain_output = plain_formatter.format(record)
    assert "[INFO]" in plain_output
    assert "[aistudio.test]" in plain_output
    assert "这是一条中文测试日志" in plain_output

    color_output = color_formatter.format(record)
    assert "\033[32m" in color_output  # Green color code for INFO
    assert "这是一条中文测试日志" in color_output


def test_logger_namespace_hierarchy():
    logger1 = get_logger("server")
    assert logger1.name == "aistudio.server"

    logger2 = get_logger("aistudio.browser")
    assert logger2.name == "aistudio.browser"

    logger3 = get_logger("aistudio")
    assert logger3.name == "aistudio"


def test_dynamic_log_level_switching():
    setup_logging("INFO")
    assert get_log_level() == "INFO"
    assert logging.getLogger("aistudio").level == logging.INFO

    set_log_level("DEBUG")
    assert get_log_level() == "DEBUG"
    assert logging.getLogger("aistudio").level == logging.DEBUG

    set_log_level("WARNING")
    assert get_log_level() == "WARNING"
    assert logging.getLogger("aistudio").level == logging.WARNING

    set_log_level(logging.ERROR)
    assert get_log_level() == "ERROR"
    assert logging.getLogger("aistudio").level == logging.ERROR

    # 恢复默认
    set_log_level("INFO")


def test_debug_env_var_triggers_dump_without_changing_log_level():
    """验证 DEBUG 环境变量仅用于触发 dump 请求，绝不影响日志输出级别。"""
    set_log_level("INFO")
    initial_level = get_log_level()

    # 1. 模拟 DEBUG="1"
    with patch.dict(os.environ, {"DEBUG": "1"}, clear=False):
        assert is_debug_env_active() is True
        assert is_dump_requests_enabled() is True
        # 严格断言：日志级别绝不能被 DEBUG 环境变量修改为 DEBUG！
        assert get_log_level() == initial_level
        assert get_log_level() == "INFO"

    # 2. 模拟 DEBUG="true"
    with patch.dict(os.environ, {"DEBUG": "true"}, clear=False):
        assert is_debug_env_active() is True
        assert is_dump_requests_enabled() is True
        assert get_log_level() == "INFO"

    # 3. 模拟 DEBUG="dump"
    with patch.dict(os.environ, {"DEBUG": "dump"}, clear=False):
        assert is_debug_env_active() is True
        assert is_dump_requests_enabled() is True
        assert get_log_level() == "INFO"

    # 4. 模拟未设置 DEBUG 且未设置其他环境变量
    env_clean = dict(os.environ)
    env_clean.pop("DEBUG", None)
    env_clean.pop("AISTUDIO_DEBUG", None)
    env_clean.pop("AISTUDIO_DUMP_REQUESTS", None)
    with patch.dict(os.environ, env_clean, clear=True):
        set_dump_requests(False)
        assert is_debug_env_active() is False
        assert is_dump_requests_enabled() is False
        assert get_log_level() == "INFO"


def test_dump_request_exchange_output():
    """验证请求详细转储报文格式与中文标识。"""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(UnifiedFormatter(use_color=False))
    dump_lgr = get_logger("dump")
    dump_lgr.addHandler(handler)
    dump_lgr.setLevel(logging.INFO)

    try:
        dump_request_exchange(
            req_id="req_test123",
            method="POST",
            url="/v1beta/models/gemini-3.7-flash:generateContent",
            client="127.0.0.1:54321",
            headers={
                "authorization": "Bearer secret1234567890",
                "user-agent": "test-client",
            },
            query_params={"key": "xyz"},
            body_text='{"contents": [{"role": "user", "parts": [{"text": "你好"}]}]}',
            status_code=200,
            elapsed_ms=123.45,
            response_headers={"content-type": "application/json"},
            response_text='{"candidates": [{"content": {"parts": [{"text": "你好！有什么我可以帮你的？"}]}}]}',
            is_stream=False,
        )
        output = stream.getvalue()

        assert (
            "==================== [请求报文转储: req_test123] ===================="
            in output
        )
        assert "客户端:       127.0.0.1:54321" in output
        assert (
            "请求接口:     POST /v1beta/models/gemini-3.7-flash:generateContent"
            in output
        )
        assert "Bearer secre..." in output  # 敏感 Token 适度脱敏
        assert "你好" in output
        assert (
            "-------------------- [响应报文转储: req_test123] --------------------"
            in output
        )
        assert "响应状态:     HTTP 200" in output
        assert "处理耗时:     123.45 ms" in output
        assert "你好！有什么我可以帮你的？" in output
        assert (
            "==================== [转储结束: req_test123] ===================="
            in output
        )
    finally:
        dump_lgr.removeHandler(handler)


@pytest.mark.asyncio
async def test_get_config_includes_logging_fields():
    """验证 GET /config 包含 log_level, dump_requests, debug_env_active 字段。"""
    async with _client() as client:
        resp = await client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "log_level" in data
        assert "dump_requests" in data
        assert "debug_env_active" in data
        assert isinstance(data["dump_requests"], bool)
        assert isinstance(data["debug_env_active"], bool)


@pytest.mark.asyncio
async def test_update_logging_config_endpoint(tmp_path):
    """验证 PUT /config/logging 能即时更新并持久化配置。"""
    test_yaml = tmp_path / "config.yaml"
    test_yaml.write_text("api_keys: []\n", encoding="utf-8")

    with patch(
        "aistudio_api.infrastructure.gateway.model_defaults._resolve_config_path",
        return_value=test_yaml,
    ):
        async with _client() as client:
            resp = await client.put(
                "/config/logging",
                json={"level": "WARNING", "dump_requests": True},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["log_level"] == "WARNING"
            assert data["dump_requests"] is True

            # 验证运行时已被即时修改
            assert get_log_level() == "WARNING"
            assert is_dump_requests_enabled() is True

            # 验证持久化到了 YAML
            saved_content = test_yaml.read_text(encoding="utf-8")
            assert "logging:" in saved_content
            assert "level: WARNING" in saved_content
            assert "dump_requests: true" in saved_content

            # 恢复设置
            await client.put(
                "/config/logging", json={"level": "INFO", "dump_requests": False}
            )
            assert get_log_level() == "INFO"


@pytest.mark.asyncio
async def test_middleware_request_dump_and_access_log():
    """验证中间件在 dump_requests=True 时正确拦截并完整响应。"""
    set_dump_requests(True)
    try:
        async with _client() as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
    finally:
        set_dump_requests(False)


def test_dump_request_to_file_by_req_id(tmp_path):
    """验证按照请求 ID 转储为独立 JSON 文件并携带所有必要完整信息。"""
    from aistudio_api.infrastructure.utils.logger import (
        get_dump_dir,
        is_dump_to_file_enabled,
        set_dump_dir,
        set_dump_to_file,
    )

    dump_test_dir = tmp_path / "test_dumps"
    set_dump_dir(dump_test_dir)
    set_dump_to_file(True)

    try:
        assert is_dump_to_file_enabled() is True
        assert get_dump_dir() == dump_test_dir

        test_req_id = "req_f96e327d"
        dump_request_exchange(
            req_id=test_req_id,
            method="POST",
            url="/v1beta/models/gemini-2.5-flash:generateContent",
            client="127.0.0.1:45678",
            headers={
                "content-type": "application/json",
                "x-goog-api-key": "secret_key",
            },
            query_params={"alt": "sse"},
            body_text='{"contents": [{"role": "user", "parts": [{"text": "测试抗截断"}]}], "tools": [{"functionDeclarations": [{"name": "reply_fn"}]}]}',
            status_code=200,
            elapsed_ms=88.5,
            response_headers={"content-type": "application/json"},
            response_text='{"candidates": [{"content": {"parts": [{"text": "成功"}]}}]}',
            is_stream=False,
        )

        target_dump_file = dump_test_dir / f"{test_req_id}.json"
        latest_dump_file = dump_test_dir / "latest_request.json"

        assert target_dump_file.exists()
        assert latest_dump_file.exists()

        import json

        data = json.loads(target_dump_file.read_text(encoding="utf-8"))
        assert data["req_id"] == test_req_id
        assert data["method"] == "POST"
        assert data["url"] == "/v1beta/models/gemini-2.5-flash:generateContent"
        assert data["client"] == "127.0.0.1:45678"
        assert data["headers"]["content-type"] == "application/json"
        assert data["query_params"]["alt"] == "sse"
        assert data["status_code"] == 200
        assert data["elapsed_ms"] == 88.5
        assert data["is_stream"] is False
        assert data["body"]["tools"][0]["functionDeclarations"][0]["name"] == "reply_fn"
        assert "测试抗截断" in data["raw_body"]
        assert (
            data["response_body"]["candidates"][0]["content"]["parts"][0]["text"]
            == "成功"
        )
    finally:
        set_dump_to_file(None)
        set_dump_dir(None)
