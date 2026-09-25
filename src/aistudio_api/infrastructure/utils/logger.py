"""Unified logging subsystem and request dump instrumentation.

Provides standardized log formatting, dynamic log level adjustments,
and full-payload request dumping controlled via the DEBUG environment
variable or web configuration.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_UNIFIED_LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
_UNIFIED_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ANSI Color codes for TTY output
_COLORS = {
    "DEBUG": "\033[36m",  # Cyan
    "INFO": "\033[32m",  # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "CRITICAL": "\033[1;31m",  # Bold Red
}
_RESET = "\033[0m"


class UnifiedFormatter(logging.Formatter):
    """Unified formatter supporting optional ANSI colors on TTY streams."""

    def __init__(self, use_color: bool | None = None) -> None:
        super().__init__(fmt=_UNIFIED_LOG_FORMAT, datefmt=_UNIFIED_DATE_FORMAT)
        if use_color is None:
            use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        orig_levelname = record.levelname
        if self.use_color and record.levelname in _COLORS:
            color = _COLORS[record.levelname]
            record.levelname = f"{color}{record.levelname}{_RESET}"
        else:
            record.levelname = record.levelname

        try:
            return super().format(record)
        finally:
            record.levelname = orig_levelname


_root_handler: logging.Handler | None = None
_current_level: str = "INFO"
_dump_requests_override: bool | None = None
_dump_to_file_override: bool | None = None
_dump_dir_override: str | None = None


def is_dump_to_file_enabled() -> bool:
    """检查是否启用将请求与响应转储至本地文件。

    优先级：
    1. 运行时动态配置 (_dump_to_file_override)
    2. 环境变量 AISTUDIO_DUMP_FILE / AISTUDIO_DUMP_TO_FILE
    3. config.yaml 中 logging.dump_to_file
    4. 当 dump_requests 开启时，若 dump_to_file 未显式禁用则默认开启
    """
    global _dump_to_file_override
    if _dump_to_file_override is not None:
        return _dump_to_file_override

    for name in ("AISTUDIO_DUMP_FILE", "AISTUDIO_DUMP_TO_FILE"):
        val = os.getenv(name)
        if val is not None:
            return val.strip().lower() in ("1", "true", "yes", "on", "dump", "y", "t")

    from aistudio_api.config import settings

    if getattr(settings, "dump_to_file", False):
        return True

    try:
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_logging_settings,
        )

        cfg = get_configured_logging_settings()
        if "dump_to_file" in cfg:
            return bool(cfg["dump_to_file"])
        return bool(cfg.get("dump_requests", False))
    except Exception:
        return False


def set_dump_to_file(enabled: bool | None) -> None:
    """动态设置是否落盘转储文件。"""
    global _dump_to_file_override
    _dump_to_file_override = enabled
    if enabled is not None:
        from aistudio_api.config import settings

        settings.dump_to_file = enabled


def get_dump_dir() -> Path:
    """获取或创建请求转储文件存放目录。"""
    global _dump_dir_override
    if _dump_dir_override:
        p = Path(_dump_dir_override)
        p.mkdir(parents=True, exist_ok=True)
        return p

    val = os.getenv("AISTUDIO_DUMP_DIR")
    if val and val.strip():
        p = Path(val.strip())
        p.mkdir(parents=True, exist_ok=True)
        return p

    try:
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_logging_settings,
        )

        cfg = get_configured_logging_settings()
        custom_dir = str(cfg.get("dump_dir") or "").strip()
        if custom_dir:
            p = Path(custom_dir)
            p.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass

    from aistudio_api.config import settings

    cfg_dir = getattr(settings, "dump_dir", None)
    if cfg_dir and str(cfg_dir).strip():
        p = Path(str(cfg_dir).strip())
        p.mkdir(parents=True, exist_ok=True)
        return p

    default_dir = Path(__file__).resolve().parents[3] / "dumps"
    default_dir.mkdir(parents=True, exist_ok=True)
    return default_dir


def set_dump_dir(dump_dir: str | Path | None) -> None:
    """动态设置转储文件存放目录。"""
    global _dump_dir_override
    _dump_dir_override = str(dump_dir) if dump_dir is not None else None
    if dump_dir is not None:
        from aistudio_api.config import settings

        settings.dump_dir = str(dump_dir)


def is_debug_env_active() -> bool:
    """检查是否通过 DEBUG / AISTUDIO_DEBUG 环境变量显式启用了请求转储。

    注意：此环境变量专用于触发请求信息 Dump，绝不作为显示 DEBUG 级别日志的依据。
    """
    for name in ("DEBUG", "AISTUDIO_DEBUG", "AISTUDIO_DUMP_REQUESTS"):
        val = os.getenv(name)
        if val is not None and val.strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
            "dump",
            "y",
            "t",
        ):
            return True
    return False


def is_dump_requests_enabled() -> bool:
    """判断当前是否开启了请求信息转储 (Dump) 功能。

    优先级：
    1. 运行时动态配置 (_dump_requests_override)
    2. DEBUG / AISTUDIO_DEBUG 环境变量
    3. config.yaml 中 logging.dump_requests 配置
    """
    global _dump_requests_override
    if _dump_requests_override is not None:
        return _dump_requests_override

    if is_debug_env_active():
        return True

    from aistudio_api.config import settings

    if getattr(settings, "dump_requests", False):
        return True

    try:
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_logging_settings,
        )

        cfg = get_configured_logging_settings()
        return bool(cfg.get("dump_requests", False))
    except Exception:
        return False


def set_dump_requests(enabled: bool) -> None:
    """动态更新请求转储开关。"""
    global _dump_requests_override
    _dump_requests_override = enabled
    from aistudio_api.config import settings

    settings.dump_requests = enabled


def get_log_level() -> str:
    """获取当前全局配置的日志级别名称 (如 'INFO', 'DEBUG')。"""
    return _current_level


def set_log_level(level: str | int) -> None:
    """动态设置全局与 aistudio 系列日志器的日志级别。"""
    global _current_level
    if isinstance(level, int):
        level_name = logging.getLevelName(level)
    else:
        level_name = str(level).strip().upper()

    numeric_level = getattr(logging, level_name, logging.INFO)
    _current_level = level_name

    root = logging.getLogger()
    root.setLevel(numeric_level)

    aistudio_logger = logging.getLogger("aistudio")
    aistudio_logger.setLevel(numeric_level)

    # 同步更新已挂载的 handler 级别
    if _root_handler:
        _root_handler.setLevel(numeric_level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(logger_name).setLevel(numeric_level)


def setup_logging(level: str | int = "INFO") -> None:
    """初始化全局日志系统并统一所有子系统的格式与输出。"""
    global _root_handler
    formatter = UnifiedFormatter()

    if _root_handler is None:
        _root_handler = logging.StreamHandler(sys.stderr)
        _root_handler.setFormatter(formatter)
        root = logging.getLogger()
        root.handlers.clear()
        root.addHandler(_root_handler)
    else:
        _root_handler.setFormatter(formatter)

    # 将 uvicorn 的日志器也接入统一格式
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lgr = logging.getLogger(logger_name)
        lgr.handlers.clear()
        if _root_handler not in lgr.handlers:
            lgr.addHandler(_root_handler)
        lgr.propagate = False

    set_log_level(level)


def get_logger(name: str = "aistudio") -> logging.Logger:
    """获取遵循统一命名空间的日志器实例。"""
    if not name.startswith("aistudio.") and name != "aistudio":
        full_name = f"aistudio.{name}"
    else:
        full_name = name
    return logging.getLogger(full_name)


dump_logger = get_logger("dump")

_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|cookie|set-cookie|token|secret|password|auth)"
)


def _mask_sensitive_value(key: str, val: str) -> str:
    if not val:
        return "***"
    k_lower = key.lower()
    if "authorization" in k_lower:
        return val[:12] + "..." if len(val) > 12 else "***"
    if _SENSITIVE_FIELD_RE.search(k_lower):
        return val[:6] + "..." if len(val) > 6 else "***"
    return val


def _mask_headers(h: dict[str, str] | None) -> dict[str, str]:
    if not h:
        return {}
    return {k: _mask_sensitive_value(k, str(v)) for k, v in h.items()}


def _mask_query_params(qp: dict[str, str] | None) -> dict[str, str]:
    if not qp:
        return {}
    return {k: _mask_sensitive_value(k, str(v)) for k, v in qp.items()}


def dump_request_exchange(
    *,
    req_id: str,
    method: str,
    url: str,
    client: str,
    headers: dict[str, str],
    query_params: dict[str, str],
    body_text: str | None,
    status_code: int,
    elapsed_ms: float,
    response_headers: dict[str, str] | None = None,
    response_text: str | None = None,
    is_stream: bool = False,
) -> None:
    """转储单次完整请求与响应详细信息。"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    safe_headers = _mask_headers(headers)
    safe_query_params = _mask_query_params(query_params)
    header_lines = "\n".join(f"    {k}: {v}" for k, v in safe_headers.items())

    # 格式化请求体
    pretty_body = ""
    if body_text:
        try:
            parsed = json.loads(body_text)
            pretty_body = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            pretty_body = body_text
        if len(pretty_body) > 4000:
            pretty_body = (
                pretty_body[:4000] + f"\n... [truncated, total {len(body_text)} chars]"
            )
    else:
        pretty_body = "(空请求体)"
    # 格式化响应体
    pretty_resp = ""
    if is_stream:
        pretty_resp = f"(流式传输中，已开始分发，截至首包耗时 {elapsed_ms:.1f}ms)"
    elif response_text:
        try:
            parsed = json.loads(response_text)
            pretty_resp = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            pretty_resp = response_text
        if len(pretty_resp) > 4000:
            pretty_resp = (
                pretty_resp[:4000]
                + f"\n... [truncated, total {len(response_text)} chars]"
            )
    else:
        pretty_resp = "(空响应体)"
    safe_resp_headers = _mask_headers(response_headers)
    resp_header_lines = ""
    if response_headers:
        resp_header_lines = (
            "响应头:\n"
            + "\n".join(f"    {k}: {v}" for k, v in safe_resp_headers.items())
            + "\n"
        )
    dump_file_path: Path | None = None
    if is_dump_to_file_enabled():
        try:
            dump_folder = get_dump_dir()
            clean_id = re.sub(r"[^\w\-.]", "_", req_id)
            dump_file_path = dump_folder / f"{clean_id}.json"

            parsed_body = None
            if body_text:
                try:
                    parsed_body = json.loads(body_text)
                except Exception:
                    parsed_body = body_text

            parsed_resp = None
            if response_text:
                try:
                    parsed_resp = json.loads(response_text)
                except Exception:
                    parsed_resp = response_text

            record_data = {
                "req_id": req_id,
                "timestamp": timestamp,
                "iso_timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "method": method,
                "url": url,
                "client": client,
                "headers": headers,
                "safe_headers": safe_headers,
                "query_params": query_params,
                "safe_query_params": safe_query_params,
                "body": parsed_body,
                "raw_body": body_text,
                "status_code": status_code,
                "elapsed_ms": elapsed_ms,
                "is_stream": is_stream,
                "response_headers": response_headers if response_headers else {},
                "safe_response_headers": safe_resp_headers if response_headers else {},
                "response_body": parsed_resp,
                "raw_response_body": response_text,
            }

            serialized = json.dumps(record_data, ensure_ascii=False, indent=2)
            dump_file_path.write_text(serialized, encoding="utf-8")

            latest_file = dump_folder / "latest_request.json"
            latest_file.write_text(serialized, encoding="utf-8")
        except Exception as e:
            dump_logger.warning("请求文件转储失败: %s", e)

    file_line = f"转储文件:     {dump_file_path}\n" if dump_file_path else ""
    dump_msg = (
        f"\n==================== [请求报文转储: {req_id}] ====================\n"
        f"记录时间:     {timestamp}\n"
        f"客户端:       {client}\n"
        f"请求接口:     {method} {url}\n"
        f"{file_line}"
        f"查询参数:     {safe_query_params if safe_query_params else '(无)'}\n"
        f"请求头:\n{header_lines}\n"
        f"请求体:\n{pretty_body}\n"
        f"-------------------- [响应报文转储: {req_id}] --------------------\n"
        f"响应状态:     HTTP {status_code}\n"
        f"处理耗时:     {elapsed_ms:.2f} ms\n"
        f"{resp_header_lines}"
        f"响应内容:\n{pretty_resp}\n"
        f"==================== [转储结束: {req_id}] ===================="
    )

    dump_logger.info(dump_msg)
