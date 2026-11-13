"""HTTP 请求头的大小写不敏感合并工具。

HTTP/2 传输层会把头部名统一规范成小写，若缓存字典里同时存在仅大小写不同的两个键
（例如 `X-Goog-Ext-519733851-bin` 与 `X-Goog-Ext-519733851-Bin`），httpx 会把它们当成
两个独立的头一起发到 wire 上，Google 侧会判定请求签名不一致并直接返回 403。

因此凡是“覆盖式写入”上游请求头的地方（默认头、签名服务动态头、鉴权头、Cookie）都必须
走本模块的合并函数，先按 `k.lower()` 剔除旧键，再写入最新的键名与值。
"""

from __future__ import annotations


def merge_header_case_insensitive(
    headers: dict[str, str], key: str, value: str
) -> None:
    """按大小写不敏感语义写入请求头，同名（忽略大小写）旧键先删除再写入新键。"""
    lowered = key.lower()
    for existing_key in [name for name in headers if name.lower() == lowered]:
        del headers[existing_key]
    headers[key] = value


__all__ = ["merge_header_case_insensitive"]
