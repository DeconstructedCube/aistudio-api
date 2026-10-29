"""解析浏览器 cookie（支持 JSON 数组、Netscape 格式与 Header KV），并提供 Google 多账号探活。"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Iterable

from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("cookie_parser")

DEFAULT_API_KEY = "AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs"
DEFAULT_EXT_BIN = "CAESAUwwATgEQABQBGICSlBwAHgBkAEAmAEB"
DEFAULT_ORIGIN = "https://aistudio.google.com"
DEFAULT_USER_AGENT = "grpc-web-javascript/0.1"


BENEFIT_TIER_TO_NAME: dict[int, str] = {
    0: "Free",
    1: "Pro",
    2: "Ultra",
    3: "Plus",
}

BENEFIT_TIER_TO_HEADER: dict[str, str] = {
    "Pro": "TIER1",
    "Ultra": "TIER2",
    "Ultra_20X": "TIER2",
    "Plus": "TIER0",
}

_CANONICAL_TIERS: dict[str, str] = {
    "free": "Free",
    "plus": "Plus",
    "pro": "Pro",
    "ultra": "Ultra",
    "ultra_20x": "Ultra_20X",
}


def normalize_benefit_tier(tier: str | None) -> str:
    """Normalize benefit tier string into canonical name (Free / Plus / Pro / Ultra / Ultra_20X)."""
    if not tier:
        return "Free"
    return _CANONICAL_TIERS.get(tier.strip().lower(), "Free")


def parse_benefit_tier_response(raw_data: object) -> str:
    """解析 GetAiStudioBenefitTier 响应数组为标准权益名称 (Free / Pro / Ultra / Plus)。"""
    if isinstance(raw_data, str):
        text = raw_data.strip().removeprefix(")]}'").strip()
        if text.startswith("[,"):
            text = "[null," + text[2:]
        try:
            raw_data = json.loads(text)
        except Exception:
            return "Free"
    if not isinstance(raw_data, list) or not raw_data or raw_data[0] is None:
        return "Free"
    val = raw_data[0]
    if isinstance(val, bool):
        return "Free"
    if isinstance(val, (int, float)):
        return BENEFIT_TIER_TO_NAME.get(int(val), "Free")
    return "Free"


def get_tier_header_value(tier: str | None) -> str | None:
    """根据账号权益等级返回对应的 X-AIStudio-G1-Tier 请求头取值。"""
    if not tier:
        return None
    clean = normalize_benefit_tier(tier)
    return BENEFIT_TIER_TO_HEADER.get(clean)


# Cookies that need httpOnly=False so JS can read them for SAPISIDHASH.
_AUTH_COOKIE_NAMES = {
    "SID",
    "APISID",
    "SAPISID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
}

_DOMAIN_OVERRIDES: dict[str, list[str]] = {
    "OSID": ["aistudio.google.com"],
    "__Secure-OSID": ["aistudio.google.com"],
    "__Secure-BUCKET": ["aistudio.google.com"],
    "OTZ": ["accounts.google.com"],
    "__Host-GAPS": ["accounts.google.com"],
    "__Host-1PLSID": ["accounts.google.com"],
    "__Host-3PLSID": ["accounts.google.com"],
    "LSID": ["accounts.google.com"],
    "SMSV": ["accounts.google.com"],
    "LSOLH": ["accounts.google.com"],
    "ACCOUNT_CHOOSER": ["accounts.google.com"],
    "__Secure-1PSIDTS": [".google.com", "aistudio.google.com", "accounts.google.com"],
    "__Secure-3PSIDTS": [".google.com", "aistudio.google.com", "accounts.google.com"],
}

# 外部导入时可安全丢弃的遥测、易失效挑战及旧环境绑定的临时 Cookie。
# 过滤这些 Cookie 既能让网页在当前 IP/环境重新自然协商下发全新凭据，
# 又能精简内存与 Cookie 存储体积（尤其对 Android 低内存环境极为友好）：
DISCARDABLE_IMPORT_COOKIE_NAMES: frozenset[str] = frozenset(
    {
        "_ga",
        "_gid",
        "_gat",
        "_gcl_au",
        "1P_JAR",
        "DV",
        "AEC",
        "NID",
        "SIDCC",
        "__Secure-1PSIDCC",
        "__Secure-3PSIDCC",
        "OSID",
        "__Secure-OSID",
    }
)


def parse_raw_cookies(raw: object) -> dict[str, str]:
    """万能 Cookie 解析器，支持：
    1. JSON 数组（EditThisCookie / Cookie-Editor 导出：`[{"name": "...", "value": "..."}, ...]`）
    2. JSON 对象（`{"cookies": [...]}` 或 `{"cookies": {"k": "v"}}`）
    3. Netscape / curl 格式（7 列 Tab 分隔）
    4. HTTP 请求头格式（`Cookie: k1=v1; k2=v2` 或带换行的多行键值对）
    """
    parsed: dict[str, str] = {}
    if not raw:
        return parsed

    text = raw.strip() if isinstance(raw, str) else json.dumps(raw)

    if re.match(r"^cookie:\s*", text, re.IGNORECASE):
        text = re.sub(r"^cookie:\s*", "", text, flags=re.IGNORECASE)

    # 1. 尝试 JSON 格式
    if text.startswith(("{", "[")):
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                for item in obj:
                    if (
                        isinstance(item, dict)
                        and item.get("name")
                        and item.get("value") is not None
                    ):
                        k = str(item["name"]).strip()
                        v = str(item["value"]).strip()
                        if k and v:
                            parsed[k] = v
                if parsed:
                    return parsed
            elif isinstance(obj, dict):
                if isinstance(obj.get("cookie"), str):
                    text = obj["cookie"]
                elif isinstance(obj.get("cookies"), str):
                    text = obj["cookies"]
                elif isinstance(obj.get("cookies"), list):
                    for item in obj["cookies"]:
                        if (
                            isinstance(item, dict)
                            and item.get("name")
                            and item.get("value") is not None
                        ):
                            k = str(item["name"]).strip()
                            v = str(item["value"]).strip()
                            if k and v:
                                parsed[k] = v
                    if parsed:
                        return parsed
                elif isinstance(obj.get("cookies"), dict):
                    for k, v in obj["cookies"].items():
                        if k and v is not None:
                            parsed[str(k).strip()] = str(v).strip()
                    return parsed
        except Exception:
            pass

    # 2. 按行与分号拆分解析
    lines = re.split(r"[\r\n;]+", text)
    ignored_keys = {
        "domain",
        "path",
        "expires",
        "samesite",
        "secure",
        "httponly",
        "priority",
        "hostonly",
    }

    for line in lines:
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue

        # Netscape 7 列 Tab 分隔
        if "\t" in trimmed:
            cols = trimmed.split("\t")
            if len(cols) >= 7:
                k = cols[5].strip() if len(cols) > 5 else ""
                v = cols[6].strip() if len(cols) > 6 else ""
                if k and v and k.lower() not in ignored_keys:
                    parsed[k] = v
                continue

        # 标准 KV：name=value
        if "=" in trimmed:
            k, _, v = trimmed.partition("=")
            k = k.strip()
            v = v.strip()
            k = re.sub(r"^cookie:\s*", "", k, flags=re.IGNORECASE)
            if (v.startswith('"') and v.endswith('"')) or (
                v.startswith("'") and v.endswith("'")
            ):
                v = v[1:-1]
            if k and v and k.lower() not in ignored_keys:
                parsed[k] = v

    return parsed


def calculate_sapisid_hash(
    cookies: dict[str, str], origin: str = DEFAULT_ORIGIN
) -> str:
    """计算 Google SAPISIDHASH 鉴权头部。"""
    sapisid = cookies.get("SAPISID") or cookies.get("__Secure-1PAPISID") or ""
    sapisid1p = cookies.get("__Secure-1PAPISID") or sapisid
    sapisid3p = cookies.get("__Secure-3PAPISID") or sapisid
    if not sapisid:
        return ""

    ts = int(time.time())

    def calc_hash(val: str) -> str:
        return hashlib.sha1(f"{ts} {val} {origin}".encode()).hexdigest()

    h1 = f"SAPISIDHASH {ts}_{calc_hash(sapisid)}"
    h2 = f"SAPISID1PHASH {ts}_{calc_hash(sapisid1p)}"
    h3 = f"SAPISID3PHASH {ts}_{calc_hash(sapisid3p)}"
    return f"{h1} {h2} {h3}"


def build_google_cookie_list(
    pairs: Iterable[tuple[str, str]] | dict[str, str],
    *,
    allow_url_targets: bool = False,
    discard_transient: bool = False,
) -> list[dict[str, object]]:
    """Build Playwright/CDP compatible cookies from name/value pairs."""
    now = int(time.time())
    default_expires = now + 86400 * 180  # 180 天后过期

    seen: set[tuple[str, str, str]] = set()
    cookies: list[dict[str, object]] = []

    def _add_cookie(name: str, value: str, target: str) -> None:
        is_host_target = not target.startswith(".")

        if name.startswith("__Host-") and not allow_url_targets:
            return

        target_kind = "url" if allow_url_targets and is_host_target else "domain"
        target_value = f"https://{target}/" if target_kind == "url" else target
        key = (name, target_kind, target_value)
        if key in seen:
            return
        seen.add(key)

        cookie: dict[str, object] = {
            "name": name,
            "value": value,
            "secure": True,
            "httpOnly": name not in _AUTH_COOKIE_NAMES,
            "sameSite": "None",
            "expires": default_expires,
        }
        if target_kind == "url":
            cookie["url"] = target_value
        else:
            cookie["domain"] = target
            cookie["path"] = "/"
        cookies.append(cookie)

    item_list: list[tuple[str, str]] = (
        [(str(k), str(v)) for k, v in pairs.items()]
        if isinstance(pairs, dict)
        else [(str(k), str(v)) for k, v in pairs]
    )
    for name, value in item_list:
        if discard_transient and (
            name in DISCARDABLE_IMPORT_COOKIE_NAMES
            or name.startswith(("_ga_", "_gcl_"))
        ):
            continue
        targets = _DOMAIN_OVERRIDES.get(name)
        if not targets:
            targets = [".google.com"]
        for target in targets:
            _add_cookie(name, value, target)

    return cookies


def parse_cookie_string(
    raw: object, *, discard_transient: bool = True
) -> dict[str, object]:
    """将任意格式 cookie 解析为 storage state dict，包含 cookies 和 origins 字段。"""
    cookie_dict = parse_raw_cookies(raw)
    return {
        "cookies": build_google_cookie_list(
            cookie_dict, allow_url_targets=False, discard_transient=discard_transient
        ),
        "origins": [],
    }


def parse_emails_from_ogs_html(
    html_text: str, default_auth_user: str = "0"
) -> dict[str, str]:
    """从 Google OGS Widget 或 AI Studio HTML 中提取 u/{index} 对应的真实邮箱字典。"""
    mapping: dict[str, str] = {}
    if not html_text:
        return mapping
    m_primary = re.search(r'"oPEP7c"\s*:\s*"([^"@\s]+@[^"\s]+)"', html_text)
    if m_primary:
        mapping[str(default_auth_user)] = m_primary.group(1).strip().lower()
    for m in re.finditer(
        r'data-au="(\d+)"\s+data-email="([^"@\s]+@[^"\s]+)"', html_text
    ):
        mapping[m.group(1)] = m.group(2).strip().lower()
    for m in re.finditer(
        r'data-email="([^"@\s]+@[^"\s]+)"\s+data-au="(\d+)"', html_text
    ):
        mapping[m.group(2)] = m.group(1).strip().lower()
    return mapping


async def probe_account_emails_map(
    raw_or_dict: object,
    auth_user: str = "0",
) -> dict[str, str]:
    """通过 Google OneGoogleWidget / AI Studio 页面探测全部已登录子账号的邮箱映射。"""
    cookie_dict = (
        raw_or_dict
        if isinstance(raw_or_dict, dict)
        and all(isinstance(v, str) for v in raw_or_dict.values())
        else parse_raw_cookies(raw_or_dict)
    )
    if not cookie_dict:
        return {}

    import httpx

    from aistudio_api.config import settings

    u_index = str(auth_user or "0")
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Cookie": cookie_header,
    }
    try:
        async with httpx.AsyncClient(
            timeout=8.0, proxy=settings.proxy_url, follow_redirects=True
        ) as client:
            resp = await client.get(
                f"https://ogs.google.com/u/{u_index}/widget/account",
                headers=headers,
            )
            if resp.status_code == 200:
                emails = parse_emails_from_ogs_html(resp.text, u_index)
                if emails:
                    return emails
            resp_ai = await client.get(
                f"https://aistudio.google.com/u/{u_index}/prompts/new_chat",
                headers=headers,
            )
            if resp_ai.status_code == 200:
                return parse_emails_from_ogs_html(resp_ai.text, u_index)
    except Exception as e:
        logger.debug("通过 HTTP 探测子账号 u/%s 邮箱失败: %s", u_index, e)
    return {}


async def probe_account_email(
    raw_or_dict: object,
    auth_user: str = "0",
) -> str | None:
    """探测单个指定 auth_user 的登录邮箱。"""
    u_index = str(auth_user or "0")
    emails_map = await probe_account_emails_map(raw_or_dict, auth_user=u_index)
    if u_index in emails_map:
        return emails_map[u_index]
    if u_index != "0":
        root_map = await probe_account_emails_map(raw_or_dict, auth_user="0")
        if u_index in root_map:
            return root_map[u_index]
    return None


async def probe_account_benefit_tier(
    raw_or_dict: object,
    auth_user: str = "0",
) -> str:
    """通过 GetAiStudioBenefitTier RPC 探测指定子账号的订阅权益等级。"""
    cookie_dict = (
        raw_or_dict
        if isinstance(raw_or_dict, dict)
        and all(isinstance(v, str) for v in raw_or_dict.values())
        else parse_raw_cookies(raw_or_dict)
    )
    if not cookie_dict:
        return "Free"

    import httpx

    from aistudio_api.config import settings

    u_index = str(auth_user or "0")
    auth_header = calculate_sapisid_hash(cookie_dict)
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    headers = {
        "Content-Type": "application/json+protobuf",
        "X-User-Agent": DEFAULT_USER_AGENT,
        "X-Goog-Api-Key": DEFAULT_API_KEY,
        "X-Goog-AuthUser": u_index,
        "X-Goog-Ext-519733851-bin": DEFAULT_EXT_BIN,
        "X-AIStudio-Visit-Id": f"v1_{uuid.uuid4()}",
        "Origin": DEFAULT_ORIGIN,
        "Referer": f"{DEFAULT_ORIGIN}/",
        "Cookie": cookie_header,
    }
    if auth_header:
        headers["Authorization"] = auth_header

    tier_url = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetAiStudioBenefitTier"
    try:
        async with httpx.AsyncClient(timeout=8.0, proxy=settings.proxy_url) as client:
            resp = await client.post(tier_url, headers=headers, content="[]")
            if resp.status_code == 200:
                return parse_benefit_tier_response(resp.text)
    except Exception as e:
        logger.debug("探测子账号 u/%s 权益等级失败: %s", u_index, e)
    return "Free"


async def probe_google_accounts_infinite(
    raw_or_dict: object,
    *,
    max_fails_in_a_row: int = 1,
) -> list[dict[str, object]]:
    """一路向下探测多账号登录索引（u/0, u/1, u/2...），直到探测失败为止。

    Returns:
        有效账号列表，例如 [{"auth_user": "0", "status_code": 200, "name": "Google Account (u/0)"}, ...]
    """
    cookie_dict = parse_raw_cookies(raw_or_dict)
    if not cookie_dict:
        return []

    import httpx

    from aistudio_api.config import settings

    cookie_header = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
    valid_accounts: list[dict[str, object]] = []

    auth_user_idx = 0
    consecutive_failures = 0
    emails_map = await probe_account_emails_map(cookie_dict, auth_user="0")
    async with httpx.AsyncClient(timeout=10.0, proxy=settings.proxy_url) as client:
        while True:
            u_index = str(auth_user_idx)
            auth_header = calculate_sapisid_hash(cookie_dict)
            headers = {
                "Content-Type": "application/json+protobuf",
                "X-User-Agent": DEFAULT_USER_AGENT,
                "X-Goog-Api-Key": DEFAULT_API_KEY,
                "X-Goog-AuthUser": u_index,
                "X-Goog-Ext-519733851-bin": DEFAULT_EXT_BIN,
                "X-AIStudio-Visit-Id": f"v1_{uuid.uuid4()}",
                "Origin": DEFAULT_ORIGIN,
                "Referer": f"{DEFAULT_ORIGIN}/",
                "Cookie": cookie_header,
            }
            if auth_header:
                headers["Authorization"] = auth_header

            url = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/ListModels"
            try:
                resp = await client.post(url, headers=headers, content="[]")
                # 200 (OK), 400 (Bad Request on param but authed), 403 (Forbidden/Permission/Quota) 均表示此账号 Session 存在
                # 401 表示该 auth_user 未登录或 Session 无效
                if resp.status_code in (200, 400, 403):
                    consecutive_failures = 0
                    detected_tier = "Free"
                    tier_url = "https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetAiStudioBenefitTier"
                    try:
                        tier_resp = await client.post(
                            tier_url, headers=headers, content="[]"
                        )
                        if tier_resp.status_code == 200:
                            detected_tier = parse_benefit_tier_response(tier_resp.text)
                    except Exception:
                        pass
                    detected_email = emails_map.get(u_index)
                    if not detected_email:
                        try:
                            extra_map = await probe_account_emails_map(
                                cookie_dict, auth_user=u_index
                            )
                            emails_map.update(extra_map)
                            detected_email = emails_map.get(u_index)
                        except Exception:
                            pass
                    valid_accounts.append(
                        {
                            "auth_user": u_index,
                            "status_code": resp.status_code,
                            "name": f"Google Account (u/{u_index})",
                            "email": detected_email,
                            "tier": detected_tier,
                        }
                    )
                    logger.info(
                        "子账号 u/%s 探活成功 (HTTP %d, email=%s, tier=%s)",
                        u_index,
                        resp.status_code,
                        detected_email or "unknown",
                        detected_tier,
                    )
                elif resp.status_code == 401:
                    consecutive_failures += 1
                    logger.debug("子账号 u/%s 探活返回 401 (未授权)", u_index)
                else:
                    consecutive_failures += 1
                    logger.debug(
                        "子账号 u/%s 探活返回 HTTP %d", u_index, resp.status_code
                    )
            except Exception as e:
                consecutive_failures += 1
                logger.debug("子账号 u/%s 探活异常: %s", u_index, e)

            if consecutive_failures >= max_fails_in_a_row:
                break

            auth_user_idx += 1

    return valid_accounts
