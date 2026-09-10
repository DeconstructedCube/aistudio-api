"""动态模型发现与元数据获取服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any

from aistudio_api.config import settings
from aistudio_api.infrastructure.account.cookie_parser import (
    DEFAULT_API_KEY,
    DEFAULT_EXT_BIN,
    DEFAULT_ORIGIN,
    DEFAULT_USER_AGENT,
    calculate_sapisid_hash,
)

logger = logging.getLogger("aistudio.model_discovery")

# 本地保底默认模型列表（当网络未就绪或未配置账号时使用）
_FALLBACK_MODELS = [
    {"id": "gemini-3.8-flash", "displayName": "Gemini 3.8 Flash", "description": "Latest Gemini 3.8 Flash model"},
    {"id": "gemini-3.7-flash", "displayName": "Gemini 3.7 Flash", "description": "Gemini 3.7 Flash default text model"},
    {"id": "gemini-3.5-flash-lite", "displayName": "Gemini 3.5 Flash Lite", "description": "Ultra fast and lightweight model"},
    {"id": "gemini-3.1-pro-preview", "displayName": "Gemini 3.1 Pro Preview", "description": "Pro capabilities with complex reasoning"},
    {"id": "gemini-3.6-flash", "displayName": "Gemini 3.6 Flash", "description": "High throughput flash model"},
    {"id": "gemini-3.5-flash", "displayName": "Gemini 3.5 Flash", "description": "Balanced high-performance flash model"},
    {"id": "gemini-3.1-flash-image", "displayName": "Nano Banana 2", "description": "Image generation and multimodal editing"},
    {"id": "gemini-3-pro-image", "displayName": "Nano Banana Pro", "description": "High-fidelity multimodal generation"},
    {"id": "gemma-4-31b-it", "displayName": "Gemma 4 31B IT", "description": "Open weights flagship text model"},
    {"id": "gemma-4-26b-a4b-it", "displayName": "Gemma 4 26B A4B IT", "description": "Open weights instruction tuned model"},
    {"id": "veo-3.1-generate-preview", "displayName": "Veo 3.1", "description": "Video generation preview model"},
    {"id": "gemini-pro-latest", "displayName": "Gemini Pro Latest", "description": "Latest stable Gemini Pro alias"},
    {"id": "gemini-flash-latest", "displayName": "Gemini Flash Latest", "description": "Latest stable Gemini Flash alias"},
    {"id": "gemini-flash-lite-latest", "displayName": "Gemini Flash-Lite Latest", "description": "Latest stable Gemini Flash Lite alias"},
]


class ModelDiscoveryService:
    """动态获取 Google AI Studio 支持的模型列表（带缓存与保底）。"""

    def __init__(self, cache_ttl_seconds: int = 600) -> None:
        self._cache_ttl = cache_ttl_seconds
        self._cached_models: list[dict[str, Any]] = []
        self._last_fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_models(
        self,
        *,
        force_refresh: bool = False,
        cookies: dict[str, str] | None = None,
        auth_user: str = "0",
        session: Any = None,
    ) -> list[dict[str, Any]]:
        """获取可用模型列表。"""
        now = time.time()
        if not force_refresh and self._cached_models and (now - self._last_fetched_at < self._cache_ttl):
            return self._cached_models

        async with self._lock:
            # 双重检查
            if not force_refresh and self._cached_models and (time.time() - self._last_fetched_at < self._cache_ttl):
                return self._cached_models

            # 1. 优先通过当前激活的浏览器 Page 会话发 XHR 拉取 ListModels
            if session is not None:
                try:
                    page = getattr(session, "_page", None)
                    if page is not None and not page.is_closed():
                        models = await self._fetch_via_page(page)
                        if models:
                            self._cached_models = models
                            self._last_fetched_at = time.time()
                            logger.info("通过 Page XHR 动态更新了 %d 个模型", len(models))
                            return models
                except Exception as e:
                    logger.debug("Fetch models via page failed: %s", e)

            # 2. 次选通过直接 HTTP 协议拉取
            if cookies:
                try:
                    models = await self._fetch_via_http(cookies, auth_user)
                    if models:
                        self._cached_models = models
                        self._last_fetched_at = time.time()
                        logger.info("通过 HTTP RPC 动态更新了 %d 个模型", len(models))
                        return models
                except Exception as e:
                    logger.debug("Fetch models via HTTP failed: %s", e)

            # 3. 保底返回本地默认模型列表
            if not self._cached_models:
                self._cached_models = [dict(m) for m in _FALLBACK_MODELS]
                self._last_fetched_at = time.time()

            return self._cached_models

    async def _fetch_via_page(self, page: Any) -> list[dict[str, Any]]:
        """在 CDP Page 环境中直接执行 XHR 拉取 ListModels（自动带齐浏览器完整凭据）。"""
        script = """
        () => {
            return new Promise((resolve) => {
                const xhr = new XMLHttpRequest();
                xhr.open('POST', 'https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels');
                xhr.setRequestHeader('Content-Type', 'application/json+protobuf');
                xhr.setRequestHeader('X-User-Agent', 'grpc-web-javascript/0.1');
                xhr.setRequestHeader('X-Goog-Api-Key', 'AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs');
                xhr.setRequestHeader('X-Goog-Ext-519733851-bin', 'CAESAUwwATgEQABQBGICSlBwAHgBkAEAmAEB');
                xhr.withCredentials = true;
                xhr.timeout = 10000;
                xhr.onload = () => {
                    if (xhr.status === 200) {
                        try {
                            resolve({ok: true, data: JSON.parse(xhr.responseText)});
                        } catch(e) {
                            resolve({ok: false, error: String(e)});
                        }
                    } else {
                        resolve({ok: false, status: xhr.status});
                    }
                };
                xhr.onerror = () => resolve({ok: false, error: 'network_error'});
                xhr.ontimeout = () => resolve({ok: false, error: 'timeout'});
                xhr.send('[]');
            });
        }
        """
        res = await page.evaluate(script, timeout_s=12.0)
        if not res or not res.get("ok"):
            return []
        raw_data = res.get("data")
        return self._parse_raw_models(raw_data)

    async def _fetch_via_http(self, cookies: dict[str, str], auth_user: str) -> list[dict[str, Any]]:
        """通过 HTTP 客户端携带 SAPISIDHASH 拉取。"""
        import httpx

        auth_header = calculate_sapisid_hash(cookies)
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers = {
            "Content-Type": "application/json+protobuf",
            "X-User-Agent": DEFAULT_USER_AGENT,
            "X-Goog-Api-Key": DEFAULT_API_KEY,
            "X-Goog-AuthUser": auth_user or "0",
            "X-Goog-Ext-519733851-bin": DEFAULT_EXT_BIN,
            "X-AIStudio-Visit-Id": f"v1_{uuid.uuid4()}",
            "Origin": DEFAULT_ORIGIN,
            "Referer": f"{DEFAULT_ORIGIN}/",
            "Cookie": cookie_header,
        }
        if auth_header:
            headers["Authorization"] = auth_header

        url = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, content="[]")
            if resp.status_code == 200:
                return self._parse_raw_models(resp.json())
        return []

    def _parse_raw_models(self, raw_data: Any) -> list[dict[str, Any]]:
        """解析 Google ListModels 的原始 protobuf 数组结构。"""
        if not isinstance(raw_data, list) or not raw_data or not isinstance(raw_data[0], list):
            return []

        models: list[dict[str, Any]] = []
        for item in raw_data[0]:
            if not isinstance(item, list) or not item or not isinstance(item[0], str):
                continue
            raw_id = item[0]  # e.g. "models/gemini-3.7-flash"
            model_id = raw_id.replace("models/", "")
            display_name = item[3] if len(item) > 3 and isinstance(item[3], str) and item[3].strip() else model_id
            description = item[4] if len(item) > 4 and isinstance(item[4], str) else ""
            input_token_limit = item[5] if len(item) > 5 and isinstance(item[5], (int, float)) else 0
            output_token_limit = item[6] if len(item) > 6 and isinstance(item[6], (int, float)) else 0
            methods = item[7] if len(item) > 7 and isinstance(item[7], list) else []

            # 过滤支持内容生成的模型
            if not any("generateContent" in str(m) for m in methods) and not any("image" in model_id or "veo" in model_id or "lyria" in model_id for _ in [0]):
                continue

            is_image = "image" in model_id or "imagen" in model_id
            is_video = "veo" in model_id
            is_audio = "lyria" in model_id or "tts" in model_id

            category = "flagship"
            if is_image:
                category = "image"
            elif is_video:
                category = "video"
            elif is_audio:
                category = "audio"
            elif "lite" in model_id or "8b" in model_id or "flash" in model_id:
                category = "flash"
            elif "gemma" in model_id:
                category = "gemma"

            models.append({
                "id": model_id,
                "name": raw_id,
                "displayName": display_name,
                "description": description,
                "category": category,
                "inputTokenLimit": int(input_token_limit),
                "outputTokenLimit": int(output_token_limit),
                "is_image_model": is_image,
                "is_video_model": is_video,
                "is_audio_model": is_audio,
            })

        return models


# 单例实例
model_discovery = ModelDiscoveryService()
