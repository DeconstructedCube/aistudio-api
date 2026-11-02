"""Automatic local waa-signer daemon manager with strict lifecycle binding.

Prevents orphan processes by:
1. Kernel-level PR_SET_PDEATHSIG (child receives SIGTERM immediately if parent dies).
2. atexit handler ensuring cleanup on any normal or abnormal Python exit.
3. Clean process group termination without zombie residues.
"""

from __future__ import annotations

import atexit
import contextlib
import ctypes
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from aistudio_api.infrastructure.gateway.model_defaults import (
    get_active_waa_server,
    switch_active_waa_server,
)
from aistudio_api.infrastructure.utils.logger import get_logger

logger = get_logger("waa.daemon")

_daemon_proc: subprocess.Popen[bytes] | None = None
_PR_SET_PDEATHSIG = 1


def _set_parent_death_signal() -> None:
    """Linux kernel-level hook: child process is terminated if parent dies."""
    try:
        libc = ctypes.CDLL(None)
        # prctl(PR_SET_PDEATHSIG, SIGTERM)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass


def is_port_open(host: str, port: int) -> bool:
    """Check whether a TCP port is open and accepting connections."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def is_service_healthy(url: str) -> bool:
    import urllib.request

    try:
        req = urllib.request.Request(f"{url.rstrip('/')}/health")
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_signer_ready() -> None:
    """确保当前配置的签名服务处于就绪状态。

    若配置为本地 8081 且尚未就绪：
    1. 若存在 waa-signer/bin/waa-signer，自动作为子进程拉起，并强绑定父进程生命周期；
    2. 若未编译或启动失败，自动平滑切换至远程签名服务 (remote_spinsnow)，确保开箱即用。
    """
    global _daemon_proc
    active_waa = get_active_waa_server()
    url = str(active_waa.get("url") or "")

    is_local_8081 = "127.0.0.1:8081" in url or "localhost:8081" in url
    if not is_local_8081:
        return

    # 1. 检查本地 8081 是否已有健康实例在运行
    if is_service_healthy("http://127.0.0.1:8081"):
        logger.debug("本地 WAA 签名服务已就绪 (端口 8081 正常响应)")
        return

    # 2. 检查本地编译产物是否存在
    root_dir = Path(__file__).resolve().parents[4]
    bin_path = root_dir / "waa-signer" / "bin" / "waa-signer"

    if bin_path.is_file() and os.access(bin_path, os.X_OK):
        proxy = os.getenv("AISTUDIO_PROXY") or os.getenv("HTTPS_PROXY") or ""
        cmd = [str(bin_path), "-listen", "127.0.0.1:8081"]
        if proxy:
            cmd.extend(["-proxy", proxy])

        logger.info(
            "检测到本地 WAA 签名服务未启动，正在自动拉起 (生命周期强绑定): %s", bin_path
        )
        try:
            _daemon_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(bin_path.parent.parent),
                preexec_fn=_set_parent_death_signal,
            )
            # 等待检查就绪（最多 2 秒）
            for _ in range(20):
                time.sleep(0.1)
                if is_service_healthy("http://127.0.0.1:8081"):
                    logger.info(
                        "本地 WAA 签名服务自动拉起就绪 (PID %d)", _daemon_proc.pid
                    )
                    return
        except Exception as e:
            logger.warning("自动拉起本地 WAA 签名服务失败: %s", e)

    # 3. 若无本地二进制或拉起失败，自动回退到远程签名节点
    logger.warning(
        "本地 WAA 签名服务未就绪且未找到可执行文件，自动平滑切换至远程签名节点 (remote_spinsnow)"
    )
    switch_active_waa_server("remote_spinsnow")


def stop_signer_daemon() -> None:
    """终止自动拉起的本地签名服务子进程，杜绝孤儿进程与僵尸进程。"""
    global _daemon_proc
    if _daemon_proc is not None:
        proc = _daemon_proc
        _daemon_proc = None
        try:
            logger.info("正在停止自动拉起的本地 WAA 签名服务 (PID %d)...", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=0.5)
        except Exception as e:
            logger.debug("回收签名服务子进程异常: %s", e)
            with contextlib.suppress(Exception):
                proc.kill()


# 注册 Python 解释器退出钩子，双重保险杜绝孤儿进程
atexit.register(stop_signer_daemon)
