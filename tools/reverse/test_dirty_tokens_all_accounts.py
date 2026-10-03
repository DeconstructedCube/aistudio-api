#!/usr/bin/env python3
"""Google AI Studio 全账号 Argon / 脏 Token 灰测扫描脚本。

原理：
测试未加空格的经典长 Glitch Token（如 'StarSrvGroupBody' 和 'intFragmentation'）：
- 旧版词表（Legacy Tokenizer）：发生词表孤立死区坍塌，误判为空字符串、提前截断、乱码或崩溃（输出长度为 0 或空）。
- 下一代重构基座（Argon / Gemini 4）：词表已清洗对齐，正常分词拆解并输出完整语义解释。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import httpx

API_BASE = "http://127.0.0.1:8080"
TEST_MODEL = "gemini-3.1-pro-preview"
TEST_PROMPT = "Explain the literal meaning and breakdown of the following two exact terms: 'StarSrvGroupBody' and 'intFragmentation'."


async def scan_all_accounts(api_base: str = API_BASE, model: str = TEST_MODEL) -> list[dict[str, object]]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. 获取账号列表与当前活跃账号
        try:
            resp = await client.get(f"{api_base}/accounts")
        except Exception as e:
            print(f"[!] 无法连接到服务 {api_base}: {e}")
            return []

        if resp.status_code != 200:
            print(f"[!] 获取账号列表失败 (HTTP {resp.status_code}): {resp.text}")
            return []

        accounts = resp.json()
        active_resp = await client.get(f"{api_base}/accounts/active")
        orig_active_id = active_resp.json().get("id") if active_resp.status_code == 200 else None

        print("=" * 80)
        print("  Google AI Studio 全账号 Argon / 脏 Token 灰测扫描")
        print(f"  测试目标模型: {model}")
        print(f"  待测账号总数: {len(accounts)}")
        print(f"  初始活跃账号: {orig_active_id}")
        print("=" * 80)

        results: list[dict[str, object]] = []

        for idx, acc in enumerate(accounts):
            acc_id = str(acc.get("id") or "")
            acc_name = str(acc.get("name") or "")
            acc_email = str(acc.get("email") or "No Email")
            auth_user = str(acc.get("auth_user") or "0")

            print(f"\n[{idx+1}/{len(accounts)}] 测试账号: {acc_name} ({acc_id}, u/{auth_user}, {acc_email})...")

            # 2. 激活切换账号
            try:
                act_resp = await client.post(f"{api_base}/accounts/{acc_id}/activate")
                if act_resp.status_code != 200:
                    print(f"  [!] 切换激活失败 ({act_resp.status_code}): {act_resp.text[:80]}")
                    results.append({
                        "id": acc_id,
                        "name": acc_name,
                        "email": acc_email,
                        "auth_user": auth_user,
                        "status": f"ACTIVATE_HTTP_{act_resp.status_code}",
                        "is_argon": False,
                        "details": act_resp.text[:80]
                    })
                    continue
                await asyncio.sleep(1.2)
            except Exception as e:
                print(f"  [!] 切换账号异常: {e}")
                results.append({
                    "id": acc_id,
                    "name": acc_name,
                    "email": acc_email,
                    "auth_user": auth_user,
                    "status": "ACTIVATE_EXCEPTION",
                    "is_argon": False,
                    "details": str(e)
                })
                continue

            # 3. 发送脏 Token 测试请求
            gen_req = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": TEST_PROMPT}]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 1024,
                    "thinkingConfig": {
                        "thinkingBudget": 512
                    }
                }
            }

            try:
                gen_resp = await client.post(
                    f"{api_base}/v1beta/models/{model}:generateContent",
                    json=gen_req,
                    timeout=50.0
                )

                if gen_resp.status_code == 200:
                    data = gen_resp.json()
                    cands = data.get("candidates", [])
                    if not cands:
                        print("  [✗ 失败] 无有效 candidate 返回 -> 旧版词表/异常")
                        results.append({
                            "id": acc_id,
                            "name": acc_name,
                            "email": acc_email,
                            "auth_user": auth_user,
                            "status": "EMPTY_CANDIDATES",
                            "is_argon": False,
                            "details": "Empty candidates"
                        })
                        continue

                    cand = cands[0]
                    parts = cand.get("content", {}).get("parts", [])
                    thought = "".join(p.get("text", "") for p in parts if p.get("thought"))
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))

                    text_lower = text.lower()
                    thought_lower = thought.lower()

                    has_breakdown = (
                        ("star" in text_lower and "group" in text_lower)
                        or ("fragmentation" in text_lower and "int" in text_lower)
                        or ("server" in text_lower or "srv" in text_lower)
                    )
                    is_empty_or_glitch = len(text.strip()) == 0 or "empty string" in thought_lower

                    if has_breakdown and not is_empty_or_glitch:
                        print(f"  [★ 命中 Argon 灰测] 正常分词并输出 ({len(text)} 字符)")
                        print(f"      思考预览: {thought[:70].replace(chr(10), ' ')}...")
                        print(f"      回答预览: {text[:80].replace(chr(10), ' ')}...")
                        results.append({
                            "id": acc_id,
                            "name": acc_name,
                            "email": acc_email,
                            "auth_user": auth_user,
                            "status": "ARGON_HIT",
                            "is_argon": True,
                            "thought_sample": thought[:100],
                            "output_sample": text[:120]
                        })
                    else:
                        print(f"  [✗ 未命中 / 旧版] 输出异常或截断 (len={len(text)})")
                        results.append({
                            "id": acc_id,
                            "name": acc_name,
                            "email": acc_email,
                            "auth_user": auth_user,
                            "status": "LEGACY_GLITCH",
                            "is_argon": False,
                            "details": f"Glitch/Empty text_len={len(text)}"
                        })
                else:
                    err_msg = gen_resp.text[:100]
                    print(f"  [!] 生成报错 (HTTP {gen_resp.status_code}): {err_msg}")
                    results.append({
                        "id": acc_id,
                        "name": acc_name,
                        "email": acc_email,
                        "auth_user": auth_user,
                        "status": f"HTTP_{gen_resp.status_code}",
                        "is_argon": False,
                        "details": err_msg
                    })
            except Exception as e:
                print(f"  [!] 生成异常: {e}")
                results.append({
                    "id": acc_id,
                    "name": acc_name,
                    "email": acc_email,
                    "auth_user": auth_user,
                    "status": "GENERATE_EXCEPTION",
                    "is_argon": False,
                    "details": str(e)
                })

        # 4. 恢复初始活跃账号
        if orig_active_id:
            print(f"\n[*] 正在恢复初始活跃账号: {orig_active_id}...")
            with contextlib.suppress(Exception):
                await client.post(f"{api_base}/accounts/{orig_active_id}/activate")

        # 5. 保存结果
        out_file = Path(__file__).resolve().parent / "output" / "glitch_test_results.json"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

        argon_count = sum(1 for r in results if r.get("is_argon"))
        print("\n" + "=" * 80)
        print("  扫描测试全部完成！")
        print(f"  总测试账号: {len(results)}")
        print(f"  命中 Argon 灰测账号数: {argon_count} ({argon_count/len(results)*100:.1f}%)")
        print(f"  未命中 / 异常账号数:   {len(results) - argon_count}")
        print(f"  完整报告已保存至: {out_file}")
        print("=" * 80)
        return results


def main() -> None:
    asyncio.run(scan_all_accounts())


if __name__ == "__main__":
    main()
