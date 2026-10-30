"""动态模型发现与元数据获取服务。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aistudio_api.infrastructure.browser.cdp_client import CDPPage
    from aistudio_api.infrastructure.gateway.session import BrowserSession

from aistudio_api.infrastructure.account.cookie_parser import (
    DEFAULT_API_KEY,
    DEFAULT_EXT_BIN,
    DEFAULT_ORIGIN,
    DEFAULT_USER_AGENT,
    calculate_sapisid_hash,
    get_tier_header_value,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("model_discovery")


# ListModels field 65 (index 64) 主能力码语义映射
MODEL_CAPABILITY_NAMES: dict[int, str] = {
    1: "chat_model",
    9: "code_execution",
    10: "function_declarations",
    12: "google_search",
    13: "url_context",
    20: "video_route",
    21: "image_route",
    25: "thinking",
    26: "live_route",
    35: "thinking_budget",
    37: "speech_route",
    43: "media_resolution",
    46: "speech_translation",
    47: "aspect_ratio",
    48: "code_assistant",
    49: "output_resolution",
    52: "thinking_level",
    53: "music_route",
    54: "image_search",
    58: "google_maps",
    59: "interaction_route",
    85: "speech_metadata",
    91: "output_continuation",
}


def _extract_int_list(raw: object) -> list[int]:
    if not isinstance(raw, list):
        return []
    res: list[int] = []
    for item in raw:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            res.append(item)
        elif isinstance(item, float) and item.is_integer():
            res.append(int(item))
    return res


def _extract_model_aliases(raw: object) -> list[str]:
    if not isinstance(raw, list) or not raw:
        return []
    if isinstance(raw[0], str):
        return [
            s.removeprefix("models/") for s in raw if isinstance(s, str) and s.strip()
        ]
    aliases: list[str] = []
    for item in raw:
        if (
            isinstance(item, list)
            and item
            and isinstance(item[0], str)
            and item[0].strip()
        ):
            aliases.append(item[0].removeprefix("models/"))
    return aliases


# 本地保底默认模型列表（当网络未就绪或未配置账号时使用）
_FALLBACK_MODELS = [
    {
        "id": "gemini-3.8-flash",
        "displayName": "Gemini 3.8 Flash",
        "description": "Latest Gemini 3.8 Flash model",
    },
    {
        "id": "gemini-3.7-flash",
        "displayName": "Gemini 3.7 Flash",
        "description": "Gemini 3.7 Flash default text model",
    },
    {
        "id": "gemini-3.5-flash-lite",
        "displayName": "Gemini 3.5 Flash Lite",
        "description": "Ultra fast and lightweight model",
    },
    {
        "id": "gemini-3.1-pro-preview",
        "displayName": "Gemini 3.1 Pro Preview",
        "description": "Pro capabilities with complex reasoning",
    },
    {
        "id": "gemini-3.6-flash",
        "displayName": "Gemini 3.6 Flash",
        "description": "High throughput flash model",
    },
    {
        "id": "gemini-3.5-flash",
        "displayName": "Gemini 3.5 Flash",
        "description": "Balanced high-performance flash model",
    },
    {
        "id": "gemini-3.1-flash-image",
        "displayName": "Nano Banana 2",
        "description": "Image generation and multimodal editing",
    },
    {
        "id": "gemini-3-pro-image",
        "displayName": "Nano Banana Pro",
        "description": "High-fidelity multimodal generation",
    },
    {
        "id": "gemma-4-31b-it",
        "displayName": "Gemma 4 31B IT",
        "description": "Open weights flagship text model",
    },
    {
        "id": "gemma-4-26b-a4b-it",
        "displayName": "Gemma 4 26B A4B IT",
        "description": "Open weights instruction tuned model",
    },
    {
        "id": "veo-3.1-generate-preview",
        "displayName": "Veo 3.1",
        "description": "Video generation preview model",
    },
    {
        "id": "gemini-pro-latest",
        "displayName": "Gemini Pro Latest",
        "description": "Latest stable Gemini Pro alias",
    },
    {
        "id": "gemini-flash-latest",
        "displayName": "Gemini Flash Latest",
        "description": "Latest stable Gemini Flash alias",
    },
    {
        "id": "gemini-flash-lite-latest",
        "displayName": "Gemini Flash-Lite Latest",
        "description": "Latest stable Gemini Flash Lite alias",
    },
]


class ModelDiscoveryService:
    """动态获取 Google AI Studio 支持的模型列表（带缓存与保底）。"""

    def __init__(self, cache_ttl_seconds: int = 600) -> None:
        self._cache_ttl = cache_ttl_seconds
        self._cached_models: list[dict[str, object]] = []
        self._last_fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_models(
        self,
        *,
        force_refresh: bool = False,
        cookies: dict[str, str] | None = None,
        auth_user: str = "0",
        session: BrowserSession | None = None,
    ) -> list[dict[str, object]]:
        """获取可用模型列表。"""
        now = time.time()
        if (
            not force_refresh
            and self._cached_models
            and (now - self._last_fetched_at < self._cache_ttl)
        ):
            return self._cached_models

        async with self._lock:
            # 双重检查
            if (
                not force_refresh
                and self._cached_models
                and (time.time() - self._last_fetched_at < self._cache_ttl)
            ):
                return self._cached_models

            # 1. 优先通过当前激活的浏览器 Page 会话发 XHR 拉取 ListModels
            if session is not None:
                try:
                    page = getattr(session, "page", None) or getattr(
                        session, "_page", None
                    )
                    if page is not None and not page.is_closed():
                        models = await self._fetch_via_page(page)
                        if models:
                            self._cached_models = models
                            self._last_fetched_at = time.time()
                            logger.info(
                                "通过 Page XHR 动态更新了 %d 个模型", len(models)
                            )
                            return models
                except Exception as e:
                    logger.debug("通过页面 XHR 拉取模型列表失败: %s", e)

            # 2. 次选通过直接 HTTP 协议拉取
            effective_cookies = cookies
            effective_auth_user = auth_user
            effective_tier = "Free"
            if not effective_cookies:
                auth_file = (
                    getattr(session, "_auth_file", None)
                    if session is not None
                    else None
                )
                if not auth_file:
                    try:
                        from aistudio_api.infrastructure.account.account_store import (
                            AccountStore,
                        )

                        store = AccountStore()
                        active = store.get_active_account()
                        if active:
                            auth_path = store.get_auth_path_optional(
                                active.id, require_exists=True
                            )
                            auth_file = str(auth_path) if auth_path else None
                            effective_auth_user = active.auth_user or "0"
                            effective_tier = getattr(active, "tier", "Free") or "Free"
                    except Exception:
                        pass
                if auth_file and Path(auth_file).is_file():
                    try:
                        data = json.loads(Path(auth_file).read_text(encoding="utf-8"))
                        raw_cookies = data.get("cookies") or []
                        effective_cookies = {
                            str(c.get("name") or ""): str(c.get("value") or "")
                            for c in raw_cookies
                            if c.get("name")
                        }
                        if session is not None and hasattr(
                            session, "get_current_auth_user"
                        ):
                            effective_auth_user = session.get_current_auth_user()
                        if session is not None and hasattr(session, "get_current_tier"):
                            effective_tier = session.get_current_tier()
                    except Exception:
                        pass

            if effective_cookies:
                try:
                    models = await self._fetch_via_http(
                        effective_cookies,
                        effective_auth_user,
                        tier=effective_tier,
                    )
                    if models:
                        self._cached_models = models
                        self._last_fetched_at = time.time()
                        logger.info("通过 HTTP RPC 动态更新了 %d 个模型", len(models))
                        return models
                except Exception as e:
                    logger.debug("通过 HTTP 协议拉取模型列表失败: %s", e)
            # 3. 保底返回本地默认模型列表
            if not self._cached_models:
                self._cached_models = [dict(m) for m in _FALLBACK_MODELS]
                self._last_fetched_at = time.time()

            return self._cached_models

    async def get_model_by_id(
        self, model_id: str, session: BrowserSession | None = None
    ) -> Mapping[str, object] | None:
        """根据 model_id 或模型别名获取单个模型元数据（含保底匹配）。"""
        discovered = await self.get_models(session=session)
        clean_target = model_id.removeprefix("models/")
        for model_info in discovered:
            model_raw_id = str(model_info.get("id") or model_info.get("name") or "")
            model_clean_id = model_raw_id.removeprefix("models/")
            if model_clean_id == clean_target:
                return model_info
            raw_aliases = model_info.get("aliases")
            if isinstance(raw_aliases, list) and clean_target in raw_aliases:
                return model_info
        for model_info in _FALLBACK_MODELS:
            model_raw_id = str(model_info.get("id") or model_info.get("name") or "")
            model_clean_id = model_raw_id.removeprefix("models/")
            if model_clean_id == clean_target:
                return model_info
        return None

    def get_cached_model_info(self, model_id: str) -> Mapping[str, object] | None:
        """同步查询已缓存的模型元数据（不触发网络请求）。"""
        clean_target = model_id.removeprefix("models/").lower()
        for model_info in self._cached_models:
            model_raw_id = str(model_info.get("id") or model_info.get("name") or "")
            if model_raw_id.removeprefix("models/").lower() == clean_target:
                return model_info
            raw_aliases = model_info.get("aliases")
            if isinstance(raw_aliases, list) and any(
                str(a).lower() == clean_target for a in raw_aliases
            ):
                return model_info
        return None

    def is_model_allowed_for_tier(self, model_id: str, tier: str | None) -> bool:
        """根据 config.yaml 付费规则以及缓存的 ListModels accessModes 判断指定订阅等级是否可访问该模型。"""
        req_tiers = self.get_required_tiers(model_id)
        if req_tiers:
            norm_tier = (tier or "Free").strip()
            return norm_tier in req_tiers

        info = self.get_cached_model_info(model_id)
        if not info:
            return True
        raw_modes = info.get("accessModes")
        if not isinstance(raw_modes, list) or not raw_modes:
            return True
        norm_tier = (tier or "Free").strip().lower()
        is_high_tier = (
            norm_tier in ("plus", "pro", "ultra")
            or "ultra" in norm_tier
            or "pro" in norm_tier
        )
        is_ultra_tier = "ultra" in norm_tier
        for mode in raw_modes:
            if mode == 3 and is_high_tier:
                return True
            if mode == 4 and is_ultra_tier:
                return True
        # 如果 accessModes 只包含 1 (Paid API key) 且不含 3/4，对所有网页端等级放行尝试
        return all(m not in (3, 4) for m in raw_modes)

    def is_paid_model(self, model_id: str) -> bool:
        """判定目标模型是否为付费模型（由 config.yaml 的 paid_model_rules 严格驱动，结合 upstream accessModes）。"""
        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_paid_model_rules,
        )

        clean_m = model_id.removeprefix("models/").lower()
        rules = get_configured_paid_model_rules()
        for rule_cfg in rules.values():
            if not isinstance(rule_cfg, dict):
                continue
            contains = [
                str(c).lower()
                for c in (rule_cfg.get("contains") or [])
                if isinstance(c, str)
            ]
            prefixes = [
                str(p).lower()
                for p in (rule_cfg.get("prefixes") or [])
                if isinstance(p, str)
            ]
            exact = [
                str(e).removeprefix("models/").lower()
                for e in (rule_cfg.get("exact") or [])
                if isinstance(e, str)
            ]
            if (
                (exact and clean_m in exact)
                or any(clean_m.startswith(p) for p in prefixes)
                or any(c in clean_m for c in contains)
            ):
                return True

        info = self.get_cached_model_info(clean_m)
        if info:
            if info.get("paid") is True:
                return True
            modes = info.get("accessModes")
            if isinstance(modes, list) and modes and all(m in (3, 4) for m in modes):
                return True
            codes = info.get("capabilityCodes")
            if isinstance(codes, list) and 20 in codes:
                return True
        return False

    def get_required_tier(self, model_id: str) -> str:
        """获取目标模型所需的最低账号层级（兼容现有单层级调用）。"""
        tiers = self.get_required_tiers(model_id)
        if not tiers:
            return "Free"
        return tiers[0]

    def get_required_tiers(self, model_id: str) -> list[str]:
        """获取目标模型所允许调用的账号层级列表（由 upstream accessModes 与 config.yaml 共同驱动）。"""
        clean_m = model_id.removeprefix("models/").lower()
        info = self.get_cached_model_info(clean_m)
        if info:
            modes = info.get("accessModes")
            if isinstance(modes, list) and 4 in modes and 3 not in modes:
                return ["Ultra", "Ultra_20X"]

        from aistudio_api.infrastructure.gateway.model_defaults import (
            get_configured_paid_model_rules,
        )

        rules = get_configured_paid_model_rules()
        for rule_cfg in rules.values():
            if not isinstance(rule_cfg, dict):
                continue
            contains = [
                str(c).lower()
                for c in (rule_cfg.get("contains") or [])
                if isinstance(c, str)
            ]
            prefixes = [
                str(p).lower()
                for p in (rule_cfg.get("prefixes") or [])
                if isinstance(p, str)
            ]
            exact = [
                str(e).removeprefix("models/").lower()
                for e in (rule_cfg.get("exact") or [])
                if isinstance(e, str)
            ]
            if (
                (exact and clean_m in exact)
                or any(clean_m.startswith(p) for p in prefixes)
                or any(c in clean_m for c in contains)
            ):
                req = rule_cfg.get("required_tiers")
                if isinstance(req, list) and req:
                    return [str(t) for t in req]

        if info:
            modes = info.get("accessModes")
            if isinstance(modes, list) and 3 in modes:
                return ["Pro", "Ultra", "Ultra_20X"]
        return []

    async def _fetch_via_page(self, page: CDPPage) -> list[dict[str, object]]:
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
        if not isinstance(res, dict) or not res.get("ok"):
            return []
        raw_data = res.get("data")
        return self._parse_raw_models(raw_data)

    async def _fetch_via_http(
        self,
        cookies: dict[str, str],
        auth_user: str,
        *,
        tier: str = "Free",
    ) -> list[dict[str, object]]:
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
        tier_header = get_tier_header_value(tier)
        if tier_header:
            headers["X-AIStudio-G1-Tier"] = tier_header

        from aistudio_api.config import settings

        url = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels"
        async with httpx.AsyncClient(timeout=10.0, proxy=settings.proxy_url) as client:
            resp = await client.post(url, headers=headers, content="[]")
            if resp.status_code == 200:
                return self._parse_raw_models(resp.json())
        return []

    def _parse_raw_models(self, raw_data: object) -> list[dict[str, object]]:
        """解析 Google ListModels 的原始 protobuf 数组结构。"""
        if (
            not isinstance(raw_data, list)
            or not raw_data
            or not isinstance(raw_data[0], list)
        ):
            return []

        models: list[dict[str, object]] = []
        for item in raw_data[0]:
            if not isinstance(item, list) or not item or not isinstance(item[0], str):
                continue
            raw_id = item[0]  # e.g. "models/gemini-3.7-flash"
            model_id = raw_id.replace("models/", "")
            version = (
                str(item[2]).strip()
                if len(item) > 2 and isinstance(item[2], str) and item[2].strip()
                else "001"
            )
            display_name = (
                item[3]
                if len(item) > 3 and isinstance(item[3], str) and item[3].strip()
                else model_id
            )
            description = item[4] if len(item) > 4 and isinstance(item[4], str) else ""
            input_token_limit = (
                item[5] if len(item) > 5 and isinstance(item[5], (int, float)) else 0
            )
            output_token_limit = (
                item[6] if len(item) > 6 and isinstance(item[6], (int, float)) else 0
            )
            methods = item[7] if len(item) > 7 and isinstance(item[7], list) else []
            supported_methods = [str(m) for m in methods if isinstance(m, str)]
            temperature = (
                float(item[8])
                if len(item) > 8
                and isinstance(item[8], (int, float))
                and not isinstance(item[8], bool)
                else 1.0
            )
            top_p = (
                float(item[9])
                if len(item) > 9
                and isinstance(item[9], (int, float))
                and not isinstance(item[9], bool)
                else 0.95
            )
            top_k = (
                int(item[10])
                if len(item) > 10
                and isinstance(item[10], (int, float))
                and not isinstance(item[10], bool)
                else 64
            )
            aliases = _extract_model_aliases(item[56]) if len(item) > 56 else []
            capability_codes = _extract_int_list(item[64]) if len(item) > 64 else []
            capabilities = [
                MODEL_CAPABILITY_NAMES[c]
                for c in capability_codes
                if c in MODEL_CAPABILITY_NAMES
            ]
            default_thinking_level: int | None = None
            thinking_levels: list[int] = []
            if len(item) > 71 and isinstance(item[71], list):
                t_cfg = item[71]
                if (
                    len(t_cfg) > 5
                    and isinstance(t_cfg[5], int)
                    and not isinstance(t_cfg[5], bool)
                ):
                    default_thinking_level = t_cfg[5]
                if len(t_cfg) > 6:
                    thinking_levels = _extract_int_list(t_cfg[6])
            paid = bool(len(item) > 77 and item[77] == 2)
            access_modes = _extract_int_list(item[82]) if len(item) > 82 else []

            # 过滤支持内容生成的模型
            if not any("generateContent" in str(m) for m in methods) and not any(
                "image" in model_id or "veo" in model_id or "lyria" in model_id
                for _ in [0]
            ):
                continue

            is_image = (
                21 in capability_codes or "image" in model_id or "imagen" in model_id
            )
            is_video = 20 in capability_codes or "veo" in model_id
            is_audio = (
                37 in capability_codes
                or 53 in capability_codes
                or "lyria" in model_id
                or "tts" in model_id
            )

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

            models.append(
                {
                    "id": model_id,
                    "name": raw_id,
                    "version": version,
                    "displayName": display_name,
                    "description": description,
                    "category": category,
                    "inputTokenLimit": int(input_token_limit),
                    "outputTokenLimit": int(output_token_limit),
                    "supportedGenerationMethods": supported_methods
                    or [
                        "generateContent",
                        "countTokens",
                        "createCachedContent",
                    ],
                    "temperature": temperature,
                    "topP": top_p,
                    "topK": top_k,
                    "aliases": aliases,
                    "capabilities": capabilities,
                    "capabilityCodes": capability_codes,
                    "defaultThinkingLevel": default_thinking_level,
                    "thinkingLevels": thinking_levels,
                    "paid": paid,
                    "accessModes": access_modes,
                    "is_image_model": is_image,
                    "is_video_model": is_video,
                    "is_audio_model": is_audio,
                }
            )

        return models


# 单例实例
model_discovery = ModelDiscoveryService()
